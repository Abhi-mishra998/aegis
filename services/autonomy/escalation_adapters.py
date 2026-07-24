"""ATF v3.2 §5.7 + Appendix D.2 — escalation channel adapters.

Existing `webhook_executor.py` already covers Slack + Jira + ServiceNow.
Adds: Microsoft Teams, PagerDuty, email, canonical webhook.

Every adapter delivers the same signed escalation object; the channel
NEVER changes the semantics (timeout→DENY, deny-wins, single-action
binding). That contract lives in the caller (`services/autonomy/router.py`);
adapters here just render + POST/send.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any, Literal

Channel = Literal["slack", "teams", "pagerduty", "email", "servicenow", "jira", "webhook"]


@dataclass(frozen=True)
class EscalationPayload:
    """The signed object every adapter transmits, matching §5.7."""

    gate_decision_id: str          # ties approval to exactly one action
    tenant_id: str
    agent_id: str
    claim: str
    action_class: Literal["C0", "C1", "C2", "C3"]
    approvers: list[str]           # SCIM refs
    quorum: int
    timeout_seconds: int
    approve_url: str               # signed HMAC URL — deny-wins on click
    reject_url: str
    signature: str                 # Ed25519 over canonical body


def render_teams_card(p: EscalationPayload) -> dict[str, Any]:
    """MS Teams Adaptive Card — post to a channel via incoming-webhook URL."""
    return {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard",
                "version": "1.4",
                "body": [
                    {"type": "TextBlock", "size": "Large", "weight": "Bolder",
                     "text": f"Approval required · {p.action_class}"},
                    {"type": "TextBlock", "wrap": True, "text": p.claim},
                    {"type": "FactSet", "facts": [
                        {"title": "Agent",  "value": p.agent_id},
                        {"title": "Tenant", "value": p.tenant_id},
                        {"title": "Quorum", "value": f"{p.quorum} of {len(p.approvers)}"},
                        {"title": "Timeout", "value": f"{p.timeout_seconds}s → DENY"},
                    ]},
                ],
                "actions": [
                    {"type": "Action.OpenUrl", "title": "Approve", "url": p.approve_url},
                    {"type": "Action.OpenUrl", "title": "Reject",  "url": p.reject_url},
                ],
            },
        }],
    }


def render_pagerduty_event(p: EscalationPayload, routing_key: str) -> dict[str, Any]:
    """PagerDuty Events API v2 — trigger event to an on-call schedule."""
    return {
        "routing_key": routing_key,
        "event_action": "trigger",
        "dedup_key": p.gate_decision_id,
        "payload": {
            "summary": f"Aegis {p.action_class} escalation: {p.claim[:80]}",
            "source":  p.tenant_id,
            "severity": "critical" if p.action_class == "C3" else "warning",
            "custom_details": {
                "agent_id": p.agent_id,
                "approve_url": p.approve_url,
                "reject_url": p.reject_url,
                "timeout_seconds": p.timeout_seconds,
                "quorum": p.quorum,
            },
        },
    }


def render_email(p: EscalationPayload) -> tuple[str, str]:
    """(subject, body) tuple. Plain text — HTML alt lives at rendering layer."""
    subject = f"[Aegis][{p.action_class}] approval required — {p.claim[:60]}"
    body = (
        f"Aegis is holding one action pending your decision.\n"
        f"\n"
        f"  Agent:   {p.agent_id}\n"
        f"  Tenant:  {p.tenant_id}\n"
        f"  Class:   {p.action_class}\n"
        f"  Claim:   {p.claim}\n"
        f"  Quorum:  {p.quorum} of {len(p.approvers)}\n"
        f"  Timeout: {p.timeout_seconds}s (defaults to DENY)\n"
        f"\n"
        f"  Approve: {p.approve_url}\n"
        f"  Reject:  {p.reject_url}\n"
        f"\n"
        f"This approval binds exactly one gate_decision_id ({p.gate_decision_id}).\n"
        f"Any explicit REJECT overrides pending or later APPROVEs (deny-wins).\n"
    )
    return subject, body


def render_canonical_webhook(p: EscalationPayload) -> dict[str, Any]:
    """Canonical JSON body — arbitrary ITSM integrations POST-consume this."""
    return {"aegis_escalation": asdict(p)}


# Optional: a channel-selecting dispatch helper. Callers can also just
# render + send themselves — this dispatch is a convenience.
def dispatch(
    channel: Channel,
    payload: EscalationPayload,
    send: Callable[[Channel, dict[str, Any]], None],
    *,
    pagerduty_routing_key: str = "",
) -> None:
    if channel == "teams":
        send(channel, render_teams_card(payload))
    elif channel == "pagerduty":
        send(channel, render_pagerduty_event(payload, pagerduty_routing_key))
    elif channel == "email":
        subject, body = render_email(payload)
        send(channel, {"subject": subject, "body": body})
    elif channel == "webhook":
        send(channel, render_canonical_webhook(payload))
    else:
        raise ValueError(f"unsupported channel: {channel}")


if __name__ == "__main__":
    p = EscalationPayload(
        gate_decision_id="gd_01J",
        tenant_id="acme",
        agent_id="ag_1",
        claim="Wire $75,000 to Vendor X",
        action_class="C3",
        approvers=["scim://acme/Users/cfo"],
        quorum=1,
        timeout_seconds=1800,
        approve_url="https://ha.aegisagent.in/slack/approve/gd_01J?sig=...",
        reject_url="https://ha.aegisagent.in/slack/reject/gd_01J?sig=...",
        signature="ed25519:signature-b64",
    )

    teams = render_teams_card(p)
    assert teams["type"] == "message"
    assert any("Approve" in a["title"] for a in teams["attachments"][0]["content"]["actions"])

    pd = render_pagerduty_event(p, routing_key="R_ROUTING")
    assert pd["payload"]["severity"] == "critical"
    assert pd["dedup_key"] == p.gate_decision_id

    subj, body = render_email(p)
    assert "C3" in subj
    assert "deny-wins" in body

    canon = render_canonical_webhook(p)
    assert canon["aegis_escalation"]["gate_decision_id"] == p.gate_decision_id

    sent: list[tuple[str, dict]] = []
    dispatch("teams", p, lambda c, body: sent.append((c, body)))
    dispatch("pagerduty", p, lambda c, body: sent.append((c, body)),
             pagerduty_routing_key="R")
    dispatch("email", p, lambda c, body: sent.append((c, body)))
    dispatch("webhook", p, lambda c, body: sent.append((c, body)))
    assert len(sent) == 4

    try:
        dispatch("slack", p, lambda c, body: None)  # type: ignore[arg-type]
        raise AssertionError("expected ValueError for unsupported")
    except ValueError:
        pass  # slack lives in existing webhook_executor

    print("escalation_adapters OK")
