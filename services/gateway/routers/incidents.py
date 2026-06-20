"""Gateway proxy routes for the incident-management surface.

All 10 ``/incidents/*`` routes lifted out of services/gateway/main.py in
the sprint-5 audit cleanup. Routes split across two upstream services:

  * ``API_SERVICE_URL``    — create / list / get / patch + actions endpoints
                            (incidents are persisted in the api service's
                            DB, with the api service owning the state
                            machine + assignment rules)
  * ``AUDIT_SERVICE_URL``  — transitions metadata, timeline comments, PDF
                            export (audit owns the cryptographic chain
                            that forensic exports embed)

The `/incidents/{incident_id}/export` route streams the upstream PDF
through a manually-constructed StreamingResponse because passthrough()
would materialise the binary into JSON.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis

from sdk.common.config import settings
from sdk.common.redis import get_redis_client
from services.gateway._helpers import (
    internal_headers,
    passthrough,
    publish_event,
    reject_mismatched_tenant_query,
    trust_proxy,
)

router = APIRouter()

# Module-level Redis client — same constructor / config as the gateway's
# main.py keeps for SSE publishes. Sub-router-local so route handlers can
# call publish_event() without depending on app.state lifespan order.
_redis: Redis = get_redis_client(settings.REDIS_URL, decode_responses=False)


def _api_base() -> str:
    return settings.API_SERVICE_URL.rstrip("/")


@router.post("/incidents", tags=["Incidents"])
async def create_incident(request: Request) -> Any:
    """Proxy → API service create incident. Injects tenant_id from headers."""
    body = await request.json()
    body = dict(body)
    if "tenant_id" not in body:
        body["tenant_id"] = request.headers.get("X-Tenant-ID", "")
    resp = await request.app.state.client.post(
        f"{_api_base()}/incidents",
        json=body,
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/incidents/transitions", tags=["Incidents"])
async def incidents_transitions(request: Request) -> Any:
    """Proxy → Audit service: valid state machine transitions for incidents."""
    return await trust_proxy(settings.AUDIT_SERVICE_URL, "/incidents/transitions", request)


@router.get("/incidents/summary", tags=["Incidents"])
async def incident_summary(request: Request) -> Any:
    """Proxy → API service incident summary (security score, MTTR, open counts)."""
    resp = await request.app.state.client.get(
        f"{_api_base()}/incidents/summary",
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/incidents", tags=["Incidents"])
async def list_incidents(request: Request) -> Any:
    """Proxy → API service incident list with optional status/severity filters."""
    reject_mismatched_tenant_query(request)
    resp = await request.app.state.client.get(
        f"{_api_base()}/incidents",
        params={
            k: v for k, v in request.query_params.items()
            if k in ("status", "severity", "limit", "offset")
        },
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/incidents/{incident_id}", tags=["Incidents"])
async def get_incident(incident_id: str, request: Request) -> Any:
    """Proxy → API service single incident."""
    resp = await request.app.state.client.get(
        f"{_api_base()}/incidents/{incident_id}",
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.patch("/incidents/{incident_id}", tags=["Incidents"])
async def update_incident(incident_id: str, request: Request) -> Any:
    """Proxy → API service update incident status.

    Emits an SSE ``incident_updated`` event on success so dashboard tiles
    refresh without polling. Same shape as the old inline handler — the
    event is per-tenant + per-agent when an agent_id is present in the
    upstream payload.
    """
    body = await request.json()
    resp = await request.app.state.client.patch(
        f"{_api_base()}/incidents/{incident_id}",
        json=body,
        headers=internal_headers(request),
    )
    result = resp.json()
    tenant_id_str = request.headers.get("X-Tenant-ID", "")
    if tenant_id_str and resp.status_code == 200:
        incident_data = result.get("data", {}) if isinstance(result, dict) else {}
        inc_agent_id = str(incident_data.get("agent_id", "")) if isinstance(incident_data, dict) else ""
        await publish_event(
            _redis, tenant_id_str, "incident_updated", incident_data,
            agent_id=inc_agent_id or None,
        )
    return result


@router.post("/incidents/{incident_id}/actions", tags=["Incidents"])
async def incident_action(incident_id: str, request: Request) -> Any:
    """Proxy → API service add response action to incident."""
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{_api_base()}/incidents/{incident_id}/actions",
        json=body,
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.post("/incidents/{incident_id}/comments", tags=["Incidents"])
async def add_incident_comment(incident_id: str, request: Request) -> Any:
    """Proxy → Audit service: add a timeline comment to an incident."""
    return await trust_proxy(
        settings.AUDIT_SERVICE_URL, f"/incidents/{incident_id}/comments", request
    )


@router.get("/incidents/{incident_id}/comments", tags=["Incidents"])
async def list_incident_comments(incident_id: str, request: Request) -> Any:
    """Proxy → Audit service: list comments for an incident (ASC order)."""
    return await trust_proxy(
        settings.AUDIT_SERVICE_URL, f"/incidents/{incident_id}/comments", request
    )


@router.post("/incidents/{incident_id}/export", tags=["Incidents"])
async def proxy_incident_export(incident_id: str, request: Request) -> Response:
    """Proxy → Audit service forensic incident PDF export.

    Streams the upstream PDF bytes directly so the download arrives intact.
    The audit service endpoint is at
    ``/compliance/incidents/{incident_id}/export`` (mounted under the
    compliance_router prefix).
    """
    upstream_req = request.app.state.client.build_request(
        "POST",
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/compliance/incidents/{incident_id}/export",
        headers=internal_headers(request),
    )
    upstream = await request.app.state.client.send(upstream_req, stream=True)

    async def _relay() -> Any:
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()

    forward_headers: dict[str, str] = {}
    if "content-disposition" in upstream.headers:
        forward_headers["Content-Disposition"] = upstream.headers["content-disposition"]
    if "content-length" in upstream.headers:
        forward_headers["Content-Length"] = upstream.headers["content-length"]

    return StreamingResponse(
        _relay(),
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/octet-stream"),
        headers=forward_headers,
    )
