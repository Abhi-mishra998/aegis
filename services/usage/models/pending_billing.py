from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from sdk.common.db import Base


class PendingBillingEvent(Base):
    """
    Durable store for billing events that failed the normal write path.

    When the usage service is temporarily unavailable and the Redis DLQ
    would be the only fallback, this table provides a crash-safe alternative
    so billing events survive a Redis FLUSHDB or node failure.

    Recovery: the background worker in services/usage/main.py retries
    events with processed_at=None and retry_count < 5, marking
    processed_at=now() on success.  After 5 failures the row is left
    for manual review and a CRITICAL log is emitted.
    """

    __tablename__ = "pending_billing_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    agent_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # The decision action: allow / throttle / kill / monitor / deny
    action: Mapped[str] = mapped_column(String(64), nullable=False)

    tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Correlates back to the originating audit_log row (unique per execution)
    audit_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # null = pending; set to now() on successful processing
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
