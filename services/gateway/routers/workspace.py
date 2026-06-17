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


# Sprint 21 — Slack approvals config.
@router.get("/workspace/slack-config")
async def workspace_slack_config_get(request: Request) -> Any:
    """OWNER/ADMIN — read the Slack approvals config. Returns
    {webhook_url, configured} but never the signing secret."""
    resp = await request.app.state.client.get(
        f"{_base()}/workspace/slack-config",
        headers=internal_headers(request),
        timeout=6.0,
    )
    upstream = passthrough(resp)
    # Strip signing_secret before forwarding to the browser — it stays
    # internal-only. The internal-secret-authed identity endpoint
    # returns it for the gateway's own use, but the UI never sees it.
    if upstream.status_code == 200:
        try:
            import json as _json
            body = _json.loads(upstream.body)
            if isinstance(body, dict) and isinstance(body.get("data"), dict):
                body["data"].pop("signing_secret", None)
            from fastapi.responses import JSONResponse as _JSON
            return _JSON(content=body, status_code=upstream.status_code)
        except Exception:  # noqa: BLE001
            pass
    return upstream


@router.put(
    "/workspace/slack-config",
    dependencies=[Depends(verify_role(Role.ADMIN))],
)
async def workspace_slack_config_put(request: Request) -> Any:
    """OWNER/ADMIN — Body: {webhook_url, rotate_secret?}.

    Notifications channels are typically managed by the security
    admin not the workspace owner, so we accept ADMIN too. The
    identity-svc handler enforces the same set.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    resp = await request.app.state.client.put(
        f"{_base()}/workspace/slack-config",
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
