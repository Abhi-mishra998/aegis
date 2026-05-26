from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict
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
    since = datetime.now(tz=UTC) - timedelta(days=7)
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
    six_months_ago = datetime.now(tz=UTC) - timedelta(days=186)
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
# COST ATTRIBUTION — per-agent weekly breakdown (last 4 weeks)
# ---------------------------------------------------------------------------

@router.get("/cost-attribution", response_model=APIResponse[dict])
async def get_cost_attribution(
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db:        Annotated[AsyncSession, Depends(get_db)],
    weeks:     int = Query(4, ge=1, le=12),
) -> APIResponse[dict]:
    """
    Return per-agent, per-week cost breakdown for the last N weeks.

    Response:
      weeks:            list of ISO week labels  ["2026-W21", ...]
      agents:           list of {agent_id, total_cost, total_calls}
      by_agent_by_week: {agent_id: {week: cost}}
      totals_by_week:   {week: cost}
      grand_total:      float
    """
    since = datetime.now(tz=UTC) - timedelta(weeks=weeks)

    week_expr = func.to_char(UsageRecord.timestamp, "IYYY-IW")
    stmt = (
        select(
            UsageRecord.agent_id,
            week_expr.label("iso_week"),
            func.count(UsageRecord.id).label("calls"),
            func.coalesce(func.sum(UsageRecord.cost), 0.0).label("cost"),
        )
        .where(
            UsageRecord.tenant_id == tenant_id,
            UsageRecord.timestamp >= since,
        )
        .group_by(UsageRecord.agent_id, week_expr)
        .order_by(week_expr)
    )
    rows = (await db.execute(stmt)).all()

    week_set: set[str] = set()
    agents_map: dict[str, dict] = {}

    for row in rows:
        aid  = str(row.agent_id) if row.agent_id else "unknown"
        week = row.iso_week or "unknown"
        week_set.add(week)
        if aid not in agents_map:
            agents_map[aid] = {"agent_id": aid, "total_cost": 0.0, "total_calls": 0, "by_week": {}}
        agents_map[aid]["total_cost"]  += float(row.cost or 0)
        agents_map[aid]["total_calls"] += int(row.calls or 0)
        agents_map[aid]["by_week"][week] = float(row.cost or 0)

    sorted_weeks = sorted(week_set)
    agents_list  = sorted(agents_map.values(), key=lambda a: a["total_cost"], reverse=True)

    by_agent_by_week = {a["agent_id"]: a.pop("by_week") for a in agents_list}

    totals_by_week: dict[str, float] = {}
    for a_bw in by_agent_by_week.values():
        for w, c in a_bw.items():
            totals_by_week[w] = round(totals_by_week.get(w, 0.0) + c, 4)

    grand_total = round(sum(a["total_cost"] for a in agents_list), 4)

    return APIResponse(data={
        "weeks":            sorted_weeks,
        "agents":           agents_list,
        "by_agent_by_week": by_agent_by_week,
        "totals_by_week":   totals_by_week,
        "grand_total":      grand_total,
        "period_weeks":     weeks,
    })


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


# ---------------------------------------------------------------------------
# BUDGET APPROVAL WORKFLOW
# ---------------------------------------------------------------------------

from services.billing import (
    budget_requests as _br,  # noqa: E402  (local import avoids circular at module load)
)


class BudgetRequestCreate(BaseModel):
    """Payload for creating a new budget increase request."""

    agent_id: uuid.UUID | None = None
    agent_name: str
    current_cap_usd: float
    requested_cap_usd: float
    reason: str

    model_config = ConfigDict(extra="ignore")


class BudgetRequestOut(BaseModel):
    """Response shape for a budget request row."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    agent_id: uuid.UUID | None
    agent_name: str
    requested_by: str
    current_cap_usd: float
    requested_cap_usd: float
    reason: str
    status: str
    reviewed_by: str | None
    reviewed_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ReviewDecision(BaseModel):
    """Payload for approve / reject endpoints."""

    approved: bool
    comment: str | None = None

    model_config = ConfigDict(extra="ignore")


def _get_redis(request: Request):
    """Extract the Redis client stored on the billing engine in app.state."""
    engine = getattr(request.app.state, "billing_engine", None)
    if engine is None or not hasattr(engine, "redis"):
        raise HTTPException(status_code=500, detail="Redis not available via billing engine")
    return engine.redis


@router.post("/budget-requests", response_model=APIResponse[BudgetRequestOut], status_code=201)
async def create_budget_request(
    body: BudgetRequestCreate,
    request: Request,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> APIResponse[BudgetRequestOut]:
    """Submit a new budget increase request for review."""
    # requested_by comes from the authenticated user email stored in app state,
    # falling back to "system" for automated submissions.
    requested_by: str = request.headers.get("X-User-Email", "system")
    row = await _br.create_request(
        db,
        tenant_id=tenant_id,
        agent_id=body.agent_id,
        agent_name=body.agent_name,
        current_cap=body.current_cap_usd,
        requested_cap=body.requested_cap_usd,
        reason=body.reason,
        requested_by=requested_by,
    )
    return APIResponse(data=BudgetRequestOut.model_validate(row))


@router.get("/budget-requests", response_model=APIResponse[list[BudgetRequestOut]])
async def list_budget_requests(
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    status: str | None = Query(default=None, description="Filter by status: pending/approved/rejected"),
) -> APIResponse[list[BudgetRequestOut]]:
    """List budget requests for the current tenant."""
    rows = await _br.list_requests(db, tenant_id=tenant_id, status=status)
    return APIResponse(data=[BudgetRequestOut.model_validate(r) for r in rows])


@router.get("/budget-requests/{req_id}", response_model=APIResponse[BudgetRequestOut])
async def get_budget_request(
    req_id: uuid.UUID,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> APIResponse[BudgetRequestOut]:
    """Fetch a single budget request by ID."""
    rows = await _br.list_requests(db, tenant_id=tenant_id)
    match = next((r for r in rows if r.id == req_id), None)
    if match is None:
        raise HTTPException(status_code=404, detail="Budget request not found")
    return APIResponse(data=BudgetRequestOut.model_validate(match))


@router.post("/budget-requests/{req_id}/approve", response_model=APIResponse[BudgetRequestOut])
async def approve_budget_request(
    req_id: uuid.UUID,
    body: ReviewDecision,
    request: Request,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> APIResponse[BudgetRequestOut]:
    """Approve a budget request and update the Redis cost-cap key."""
    reviewer: str = request.headers.get("X-User-Email", "manager")
    redis = _get_redis(request)
    try:
        row = await _br.review_request(
            db,
            redis,
            request_id=req_id,
            tenant_id=tenant_id,
            approved=True,
            reviewed_by=reviewer,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return APIResponse(data=BudgetRequestOut.model_validate(row))


@router.post("/budget-requests/{req_id}/reject", response_model=APIResponse[BudgetRequestOut])
async def reject_budget_request(
    req_id: uuid.UUID,
    body: ReviewDecision,
    request: Request,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> APIResponse[BudgetRequestOut]:
    """Reject a budget request (no cap change)."""
    reviewer: str = request.headers.get("X-User-Email", "manager")
    redis = _get_redis(request)
    try:
        row = await _br.review_request(
            db,
            redis,
            request_id=req_id,
            tenant_id=tenant_id,
            approved=False,
            reviewed_by=reviewer,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return APIResponse(data=BudgetRequestOut.model_validate(row))
