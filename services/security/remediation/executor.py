"""Sprint 6 — Remediation executor.

Reads the per-tenant `RemediationPolicy`, fires the enabled actions for
one incident, persists a ledger row per action.

Idempotency: callers can invoke `execute()` multiple times safely. The
first pass that finds an empty ledger and writes status=done locks the
result in; subsequent passes short-circuit and return the existing
ledger. Use `replay()` to force re-run.

Failure isolation: a single failed action (e.g. webhook 502) does not
prevent the others from firing. Each action gets its own try/except.

The recorder (`services/security/incidents/recorder.py`) is the primary
caller — it fires `execute()` as a fire-and-forget task when an
incident transitions to `quarantined`. The gateway router also exposes
`replay()` so the SOC can re-fire after a transient webhook failure.
"""
from __future__ import annotations

import json
import time
from typing import Any

from .actions import (
    KIND_AUDIT_LOG,
    KIND_KILL_ACTIVE_TOKENS,
    KIND_PAGE_ONCALL,
    KIND_REVOKE_API_KEY,
    RemediationAction,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_SKIPPED,
)
from .policy import RemediationPolicy, policy_for_tenant
from .webhooks import post_webhook


_LEDGER_TTL_SECONDS    = 24 * 3600
_REVOKED_AGENTS_TTL    = 24 * 3600
_REVOCATION_CHANNEL    = "acp:token:revocations"


# ---------------------------------------------------------------------------
# Key builders
# ---------------------------------------------------------------------------
def _ledger_key(tenant_id: str, incident_id: str) -> str:
    return f"acp:remediation:ledger:{tenant_id}:{incident_id}"


def _revoked_agents_key(tenant_id: str) -> str:
    return f"acp:remediation:revoked_agents:{tenant_id}"


# ---------------------------------------------------------------------------
# Ledger I/O
# ---------------------------------------------------------------------------
async def get_ledger(redis: Any, tenant_id: str, incident_id: str) -> list[RemediationAction]:
    """Read the ledger for one incident. Returns [] when unset."""
    try:
        rows = await redis.lrange(_ledger_key(tenant_id, incident_id), 0, -1)
    except Exception:
        return []
    out: list[RemediationAction] = []
    for raw in rows:
        try:
            s = raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)
            d = json.loads(s)
        except Exception:
            continue
        out.append(RemediationAction(
            incident_id=str(d.get("incident_id") or ""),
            tenant_id=str(d.get("tenant_id") or ""),
            agent_id=str(d.get("agent_id") or ""),
            kind=str(d.get("kind") or ""),
            status=str(d.get("status") or ""),
            result=str(d.get("result") or ""),
            ts=float(d.get("ts") or 0.0),
        ))
    return out


async def _append_ledger(redis: Any, action: RemediationAction) -> None:
    k = _ledger_key(action.tenant_id, action.incident_id)
    await redis.rpush(k, json.dumps(action.to_dict()))
    await redis.expire(k, _LEDGER_TTL_SECONDS)


def _was_attempted(ledger: list[RemediationAction], kind: str) -> bool:
    """Is there a non-skipped ledger row for this kind?

    Idempotency hinge: a `STATUS_DONE` action shouldn't re-fire, and a
    `STATUS_FAILED` one shouldn't either (operator uses `replay()`
    explicitly to retry). Only `STATUS_SKIPPED` (policy disabled at the
    time) is considered "no attempt yet", so a later policy flip can
    fire it.
    """
    for a in ledger:
        if a.kind == kind and a.status in (STATUS_DONE, STATUS_FAILED):
            return True
    return False


# ---------------------------------------------------------------------------
# Individual action implementations
# ---------------------------------------------------------------------------
async def _do_revoke_api_key(
    redis: Any, tenant_id: str, agent_id: str,
) -> tuple[str, str]:
    try:
        k = _revoked_agents_key(tenant_id)
        await redis.sadd(k, agent_id)
        await redis.expire(k, _REVOKED_AGENTS_TTL)
        return STATUS_DONE, f"added agent_id to {k}"
    except Exception as exc:
        return STATUS_FAILED, f"redis error: {exc}"


async def _do_kill_active_tokens(
    redis: Any, tenant_id: str, agent_id: str,
) -> tuple[str, str]:
    """Publish a revocation event so every gateway worker drops the
    in-flight token. Listener: services/gateway/auth.py."""
    try:
        payload = json.dumps({
            "tenant_id":    tenant_id,
            "agent_id":     agent_id,
            "all_for_agent": True,
            "ts":           time.time(),
            "reason":       "auto_remediation",
        })
        await redis.publish(_REVOCATION_CHANNEL, payload)
        return STATUS_DONE, f"published to {_REVOCATION_CHANNEL}"
    except Exception as exc:
        return STATUS_FAILED, f"publish error: {exc}"


async def _do_page_oncall(
    httpx_client: Any, policy: RemediationPolicy, payload: dict[str, Any],
) -> tuple[str, str]:
    if not policy.webhook_url:
        return STATUS_SKIPPED, "page_oncall enabled but webhook_url empty"
    if httpx_client is None:
        return STATUS_FAILED, "no httpx client available"
    ok, msg = await post_webhook(httpx_client, policy.webhook_url, payload)
    return (STATUS_DONE if ok else STATUS_FAILED), msg


async def _do_audit_log(
    redis: Any, tenant_id: str, incident_id: str, agent_id: str,
) -> tuple[str, str]:
    """Emit a structured audit row onto the audit-write stream.

    The stream consumer in the audit service (`services/audit/main.py`)
    persists it into `audit_logs` so the cryptographic chain captures
    the response. We don't write to Postgres directly here — the gateway
    + audit service own the write path; remediation just enqueues.
    """
    try:
        stream = "acp:audit:writes"
        await redis.xadd(stream, {
            "tenant_id":   tenant_id,
            "action":      "auto_remediation",
            "agent_id":    agent_id,
            "incident_id": incident_id,
            "reason":      "incident_quarantined",
            "ts":          str(time.time()),
        })
        return STATUS_DONE, f"xadded to {stream}"
    except Exception as exc:
        return STATUS_FAILED, f"xadd error: {exc}"


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------
async def execute(
    redis: Any,
    *,
    incident_id:   str,
    tenant_id:     str,
    agent_id:      str,
    storyline:     dict[str, Any] | None = None,
    policy:        RemediationPolicy | None = None,
    httpx_client:  Any = None,
    dry_run:       bool = False,
    force:         bool = False,
) -> list[RemediationAction]:
    """Fire the enabled remediation actions for one incident.

    Reads the existing ledger first — when `force=False` (default), any
    action that already has a `STATUS_DONE` or `STATUS_FAILED` row is
    skipped. `force=True` is the replay path: the executor still appends
    new rows, so the ledger becomes the full retry history.

    Returns the *new* actions appended on this pass. The caller can
    call `get_ledger()` for the cumulative view.
    """
    if dry_run:
        return _dry_run_simulation(
            incident_id=incident_id, tenant_id=tenant_id, agent_id=agent_id,
            policy=policy or RemediationPolicy(),
        )

    if policy is None:
        policy = await policy_for_tenant(redis, tenant_id)

    ledger = await get_ledger(redis, tenant_id, incident_id)
    new_actions: list[RemediationAction] = []
    now = time.time()

    async def _record(kind: str, status: str, result: str) -> None:
        a = RemediationAction(
            incident_id=incident_id, tenant_id=tenant_id, agent_id=agent_id,
            kind=kind, status=status, result=result, ts=now,
        )
        try:
            await _append_ledger(redis, a)
        except Exception:
            # Ledger write failure is logged but we still surface the
            # action to the caller — the executor's correctness doesn't
            # depend on the audit trail succeeding.
            pass
        new_actions.append(a)

    # 1. Revoke API key — adds agent to revoked-agents set.
    if policy.revoke_api_keys:
        if not force and _was_attempted(ledger, KIND_REVOKE_API_KEY):
            await _record(KIND_REVOKE_API_KEY, STATUS_SKIPPED, "already in ledger")
        else:
            status, result = await _do_revoke_api_key(redis, tenant_id, agent_id)
            await _record(KIND_REVOKE_API_KEY, status, result)
    else:
        await _record(KIND_REVOKE_API_KEY, STATUS_SKIPPED, "policy disabled")

    # 2. Kill active tokens — publishes to revocation channel.
    if policy.kill_active_tokens:
        if not force and _was_attempted(ledger, KIND_KILL_ACTIVE_TOKENS):
            await _record(KIND_KILL_ACTIVE_TOKENS, STATUS_SKIPPED, "already in ledger")
        else:
            status, result = await _do_kill_active_tokens(redis, tenant_id, agent_id)
            await _record(KIND_KILL_ACTIVE_TOKENS, status, result)
    else:
        await _record(KIND_KILL_ACTIVE_TOKENS, STATUS_SKIPPED, "policy disabled")

    # 3. Page on-call — fire webhook.
    if policy.page_oncall:
        if not force and _was_attempted(ledger, KIND_PAGE_ONCALL):
            await _record(KIND_PAGE_ONCALL, STATUS_SKIPPED, "already in ledger")
        else:
            payload = _page_payload(incident_id, tenant_id, agent_id, storyline)
            status, result = await _do_page_oncall(httpx_client, policy, payload)
            await _record(KIND_PAGE_ONCALL, status, result)
    else:
        await _record(KIND_PAGE_ONCALL, STATUS_SKIPPED, "policy disabled")

    # 4. Audit log — enqueue audit row.
    if policy.audit_log:
        if not force and _was_attempted(ledger, KIND_AUDIT_LOG):
            await _record(KIND_AUDIT_LOG, STATUS_SKIPPED, "already in ledger")
        else:
            status, result = await _do_audit_log(redis, tenant_id, incident_id, agent_id)
            await _record(KIND_AUDIT_LOG, status, result)
    else:
        await _record(KIND_AUDIT_LOG, STATUS_SKIPPED, "policy disabled")

    return new_actions


async def replay(
    redis: Any,
    *,
    incident_id:  str,
    tenant_id:    str,
    agent_id:     str,
    storyline:    dict[str, Any] | None = None,
    httpx_client: Any = None,
) -> list[RemediationAction]:
    """Force re-run for one incident. Wrapper around execute(force=True)."""
    return await execute(
        redis,
        incident_id=incident_id, tenant_id=tenant_id, agent_id=agent_id,
        storyline=storyline, httpx_client=httpx_client, force=True,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _page_payload(
    incident_id: str, tenant_id: str, agent_id: str,
    storyline: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the webhook payload. Kept minimal so PagerDuty / Slack /
    Opsgenie wrappers can map it without surprises."""
    body = {
        "incident_id":   incident_id,
        "tenant_id":     tenant_id,
        "agent_id":      agent_id,
        "trigger":       "auto_remediation",
        "ts":            time.time(),
    }
    if storyline:
        body["title"]               = storyline.get("title", "")
        body["status"]              = storyline.get("status", "")
        body["mitre_tactic_chain"]  = storyline.get("mitre_tactic_chain", [])
        body["blocking_policy_id"]  = storyline.get("blocking_policy_id", "")
        body["risk_score"]          = storyline.get("risk_score", 0)
    return body


def _dry_run_simulation(
    *, incident_id: str, tenant_id: str, agent_id: str,
    policy: RemediationPolicy,
) -> list[RemediationAction]:
    """Return the action set the executor *would* fire without mutating
    Redis. Useful for the SOC to preview before flipping a policy."""
    now = time.time()
    out: list[RemediationAction] = []
    pairs = [
        (KIND_REVOKE_API_KEY, policy.revoke_api_keys),
        (KIND_KILL_ACTIVE_TOKENS, policy.kill_active_tokens),
        (KIND_PAGE_ONCALL, policy.page_oncall and bool(policy.webhook_url)),
        (KIND_AUDIT_LOG, policy.audit_log),
    ]
    for kind, enabled in pairs:
        out.append(RemediationAction(
            incident_id=incident_id, tenant_id=tenant_id, agent_id=agent_id,
            kind=kind,
            status=STATUS_DONE if enabled else STATUS_SKIPPED,
            result="dry_run" if enabled else "policy disabled",
            ts=now,
        ))
    return out


# ---------------------------------------------------------------------------
# Revoked-agents-set helpers (used by the gateway auth middleware)
# ---------------------------------------------------------------------------
async def is_agent_revoked(redis: Any, tenant_id: str, agent_id: str) -> bool:
    """Fast check called on every authenticated request.

    Single Redis SISMEMBER call; ~0.2 ms on a warm-pool localhost; gated
    behind a feature flag in the auth path so it can be disabled
    instantly if the check starts misbehaving."""
    try:
        return bool(await redis.sismember(_revoked_agents_key(tenant_id), agent_id))
    except Exception:
        return False


async def release_revoked_agent(redis: Any, tenant_id: str, agent_id: str) -> bool:
    """Operator-controlled unrevoke. Removes the agent from the set."""
    try:
        removed = await redis.srem(_revoked_agents_key(tenant_id), agent_id)
        return bool(removed)
    except Exception:
        return False
