from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Numeric, String
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

    Sprint 17 (Aegis for Teams) — ``subject_kind`` distinguishes the new
    employee-scoped keys (``acp_emp_…``) from the legacy tenant + agent
    keys. Employee keys carry ``subject_email`` so every /v1/messages
    Anthropic-proxy call can be attributed back to the human who made it,
    and ``daily_budget_usd`` + ``monthly_budget_usd`` so the gateway can
    refuse over-budget requests before they ever reach upstream Anthropic.
    Legacy rows leave the new columns ``NULL`` / 'tenant' and behave
    exactly as before.
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

    # Sprint 17 — subject kind: 'tenant' (legacy) | 'agent' (SDK keys
    # bound to one agent) | 'employee' (LLM-proxy virtual keys minted for
    # human employees). VARCHAR not enum so adding a new kind later is a
    # one-line migration and doesn't require a Postgres ALTER TYPE.
    subject_kind: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="tenant",
        server_default="tenant",
        index=True,
    )

    # Sprint 17 — employee identity carry-through for /v1/messages spend
    # rollup. NULL for tenant + agent keys.
    subject_email: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )

    # Sprint 17 — per-employee budget caps (USD). NULL means no cap.
    daily_budget_usd: Mapped[float | None] = mapped_column(
        Numeric(10, 2), nullable=True,
    )
    monthly_budget_usd: Mapped[float | None] = mapped_column(
        Numeric(10, 2), nullable=True,
    )
