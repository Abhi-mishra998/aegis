"""Gateway proxy routes for the forensics service.

Routes lifted out of services/gateway/main.py in the sprint-5 audit
cleanup, extended in 2026-06-21 with the timeline / blast-radius /
export proxies that the UI's ``api.js`` already calls (the SPA was
hitting 404 because the backend service exposes them but no gateway
proxy existed):

  GET  /forensics/investigation              — tenant-wide investigation list
  GET  /forensics/investigation/{agent_id}   — per-agent forensic report
  GET  /forensics/replay/{agent_id}          — step-by-step replay
  GET  /forensics/timeline/{agent_id}        — chronological audit timeline
  GET  /forensics/blast-radius/{agent_id}    — connected-agent graph (1..3 hops)
  POST /forensics/export/{agent_id}          — full forensic-package export
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from sdk.common.config import settings
from services.gateway._helpers import internal_headers, passthrough

router = APIRouter()


def _base() -> str:
    return settings.FORENSICS_SERVICE_URL.rstrip("/")


@router.get("/forensics/investigation", tags=["forensics"])
async def forensics_investigation(request: Request) -> Any:
    """Proxy → Forensics service investigation list."""
    resp = await request.app.state.client.get(
        f"{_base()}/forensics/investigation",
        headers=internal_headers(request),
        params=dict(request.query_params),
    )
    return passthrough(resp)


@router.get("/forensics/investigation/{agent_id}", tags=["forensics"])
async def get_investigation_report(agent_id: str, request: Request) -> Any:
    """Proxy → Forensics service investigation report for an agent."""
    resp = await request.app.state.client.get(
        f"{_base()}/forensics/investigation/{agent_id}",
        headers=internal_headers(request),
        params=dict(request.query_params),
    )
    return passthrough(resp)


@router.get("/forensics/replay/{agent_id}", tags=["forensics"])
async def replay_agent_behavior(agent_id: str, request: Request) -> Any:
    """Proxy → Forensics service forensic replay for an agent."""
    resp = await request.app.state.client.get(
        f"{_base()}/forensics/replay/{agent_id}",
        headers=internal_headers(request),
        params=dict(request.query_params),
    )
    return passthrough(resp)


@router.get("/forensics/timeline/{agent_id}", tags=["forensics"])
async def forensic_timeline(agent_id: str, request: Request) -> Any:
    """Proxy → Forensics service ``/timeline/{agent_id}``.

    Added 2026-06-21: the UI's ``forensicsService.getTimeline`` calls
    this path; the backend service has always exposed it but the
    gateway proxy was missing, so /forensics page calls were 404'ing.
    """
    resp = await request.app.state.client.get(
        f"{_base()}/timeline/{agent_id}",
        headers=internal_headers(request),
        params=dict(request.query_params),
    )
    return passthrough(resp)


@router.get("/forensics/blast-radius/{agent_id}", tags=["forensics"])
async def forensic_blast_radius(agent_id: str, request: Request) -> Any:
    """Proxy → Forensics service ``/blast-radius/{agent_id}``."""
    resp = await request.app.state.client.get(
        f"{_base()}/blast-radius/{agent_id}",
        headers=internal_headers(request),
        params=dict(request.query_params),
    )
    return passthrough(resp)


@router.post("/forensics/export/{agent_id}", tags=["forensics"])
async def forensic_export(agent_id: str, request: Request) -> Any:
    """Proxy → Forensics service ``/export/{agent_id}``.

    Returns the full forensic JSON package the UI's
    ``forensicsService.exportInvestigation`` downloads.
    """
    body = await request.body()
    resp = await request.app.state.client.post(
        f"{_base()}/export/{agent_id}",
        headers=internal_headers(request),
        content=body,
    )
    return passthrough(resp)
