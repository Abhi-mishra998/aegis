"""Sprint 21 — Slack approval card + signed callback links.

Two surfaces:

  1. :func:`build_slack_card` returns the Block Kit JSON the gateway
     posts to a tenant's incoming-webhook URL the moment /v1/messages
     creates an escalation. The card carries the request context plus
     two large action buttons whose URLs point back at Aegis.

  2. :func:`sign_link` / :func:`verify_sig` mint and check the HMAC
     signature on each button URL. The signature binds (approval_id,
     decision, tenant_id, expiry_unix) so a leaked link can't be
     replayed for a different request or after it expires.

The design deliberately avoids Slack's interactive-message OAuth flow
because that requires a per-customer Slack-app install. An incoming
webhook URL + signed callbacks works on every Slack workspace today
with zero install. Sprint 22 can layer the OAuth flow on top for
customers that want native ephemeral acks; the data path stays the
same.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any


_SIG_VERSION = "v1"   # Bump if the signed payload format changes.


def _canonical(approval_id: str, decision: str, tenant_id: str, exp: int) -> str:
    return f"{_SIG_VERSION}|{approval_id}|{decision}|{tenant_id}|{exp}"


def sign_link(
    *,
    approval_id: str,
    decision: str,       # 'approve' or 'reject'
    tenant_id: str,
    secret: str,
    ttl_seconds: int = 24 * 60 * 60,
) -> tuple[int, str]:
    """Return (expiry_unix, base64-ish HMAC). The caller appends both as
    query params on the callback URL."""
    exp = int(time.time()) + ttl_seconds
    canon = _canonical(approval_id, decision, tenant_id, exp)
    sig = hmac.new(secret.encode(), canon.encode(), hashlib.sha256).hexdigest()
    return exp, sig


def verify_sig(
    *,
    approval_id: str,
    decision: str,
    tenant_id: str,
    exp: int,
    secret: str,
    sig: str,
) -> bool:
    """Constant-time verify the signature + check the deadline."""
    if not secret or not sig:
        return False
    if exp < int(time.time()):
        return False
    canon = _canonical(approval_id, decision, tenant_id, exp)
    expected = hmac.new(secret.encode(), canon.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


def build_slack_card(
    *,
    base_url: str,
    tenant_id: str,
    secret: str,
    approval_id: str,
    approver_role: str,
    matched_pattern: str,
    employee_email: str,
    prompt_excerpt: str,
    requested_at_iso: str | None,
) -> dict[str, Any]:
    """Return the Block-Kit payload to POST to the incoming webhook.

    Two action buttons render as inline links because incoming webhooks
    can't render Slack interactive blocks for an unauthenticated app.
    The fallback design — buttons that look like buttons but are anchor
    links — works in every Slack client and degrades gracefully on the
    mobile app.
    """
    base = base_url.rstrip("/")
    approve_exp, approve_sig = sign_link(
        approval_id=approval_id, decision="approve",
        tenant_id=tenant_id, secret=secret,
    )
    reject_exp, reject_sig = sign_link(
        approval_id=approval_id, decision="reject",
        tenant_id=tenant_id, secret=secret,
    )
    approve_url = (
        f"{base}/slack/approve/{approval_id}"
        f"?exp={approve_exp}&sig={approve_sig}&tenant_id={tenant_id}"
    )
    reject_url = (
        f"{base}/slack/reject/{approval_id}"
        f"?exp={reject_exp}&sig={reject_sig}&tenant_id={tenant_id}"
    )

    header_emoji = {
        "CFO":      ":money_with_wings:",
        "CISO":     ":closed_lock_with_key:",
        "SRE_LEAD": ":gear:",
        "OWNER":    ":lock:",
    }.get(approver_role, ":bell:")

    # Block Kit fields are deliberately narrow — they render fine on
    # Slack desktop and don't truncate awkwardly on the mobile app.
    fields = [
        {"type": "mrkdwn", "text": f"*Approver*\n`{approver_role}`"},
        {"type": "mrkdwn", "text": f"*Rule*\n`{matched_pattern}`"},
        {"type": "mrkdwn", "text": f"*Employee*\n{employee_email or 'unknown'}"},
        {"type": "mrkdwn", "text": f"*When*\n{requested_at_iso or 'just now'}"},
    ]
    return {
        "text": f"Aegis: {approver_role} approval needed for {employee_email}",
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{header_emoji} Aegis approval needed — {approver_role}",
                },
            },
            {"type": "section", "fields": fields},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Prompt the agent was about to send Claude:*\n"
                        f"```{(prompt_excerpt or '').strip()[:500]}```"
                    ),
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": ":white_check_mark: Approve"},
                        "style": "primary",
                        "url": approve_url,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": ":x: Reject"},
                        "style": "danger",
                        "url": reject_url,
                    },
                ],
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            f"approval_id `{approval_id[:18]}…` · the Approve / "
                            f"Reject links are HMAC-signed and expire in 24h."
                        ),
                    },
                ],
            },
        ],
    }


def render_result_html(decision: str, approval_id: str, ok: bool, reason: str = "") -> str:
    """Tiny standalone HTML page returned to the operator's browser
    after they click the Approve / Reject link. No JS, no external
    fonts — works on a phone with restricted data."""
    title = (
        f"Approved" if decision == "approve" and ok
        else "Rejected" if decision == "reject" and ok
        else "Link invalid"
    )
    color = "#10b981" if ok and decision == "approve" else ("#ef4444" if ok else "#f59e0b")
    body_text = (
        f"Approval <code>{approval_id[:18]}…</code> was {title.lower()}." if ok
        else f"This Approve / Reject link is expired or invalid. {reason}".strip()
    )
    return f"""<!doctype html>
<html><head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>Aegis — {title}</title>
<style>
  body {{ background:#0a0a0a; color:#fff; font-family:-apple-system,system-ui,sans-serif;
         margin:0; padding:0; min-height:100vh; display:flex; align-items:center;
         justify-content:center; }}
  .card {{ background:#171717; border:1px solid #2a2a2a; border-radius:12px;
           padding:28px 32px; max-width:420px; }}
  h1 {{ font-size:18px; margin:0 0 12px; color:{color}; }}
  p  {{ font-size:13px; line-height:1.55; color:#bdbdbd; margin:0; }}
  code {{ background:#0a0a0a; padding:2px 6px; border-radius:4px;
          font-family:ui-monospace,Menlo,Consolas,monospace; font-size:12px; }}
</style>
</head><body>
  <div class="card">
    <h1>{title}</h1>
    <p>{body_text}</p>
    <p style="margin-top:14px; color:#7a7a7a; font-size:11px;">
      You can close this tab. The audit trail captured your decision.
    </p>
  </div>
</body></html>
"""
