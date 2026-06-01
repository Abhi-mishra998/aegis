"""Gateway proxy routes — extracted from main.py in sprint-5.1.

Three router groups share this module because they all use the trust_proxy
helper and have no other state:
  - runtime-trust passthrough: /graph, /flight, /autonomy
  - playbooks: /playbooks (proxied to autonomy)
  - webhooks: /webhooks/* (proxied to autonomy)
  - notifications: /notifications (proxied to audit)
"""
from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from sdk.common.config import settings
from services.gateway._helpers import (
    internal_headers,
    trust_proxy,
)

router = APIRouter()

# ─────────────────────────────────────────────────────────────
# Runtime-trust passthrough proxies
# ─────────────────────────────────────────────────────────────


@router.api_route("/graph/{full_path:path}", methods=["GET", "POST", "PATCH", "DELETE"], tags=["graph"])
async def proxy_graph(full_path: str, request: Request) -> Any:
    return await trust_proxy(settings.IDENTITY_GRAPH_SERVICE_URL, f"/graph/{full_path}", request)


@router.api_route("/flight/{full_path:path}", methods=["GET", "POST", "PATCH", "DELETE"], tags=["flight"])
async def proxy_flight(full_path: str, request: Request) -> Any:
    return await trust_proxy(settings.FLIGHT_RECORDER_SERVICE_URL, f"/flight/{full_path}", request)


@router.api_route("/autonomy/{full_path:path}", methods=["GET", "POST", "PATCH", "DELETE"], tags=["autonomy"])
async def proxy_autonomy(full_path: str, request: Request) -> Any:
    return await trust_proxy(settings.AUTONOMY_SERVICE_URL, f"/autonomy/{full_path}", request)


# ─────────────────────────────────────────────────────────────
# Playbooks (proxied to autonomy service /autonomy/playbooks/*)
# ─────────────────────────────────────────────────────────────


@router.get("/playbooks/templates", tags=["playbooks"])
async def get_playbook_templates_proxy(request: Request) -> Any:
    return await trust_proxy(settings.AUTONOMY_SERVICE_URL, "/autonomy/playbooks/templates", request)


# /playbooks/stats must precede the /playbooks/{pid} catch-all below;
# otherwise FastAPI matches "stats" as a path param and the upstream
# UUID-validating route returns 422.
@router.get("/playbooks/stats", tags=["playbooks"])
async def get_playbooks_stats(request: Request) -> Any:
    """Aggregate playbook stats — calls upstream and rolls up counters.

    Tolerates sub-call failures by zeroing the affected counters; never raises.
    """
    client: httpx.AsyncClient = request.app.state.client
    hdrs = internal_headers(request)
    base = settings.AUTONOMY_SERVICE_URL.rstrip("/")

    total_installed = active = triggers_24h = 0
    last_trigger_at: str | None = None
    total_templates = 0

    try:
        pb = await client.get(f"{base}/autonomy/playbooks", headers=hdrs,
                              params=request.query_params, timeout=5.0)
        if pb.status_code == 200:
            data = pb.json()
            playbooks = data.get("data", data) if isinstance(data, dict) else data
            if isinstance(playbooks, list):
                total_installed = len(playbooks)
                for p in playbooks:
                    if not isinstance(p, dict):
                        continue
                    if p.get("status") == "active":
                        active += 1
                    triggers_24h += int(p.get("triggers_24h") or 0)
                    last = p.get("last_trigger_at")
                    if last and (last_trigger_at is None or str(last) > last_trigger_at):
                        last_trigger_at = str(last)
    except Exception:
        pass

    try:
        tpl = await client.get(f"{base}/autonomy/playbooks/templates", headers=hdrs, timeout=5.0)
        if tpl.status_code == 200:
            data = tpl.json()
            templates = data.get("data", data) if isinstance(data, dict) else data
            total_templates = len(templates) if isinstance(templates, list) else 0
    except Exception:
        pass

    return JSONResponse({
        "total_installed": total_installed,
        "total_templates": total_templates,
        "active": active,
        "triggers_24h": triggers_24h,
        "last_trigger_at": last_trigger_at,
    })


@router.get("/playbooks", tags=["playbooks"])
async def list_playbooks_proxy(request: Request) -> Any:
    return await trust_proxy(settings.AUTONOMY_SERVICE_URL, "/autonomy/playbooks", request)


@router.post("/playbooks", tags=["playbooks"])
async def create_playbook_proxy(request: Request) -> Any:
    return await trust_proxy(settings.AUTONOMY_SERVICE_URL, "/autonomy/playbooks", request)


@router.get("/playbooks/{pid}/runs", tags=["playbooks"])
async def list_playbook_runs_proxy(pid: str, request: Request) -> Any:
    return await trust_proxy(settings.AUTONOMY_SERVICE_URL, f"/autonomy/playbooks/{pid}/runs", request)


@router.post("/playbooks/{pid}/trigger", tags=["playbooks"])
async def trigger_playbook_proxy(pid: str, request: Request) -> Any:
    return await trust_proxy(settings.AUTONOMY_SERVICE_URL, f"/autonomy/playbooks/{pid}/trigger", request)


@router.get("/playbooks/{pid}", tags=["playbooks"])
async def get_playbook_proxy(pid: str, request: Request) -> Any:
    return await trust_proxy(settings.AUTONOMY_SERVICE_URL, f"/autonomy/playbooks/{pid}", request)


@router.patch("/playbooks/{pid}", tags=["playbooks"])
async def update_playbook_proxy(pid: str, request: Request) -> Any:
    return await trust_proxy(settings.AUTONOMY_SERVICE_URL, f"/autonomy/playbooks/{pid}", request)


@router.delete("/playbooks/{pid}", tags=["playbooks"])
async def delete_playbook_proxy(pid: str, request: Request) -> Any:
    return await trust_proxy(settings.AUTONOMY_SERVICE_URL, f"/autonomy/playbooks/{pid}", request)


# ─────────────────────────────────────────────────────────────
# Webhooks (proxied to autonomy service)
# ─────────────────────────────────────────────────────────────


@router.get("/webhooks/config", tags=["webhooks"])
async def get_webhook_config_proxy(request: Request) -> Any:
    return await trust_proxy(settings.AUTONOMY_SERVICE_URL, "/autonomy/webhooks/config", request)


@router.post("/webhooks/config", tags=["webhooks"])
async def save_webhook_config_proxy(request: Request) -> Any:
    return await trust_proxy(settings.AUTONOMY_SERVICE_URL, "/autonomy/webhooks/config", request)


@router.post("/webhooks/test/slack", tags=["webhooks"])
async def test_slack_proxy(request: Request) -> Any:
    return await trust_proxy(settings.AUTONOMY_SERVICE_URL, "/autonomy/webhooks/test/slack", request)


@router.post("/webhooks/test/pagerduty", tags=["webhooks"])
async def test_pagerduty_proxy(request: Request) -> Any:
    return await trust_proxy(settings.AUTONOMY_SERVICE_URL, "/autonomy/webhooks/test/pagerduty", request)


@router.post("/webhooks/test/webhook", tags=["webhooks"])
async def test_generic_webhook_proxy(request: Request) -> Any:
    return await trust_proxy(settings.AUTONOMY_SERVICE_URL, "/autonomy/webhooks/test/webhook", request)


# ─────────────────────────────────────────────────────────────
# Notifications (proxied to audit service)
# ─────────────────────────────────────────────────────────────


@router.get("/notifications", tags=["notifications"])
async def list_notifications_proxy(request: Request) -> Any:
    return await trust_proxy(settings.AUDIT_SERVICE_URL, "/notifications", request)


@router.post("/notifications", tags=["notifications"])
async def create_notification_proxy(request: Request) -> Any:
    return await trust_proxy(settings.AUDIT_SERVICE_URL, "/notifications", request)


@router.post("/notifications/read-all", tags=["notifications"])
async def mark_all_notifications_read_proxy(request: Request) -> Any:
    return await trust_proxy(settings.AUDIT_SERVICE_URL, "/notifications/read-all", request)


@router.get("/notifications/count", tags=["notifications"])
async def get_notifications_count_proxy(request: Request) -> Any:
    return await trust_proxy(settings.AUDIT_SERVICE_URL, "/notifications/count", request)


@router.post("/notifications/{notification_id}/read", tags=["notifications"])
async def mark_notification_read_proxy(notification_id: str, request: Request) -> Any:
    return await trust_proxy(settings.AUDIT_SERVICE_URL, f"/notifications/{notification_id}/read", request)
