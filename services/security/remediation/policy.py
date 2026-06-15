"""Sprint 6 — per-tenant remediation policy.

A `RemediationPolicy` decides which actions the executor fires when an
incident reaches `quarantined`. Defaults are "all on except paging" — the
revoke + kill + audit actions are cheap, idempotent, and obviously
useful; paging requires the tenant to have configured a webhook URL.

Storage: one Redis hash per tenant. No DB for Sprint 6 — the policy is
operator-managed via the gateway router, and a fresh tenant inherits
defaults silently.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


def _policy_key(tenant_id: str) -> str:
    return f"acp:remediation:policy:{tenant_id}"


@dataclass(frozen=True)
class RemediationPolicy:
    """Toggles + paging endpoint. New fields can be added without
    breaking older serialised hashes — the loader applies defaults for
    any missing field."""
    revoke_api_keys:     bool = True
    kill_active_tokens:  bool = True
    page_oncall:         bool = False        # off until a webhook is set
    audit_log:           bool = True
    webhook_url:         str  = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT_POLICY = RemediationPolicy()


def _to_bool(v: Any) -> bool:
    """Redis hash values are bytes/str. '1', 'true', 'yes' → True."""
    if isinstance(v, (bytes, bytearray)):
        v = v.decode("utf-8", "replace")
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _decode_str(v: Any) -> str:
    if isinstance(v, (bytes, bytearray)):
        return v.decode("utf-8", "replace")
    return "" if v is None else str(v)


async def policy_for_tenant(redis: Any, tenant_id: str) -> RemediationPolicy:
    """Load the tenant's policy from Redis or return defaults.

    Missing fields fall back to defaults so partial policies (e.g.
    a tenant who only set `webhook_url`) don't accidentally disable the
    revoke / kill / audit actions.
    """
    try:
        raw = await redis.hgetall(_policy_key(tenant_id))
    except Exception:
        return DEFAULT_POLICY
    if not raw:
        return DEFAULT_POLICY
    d: dict[str, str] = {}
    for k, v in raw.items():
        d[_decode_str(k)] = _decode_str(v)
    return RemediationPolicy(
        revoke_api_keys=_to_bool(d.get("revoke_api_keys", "1")),
        kill_active_tokens=_to_bool(d.get("kill_active_tokens", "1")),
        page_oncall=_to_bool(d.get("page_oncall", "0")),
        audit_log=_to_bool(d.get("audit_log", "1")),
        webhook_url=d.get("webhook_url", ""),
    )


async def upsert_policy(redis: Any, tenant_id: str, policy: RemediationPolicy) -> None:
    """Replace the tenant's policy. Idempotent."""
    k = _policy_key(tenant_id)
    await redis.hset(k, mapping={
        "revoke_api_keys":    "1" if policy.revoke_api_keys else "0",
        "kill_active_tokens": "1" if policy.kill_active_tokens else "0",
        "page_oncall":        "1" if policy.page_oncall else "0",
        "audit_log":          "1" if policy.audit_log else "0",
        "webhook_url":        policy.webhook_url or "",
    })
    # No TTL — policy is "configured" state, not cached state.
