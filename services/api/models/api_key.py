from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from sdk.common.db import Base, IdMixin, TenantMixin, TimestampMixin


class APIKey(Base, TenantMixin, IdMixin, TimestampMixin):
    """Stores API Keys for customers/users of a Tenant.

    Sprint 1.5 — ``agent_id`` lets operators issue per-agent-scoped keys.
    When set, the gateway requires the inbound ``X-Agent-ID`` header to match
    this value (otherwise an attacker holding the key could impersonate any
    agent within the tenant). Existing tenant-scoped keys leave it ``NULL``
    and the gateway falls back to the legacy behavior — back-compat preserved.
    """

    __tablename__ = "api_keys"

    name: Mapped[str] = mapped_column(String(100), nullable=False)

    # The actual key hint or prefix for display
    key_prefix: Mapped[str] = mapped_column(String(10), nullable=False)

    # Hashed version of the full API key
    key_hash: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Sprint 1.5 — optional per-agent scope. NULL means tenant-scoped (legacy).
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )
