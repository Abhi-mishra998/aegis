"""Scheduled report delivery — model + CRUD."""
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as aioredis
import structlog
from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from sdk.common.db import Base

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# SQLAlchemy Model
# ---------------------------------------------------------------------------


class ScheduledReport(Base):
    """Configuration row for a periodic compliance report delivery."""

    __tablename__ = "scheduled_reports"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)            # "Monthly Board Report"
    report_type = Column(String, nullable=False)     # "board" | "compliance" | "eu-ai-act" | "nist"
    schedule = Column(String, nullable=False)        # "daily" | "weekly" | "monthly"
    recipients = Column(JSONB, default=list)         # ["ceo@company.com"]
    framework = Column(String, nullable=True)        # for compliance type: "EU_AI_ACT" etc
    is_active = Column(Boolean, default=True)
    last_run_at = Column(DateTime(timezone=True), nullable=True)
    next_run_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# Delivery History Model
# ---------------------------------------------------------------------------


class ReportDelivery(Base):
    """One delivery attempt for a scheduled report."""

    __tablename__ = "report_deliveries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    tenant_id = Column(String, nullable=False, index=True)
    status = Column(String(20), nullable=False)        # "success" | "failed" | "skipped"
    triggered_by = Column(String(20), nullable=False)  # "scheduler" | "manual"
    recipients = Column(JSONB, default=list)
    error_message = Column(Text, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# Delivery CRUD
# ---------------------------------------------------------------------------


async def record_delivery(
    db: AsyncSession,
    report_id: str | uuid.UUID,
    tenant_id: str,
    status: str,
    triggered_by: str = "scheduler",
    recipients: list[str] | None = None,
    error_message: str | None = None,
    duration_ms: int | None = None,
) -> ReportDelivery:
    """Record one delivery attempt in the report_deliveries table."""
    delivery = ReportDelivery(
        id=uuid.uuid4(),
        report_id=report_id if isinstance(report_id, uuid.UUID) else uuid.UUID(str(report_id)),
        tenant_id=tenant_id,
        status=status,
        triggered_by=triggered_by,
        recipients=recipients or [],
        error_message=error_message,
        duration_ms=duration_ms,
        created_at=datetime.now(UTC),
    )
    db.add(delivery)
    await db.commit()
    await db.refresh(delivery)
    logger.info(
        "report_delivery_recorded",
        delivery_id=str(delivery.id),
        report_id=str(report_id),
        status=status,
    )
    return delivery


async def list_deliveries(
    db: AsyncSession,
    tenant_id: str,
    report_id: str,
    limit: int = 20,
) -> list[ReportDelivery]:
    """Return recent delivery attempts for one scheduled report."""
    result = await db.execute(
        select(ReportDelivery)
        .where(ReportDelivery.report_id == uuid.UUID(report_id))
        .where(ReportDelivery.tenant_id == tenant_id)
        .order_by(ReportDelivery.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Redis helper
# ---------------------------------------------------------------------------


def _get_redis() -> aioredis.Redis:
    return aioredis.from_url(os.environ.get("REDIS_URL", "redis://redis:6379"))


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


async def create_report(db: AsyncSession, tenant_id: str, data: dict[str, Any]) -> ScheduledReport:
    """Create a new scheduled report config row."""
    report = ScheduledReport(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name=data["name"],
        report_type=data["report_type"],
        schedule=data["schedule"],
        recipients=data.get("recipients", []),
        framework=data.get("framework"),
        is_active=data.get("is_active", True),
        last_run_at=None,
        next_run_at=data.get("next_run_at"),
        created_at=datetime.now(UTC),
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)
    logger.info("scheduled_report_created", report_id=str(report.id), tenant_id=tenant_id)
    return report


async def list_reports(db: AsyncSession, tenant_id: str) -> list[ScheduledReport]:
    """Return all scheduled reports for a tenant."""
    result = await db.execute(
        select(ScheduledReport)
        .where(ScheduledReport.tenant_id == tenant_id)
        .order_by(ScheduledReport.created_at.asc())
    )
    return list(result.scalars().all())


async def get_report(db: AsyncSession, tenant_id: str, report_id: str) -> ScheduledReport | None:
    """Fetch a single scheduled report, scoped to tenant."""
    result = await db.execute(
        select(ScheduledReport)
        .where(ScheduledReport.tenant_id == tenant_id)
        .where(ScheduledReport.id == report_id)
    )
    return result.scalars().first()


async def update_report(
    db: AsyncSession, tenant_id: str, report_id: str, data: dict[str, Any]
) -> ScheduledReport | None:
    """Update mutable fields of a scheduled report. Returns None if not found."""
    report = await get_report(db, tenant_id, report_id)
    if report is None:
        return None

    updatable = ("name", "schedule", "recipients", "is_active", "framework", "next_run_at")
    for field in updatable:
        if field in data:
            setattr(report, field, data[field])

    await db.commit()
    await db.refresh(report)
    logger.info("scheduled_report_updated", report_id=report_id, tenant_id=tenant_id)
    return report


async def delete_report(db: AsyncSession, tenant_id: str, report_id: str) -> bool:
    """Delete a scheduled report. Returns True if deleted, False if not found."""
    report = await get_report(db, tenant_id, report_id)
    if report is None:
        return False
    await db.delete(report)
    await db.commit()
    logger.info("scheduled_report_deleted", report_id=report_id, tenant_id=tenant_id)
    return True


async def trigger_report_now(db: AsyncSession, tenant_id: str, report_id: str) -> dict[str, Any]:
    """
    Queue a one-shot report run immediately.

    Writes `acp:report_trigger:{report_id}` → ISO timestamp to Redis with TTL 3600.
    Returns {"status": "queued", "report_id": ...}.
    """
    report = await get_report(db, tenant_id, report_id)
    if report is None:
        return {"status": "not_found", "report_id": report_id}

    trigger_key = f"acp:report_trigger:{report_id}"
    now_iso = datetime.now(UTC).isoformat()

    r = _get_redis()
    try:
        await r.set(trigger_key, now_iso, ex=3600)
    finally:
        await r.aclose()

    logger.info("scheduled_report_triggered", report_id=report_id, tenant_id=tenant_id)
    return {"status": "queued", "report_id": report_id, "queued_at": now_iso}
