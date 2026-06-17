"""Gateway helper utilities — extracted from services/gateway/main.py in sprint-3.1.

These helpers were previously defined inline in the 3,920-LOC gateway main.py
god-file. Extracting them to this module lets per-domain router modules under
services/gateway/routers/ depend on them without re-importing main.py and
introducing a load-time cycle.

The functions exported here MUST NOT depend on app.state or any FastAPI
lifespan-mutated state — they take all they need from `request` and module-level
settings. This is what makes them safe to use from any route module.

──────────────────────────────────────────────────────────────────────────────
Canonical SSE event registry (UI source of truth: ui/src/pages/LiveFeed.jsx
EVENT_META). All 17 names — and their emitters — are documented below.
Adding a new event type is a UI ↔ gateway contract change: bump EVENT_META
AND wire the publisher here in the same PR.

  1.  ``llm_proxy_call``        — emitted from services/gateway/middleware.py
      :``_run_inference_proxy`` when the per-request inference proxy admits
      a call (post-decision, pre-dispatch).
  2.  ``llm_proxy_escalate``    — emitted from services/gateway/middleware.py
      :``_run_inference_proxy`` when the inference proxy blocks
      (injection / risk-score / tool-guard).
  3.  ``approval_required``     — emitted from services/gateway/middleware.py
      autonomy ladder when ``check_autonomy_contract`` returns
      ``requires_approval``.
  4.  ``approval_resolved``     — emitted from services/autonomy/router.py
      :``add_override`` (POST /autonomy/overrides) when an admin approves /
      rejects a pending action.
  5.  ``risk_updated``          — emitted from services/gateway/main.py
      gateway proxy on /execute when the per-call risk crosses 0.5.
  6.  ``tool_executed``         — emitted from services/gateway/main.py
      gateway proxy on /execute success path.
  7.  ``policy_decision``       — emitted from
      services/gateway/routers/policy.py:``_maybe_publish_policy_event`` on
      every non-trivial /policy/* outcome (deny / escalate / approval).
  8.  ``alert``                 — reserved UI fallback for unknown event
      types. Not emitted directly by the backend; LiveFeed renders any
      unrecognised SSE message as ``alert`` (see LiveFeed.jsx:186).
  9.  ``agent_changed``         — emitted from
      services/gateway/routers/agents.py:``update_agent`` on PATCH
      /agents/{id} success.
  10. ``agent_created``         — emitted from
      services/gateway/routers/agents.py:``create_agent`` and
      ``wizard_create_agent`` on agent creation.
  11. ``agent_deleted``         — emitted from
      services/gateway/routers/agents.py:``delete_agent`` on DELETE
      /agents/{id} success.
  12. ``incident_updated``      — emitted from
      services/gateway/routers/incidents.py on PATCH /incidents/{id}
      success.
  13. ``insight_generated``     — emitted from services/insight/worker.py
      after the Groq narrative engine writes a fresh insight row.
  14. ``behavior_flagged``      — emitted from services/gateway/middleware.py
      once the behavior baseline + canonical evaluation surfaces non-empty
      findings (deviation, attack-chain match, anomaly).
  15. ``would_have_blocked``    — emitted from services/gateway/middleware.py
      shadow-mode downgrade branch when policy DENY/ESCALATE is observed
      but suppressed during the 14-day observe window.
  16. ``quota_warning``         — emitted from
      services/gateway/routers/tenant.py:``get_tenant_quota`` when the
      tenant's monthly request cap crosses 80% (idempotent per
      tenant + calendar month).
  17. ``kill_switch``           — emitted from
      services/gateway/routers/decision.py:``toggle_kill_switch`` and
      ``disengage_kill_switch`` on tenant kill-switch flip.

Publishing is best-effort and per-tenant — see :func:`publish_event` for the
channel naming + failure semantics.
"""
from __future__ import annotations

import json
import time
from typing import Any

import httpx
import structlog
from fastapi import HTTPException, Request, Response

from sdk.common.config import settings

logger = structlog.get_logger(__name__)

# ─────────────────────────────────────────────────────────────
# Role enforcement (shared across all role-gated routers)
# ─────────────────────────────────────────────────────────────

# Roles allowed to call any /admin/* GET that proxies tenant-list data.
# Note: the gateway middleware (services/gateway/_mw_auth.py:163-168) already
# blocks WRITE methods for non-(ADMIN|SECURITY); this set guards GETs that
# would otherwise be readable by any authenticated VIEWER.
_ADMIN_ROLES = frozenset(("ADMIN", "SECURITY"))


def require_admin_role(request: Request) -> None:
    """Reject any caller whose JWT role is not ADMIN or SECURITY."""
    role = (getattr(request.state, "role", "") or "").upper()
    if role not in _ADMIN_ROLES:
        logger.warning(
            "admin_route_denied",
            role=role,
            path=request.url.path,
            actor=getattr(request.state, "actor", "unknown"),
        )
        raise HTTPException(status_code=403, detail="Admin role required")


def assert_path_tenant_matches_jwt(request: Request, path_tenant_id: str) -> None:
    """Reject cross-tenant operations where the URL path tenant != JWT tenant.

    Used by tenant-scoped admin routes (kill-switch, etc.). Without this an
    authenticated SECURITY user in Tenant A could change Tenant B's state by
    changing the URL path parameter.
    """
    claims = getattr(request.state, "jwt_claims", None) or {}
    jwt_tenant = claims.get("tenant_id") or ""
    if not jwt_tenant or jwt_tenant != path_tenant_id:
        logger.critical(
            "cross_tenant_path_access_blocked",
            jwt_tenant=jwt_tenant,
            path_tenant=path_tenant_id,
            actor=getattr(request.state, "actor", "unknown"),
        )
        raise HTTPException(
            status_code=403,
            detail="Cannot operate on a different tenant",
        )


# ─────────────────────────────────────────────────────────────
# Inter-service headers + response forwarding
# ─────────────────────────────────────────────────────────────


def clamp_int(value: str | None, default: int, lo: int, hi: int) -> int:
    """Parse a query-string integer, clamp it into ``[lo, hi]``, fall back to
    ``default`` on parse failure. Used everywhere a sub-router needs to read
    a paginated ``?limit=`` / ``?offset=`` from request.query_params."""
    if value is None:
        return default
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    if n < lo:
        return lo
    if n > hi:
        return hi
    return n


def internal_headers(request: Request | None = None) -> dict[str, str]:
    """Build internal service-to-service headers, forwarding tenant/auth context.

    X-ACP-Role is injected from the JWT-validated request.state.role — never from
    the client header — to prevent privilege escalation via forged role claims.
    """
    headers: dict[str, str] = {"X-Internal-Secret": settings.INTERNAL_SECRET}
    if request is not None:
        for h in ("X-Tenant-ID", "X-Agent-ID", "Authorization", "X-Request-ID", "X-Trace-ID"):
            val = request.headers.get(h)
            if val:
                headers[h] = val

        if "X-Tenant-ID" not in headers and hasattr(request.state, "tenant_id") and request.state.tenant_id is not None:
            headers["X-Tenant-ID"] = str(request.state.tenant_id)

        if "X-Agent-ID" not in headers and hasattr(request.state, "agent_id") and request.state.agent_id is not None:
            headers["X-Agent-ID"] = str(request.state.agent_id)

        # Cookie-to-header bridge for browser/SSE clients.
        if "Authorization" not in headers:
            cookie_token = request.cookies.get("acp_token")
            if cookie_token:
                headers["Authorization"] = f"Bearer {cookie_token}"
        role = getattr(request.state, "role", None)
        if role:
            headers["X-ACP-Role"] = str(role)
        actor = getattr(request.state, "actor", None)
        if actor:
            headers["X-ACP-Actor"] = str(actor)
    return headers


def passthrough(resp: httpx.Response) -> Response:
    """Forward upstream JSON + status code to the client.

    Without this the prior pattern `return resp.json()` collapsed every
    upstream 4xx/5xx into a 200 body — the UI's request() wrapper only treats
    non-2xx as errors so it silently rendered empty state on every backend
    failure.
    """
    try:
        body = resp.json()
    except Exception:
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type", "application/octet-stream"),
        )
    return Response(
        content=json.dumps(body),
        status_code=resp.status_code,
        media_type="application/json",
    )


# ─────────────────────────────────────────────────────────────
# SSE publish helper (shared with autonomy + decision proxies)
# ─────────────────────────────────────────────────────────────


async def trust_proxy(base_url: str, path: str, request: Request) -> Response:
    """Generic forwarder for runtime-trust services (graph, flight, autonomy).

    Preserves method, body, query string, and tenant + auth context.
    Returns the upstream JSON + status code via passthrough().

    Body handling: parses JSON eagerly when the body is JSON so httpx sets
    the Content-Type header upstream. Non-JSON bodies forward raw with the
    original Content-Type. Fixes a 2026-05-13 bug where _internal_headers
    didn't include Content-Type and the upstream saw bytes-not-JSON.
    """
    import json as _json
    client = request.app.state.client  # httpx.AsyncClient
    method = request.method.upper()
    url = f"{base_url.rstrip('/')}{path}"
    headers = internal_headers(request)
    json_body: Any | None = None
    raw_body: bytes | None = None
    if method in ("POST", "PATCH", "PUT"):
        try:
            raw_body = await request.body()
            if raw_body:
                try:
                    json_body = _json.loads(raw_body)
                except Exception:
                    json_body = None
        except Exception:
            raw_body = None
    try:
        if json_body is not None:
            resp = await client.request(
                method, url,
                headers=headers, params=request.query_params, json=json_body,
                timeout=10.0,
            )
        else:
            ct = request.headers.get("content-type")
            fwd = dict(headers)
            if ct:
                fwd["Content-Type"] = ct
            resp = await client.request(
                method, url,
                headers=fwd, params=request.query_params, content=raw_body,
                timeout=10.0,
            )
        return passthrough(resp)
    except Exception as exc:
        logger.error("trust_proxy_error", base_url=base_url, path=path, error=str(exc))
        return Response(
            content=_json.dumps({"success": False, "error": f"Upstream unreachable: {type(exc).__name__}"}),
            status_code=502,
            media_type="application/json",
        )


async def publish_event(
    r: Any, tenant_id: str, event_type: str, data: dict, *, agent_id: str | None = None
) -> None:
    """Publish a single SSE event to the per-tenant Redis Pub/Sub channel.

    Best-effort — never raises. SSE is a side channel and a publish failure
    must NOT bring down the originating handler.
    """
    if not tenant_id:
        return
    try:
        payload = json.dumps({
            "type": event_type,
            "data": data,
            "ts": int(time.time()),
        })
    except Exception as exc:
        logger.warning("sse_publish_serialise_failed", event_type=event_type, error=str(exc))
        return
    try:
        await r.publish(f"acp:events:{tenant_id}", payload)
    except Exception as exc:
        logger.warning("sse_publish_failed", event_type=event_type, error=str(exc))
    if agent_id:
        try:
            await r.publish(f"acp:events:{tenant_id}:{agent_id}", payload)
        except Exception as exc:
            logger.warning(
                "sse_publish_agent_channel_failed",
                event_type=event_type, agent_id=agent_id, error=str(exc),
            )
