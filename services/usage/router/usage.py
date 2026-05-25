from __future__ import annotations

import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from sdk.common.auth import verify_internal_secret
from sdk.common.db import get_db, get_tenant_id
from sdk.common.response import APIResponse
from sdk.utils import TENANT_DAILY_COST_USD, TOTAL_COST_USD_TOTAL
from services.usage.repository.usage import UsageRepository
from services.usage.schemas.usage import UsageCreate, UsageResponse, UsageSummary

# NOTE: /usage/billing/invoices is served by billing_router mounted at /usage prefix
# in usage/main.py — do not duplicate it here.

logger = structlog.get_logger(__name__)

# 1 million tokens = $0.05 compute cost
_USD_PER_TOKEN = 0.05 / 1_000_000

router = APIRouter(prefix="/usage", tags=["usage"], dependencies=[Depends(verify_internal_secret)])


@router.post(
    "/record",
    response_model=APIResponse[UsageResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Record tool usage event",
)
async def record_usage(
    db: Annotated[AsyncSession, Depends(get_db)],
    payload: UsageCreate,
) -> APIResponse[UsageResponse]:
    """Internal endpoint to record a billable event."""
    repo = UsageRepository(db)
    record = await repo.record(payload)
    # C-3 FIX (2026-05-13): Mark audit log completion synchronously with a tight
    # timeout. Previously asyncio.create_task dropped the work on event-loop pressure
    # which left audit_logs.billing_status in 'pending' state (FSM dishonest).
    # The Audit Service endpoint is idempotent so retry-safety is preserved.
    if payload.audit_id:
        import asyncio
        try:
            await asyncio.wait_for(
                repo.mark_audit_billing_complete(payload.audit_id),
                timeout=1.0,
            )
        except TimeoutError:
            # Don't fail the usage write; log a critical so monitoring can detect drift.
            # The audit row remains in default 'completed' state (writer.py default),
            # so this is consistency-only, not correctness-breaking.
            logger.critical(
                "audit_billing_status_timeout",
                audit_id=str(payload.audit_id),
            )

    # ── Cost Prometheus metrics ────────────────────────────────────────────────
    # Compute event-level cost: use the stored cost field if non-zero,
    # otherwise derive from token units at $0.05 / 1M tokens.
    event_cost_usd: float = float(record.cost) if record.cost else payload.units * _USD_PER_TOKEN
    tenant_str = str(payload.tenant_id)

    # Monotonic running-total counter across all tenants.
    TOTAL_COST_USD_TOTAL.inc(event_cost_usd)

    # Per-tenant daily cost gauge: fetch the current-day aggregate from the
    # DB so the gauge is accurate even after service restarts.
    try:
        summary = await repo.get_summary(payload.tenant_id)
        daily_cost = float(summary.total_cost) if summary.total_cost else event_cost_usd
        TENANT_DAILY_COST_USD.labels(tenant_id=tenant_str).set(daily_cost)
    except Exception as _exc:
        # Fallback: increment by this event's cost so the gauge is never stale.
        logger.warning("tenant_daily_cost_gauge_update_failed", tenant_id=tenant_str, error=str(_exc))
        TENANT_DAILY_COST_USD.labels(tenant_id=tenant_str).inc(event_cost_usd)

    return APIResponse(data=UsageResponse.model_validate(record))


@router.get(
    "/summary",
    response_model=APIResponse[UsageSummary],
    summary="Get billing summary for the tenant",
)
async def get_summary(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[UsageSummary]:
    """Returns aggregated usage and cost summary for the current tenant."""
    repo = UsageRepository(db)
    summary = await repo.get_summary(tenant_id)
    return APIResponse(data=summary)


@router.get(
    "/history",
    response_model=APIResponse[list[UsageResponse]],
    summary="Get detailed usage history",
)
async def get_history(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    limit: int = 50,
) -> APIResponse[list[UsageResponse]]:
    """Returns the most recent usage records for the current tenant."""
    repo = UsageRepository(db)
    records = await repo.list_for_tenant(tenant_id, limit=limit)
    return APIResponse(data=[UsageResponse.model_validate(r) for r in records])

@router.get(
    "/dashboard",
    response_model=APIResponse[dict],
    summary="Get revenue dashboard data",
)
async def get_revenue_dashboard(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[dict]:
    repo = UsageRepository(db)
    data = await repo.get_revenue_dashboard(tenant_id)

    import structlog as _sl
    _log = _sl.get_logger(__name__)
    redis = getattr(request.app.state, "billing_engine", None)
    data["retry_queue_size"] = 0
    data["dlq_size"] = 0
    if redis and hasattr(redis, "redis"):
        try:
            data["retry_queue_size"] = int(await redis.redis.llen("acp:billing_retry_queue"))
            data["dlq_size"] = int(await redis.redis.llen("acp:billing_dlq"))
        except (ConnectionError, TimeoutError, OSError) as exc:
            # Per production_hardening_spec: log every transient observability
            # failure — never silently fall back to "0" without telemetry.
            _log.warning(
                "billing_dashboard_redis_unreachable",
                tenant_id=str(tenant_id),
                error=str(exc),
                exc_type=type(exc).__name__,
            )

    return APIResponse(data=data)

@router.get(
    "/anomalies",
    response_model=APIResponse[list[dict]],
    summary="Get billing anomalies",
)
async def get_anomalies(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[list[dict]]:
    repo = UsageRepository(db)
    data = await repo.get_anomalies(tenant_id)
    return APIResponse(data=data)


