from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import uuid
from typing import Any

import structlog
from sqlalchemy import desc, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from sdk.common.audit_hash import GENESIS_HASH, compute_event_hash
from sdk.common.background import safe_bg as _safe_bg
from sdk.utils import (
    AUDIT_DUPLICATES_DROPPED_TOTAL,
    BILLING_OUTBOX_COVERAGE_GAP_TOTAL,
    SLO_AUDIT_DURABILITY_TOTAL,
)
from services.audit.models import AuditLog, PendingUsageEvent
from services.audit.schemas import AuditLogCreate

logger = structlog.get_logger(__name__)

# H-2 FIX (2026-05-13): Number of chain shards per tenant. Each shard is an
# independent verifiable chain protected by its own advisory lock. Default 16
# is conservative — single-tenant throughput scales linearly with shard count.
AUDIT_CHAIN_SHARD_COUNT: int = int(os.getenv("AUDIT_CHAIN_SHARD_COUNT", "16"))


def compute_chain_shard(request_id: str | None) -> int:
    """Stable shard derivation from request_id. Falls back to shard 0."""
    if not request_id:
        return 0
    digest = hashlib.md5(request_id.encode()).digest()
    return int.from_bytes(digest[:2], "big") % AUDIT_CHAIN_SHARD_COUNT


class AuditWriter:
    """Service class for persisting audit logs with cryptographic integrity."""

    @staticmethod
    async def log(db: AsyncSession, redis: Any, payload: AuditLogCreate,
                  billing_data: dict[str, Any] | None = None) -> tuple[AuditLog | None, PendingUsageEvent | None]:
        """
        Idempotent audit logging with cryptographic chaining.
        P0 FIX (2026-05-04): Removed broken pending_usage_events pattern that caused
        5,926 orphaned audit logs in load test. Billing is now handled synchronously
        by gateway middleware with timeout+fallback, not via async worker.
        Uses a PostgreSQL advisory lock (pg_advisory_xact_lock) to serialize chain
        writes per tenant across all workers — held for the duration of the transaction.

        Returns: (audit_log, pending_event) where pending_event is None (pattern removed).
        """
        # H-2 FIX (2026-05-13): Lock per (tenant, chain_shard). Concurrent writes for
        # the same tenant on different shards proceed in parallel. The two-key
        # advisory lock uses the int form pg_advisory_xact_lock(int, int).
        chain_shard = compute_chain_shard(payload.request_id)
        tenant_lock = int.from_bytes(payload.tenant_id.bytes[:4], "big") & 0x7FFFFFFF

        try:
            await db.execute(
                text("SELECT pg_advisory_xact_lock(:t, :s)"),
                {"t": tenant_lock, "s": chain_shard},
            )

            # 1. Fetch previous hash for this (tenant, shard) chain
            prev_stmt = (
                select(AuditLog.event_hash)
                .where(
                    AuditLog.tenant_id == payload.tenant_id,
                    AuditLog.chain_shard == chain_shard,
                )
                .order_by(desc(AuditLog.timestamp), desc(AuditLog.id))
                .limit(1)
            )
            prev_result = await db.execute(prev_stmt)
            prev_hash: str = prev_result.scalar_one_or_none() or GENESIS_HASH

            # 2. Canonical hash — MUST match main.py consumer and integrity.py verifier
            event_hash = compute_event_hash(
                prev_hash=prev_hash,
                tenant_id=str(payload.tenant_id),
                agent_id=str(payload.agent_id),
                action=payload.action,
                tool=payload.tool,
                decision=payload.decision,
                request_id=payload.request_id,
            )

            # 3. Insert with ON CONFLICT handling
            data = payload.model_dump()
            data["prev_hash"] = prev_hash
            data["event_hash"] = event_hash
            data["chain_shard"] = chain_shard

            # HARDENED: Explicitly set org_id from tenant_id for Core inserts
            if data.get("org_id") is None:
                data["org_id"] = data.get("tenant_id")

            # No conflict target: suppresses conflicts on any unique constraint
            # (PK, request_id partial index). Safe because duplicates are counted
            # by AUDIT_DUPLICATES_DROPPED_TOTAL and the caller handles None.
            stmt = (
                insert(AuditLog)
                .values(**data)
                .on_conflict_do_nothing()
                .returning(AuditLog)
            )

            result = await db.execute(stmt)
            audit_row = result.fetchone()

            if audit_row is None:
                logger.info("audit_duplicate_detected", request_id=payload.request_id)
                AUDIT_DUPLICATES_DROPPED_TOTAL.inc()
                SLO_AUDIT_DURABILITY_TOTAL.labels(stage="duplicate_dropped").inc()
                await db.commit()
                return None, None

            audit_log = audit_row[0]
            pending_event: PendingUsageEvent | None = None

            # 2026-05-14 — Transactional Outbox (per production_hardening_spec).
            # The gateway middleware does sync billing as the FAST path. This
            # outbox write is the DURABILITY backstop: if the sync path drops
            # the event (network blip, container OOM, retry exhaustion), the
            # outbox row is still here and the outbox_worker drains it after a
            # short grace period. Both paths converge on the same usage_records
            # row via the UNIQUE(audit_id) constraint → exactly-once semantics.
            #
            # We only outbox billable execute_tool events (not denies, not
            # decision_evaluate, not user_login). Non-billable events return
            # immediately so the outbox table stays a tight working set.
            if (
                payload.action == "execute_tool"
                and (payload.decision or "").lower() not in ("reject",)
            ):
                units = 1
                cost = 0.001
                tool_name = payload.tool or "unknown"
                if billing_data:
                    units = int(billing_data.get("units") or 1)
                    cost = float(billing_data.get("cost") or 0.001)
                    tool_name = billing_data.get("tool") or tool_name

                # Both the gateway's sync billing path and this outbox row
                # converge on `usage_records.audit_id` (UNIQUE). The sync path
                # uses `X-Request-ID` (a UUID string) as the audit_id, so the
                # outbox MUST use the same value — otherwise the two writes
                # produce two distinct usage rows and the UNIQUE constraint
                # never fires (see production_gaps Gap 3, 2026-05-15).
                outbox_audit_id: uuid.UUID = audit_log.id
                if payload.request_id:
                    with contextlib.suppress(ValueError):
                        outbox_audit_id = uuid.UUID(payload.request_id)

                outbox_stmt = (
                    insert(PendingUsageEvent)
                    .values(
                        tenant_id=payload.tenant_id,
                        org_id=payload.tenant_id,
                        audit_id=outbox_audit_id,
                        agent_id=payload.agent_id,
                        tool=tool_name,
                        units=units,
                        cost=cost,
                        status="pending",
                    )
                    .on_conflict_do_nothing(index_elements=["audit_id"])
                    .returning(PendingUsageEvent)
                )
                ob_res = await db.execute(outbox_stmt)
                ob_row = ob_res.fetchone()
                if ob_row is not None:
                    pending_event = ob_row[0]
                else:
                    # on_conflict_do_nothing silently dropped a duplicate — the sync
                    # billing path already created this outbox row. Count it so
                    # acp_billing_outbox_coverage_gap_total shows unexpected gaps.
                    BILLING_OUTBOX_COVERAGE_GAP_TOTAL.inc()

            # Atomic commit: audit_log + (optional) pending_usage_events in the
            # same transaction. If either insert fails, neither row exists.
            await db.commit()

            SLO_AUDIT_DURABILITY_TOTAL.labels(stage="persisted").inc()

            # Fire-and-forget SIEM forward — never blocks the audit write path.
            # Import is deferred to avoid a circular dependency at module load.
            try:
                from services.audit.siem import siem_forward
                asyncio.create_task(_safe_bg(siem_forward(audit_log)))
            except Exception:
                pass  # SIEM forward failure must never affect audit durability

            return audit_log, pending_event

        except Exception as exc:
            await db.rollback()
            logger.error(
                "audit_writer_error",
                error=str(exc),
                request_id=payload.request_id,
            )
            raise
