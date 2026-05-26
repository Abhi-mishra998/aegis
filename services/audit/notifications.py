"""In-app notifications — SQLAlchemy model + CRUD.

Columns: id (UUID), tenant_id (str), title (str), body (str),
         level (str: info|warning|error|success), category (str: policy|incident|quota|system),
         is_read (bool, default False), link (str, nullable),
         created_at (DateTime UTC)
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import Boolean, DateTime, Index, String, Text, func, select, update
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from sdk.common.db import Base

logger = structlog.get_logger(__name__)


class Notification(Base):
    """In-app notification row scoped to a tenant."""

    __tablename__ = "acp_notifications"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    level: Mapped[str] = mapped_column(
        String(20), nullable=False, default="info"
    )  # info | warning | error | success
    category: Mapped[str] = mapped_column(
        String(50), nullable=False, default="system"
    )  # policy | incident | quota | system
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    link: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default="now()",
    )

    __table_args__ = (
        Index("ix_acp_notifications_tenant_read_ts", "tenant_id", "is_read", "created_at"),
    )


# ---------------------------------------------------------------------------
# CRUD helpers
# ---------------------------------------------------------------------------


async def create_notification(
    db: AsyncSession,
    tenant_id: str,
    title: str,
    body: str,
    level: str = "info",
    category: str = "system",
    link: str | None = None,
) -> Notification:
    """Insert a new notification row and return it."""
    notif = Notification(
        id=uuid.uuid4(),
        tenant_id=str(tenant_id),
        title=title,
        body=body,
        level=level,
        category=category,
        is_read=False,
        link=link,
        created_at=datetime.now(UTC),
    )
    db.add(notif)
    await db.commit()
    await db.refresh(notif)
    logger.info("notification_created", tenant_id=str(tenant_id), title=title, level=level)
    return notif


async def list_notifications(
    db: AsyncSession,
    tenant_id: str,
    unread_only: bool = False,
    limit: int = 50,
) -> list[Notification]:
    """Return notifications for a tenant, newest first."""
    q = select(Notification).where(Notification.tenant_id == str(tenant_id))
    if unread_only:
        q = q.where(Notification.is_read.is_(False))
    q = q.order_by(Notification.created_at.desc()).limit(limit)
    result = await db.execute(q)
    return list(result.scalars().all())


async def mark_read(
    db: AsyncSession,
    tenant_id: str,
    notification_id: str,
) -> bool:
    """Mark a single notification as read. Returns True if found, False otherwise."""
    try:
        nid = uuid.UUID(str(notification_id))
    except ValueError:
        return False

    q = (
        update(Notification)
        .where(Notification.id == nid, Notification.tenant_id == str(tenant_id))
        .values(is_read=True)
        .returning(Notification.id)
    )
    result = await db.execute(q)
    await db.commit()
    row = result.fetchone()
    return row is not None


async def mark_all_read(
    db: AsyncSession,
    tenant_id: str,
) -> int:
    """Mark all unread notifications for the tenant as read. Returns count marked."""
    q = (
        update(Notification)
        .where(Notification.tenant_id == str(tenant_id), Notification.is_read.is_(False))
        .values(is_read=True)
        .returning(Notification.id)
    )
    result = await db.execute(q)
    await db.commit()
    rows = result.fetchall()
    return len(rows)


async def get_unread_count(
    db: AsyncSession,
    tenant_id: str,
) -> int:
    """Return the number of unread notifications for the tenant."""
    q = select(func.count()).where(
        Notification.tenant_id == str(tenant_id),
        Notification.is_read.is_(False),
    )
    result = await db.execute(q)
    return int(result.scalar_one_or_none() or 0)
