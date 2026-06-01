"""
Budget request workflow — agents request additional budget, managers approve/reject.

Requests are stored in the shared usage DB (budget_requests table created via
Alembic migration b1c2d3e4f5g6_budget_requests).

Redis key written on approval: ``acp:agent_cost_cap:{agent_id}`` (same key
read by the middleware _enforce_inference_cost_cap).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import DateTime, Float, String, Text, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from sdk.common.db import Base

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# ORM Model
# ---------------------------------------------------------------------------


class BudgetRequest(Base):
    """One budget-increase request submitted by an agent or operator."""

    __tablename__ = "budget_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    agent_name: Mapped[str] = mapped_column(String(255), nullable=False)
    requested_by: Mapped[str] = mapped_column(String(255), nullable=False)
    current_cap_usd: Mapped[float] = mapped_column(Float, nullable=False)
    requested_cap_usd: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    reviewed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


# ---------------------------------------------------------------------------
# Business Logic
# ---------------------------------------------------------------------------


async def create_request(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID | None,
    agent_name: str,
    current_cap: float,
    requested_cap: float,
    reason: str,
    requested_by: str,
) -> BudgetRequest:
    """Insert a new pending budget request and return it."""
    row = BudgetRequest(
        tenant_id=tenant_id,
        agent_id=agent_id,
        agent_name=agent_name,
        current_cap_usd=current_cap,
        requested_cap_usd=requested_cap,
        reason=reason,
        requested_by=requested_by,
        status="pending",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    logger.info(
        "budget_request_created",
        request_id=str(row.id),
        tenant_id=str(tenant_id),
        agent_name=agent_name,
        requested_cap_usd=requested_cap,
    )
    return row


async def list_requests(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    status: str | None = None,
) -> list[BudgetRequest]:
    """Return budget requests for the given tenant, optionally filtered by status."""
    stmt = select(BudgetRequest).where(BudgetRequest.tenant_id == tenant_id)
    if status is not None:
        stmt = stmt.where(BudgetRequest.status == status)
    stmt = stmt.order_by(BudgetRequest.created_at.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def review_request(
    db: AsyncSession,
    redis,  # redis.asyncio.Redis or compatible
    *,
    request_id: uuid.UUID,
    tenant_id: uuid.UUID,
    approved: bool,
    reviewed_by: str,
) -> BudgetRequest:
    """Approve or reject a budget request.

    On approval the per-agent Redis cost-cap key is updated so the change
    takes effect immediately without a service restart.
    """
    stmt = select(BudgetRequest).where(
        BudgetRequest.id == request_id,
        BudgetRequest.tenant_id == tenant_id,
    )
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        raise LookupError(f"BudgetRequest {request_id} not found for tenant {tenant_id}")
    if row.status != "pending":
        raise ValueError(f"BudgetRequest {request_id} is already {row.status}")

    row.status = "approved" if approved else "rejected"
    row.reviewed_by = reviewed_by
    row.reviewed_at = datetime.now(UTC)

    if approved and row.agent_id is not None:
        # Mirror the key written by the middleware:
        # services/gateway/_mw_rate_limit.py → acp:agent_cost_cap:{agent_id}
        redis_key = f"acp:agent_cost_cap:{row.agent_id}"
        await redis.set(redis_key, str(row.requested_cap_usd))
        logger.info(
            "budget_request_approved_redis_updated",
            request_id=str(request_id),
            agent_id=str(row.agent_id),
            new_cap_usd=row.requested_cap_usd,
            redis_key=redis_key,
        )

    await db.commit()
    await db.refresh(row)
    logger.info(
        "budget_request_reviewed",
        request_id=str(request_id),
        status=row.status,
        reviewed_by=reviewed_by,
    )
    return row
