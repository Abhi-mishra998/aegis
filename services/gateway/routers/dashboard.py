"""Gateway dashboard aggregator — extracted from main.py in sprint-7.6.

The /dashboard/state route fans out to 5 downstream services and rolls
their responses up into the JSON the ExecutiveDashboard.jsx page renders.
Latent bug fix: the previous in-main implementation used `_passthrough()`
which returns a Response object, then immediately did `.get('data', ...)`
on it — which the `isinstance(x, dict)` guard quietly turned into {}.
The dashboard's `audit`, `billing`, `insights` fields were therefore
always empty in production. This module uses `resp.json()` directly so
the data actually reaches the UI.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Request

from sdk.common.config import settings
from services.gateway._helpers import internal_headers

router = APIRouter(tags=["dashboard"])


async def _safe_json(client, url: str, headers: dict, params: dict | None = None) -> dict:
    """Best-effort GET that returns the parsed JSON body or {} on any error.

    Distinct from _helpers.passthrough which is for proxy ENDPOINTS that
    need to forward a Response. Aggregator endpoints want the body, not
    the Response.
    """
    try:
        resp = await client.get(url, headers=headers, params=params or {}, timeout=5.0)
        if resp.status_code >= 500:
            return {}
        try:
            body = resp.json()
        except Exception:
            return {}
        return body if isinstance(body, dict) else {"data": body}
    except Exception:
        return {}


@router.get("/dashboard/state")
async def dashboard_state(request: Request) -> dict[str, Any]:
    """Aggregated state for the executive dashboard.

    Fans out to audit, registry, usage, insight, and decision services
    concurrently. Each service failure returns an empty fallback — the
    dashboard always loads.
    """
    client = request.app.state.client
    headers = internal_headers(request)
    tenant_id = request.headers.get("X-Tenant-ID", "")

    audit_r, agents_r, billing_r, insights_r = await asyncio.gather(
        _safe_json(client, f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/summary",     headers),
        _safe_json(client, f"{settings.REGISTRY_SERVICE_URL.rstrip('/')}/agents/summary", headers),
        _safe_json(client, f"{settings.USAGE_SERVICE_URL.rstrip('/')}/billing/summary",   headers),
        _safe_json(client, f"{settings.INSIGHT_SERVICE_URL.rstrip('/')}/insights",         headers, {"limit": 5}),
    )

    kill_r: dict = {}
    if tenant_id:
        kill_r = await _safe_json(
            client,
            f"{settings.DECISION_SERVICE_URL.rstrip('/')}/decision/kill-switch/{tenant_id}",
            headers,
        )

    # Each upstream may have envelope `{"data": ...}` or be the bare body.
    def _unwrap(payload: dict) -> Any:
        return payload.get("data", payload) if isinstance(payload, dict) else {}

    agents_summary = _unwrap(agents_r)
    if not isinstance(agents_summary, dict):
        agents_summary = {}

    return {
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
