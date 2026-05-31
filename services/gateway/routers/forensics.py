"""Gateway proxy routes for the forensics service.

3 routes lifted out of services/gateway/main.py in the sprint-5 audit
cleanup:

  GET /forensics/investigation              — tenant-wide investigation list
  GET /forensics/investigation/{agent_id}   — per-agent forensic report
  GET /forensics/replay/{agent_id}          — step-by-step replay
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
    )
    return passthrough(resp)


@router.get("/forensics/investigation/{agent_id}", tags=["forensics"])
async def get_investigation_report(agent_id: str, request: Request) -> Any:
    """Proxy → Forensics service investigation report for an agent."""
    resp = await request.app.state.client.get(
        f"{_base()}/forensics/investigation/{agent_id}",
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/forensics/replay/{agent_id}", tags=["forensics"])
async def replay_agent_behavior(agent_id: str, request: Request) -> Any:
    """Proxy → Forensics service forensic replay for an agent."""
    resp = await request.app.state.client.get(
        f"{_base()}/forensics/replay/{agent_id}",
        headers=internal_headers(request),
    )
    return passthrough(resp)
