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


# Sprint 23 — Policy Packs (SOC2 / PCI / HIPAA / Finance / DevOps).
@router.get("/policy-packs/catalog")
async def policy_packs_catalog(request: Request) -> Any:
    resp = await request.app.state.client.get(
        f"{_base()}/policy-packs/catalog",
        headers=internal_headers(request),
        timeout=6.0,
    )
    return passthrough(resp)


@router.get("/workspace/policy-packs")
async def workspace_policy_packs_get(request: Request) -> Any:
    resp = await request.app.state.client.get(
        f"{_base()}/workspace/policy-packs",
        headers=internal_headers(request),
        timeout=6.0,
    )
    return passthrough(resp)


@router.put(
    "/workspace/policy-packs",
    dependencies=[Depends(verify_role(Role.ADMIN))],
)
async def workspace_policy_packs_put(request: Request) -> Any:
    try:
        body = await request.json()
    except Exception:
        body = {}
    resp = await request.app.state.client.put(
        f"{_base()}/workspace/policy-packs",
        headers=internal_headers(request),
        json=body or {},
        timeout=6.0,
    )
    return passthrough(resp)


@router.post(
    "/workspace/apply-preset",
    dependencies=[Depends(verify_role(Role.OWNER))],
)
async def workspace_apply_preset(request: Request) -> Any:
    """Sprint S1 (2026-06-19) — Industry preset applier for OnboardingWizard
    Step 0. OWNER-only. One call does:

      1. PUT /workspace/policy-packs       (enable matching packs)
      2. PATCH /workspace/system-values    (store dashboard_preset + industry_id)

    Body: ``{"industry_id": "fintech|healthcare|devops|ai_startup|custom",
              "policy_packs": ["SOC2","FINANCE","PCI"],
              "dashboard_preset": "finance"}``

    Returns the merged result; the wizard advances to Step 1 on 200.
    Failures on the system-values patch are non-fatal — packs were
    already applied, so the workspace is still usable.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return {"status": "error", "error": "Body must be a JSON object"}

    packs = body.get("policy_packs") or []
    industry_id = body.get("industry_id") or "custom"
    dashboard_preset = body.get("dashboard_preset")
    applied: dict[str, Any] = {"industry_id": industry_id}

    # 1. Apply the policy-pack list. Empty list = "custom" → still call so
    #    we record the deliberate "no packs" choice in the audit log.
    if isinstance(packs, list):
        pp_resp = await request.app.state.client.put(
            f"{_base()}/workspace/policy-packs",
            headers=internal_headers(request),
            json={"enabled": [str(p) for p in packs if isinstance(p, str)]},
            timeout=6.0,
        )
        try:
            applied["policy_packs"] = pp_resp.json().get("data", {})
        except Exception:  # noqa: BLE001
            applied["policy_packs"] = {"status_code": pp_resp.status_code}

    # 2. Stash the industry_id + dashboard_preset on tenant.system_values so
    #    Dashboard.jsx (S8) can route the layout. Best-effort — never fails
    #    the preset apply if this 5xx's.
    try:
        sv_resp = await request.app.state.client.patch(
            f"{_base()}/workspace/system-values",
            headers=internal_headers(request),
            json={
                "industry_preset": industry_id,
                "dashboard_preset": dashboard_preset,
            },
            timeout=6.0,
        )
        applied["system_values_status"] = sv_resp.status_code
    except Exception as exc:  # noqa: BLE001
        logger.warning("apply_preset_system_values_failed", error=str(exc))
        applied["system_values_status"] = 0

    return {"status": "ok", "data": applied}


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
