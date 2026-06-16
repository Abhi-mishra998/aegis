"""Gateway helper utilities — extracted from services/gateway/main.py in sprint-3.1.

These helpers were previously defined inline in the 3,920-LOC gateway main.py
god-file. Extracting them to this module lets per-domain router modules under
services/gateway/routers/ depend on them without re-importing main.py and
introducing a load-time cycle.

The functions exported here MUST NOT depend on app.state or any FastAPI
lifespan-mutated state — they take all they need from `request` and module-level
settings. This is what makes them safe to use from any route module.
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
# Sprint 1 added OWNER (top tier) + SECURITY_ANALYST (renamed from SECURITY);
# both legacy + new names are accepted so existing JWTs keep working.
# The gateway middleware (services/gateway/_mw_auth.py) already blocks WRITE
# methods for non-admin-tier roles; this set guards GETs that would
# otherwise be readable by any READ_ONLY/VIEWER user.
_ADMIN_ROLES = frozenset(("OWNER", "ADMIN", "SECURITY_ANALYST", "SECURITY"))


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
