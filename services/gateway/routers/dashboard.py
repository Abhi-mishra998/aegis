"""Gateway dashboard aggregator — extracted from main.py in sprint-7.6.

The /dashboard/state route fans out to 5 downstream services and rolls
their responses up into the JSON the ExecutiveDashboard.jsx page renders.
Latent bug fix: the previous in-main implementation used `_passthrough()`
which returns a Response object, then immediately did `.get('data', ...)`
on it — which the `isinstance(x, dict)` guard quietly turned into {}.
The dashboard's `audit`, `billing`, `insights` fields were therefore
always empty in production. This module uses `resp.json()` directly so
the data actually reaches the UI.

U10 (2026-06-17): The first version of this module replaced the
Response-handling bug but still swallowed every downstream 5xx / network
failure as `return {}` — operators saw "all good" while audit / billing /
insights were actually down. This is operator-hostile.

Now: the handler tracks WHICH downstreams failed, logs a structured
warning per failure, and surfaces the failure to the caller:

- All required downstreams dead → HTTP 503 with
  {"error": "downstream_unavailable", "missing": [...]}
- Some required downstreams dead → HTTP 200 with
  {"success": True, "partial": True, "missing": [...], "data": {...}}
  so the UI can render partial KPIs with a banner.
- All live → HTTP 200 with {"success": True, "data": {...}} (unchanged).

The Playwright dashboard.spec.ts test that routes all /dashboard/state
hits to 503 still works under the Option B behavior — when every
required downstream is dead we return 503.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from sdk.common.config import settings
from services.gateway._helpers import internal_headers

router = APIRouter(tags=["dashboard"])

log = logging.getLogger("gateway.dashboard")


async def _safe_json(
    name: str,
    client,
    url: str,
    headers: dict,
    params: dict | None = None,
) -> tuple[str, dict, bool]:
    """Best-effort GET that returns (name, parsed-body, alive).

    `alive` is False on any of: connection error, timeout, non-2xx,
    unparseable body. The handler uses `alive` to decide whether to
    return partial / 503 to the caller — silently returning {} for a
    dead downstream is operator-hostile (caller sees "all good" while
    the service is actually down).

    Distinct from _helpers.passthrough which is for proxy ENDPOINTS that
    need to forward a Response. Aggregator endpoints want the body, not
    the Response.
    """
    try:
        resp = await client.get(url, headers=headers, params=params or {}, timeout=5.0)
    except Exception as exc:
        log.warning(
            "dashboard.downstream_unreachable name=%s url=%s err=%s",
            name, url, exc.__class__.__name__,
        )
        return name, {}, False

    if resp.status_code >= 500:
        log.warning(
            "dashboard.downstream_5xx name=%s url=%s status=%d",
            name, url, resp.status_code,
        )
        return name, {}, False
    if resp.status_code >= 400:
        # 4xx (e.g. 404 on missing endpoint) is a real failure too — the
        # caller cannot render audit/billing KPIs from an empty body.
        # We still mark it dead so the operator sees it.
        log.warning(
            "dashboard.downstream_4xx name=%s url=%s status=%d",
            name, url, resp.status_code,
        )
        return name, {}, False

    try:
        body = resp.json()
    except Exception as exc:
        log.warning(
            "dashboard.downstream_unparseable name=%s url=%s err=%s",
            name, url, exc.__class__.__name__,
        )
        return name, {}, False

    payload = body if isinstance(body, dict) else {"data": body}
    return name, payload, True


@router.get("/dashboard/state")
async def dashboard_state(request: Request):
    """Aggregated state for the executive dashboard.

    Fans out to audit, registry, usage, insight, and decision services
    concurrently. Tracks which downstreams responded so the caller can
    see partial / fully-degraded state.

    Returns:
        - 200 with `partial: True` + `missing: [...]` when some required
          downstreams failed but at least one returned.
        - 503 with `error: "downstream_unavailable"` + `missing: [...]`
          when EVERY required downstream is dead.
        - 200 (no partial flag) when all required downstreams are live.
    """
    client = request.app.state.client
    headers = internal_headers(request)
    tenant_id = request.headers.get("X-Tenant-ID", "")

    audit_res, agents_res, billing_res, insights_res = await asyncio.gather(
        _safe_json("audit",    client, f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/summary",     headers),
        _safe_json("agents",   client, f"{settings.REGISTRY_SERVICE_URL.rstrip('/')}/agents/summary", headers),
        _safe_json("billing",  client, f"{settings.USAGE_SERVICE_URL.rstrip('/')}/billing/summary",   headers),
        _safe_json("insights", client, f"{settings.INSIGHT_SERVICE_URL.rstrip('/')}/insights",         headers, {"limit": 5}),
    )

    # kill-switch is OPTIONAL — only consulted when tenant_id is present
    # and its absence doesn't count toward `missing`.
    kill_r: dict = {}
    if tenant_id:
        _, kill_r, kill_alive = await _safe_json(
            "kill_switch",
            client,
            f"{settings.DECISION_SERVICE_URL.rstrip('/')}/decision/kill-switch/{tenant_id}",
            headers,
        )
        if not kill_alive:
            # Surfaced via logs already; we intentionally don't add it to
            # `missing` because kill-switch is a soft signal — its absence
            # shouldn't trip the partial banner.
            kill_r = {}

    # Roll up the four required downstreams and track who failed.
    required = [audit_res, agents_res, billing_res, insights_res]
    missing = [name for (name, _body, alive) in required if not alive]
    live_count = sum(1 for (_n, _b, alive) in required if alive)

    # All four dead → 503. The caller has nothing to render and lying
    # with "success: True, data: {}" is exactly the failure mode this
    # fix exists to remove.
    if live_count == 0:
        log.error(
            "dashboard.all_downstreams_dead tenant_id=%s missing=%s",
            tenant_id or "-", ",".join(missing),
        )
        return JSONResponse(
            status_code=503,
            content={
                "success": False,
                "error": "downstream_unavailable",
                "missing": missing,
                "ts": int(time.time()),
            },
        )

    audit_r   = audit_res[1]
    agents_r  = agents_res[1]
    billing_r = billing_res[1]
    insights_r = insights_res[1]

    # Each upstream may have envelope `{"data": ...}` or be the bare body.
    def _unwrap(payload: dict) -> Any:
        return payload.get("data", payload) if isinstance(payload, dict) else {}

    agents_summary = _unwrap(agents_r)
    if not isinstance(agents_summary, dict):
        agents_summary = {}

    body: dict[str, Any] = {
        "success": True,
        "data": {
            "audit":       _unwrap(audit_r) if isinstance(_unwrap(audit_r), dict) else {},
            "agents": {
                "total":       agents_summary.get("total", 0),
                "active":      agents_summary.get("active", 0),
                "quarantined": agents_summary.get("quarantined", 0),
                "high_risk":   agents_summary.get("high_risk", 0),
            },
            "billing":     _unwrap(billing_r) if isinstance(_unwrap(billing_r), dict) else {},
            "insights":    _unwrap(insights_r) if isinstance(_unwrap(insights_r), list) else [],
            "kill_switch": _unwrap(kill_r) if isinstance(_unwrap(kill_r), dict) else {},
            "ts": int(time.time()),
        },
    }

    if missing:
        # Partial — at least one required downstream is live, at least one
        # is dead. Surfaced as a flag on the body so the UI can render a
        # "Audit subsystem unreachable" banner above whatever KPIs did
        # come back.
        body["partial"] = True
        body["missing"] = missing
        log.warning(
            "dashboard.partial tenant_id=%s missing=%s",
            tenant_id or "-", ",".join(missing),
        )

    return body
