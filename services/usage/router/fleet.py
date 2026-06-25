"""
Sprint 4.4 — Agent FinOps burn-down endpoint.

The dashboard's value proposition (per ``agies-refractor.md`` Phase B):

  "You don't just chart cost, you STOP it."

The chart side is this endpoint. Cap-enforcement lives in
``services/gateway/_mw_rate_limit::_enforce_inference_cost_cap`` and uses
the same ``InferenceCostLimiter`` — so the burn-down number the operator
sees is the exact same Redis counter the cap is enforced against. No
two-source-of-truth drift.

  GET /usage/fleet/burn-down?agent_id=<uuid>

Returns per-period (monthly by default — see Sprint 2.2) usage in USD,
the configured cap, and the remaining budget. All money math goes
through the cents-precision counters from ``sdk/common/inference_cost``;
no floats are introduced in this module beyond the final display-friendly
USD value computed on the read side.
"""
from __future__ import annotations

import uuid
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Query
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from sdk.common.auth import verify_internal_secret
from sdk.common.db import get_db, get_tenant_id
from sdk.common.response import APIResponse
from sdk.common.config import settings
from sdk.common.inference_cost import InferenceCostLimiter

logger = structlog.get_logger(__name__)

fleet_router = APIRouter(
    prefix="/usage/fleet",
    tags=["usage_fleet"],
    dependencies=[Depends(verify_internal_secret)],
)


async def _redis_client() -> Redis:
    """Per-request Redis client — kept narrow so the dependency wires
    cleanly when the audit/usage services share the same module."""
    return Redis.from_url(settings.REDIS_URL, decode_responses=True)  # type: ignore[arg-type]


async def _read_tenant_cap_usd(db: AsyncSession, tenant_id: uuid.UUID) -> float:
    """The tenant's configured daily cost cap. ``None`` or absent = no cap.

    Sprint 9 — graceful degradation: this is a DISPLAY endpoint, not the
    cap-enforcement hot path (that's in `InferenceCostLimiter`). If the
    cross-DB cap lookup fails for any reason — missing table on a fresh
    deploy, schema mismatch, network blip — we return 0 (no cap) rather
    than 500 the entire burn-down dashboard. The hot path still enforces
    whatever cap Redis tells it about.

    The "right" architecture is for usage to call identity's HTTP API
    here; that change is tracked as a Sprint 9 follow-up. Until then,
    the legacy direct-DB path falls through cleanly.
    """
    from sqlalchemy import text  # noqa: PLC0415
    try:
        row = (await db.execute(
            text(
                "SELECT daily_inference_cost_cap_usd "
                "FROM acp_identity.tenants WHERE id = :tid"
            ),
            {"tid": str(tenant_id)},
        )).first()
    except Exception as exc:
        logger.warning(
            "tenant_cap_lookup_degraded",
            tenant_id=str(tenant_id),
            error=str(exc),
            hint=("usage→identity DB read failed; treating as 'no cap'. "
                  "Migrate to HTTP-based identity client in Sprint-10."),
        )
        return 0.0
    if row is None or row[0] is None:
        return 0.0
    try:
        return float(row[0])
    except (TypeError, ValueError):
        return 0.0


async def _read_agent_cap_usd(redis: Redis, agent_id: uuid.UUID) -> float:
    """Per-agent hot-config Redis override. 0 / missing = no cap."""
    try:
        raw = await redis.get(f"acp:agent_cost_cap:{agent_id}")
    except Exception:
        return 0.0
    if raw is None:
        return 0.0
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("ascii", errors="replace")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _burn_down(used_usd: float, cap_usd: float) -> dict[str, Any]:
    """Compute the burn-down envelope from used + cap.

    Returns ``percent_used`` (None when no cap), ``remaining_usd``,
    ``status`` (one of ``no_cap`` / ``ok`` / ``warning`` / ``critical`` /
    ``over``). Thresholds match the Sprint 2.2 80%/100% gate.
    """
    if cap_usd <= 0:
        return {"percent_used": None, "remaining_usd": None, "status": "no_cap"}
    percent = used_usd / cap_usd if cap_usd else 0.0
    remaining = max(0.0, cap_usd - used_usd)
    if percent >= 1.0:
        status = "over"
    elif percent >= 0.8:
        status = "critical"
    elif percent >= 0.5:
        status = "warning"
    else:
        status = "ok"
    return {
        "percent_used":   round(percent, 4),
        "remaining_usd":  round(remaining, 4),
        "status":         status,
    }


@fleet_router.get(
    "/burn-down",
    response_model=APIResponse[dict],
    summary="Per-tenant / per-agent inference USD burn-down",
)
async def get_burn_down(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    agent_id: uuid.UUID | None = Query(None),
) -> APIResponse[dict]:
    """Return current-period usage + configured cap + remaining budget.

    The endpoint reads from the same Redis counters the gateway's
    ``InferenceCostLimiter`` increments + caps against, so the chart
    matches the cap-enforcement exactly. Money is stored as cents (int)
    internally; the USD float is computed at read time only.
    """
    redis = await _redis_client()
    try:
        limiter = InferenceCostLimiter(redis)
        snapshot = await limiter.usage_snapshot(
            tenant_id=str(tenant_id),
            agent_id=str(agent_id) if agent_id else None,
        )
        tenant_used_usd = float(snapshot.get("tenant_usd_used") or 0.0)
        agent_used_usd  = float(snapshot.get("agent_usd_used") or 0.0)
        period          = snapshot.get("period")
        resets_at       = snapshot.get("tenant_resets_at") or snapshot.get("agent_resets_at")

        tenant_cap = await _read_tenant_cap_usd(db, tenant_id)
        agent_cap  = (
            await _read_agent_cap_usd(redis, agent_id) if agent_id is not None else 0.0
        )

        return APIResponse(data={
            "period":      period,
            "resets_at":   resets_at,
            "tenant": {
                "used_usd": round(tenant_used_usd, 4),
                "cap_usd":  round(tenant_cap, 4) if tenant_cap else None,
                **_burn_down(tenant_used_usd, tenant_cap),
            },
            "agent": (
                {
                    "agent_id":  str(agent_id),
                    "used_usd":  round(agent_used_usd, 4),
                    "cap_usd":   round(agent_cap, 4) if agent_cap else None,
                    **_burn_down(agent_used_usd, agent_cap),
                }
                if agent_id is not None else None
            ),
        })
    finally:
        try:
            await redis.aclose()
        except Exception as exc:
            logger.debug("redis_aclose_failed", error=str(exc))
