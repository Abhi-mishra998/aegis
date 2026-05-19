from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta, timezone
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sdk.common.auth import verify_internal_secret
from sdk.common.db import get_db, get_tenant_id
from sdk.common.response import APIResponse
from services.billing.value_engine import BillingValueEngine
from services.usage.models.usage import UsageRecord

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Router Setup
# ---------------------------------------------------------------------------

router = APIRouter(
    prefix="/billing",
    tags=["billing"],
    dependencies=[Depends(verify_internal_secret)],
)


def get_billing_engine(request: Request) -> BillingValueEngine:
    """
    FastAPI dependency to safely access BillingValueEngine from app state.
    """
    engine = getattr(request.app.state, "billing_engine", None)
    if engine is None:
        raise HTTPException(
            status_code=500,
            detail="Billing engine not initialized in app state."
        )
    return engine


# ---------------------------------------------------------------------------
# ROUTES — SUMMARY
# ---------------------------------------------------------------------------

@router.get("/summary", response_model=APIResponse[dict])
async def get_billing_summary(
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    engine: Annotated[BillingValueEngine, Depends(get_billing_engine)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> APIResponse[dict]:
    """
    Returns full billing ROI summary for the tenant + a 7-day daily_trend
    derived from usage_records (drives the API Call Volume chart in the UI).
    """
    data = await engine.get_tenant_billing_summary(str(tenant_id))

    # 2026-05-13: enrich with a 7-day daily call-volume + cost trend pulled
    # from acp_usage. Without this the Billing UI's "API Call Volume Trend"
    # chart had nothing to plot.
    since = datetime.now(tz=timezone.utc) - timedelta(days=7)
    stmt = (
        select(
            func.date_trunc("day", UsageRecord.timestamp).label("day"),
            func.count(UsageRecord.id).label("calls"),
            func.coalesce(func.sum(UsageRecord.cost), 0.0).label("cost"),
            func.coalesce(func.sum(UsageRecord.units), 0).label("tokens"),
        )
        .where(UsageRecord.tenant_id == tenant_id)
        .where(UsageRecord.timestamp >= since)
        .group_by("day")
        .order_by("day")
    )
    rows = (await db.execute(stmt)).all()
    daily_trend = [
        {
            "day":    row.day.strftime("%a"),  # Mon / Tue / Wed — fits the chart x-axis
            "date":   row.day.isoformat(),
            "calls":  int(row.calls or 0),
            "cost":   float(row.cost or 0.0),
            "tokens": int(row.tokens or 0),
        }
        for row in rows
    ]
    data["daily_trend"] = daily_trend
    data["total_calls"] = sum(r["calls"] for r in daily_trend)
    data["total_tokens"] = sum(r["tokens"] for r in daily_trend)
    return APIResponse(data=data)


@router.get("/invoices", response_model=APIResponse[dict])
async def get_billing_invoices(
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    engine: Annotated[BillingValueEngine, Depends(get_billing_engine)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> APIResponse[dict]:
    """
    Per-month invoice rollup derived from usage_records (last 6 months).

    2026-05-13 (Run-3): replaced the dummy single-invoice shim with a real
    SQL aggregation so the UI Invoice Ledger shows actual billable rows.
    The Redis-based engine summary is used to overlay current-month
    threats_blocked + money_saved (live ROI counters).
    """
    six_months_ago = datetime.now(tz=timezone.utc) - timedelta(days=186)
    # 2026-05-14: define the month expression once so both GROUP BY and ORDER BY
    # reference the SAME expression. Using `.group_by("month")` (label string)
    # while `.order_by(func.date_trunc(...))` introduces a fresh expression
    # produced PG "column must appear in GROUP BY" → 500 → empty Invoice Ledger.
    month_expr = func.date_trunc("month", UsageRecord.timestamp)
    stmt = (
        select(
            month_expr.label("month"),
            func.count(UsageRecord.id).label("calls"),
            func.coalesce(func.sum(UsageRecord.units), 0).label("tokens"),
            func.coalesce(func.sum(UsageRecord.cost), 0.0).label("cost"),
        )
        .where(UsageRecord.tenant_id == tenant_id)
        .where(UsageRecord.timestamp >= six_months_ago)
        .group_by(month_expr)
        .order_by(month_expr.desc())
    )
    rows = (await db.execute(stmt)).all()

    # Overlay live ROI counters (Redis) onto the current-month row so the
    # operator sees up-to-the-second threats_blocked without waiting for the
    # daily Redis-to-Postgres flush.
    summary = await engine.get_tenant_billing_summary(str(tenant_id))
    current_month = datetime.now(tz=UTC).strftime("%Y-%m")

    invoices: list[dict] = []
    for row in rows:
        period_str = row.month.strftime("%Y-%m")
        is_current = period_str == current_month
        invoices.append({
            "invoice_id": f"INV-{period_str.replace('-', '')}-01",
            "period": period_str,
            "total_calls": int(row.calls or 0),
            "total_tokens": int(row.tokens or 0),
            "threats_blocked": (
                summary.get("today", {}).get("threats_blocked", 0)
                if is_current else 0
            ),
            "total_saved_usd": (
                summary.get("today", {}).get("money_saved", 0.0)
                if is_current else 0.0
            ),
            "cost_usd": round(float(row.cost or 0.0), 4),
            "status": "open" if is_current else "generated",
        })

    # When the tenant has zero usage rows we still want the UI to render the
    # current period row (so the operator sees the table, not an empty state).
    if not invoices:
        invoices.append({
            "invoice_id": f"INV-{current_month.replace('-', '')}-01",
            "period": current_month,
            "total_calls": 0,
            "total_tokens": 0,
            "threats_blocked": summary.get("today", {}).get("threats_blocked", 0),
            "total_saved_usd": summary.get("today", {}).get("money_saved", 0.0),
            "cost_usd": 0.0,
            "status": "open",
        })

    return APIResponse(data={"invoices": invoices, "tenant_id": str(tenant_id)})


# ---------------------------------------------------------------------------
# EVENTS — BILLING TRIGGERS
# ---------------------------------------------------------------------------

class BillingEvent(BaseModel):
    tenant_id: uuid.UUID
    action: str
    agent_id: uuid.UUID | None = None
    audit_id: str | None = None
    tokens: int = 1
    # C-1 FIX (2026-05-13): Accept idempotency_key from gateway so
    # record_protection_event can dedupe Redis HINCRBYFLOAT on retry.
    idempotency_key: str | None = None

    model_config = ConfigDict(extra="ignore")


@router.post("/events", response_model=APIResponse[dict])
async def record_billing_event(
    event: BillingEvent,
    engine: Annotated[BillingValueEngine, Depends(get_billing_engine)],
    jwt_tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[dict]:
    """
    Records a protection event and calculates money saved.

    CRITICAL FIX:
    - Convert UUID → string BEFORE passing to engine
    """
    if str(jwt_tenant_id) != str(event.tenant_id):
        raise HTTPException(status_code=403, detail="Tenant mismatch")

    # C-1 FIX (2026-05-13): forward idempotency_key (fall back to audit_id) so the
    # engine actually dedupes retries. Without this, every retry double-counts in Redis.
    event_id = event.idempotency_key or event.audit_id

    try:
        saved = await engine.record_protection_event(
            tenant_id=str(event.tenant_id),
            action=event.action,
            agent_id=str(event.agent_id) if event.agent_id else None,
            event_id=event_id,
        )

        logger.info(
            "billing_event_recorded",
            tenant_id=str(event.tenant_id),
            agent_id=str(event.agent_id) if event.agent_id else None,
            action=event.action,
            saved_usd=saved,
        )

        return APIResponse(data={"saved_usd": saved})

    except Exception as e:
        logger.error(
            "billing_event_failed",
            tenant_id=str(event.tenant_id),
            action=event.action,
            error=str(e),
        )
        raise HTTPException(
            status_code=500,
            detail=f"Billing event processing failed: {str(e)}"
        )