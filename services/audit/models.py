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


# ---------------------------------------------------------------------------
# Sprint 5 — Evaluation suite (Datasets, Evaluators, Eval Jobs).
#
# These tables back the attack-evaluation workflow: a Dataset groups labelled
# DatasetCases (attack or benign), an Evaluator is a named scorer config, and
# an EvalJob replays a dataset through the REAL /execute pipeline and stores
# per-case EvalJobResult rows. EvaluatorScoreSnapshot rolls up per-evaluator
# per-day numbers so the dashboard can show a "biggest evaluator score
# changes" trend without re-aggregating EvalJobResult on every page load.
# ---------------------------------------------------------------------------


class EvalDataset(Base):
    """A named, versioned corpus of labelled test cases.

    A Dataset is the unit a user (or the nightly cron) picks up and replays
    through the pipeline. `kind` tells the runner whether to expect denies
    (attack), allows (benign), or both (mixed) — the evaluators use the same
    label to compute recall vs FP rate.
    """

    __tablename__ = "eval_datasets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="mixed"
    )  # attack | benign | mixed
    version: Mapped[str] = mapped_column(String(50), nullable=False, server_default="1")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    case_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_eval_datasets_tenant_name", "tenant_id", "name"),
    )


class EvalDatasetCase(Base):
    """A single labelled case inside a Dataset.

    `base_id` groups all mutations of the same root attack so the per-rule
    evaluator can see, e.g., that 6 of 8 mutations of the same payload were
    caught. `expected_outcome` is the ground truth the evaluator compares
    actual_outcome against.
    """

    __tablename__ = "eval_dataset_cases"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    case_kind: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # attack | benign
    owasp_category: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    base_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    mutation: Mapped[str] = mapped_column(
        String(40), nullable=False, server_default="none"
    )  # none | case | whitespace | comment_split | url_encode | base64 | homoglyph | multilingual
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    expected_outcome: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # deny | allow
    expected_findings: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_eval_cases_dataset_kind", "dataset_id", "case_kind"),
        Index("ix_eval_cases_dataset_owasp", "dataset_id", "owasp_category"),
    )


class Evaluator(Base):
    """A named scorer configuration.

    `kind` selects which scorer the runner instantiates. `config_json`
    carries scorer-specific parameters (e.g., owasp_category filter, target
    rule id, minimum-samples threshold).
    """

    __tablename__ = "eval_evaluators"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(
        String(40), nullable=False
    )  # detection_rate | fp_rate | per_rule_efficacy
    config_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(
        SmallInteger, nullable=False, server_default="1"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_evaluators_tenant_name", "tenant_id", "name"),
    )


class EvalJob(Base):
    """One run of a Dataset through the pipeline, scored by Evaluators.

    `summary_json` carries the rolled-up scores per evaluator so dashboards
    can render without scanning eval_job_results.
    """

    __tablename__ = "eval_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    evaluator_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    schedule: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="manual"
    )  # manual | nightly | shadow
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="queued", index=True
    )  # queued | running | completed | failed | cancelled
    cases_total: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    cases_done: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    summary_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_eval_jobs_tenant_queued", "tenant_id", "queued_at"),
        Index("ix_eval_jobs_status_queued", "status", "queued_at"),
    )


class EvalJobResult(Base):
    """One row per (job, case): what the pipeline actually returned.

    `rule_attribution_json` is the per-rule trace harvested from the
    decision response — e.g., `{"policy_rule_id": "tool_not_allowed",
    "behavior_heuristic": null, "injection_pattern_id": "p_owasp_lm01_3"}`.
    The per-rule evaluator slices on this column.
    """

    __tablename__ = "eval_job_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    eval_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    owasp_category: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    case_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    expected_outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    actual_outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    passed: Mapped[bool] = mapped_column(SmallInteger, nullable=False)
    findings: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    rule_attribution_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    latency_ms: Mapped[float] = mapped_column(
        Float, nullable=False, server_default="0.0"
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_results_job_passed", "eval_job_id", "passed"),
        Index("ix_results_tenant_created", "tenant_id", "created_at"),
        # Idempotency: never store the same (job, case) twice.
        Index("uq_results_job_case", "eval_job_id", "case_id", unique=True),
    )


class EvaluatorScoreSnapshot(Base):
    """Daily per-evaluator rollup — the dashboard's "biggest score changes"
    trend reads from this table so the per-rule efficacy sparkline doesn't
    re-scan eval_job_results.
    """

    __tablename__ = "eval_evaluator_score_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    evaluator_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    rule_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    score: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.0")
    samples: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    eval_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "ix_snap_evaluator_rule_date",
            "evaluator_id",
            "rule_id",
            "snapshot_date",
        ),
        Index(
            "uq_snap_evaluator_rule_date",
            "tenant_id",
            "evaluator_id",
            "rule_id",
            "snapshot_date",
            unique=True,
        ),
    )


# ---------------------------------------------------------------------------
# Sprint 6 — Shadow-mode policies + online evaluation.
#
# A ShadowPolicy is a candidate policy in one of four modes:
#
#   draft     — under construction; not evaluated against live traffic
#   shadow    — evaluated on every (or sampled) /execute, records would-have
#               decisions, NEVER affects real enforcement
#   enforce   — promoted; conceptually equivalent to deployed OPA bundle
#   archived  — historical, kept for audit / rollback
#
# A ShadowPolicyVersion row is appended on every state change so an
# operator can rollback to any prior {rules_json, mode} pair.
#
# A ShadowDecision row is the per-request "what would this candidate have
# done?" outcome — produced from the gateway's fire-and-forget shadow
# evaluator. The hot path NEVER reads from this table.
# ---------------------------------------------------------------------------


class ShadowPolicy(Base):
    """A candidate policy under shadow evaluation or already enforcing.

    `agent_id IS NULL` means tenant-wide (every agent in the tenant).
    `rules_json` is a list of PolicyRule dicts using the existing
    services/policy/schemas.py::PolicyRule shape (conditions + action).
    The shape is preserved so a promoted ShadowPolicy can be compiled to
    Rego by the existing /simulate evaluator without translation.
    """

    __tablename__ = "shadow_policies"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    mode: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="draft", index=True
    )  # draft | shadow | enforce | archived
    rules_json: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    sample_rate: Mapped[float] = mapped_column(
        Float, nullable=False, server_default="1.0"
    )  # 0.0-1.0; what fraction of /execute the shadow evaluator runs
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    promoted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_shadow_policies_tenant_mode", "tenant_id", "mode"),
        Index("ix_shadow_policies_tenant_agent", "tenant_id", "agent_id"),
    )


class ShadowPolicyVersion(Base):
    """Append-only version log — every {mode, rules_json} change writes a row.

    Rollback restores `rules_json` + `mode_after` from a target version row
    onto the parent ShadowPolicy + bumps its version.
    """

    __tablename__ = "shadow_policy_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    policy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    change_kind: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # create | edit | promote | rollback | archive
    mode_before: Mapped[str | None] = mapped_column(String(20), nullable=True)
    mode_after: Mapped[str] = mapped_column(String(20), nullable=False)
    rules_json: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    changed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "uq_shadow_policy_version",
            "policy_id",
            "version",
            unique=True,
        ),
    )


class ShadowDecision(Base):
    """Per-/execute would-have-decided record from the shadow evaluator.

    Written async via asyncio.create_task — the request handler never
    awaits this. `real_action` is what the live pipeline actually
    returned; `shadow_action` is what the candidate policy would have
    returned. Drift = (real_action != shadow_action).
    """

    __tablename__ = "shadow_decisions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    policy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    policy_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    request_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    audit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    tool: Mapped[str | None] = mapped_column(String(255), nullable=True)
    real_action: Mapped[str] = mapped_column(String(20), nullable=False)
    shadow_action: Mapped[str] = mapped_column(String(20), nullable=False)
    matched_rule_index: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    matched_rule_description: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    eval_latency_ms: Mapped[float] = mapped_column(
        Float, nullable=False, server_default="0.0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "ix_shadow_decisions_policy_created",
            "policy_id",
            "created_at",
        ),
        Index(
            "ix_shadow_decisions_tenant_created",
            "tenant_id",
            "created_at",
        ),
        # Drift slice — what was wrongly denied by shadow that real allowed.
        Index(
            "ix_shadow_decisions_drift",
            "policy_id",
            "real_action",
            "shadow_action",
        ),
    )


class OnlineEvalSampleConfig(Base):
    """Per-tenant sampling config for the online evaluator.

    `sample_rate` is the fraction of recent audit_logs the online
    evaluator scores per polling interval. `fp_threshold` is the
    benign‑deny rate at which we fire a drift notification (level=warning,
    category=policy).
    """

    __tablename__ = "online_eval_configs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True, index=True
    )
    enabled: Mapped[bool] = mapped_column(
        SmallInteger, nullable=False, server_default="1"
    )
    sample_rate: Mapped[float] = mapped_column(
        Float, nullable=False, server_default="0.05"
    )  # 0.0-1.0
    fp_threshold: Mapped[float] = mapped_column(
        Float, nullable=False, server_default="0.05"
    )
    poll_interval_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="900"
    )  # 15 minutes default
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
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
