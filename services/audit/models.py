from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from sdk.common.db import Base, IdMixin, OrgMixin, TenantMixin, TimestampMixin


class AuditLog(Base, OrgMixin, TenantMixin, IdMixin, TimestampMixin):
    __tablename__ = "audit_logs"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        index=True,
        nullable=False,
    )

    action: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    tool: Mapped[str] = mapped_column(String(255), index=True, nullable=True)
    decision: Mapped[str] = mapped_column(
        String(50), index=True, nullable=False
    )  # allow / deny / error
    reason: Mapped[str] = mapped_column(Text, nullable=True)

    # Stores request/response payload or context
    metadata_json: Mapped[dict] = mapped_column(JSONB, default={}, nullable=False)

    request_id: Mapped[str] = mapped_column(String(50), index=True, nullable=True)
    event_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=True)
    prev_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=True)

    # H-2 FIX (2026-05-13): Shard id for parallel per-tenant chain locking.
    # Default 0 preserves single-chain semantics for legacy rows; new writes
    # derive shard = hash(request_id) % AUDIT_CHAIN_SHARD_COUNT.
    chain_shard: Mapped[int] = mapped_column(
        SmallInteger, default=0, server_default="0", nullable=False
    )

    # P0 FIX (2026-05-04): Changed default to "completed" since billing is now processed
    # synchronously in gateway middleware (record_billing_event) with timeout+fallback.
    # This eliminates the async gap that caused 5,926 orphaned audits with status='pending'
    # in the load test. Billing is guaranteed before response is sent to client.
    billing_status: Mapped[str] = mapped_column(String(20), default="completed", server_default="completed", index=True)

    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        index=True,
    )

    __table_args__ = (
        # Partial unique index on request_id (non-null only) prevents dual-instance
        # races from inserting duplicate rows for the same request. The migration
        # j5k6l7m8n9o0 replaced the old (request_id, event_hash) composite constraint.
        Index(
            "uq_audit_request_id_notnull",
            "request_id",
            unique=True,
            postgresql_where="request_id IS NOT NULL",
        ),
        Index("ix_audit_logs_org_id_tenant_id", "org_id", "tenant_id"),
        Index("ix_audit_logs_chain_shard", "tenant_id", "chain_shard", "timestamp"),
    )


# ---------------------------------------------------------------------------
# Transparency log — daily Merkle root commitment over signed receipts.
# ---------------------------------------------------------------------------


class TransparencyRoot(Base):
    """One row per (tenant, day). The root commits to every signed receipt in
    that window. Customers archive the row at end-of-day and can later detect
    retroactive tampering or deletion: any change to the underlying audit
    rows shifts the recomputed root.

    2026-05-15 — Crypto Sprint: added `prev_root_hash` so daily roots form an
    append-only Merkle-of-Merkles chain. Each day's signed payload commits
    to the previous day's root_hash. An adversary in possession of the root
    signing key still cannot silently rewrite history, because rewriting
    yesterday changes its root_hash, which breaks today's prev_root_hash
    pointer, which would require re-signing the entire suffix of days — any
    customer who archived an earlier root sees the break instantly.
    """

    __tablename__ = "transparency_roots"

    tenant_id:           Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    root_date:           Mapped[date]      = mapped_column(Date,              nullable=False)
    root_hash:           Mapped[str]       = mapped_column(String(64),        nullable=False)
    # Pointer to the immediately-previous (tenant, root_date) row's root_hash.
    # NULL only for the very first persisted day per tenant. Together with
    # root_hash this column makes transparency_roots an append-only chain.
    prev_root_hash:      Mapped[str | None] = mapped_column(String(64),       nullable=True)
    leaf_count:          Mapped[int]       = mapped_column(Integer,           nullable=False)
    signed_root_payload: Mapped[dict]      = mapped_column(JSONB,             nullable=False)
    # 2026-05-15 — Transparency Log sprint: leaf range + signing fingerprint.
    # leaf_range_*_id pin the exact audit_logs row span the root committed to.
    # signing_key_fingerprint records which root key signed this row so the
    # historical keys table can authenticate old roots after rotation.
    leaf_range_start_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    leaf_range_end_id:   Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    signing_key_fingerprint: Mapped[str | None]   = mapped_column(String(64),          nullable=True, index=True)
    computed_at:         Mapped[datetime]  = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "root_date"),
        Index("ix_transparency_roots_date", "root_date", "tenant_id"),
    )


class TransparencyHistoricalKey(Base):
    """Registry of rotated root-signing public keys.

    The active key lives on disk (/data/keys/root-signing.pem) or env var.
    On rotation, the previous key's PEM + fingerprint is written here so
    that `/receipts/verify` and `/transparency/verify-root` continue to
    validate payloads signed before the rotation.

    Written by `scripts/maintenance/rotate_transparency_key.py`; read by the
    signer module's verify path.
    """

    __tablename__ = "transparency_historical_keys"

    id:             Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    fingerprint:    Mapped[str]       = mapped_column(String(64),         nullable=False, unique=True, index=True)
    public_key_pem: Mapped[str]       = mapped_column(Text,               nullable=False)
    algorithm:      Mapped[str]       = mapped_column(String(32),         nullable=False, server_default="ed25519")
    rotated_at:     Mapped[datetime]  = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    retired_reason: Mapped[str | None] = mapped_column(Text,              nullable=True)


# ---------------------------------------------------------------------------
# Analyst Notes — per-audit-entry investigation annotations
# ---------------------------------------------------------------------------


class AuditNote(Base):
    """Analyst-written note on a single audit log entry.

    Analysts use this during incident investigation to record whether a
    decision was a false positive, a confirmed threat, or to leave context
    for the next reviewer.  Notes are append-only and linked to the parent
    audit_log row via ``audit_id``.
    """

    __tablename__ = "audit_notes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    audit_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    note_type: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="analysis"
    )  # analysis | false_positive | confirmed_threat | escalated
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_audit_notes_audit_id_tenant", "audit_id", "tenant_id"),
    )


# ---------------------------------------------------------------------------
# HARDENED INVARIANTS (SQLAlchemy Events)
# ---------------------------------------------------------------------------

from sqlalchemy import event


class PendingUsageEvent(Base, OrgMixin, TenantMixin, IdMixin):
    """Outbox pattern: pending billing events guaranteed to be processed.

    Written atomically with AuditLog in same transaction.
    Background worker processes these and writes to UsageRecord (usage service).
    Guarantees zero orphaned audits: 100% audit_logs = 100% usage_records.
    """

    __tablename__ = "pending_usage_events"

    audit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), unique=True, nullable=False, index=True
    )

    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

    tool: Mapped[str] = mapped_column(String(255), nullable=False)
    units: Mapped[int] = mapped_column(Integer, default=1)
    cost: Mapped[float] = mapped_column(Float, default=0.0)

    # Status: 'pending' (queued), 'completed' (written to usage_records), 'failed' (retries exhausted)
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending", index=True)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


# ---------------------------------------------------------------------------
# Incident Workflow — audit-service-side incident records & comment thread
# ---------------------------------------------------------------------------


class AuditIncident(Base):
    """Audit-service incident record.

    Tracks the full lifecycle of a security incident: status transitions,
    assignee, severity, and free-form notes.  A separate IncidentComment
    table provides a chronological comment thread per incident.
    """

    __tablename__ = "acp_incidents"

    id:         Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id:  Mapped[str]       = mapped_column(String(64),  nullable=False, index=True)
    title:      Mapped[str]       = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text,     nullable=True)
    severity:   Mapped[str]       = mapped_column(String(20),  nullable=False, server_default="medium")
    status:     Mapped[str]       = mapped_column(
        String(30), nullable=False, server_default="open", index=True
    )  # open | investigating | contained | resolved | closed
    assignee:   Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes:      Mapped[str | None] = mapped_column(Text,        nullable=True)
    # Reference to the originating audit row / external incident id
    source_audit_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    created_at: Mapped[datetime]  = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime]  = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class IncidentComment(Base):
    """Timeline comment on an AuditIncident.

    Ordered by `created_at` ascending to produce a chronological thread.
    """

    __tablename__ = "acp_incident_comments"

    id:          Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )  # FK → acp_incidents.id (enforced at migration level)
    tenant_id:   Mapped[str]       = mapped_column(String(64),  nullable=False)
    author:      Mapped[str]       = mapped_column(String(255), nullable=False)
    body:        Mapped[str]       = mapped_column(Text,        nullable=False)
    created_at:  Mapped[datetime]  = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_incident_comments_incident_created", "incident_id", "created_at"),
    )


@event.listens_for(AuditLog, "before_insert")
def enforce_org_id_invariant(mapper, connection, target) -> None:
    """
    Enforces the SaaS strict invariant: org_id MUST equal tenant_id.
    If org_id is missing, it auto-fills from tenant_id.
    If both are present but mismatch, it raises a security error.
    """
    tenant_id = getattr(target, "tenant_id", None)
    org_id = getattr(target, "org_id", None)

    if org_id is None and tenant_id is not None:
        target.org_id = tenant_id
    elif org_id is not None and tenant_id is not None and org_id != tenant_id:
        raise ValueError(
            f"SaaS Multi-tenant Violation: org_id ({org_id}) != tenant_id ({tenant_id})"
        )
