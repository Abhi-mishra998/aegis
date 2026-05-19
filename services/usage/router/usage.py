from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status, Request
from sqlalchemy.ext.asyncio import AsyncSession

from sdk.common.auth import verify_internal_secret
from sdk.common.db import get_db, get_tenant_id
from sdk.common.response import APIResponse
from services.usage.repository.usage import UsageRepository
from services.usage.schemas.usage import UsageCreate, UsageResponse, UsageSummary

# NOTE: /usage/billing/invoices is served by billing_router mounted at /usage prefix
# in usage/main.py — do not duplicate it here.

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
        except asyncio.TimeoutError:
            # Don't fail the usage write; log a critical so monitoring can detect drift.
            # The audit row remains in default 'completed' state (writer.py default),
            # so this is consistency-only, not correctness-breaking.
            import structlog as _sl
            _sl.get_logger(__name__).critical(
                "audit_billing_status_timeout",
                audit_id=str(payload.audit_id),
            )
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


