"""Gateway decision-service proxy routes — extracted from main.py in sprint-4.E.

The kill-switch routes here are the most security-critical proxies in the
gateway: a bug here was the sprint-1 cross-tenant escalation. Both the
gateway-side check (assert_path_tenant_matches_jwt) and the decision-side
check (services/decision/router.py:_assert_authenticated_tenant_matches)
must agree, or the escalation re-opens.

N9 fix (brutal-review 2026-06-21): the GET kill-switch proxy must also
enforce ADMIN/SECURITY_ANALYST role at the gateway boundary. Without it
ANY authenticated user (including READ_ONLY/DEVELOPER) in the same tenant
could poll the proxy to detect when the security team activates an
emergency lockdown — letting an attacker time exfil operations around
active defenses. The downstream decision service does enforce the same
role check (services/decision/router.py:_require_admin_or_security) but
defense-in-depth requires the gateway to reject before the request ever
leaves the perimeter. Matches the pattern in services/gateway/routers/
admin.py — see audit-v2 §3.4 finding.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from sdk.common.config import settings
from sdk.common.redis import get_redis_client
from services.gateway._helpers import (
    assert_path_tenant_matches_jwt,
    internal_headers,
    passthrough,
    publish_event,
    require_admin_role,
)

router = APIRouter(tags=["decision"])

# Per-process Redis client for SSE publishes. Reuses the same singleton
# helper main.py uses, so connection-pool behaviour matches.
_redis = get_redis_client(settings.REDIS_URL, decode_responses=False)


def _clamp_int(value: str | None, default: int, lo: int, hi: int) -> int:
    """Local copy of the gateway _clamp_int helper, scoped to decision
    routes that need ``?limit=`` query-param coercion."""
    if value is None:
        return default
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


@router.get("/decision/history")
async def decision_history(request: Request) -> Any:
    """Proxy → Decision service decision history."""
    resp = await request.app.state.client.get(
        f"{settings.DECISION_SERVICE_URL.rstrip('/')}/decision/history",
        params={"limit": _clamp_int(request.query_params.get("limit"), 20, 1, 200)},
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/decision/summary")
async def decision_summary(request: Request) -> Any:
    """Proxy → Decision service risk summary (Redis-based counters)."""
    resp = await request.app.state.client.get(
        f"{settings.DECISION_SERVICE_URL.rstrip('/')}/decision/summary",
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/decision/kill-switch/{tenant_id}")
async def get_kill_switch_status(tenant_id: str, request: Request) -> Any:
    """Proxy → Decision service kill-switch status.

    Role-gated (OWNER/ADMIN/SECURITY_ANALYST/SECURITY) so a READ_ONLY or
    DEVELOPER caller can't poll the endpoint to detect when the security
    team activates an emergency lockdown. See module docstring for the
    full N9 brutal-review writeup.
    """
    require_admin_role(request)
    assert_path_tenant_matches_jwt(request, tenant_id)
    resp = await request.app.state.client.get(
        f"{settings.DECISION_SERVICE_URL.rstrip('/')}/decision/kill-switch/{tenant_id}",
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.post("/decision/kill-switch/{tenant_id}")
async def toggle_kill_switch(tenant_id: str, request: Request) -> Any:
    """Proxy → Decision service toggle kill-switch.

    Role-gated at the gateway as defense-in-depth; the gateway middleware
    already blocks non-admin POSTs but an explicit role check keeps the
    write + read paths symmetric and removes the implicit dependency on
    middleware ordering.
    """
    require_admin_role(request)
    assert_path_tenant_matches_jwt(request, tenant_id)
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{settings.DECISION_SERVICE_URL.rstrip('/')}/decision/kill-switch/{tenant_id}",
        json=body,
        headers=internal_headers(request),
    )
    if resp.status_code in (200, 201, 204):
        await publish_event(
            _redis, tenant_id, "kill_switch",
            {
                "tenant_id": tenant_id,
                "engaged": True,
                "tenant_wide": True,
                "reason": (body or {}).get("reason") if isinstance(body, dict) else None,
            },
        )
    return passthrough(resp)


@router.delete("/decision/kill-switch/{tenant_id}")
async def disengage_kill_switch(tenant_id: str, request: Request) -> Any:
    """Proxy → Decision service disengage kill-switch.

    Role-gated (see toggle_kill_switch above for rationale).
    """
    require_admin_role(request)
    assert_path_tenant_matches_jwt(request, tenant_id)
    resp = await request.app.state.client.delete(
        f"{settings.DECISION_SERVICE_URL.rstrip('/')}/decision/kill-switch/{tenant_id}",
        headers=internal_headers(request),
    )
    if resp.status_code in (200, 204):
        await publish_event(
            _redis, tenant_id, "kill_switch",
            {
                "tenant_id": tenant_id,
                "engaged": False,
                "tenant_wide": True,
            },
        )
    return passthrough(resp)
