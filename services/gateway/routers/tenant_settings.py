"""Per-tenant admin toggles for opt-in features (Sprint UI-3).

Two flags today:
  * c3_sampling — enables ATF §9.3 consistency sampling (3× planner
    cost on C3 actions; blocks INCONSISTENT plans).
  * behavior_fingerprinting — enables the learned behavioral signal
    (§9.2 opt-in; advisory-only, never authoritative).

Read/write live in `sdk/common/tenant_settings.py`. This router is the
HTTP surface. OWNER role required on write — flipping a policy flag
is a Category-C admin action.
"""
from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request

from sdk.common.config import settings
from sdk.common.redis import get_redis_client
from sdk.common.response import APIResponse
from sdk.common.roles import Role
from sdk.common.tenant_settings import get_all_flags, get_flag, set_flag
from services.gateway.auth import verify_role

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/tenant/settings", tags=["tenant-settings"])

_redis = get_redis_client(settings.REDIS_URL, decode_responses=False)

# Whitelist of flag names the endpoint accepts. Explicit list prevents
# an admin from writing arbitrary keys into the settings hash (which
# would still be tenant-scoped, but confuses reads + telemetry).
_ALLOWED_FLAGS = frozenset({"c3_sampling", "behavior_fingerprinting"})


@router.get("", response_model=APIResponse[dict])
async def get_tenant_settings(request: Request) -> APIResponse[dict]:
    """Return effective + raw values for each supported flag.

    `effective` is what the runtime actually observes (Redis override
    OR env-var fallback). `override` is what the admin explicitly set
    via the UI (None = unset, using env fallback). Distinguishing lets
    the UI show "using ops default" vs "you overrode this".
    """
    tenant_id = getattr(request.state, "tenant_id", None)
    if tenant_id is None:
        raise HTTPException(status_code=401, detail="tenant context required")
    tid = str(tenant_id)

    raw_overrides = await get_all_flags(_redis, tid)

    data: dict[str, Any] = {}
    for flag, env_var in (
        ("c3_sampling",              "ACP_C3_SAMPLING_TENANTS"),
        ("behavior_fingerprinting",  "ACP_BEHAVIOR_FINGERPRINTING_TENANTS"),
    ):
        data[flag] = {
            "effective": await get_flag(_redis, tid, flag, env_var=env_var),
            "override":  raw_overrides.get(flag),  # None if unset
        }
    return APIResponse(data=data)


@router.post(
    "",
    response_model=APIResponse[dict],
    dependencies=[Depends(verify_role(Role.OWNER))],
)
async def set_tenant_settings(request: Request) -> APIResponse[dict]:
    """Set one or more flag overrides. Body: `{c3_sampling: bool, ...}`.

    OWNER role required. Only whitelisted flags accepted. An unset flag
    stays unset (env-var fallback applies) — to clear an override, the
    UI must POST the desired boolean explicitly. Removing the override
    entirely is `DELETE /tenant/settings/{flag}` (Sprint UI-3 follow-up
    if operators request it).
    """
    tenant_id = getattr(request.state, "tenant_id", None)
    if tenant_id is None:
        raise HTTPException(status_code=401, detail="tenant context required")
    tid = str(tenant_id)

    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"body must be JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be an object")

    accepted: dict[str, bool] = {}
    for flag, value in body.items():
        if flag not in _ALLOWED_FLAGS:
            raise HTTPException(
                status_code=422,
                detail=f"unknown flag: {flag!r} (allowed: {sorted(_ALLOWED_FLAGS)})",
            )
        if not isinstance(value, bool):
            raise HTTPException(
                status_code=422,
                detail=f"flag {flag!r} must be boolean, got {type(value).__name__}",
            )
        await set_flag(_redis, tid, flag, value)
        accepted[flag] = value

    logger.info(
        "tenant_settings_updated",
        tenant_id=tid,
        flags=list(accepted.keys()),
        actor=str(getattr(request.state, "actor", "") or ""),
    )
    return APIResponse(data={"updated": accepted})
