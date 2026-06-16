"""
Sprint 3 — Workspace + Shadow Mode gateway proxies.

  GET  /workspace/me                — read the signed-in workspace's
                                       summary (incl. shadow_mode_until).
  POST /workspace/exit-shadow-mode  — OWNER only. Clears the shadow window
                                       so the next deny/escalate from the
                                       decision engine actually blocks.

Both routes forward to the identity service with the X-ACP-Role header
set from the JWT-validated request.state.role. The OWNER gate is enforced
twice: once by the gateway via Depends(verify_role(Role.OWNER)) below,
and again by the identity handler reading X-ACP-Role — defense in depth
so a misconfigured downstream service can't accidentally widen the role.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, Request

from sdk.common.config import settings
from sdk.common.roles import Role
from services.gateway._helpers import internal_headers, passthrough
from services.gateway.auth import verify_role

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["workspace"])


def _base() -> str:
    return settings.IDENTITY_SERVICE_URL.rstrip("/")


@router.get("/workspace/me")
async def workspace_me(request: Request) -> Any:
    """Proxy → identity:/workspace/me. Any signed-in user can read."""
    resp = await request.app.state.client.get(
        f"{_base()}/workspace/me",
        headers=internal_headers(request),
        timeout=6.0,
    )
    return passthrough(resp)


@router.get("/workspace/inventory")
async def workspace_inventory(request: Request) -> Any:
    """Sprint 4 — Dashboard hero data. Proxy → registry:/workspace/inventory."""
    resp = await request.app.state.client.get(
        f"{settings.REGISTRY_SERVICE_URL.rstrip('/')}/workspace/inventory",
        headers=internal_headers(request),
        timeout=6.0,
    )
    return passthrough(resp)


@router.patch(
    "/workspace/system-values",
    dependencies=[Depends(verify_role(Role.OWNER))],
)
async def workspace_system_values(request: Request) -> Any:
    """Sprint 8 — OWNER-only. Proxy → identity:/workspace/system-values."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    resp = await request.app.state.client.patch(
        f"{_base()}/workspace/system-values",
        headers=internal_headers(request),
        json=body or {},
        timeout=6.0,
    )
    return passthrough(resp)


@router.post(
    "/workspace/exit-shadow-mode",
    dependencies=[Depends(verify_role(Role.OWNER))],
)
async def workspace_exit_shadow_mode(request: Request) -> Any:
    """OWNER-only. Proxy → identity:/workspace/exit-shadow-mode."""
    resp = await request.app.state.client.post(
        f"{_base()}/workspace/exit-shadow-mode",
        headers=internal_headers(request),
        timeout=6.0,
    )
    return passthrough(resp)
