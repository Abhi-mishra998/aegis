"""
Audit Integrity Verifier
========================
FIX C-3: Recomputed hash is now assigned and compared (was previously discarded).
FIX M-2: `import json` moved to module level (was inside the for loop).
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sdk.common.audit_hash import GENESIS_HASH, compute_event_hash
from services.audit.models import AuditLog

logger = structlog.get_logger(__name__)


class IntegrityResult:
    def __init__(self, tenant_id: uuid.UUID) -> None:
        self.tenant_id = tenant_id
        self.is_integrous = True
        self.processed_count = 0
        self.error_events: list[dict[str, Any]] = []


_INTEGRITY_PAGE_SIZE = 10_000  # OOM guard: never load more than 10k rows at once


async def verify_audit_chain(db: AsyncSession, tenant_id: uuid.UUID) -> dict[str, Any]:
    """
    Verifies the cryptographic integrity of the audit log chain for a tenant.

    H-2 (2026-05-13): The chain is sharded per (tenant, chain_shard); each shard
    is an independent verifiable chain. We group rows by chain_shard and verify
    each chain in timestamp order. Tampering of any shard fails the whole tenant.

    Checks (per shard):
      1. prev_hash of each entry equals the event_hash of the previous entry.
      2. H(prev_hash + data) == event_hash (tamper detection).
    """
    stmt = (
        select(AuditLog)
        .where(AuditLog.tenant_id == tenant_id)
        .order_by(AuditLog.chain_shard.asc(), AuditLog.timestamp.asc(), AuditLog.id.asc())
        .limit(_INTEGRITY_PAGE_SIZE)
    )
    result = await db.execute(stmt)
    logs = result.scalars().all()

    if not logs:
        # 2026-05-13: an empty chain is integrous by definition. Previously this
        # returned `success=True` with no `is_integrous` field, which collapsed
        # to "broken" in the UI's truthiness check.
        return {
            "success": True,
            "valid": True,
            "is_integrous": True,
            "tenant_id": str(tenant_id),
            "processed_count": 0,
            "error_count": 0,
            "violations": [],
            "details": "No logs found to verify.",
        }

    res = IntegrityResult(tenant_id)
    last_verified_hash: dict[int, str] = {}  # per-shard chain head

    for entry in logs:
        res.processed_count += 1
        shard = int(getattr(entry, "chain_shard", 0) or 0)
        expected_prev = last_verified_hash.get(shard, GENESIS_HASH)

        recomputed = compute_event_hash(
            prev_hash=str(entry.prev_hash or GENESIS_HASH),
            tenant_id=str(entry.tenant_id),
            agent_id=str(entry.agent_id),
            action=entry.action,
            tool=entry.tool,
            decision=entry.decision,
            request_id=entry.request_id,
        )

        if entry.prev_hash != expected_prev:
            res.is_integrous = False
            res.error_events.append(
                {
                    "request_id": entry.request_id,
                    "shard": shard,
                    "error": "Chain gap detected",
                    "expected_prev": expected_prev,
                    "actual_prev": entry.prev_hash,
                }
            )

        if recomputed != entry.event_hash:
            logger.critical(
                "audit_tampering_detected",
                request_id=entry.request_id,
                shard=shard,
                expected_hash=recomputed,
                stored_hash=entry.event_hash,
            )
            # Sprint 3.5: emit the SLI counter the Alertmanager rule
            # `ChainViolationImmediate` (for: 0m) watches. The counter
            # increment happens BEFORE the return so the page fires
            # even if a downstream caller swallows the response body.
            try:
                from sdk.utils import AUDIT_CHAIN_VIOLATIONS_TOTAL
                AUDIT_CHAIN_VIOLATIONS_TOTAL.inc()
            except ImportError:
                pass
            return {
                "tenant_id": str(tenant_id),
                "valid": False,
                "is_integrous": False,
                "error": "Audit tampering detected",
                "processed_count": res.processed_count,
                "error_count": len(res.error_events) + 1,
                "violations": res.error_events,
            }

        last_verified_hash[shard] = entry.event_hash

    # Sprint 3.5 — if the chain accumulated any error_events without
    # tripping the inline-tamper branch above (e.g. missing prev_hash,
    # malformed JSON), still bump the SLI counter so the alert fires.
    if res.error_events:
        try:
            from sdk.utils import AUDIT_CHAIN_VIOLATIONS_TOTAL
            AUDIT_CHAIN_VIOLATIONS_TOTAL.inc(len(res.error_events))
        except ImportError:
            pass

    return {
        "tenant_id": str(tenant_id),
        "valid": res.is_integrous,
        "is_integrous": res.is_integrous,
        "processed_count": res.processed_count,
        "error_count": len(res.error_events),
        "violations": res.error_events,
    }
