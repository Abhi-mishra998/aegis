"""Gateway proxy routes for the policy service.

3 routes lifted out of services/gateway/main.py in the sprint-5 audit
cleanup:

  POST /policy/simulate  — dry-run a policy against recent audit events
  POST /policy/test      — run a Rego policy against sample inputs
  POST /policy/upload    — persist a named Rego policy

The two evaluate-style endpoints fan out a ``policy_decision`` SSE
event on non-trivial outcomes (deny / escalate / approval_required).
The fan-out helpers ``_is_nontrivial_policy_decision`` and
``_extract_policy_reasons`` moved with the routes — they were already
private to these handlers.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from redis.asyncio import Redis

from sdk.common.config import settings
from sdk.common.redis import get_redis_client
from services.gateway._helpers import (
    internal_headers,
    passthrough,
    publish_event,
)

router = APIRouter()

_redis: Redis = get_redis_client(settings.REDIS_URL, decode_responses=False)


def _is_nontrivial_policy_decision(decision_data: Any) -> bool:
    """True when a policy result is worth notifying the LiveFeed about.

    Allowed decisions are noisy and not actionable — only surface deny /
    escalate / approval_required style outcomes.
    """
    if not isinstance(decision_data, dict):
        return False
    if decision_data.get("allowed") is False:
        return True
    action = str(decision_data.get("action", "")).lower()
    return action in {"deny", "escalate", "approval_required", "block"}


def _extract_policy_reasons(decision_data: Any) -> list[str]:
    """Normalise the heterogeneous policy reason shapes into list[str]."""
    if not isinstance(decision_data, dict):
        return []
    reasons = decision_data.get("reasons")
    if isinstance(reasons, list) and reasons:
        return [str(r) for r in reasons[:3]]
    reason = decision_data.get("reason")
    return [str(reason)] if reason else []


async def _maybe_publish_policy_event(
    request: Request, body: Any, result: Any, source: str
) -> None:
    """Emit ``policy_decision`` SSE event for non-trivial outcomes."""
    tenant_id_str = request.headers.get("X-Tenant-ID", "") or (
        str(getattr(request.state, "tenant_id", "") or "")
    )
    data = result.get("data", result) if isinstance(result, dict) else None
    if not tenant_id_str or not _is_nontrivial_policy_decision(data):
        return
    body_dict = body if isinstance(body, dict) else {}
    agent_id_val = str(body_dict.get("agent_id", "") or "")
    reasons_list = _extract_policy_reasons(data)
    await publish_event(
        _redis, tenant_id_str, "policy_decision",
        {
            "agent_id": agent_id_val or None,
            "action": body_dict.get("tool") or body_dict.get("action"),
            "allowed": bool(data.get("allowed", False)) if isinstance(data, dict) else False,
            "reasons": reasons_list,
            "source": source,
        },
        agent_id=agent_id_val or None,
    )


def _policy_base() -> str:
    return settings.POLICY_SERVICE_URL.rstrip("/")


@router.post("/policy/simulate", tags=["policy"])
async def simulate_policy(request: Request) -> Any:
    """Proxy → Policy service dry-run simulation."""
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{_policy_base()}/policy/simulate",
        json=body,
        headers=internal_headers(request),
    )
    if resp.status_code == 200:
        try:
            result = resp.json()
        except Exception:
            result = None
        await _maybe_publish_policy_event(request, body, result, source="simulate")
    return passthrough(resp)


@router.post("/policy/test", tags=["policy"])
async def test_policy_proxy(request: Request) -> Any:
    """Proxy → Policy service — test Rego against sample inputs."""
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{_policy_base()}/policy/test",
        json=body,
        headers=internal_headers(request),
    )
    if resp.status_code == 200:
        try:
            result = resp.json()
        except Exception:
            result = None
        await _maybe_publish_policy_event(request, body, result, source="test")
    return passthrough(resp)


@router.post("/policy/upload", tags=["policy"])
async def upload_policy_proxy(request: Request) -> Any:
    """Proxy → Policy service — save a named Rego policy (ADMIN/SECURITY only).

    On a successful upload we INCR ``acp:tenant:policy_version:{tenant_id}``.
    The X-Aegis-Approval-ID replay path in ``proxy_helpers.lookup_approval``
    rejects any approval whose recorded policy_version doesn't match the
    current value — closing the policy-drift replay attack documented in
    testing.md.
    """
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{_policy_base()}/policy/upload",
        json=body,
        headers=internal_headers(request),
    )
    if 200 <= resp.status_code < 300:
        try:
            tenant_id_str = (
                getattr(request.state, "tenant_id", None)
                or request.headers.get("X-Tenant-ID", "")
            )
            if tenant_id_str:
                from sdk.common.redis import get_redis_client
                from sdk.common.config import settings as _settings
                r = get_redis_client(_settings.REDIS_URL, decode_responses=True)
                try:
                    await r.incr(f"acp:tenant:policy_version:{tenant_id_str}")
                finally:
                    try:
                        await r.aclose()
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001
            # Version-bump failure leaves the SDK on the previous version —
            # operator just needs to re-upload to surface the new policy on
            # replay. Better than failing the whole upload.
            pass
    return passthrough(resp)
