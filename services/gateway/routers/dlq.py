"""Sprint 25 C6 — DLQ permanently-failed viewer endpoints.

Two read-only admin endpoints so the SOC can list audit + billing events
that were promoted to permanently-failed (after MAX_RETRIES or a permanent-
error marker like ForeignKeyViolation). Pre-Sprint-25, these queues grew
silently with no way to inspect them until month-end reconciliation
surfaced the gap.

Storage shapes (verified in the replay workers):
  * Audit   → ``acp:audit_stream:permanently_failed``  Redis STREAM (XADD)
  * Billing → ``acp:billing_dlq:permanently_failed``    Redis LIST   (LPUSH)

Replay/Re-queue is intentionally NOT exposed here — that's a separate
ticket (sprint-26) because it needs an audit trail of who manually
re-played which entry. This batch is read-only by design.
"""
from __future__ import annotations

import json
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Query, Request

from sdk.common.config import settings
from sdk.common.redis import get_redis_client
from services.gateway._helpers import require_admin_role

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["ops", "dlq"])

_AUDIT_PF_KEY = "acp:audit_stream:permanently_failed"
_BILLING_PF_KEY = "acp:billing_dlq:permanently_failed"


def _decode(v: Any) -> Any:
    if isinstance(v, bytes):
        try:
            return v.decode("utf-8")
        except UnicodeDecodeError:
            return v.decode("utf-8", errors="replace")
    return v


@router.get("/system/dlq/audit/permanently-failed")
async def audit_permanently_failed(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    _: Annotated[None, Depends(require_admin_role)] = None,
) -> dict[str, Any]:
    """List the most-recent ``limit`` permanently-failed audit entries."""
    redis = get_redis_client(settings.REDIS_URL, decode_responses=False)
    try:
        depth = int(await redis.xlen(_AUDIT_PF_KEY) or 0)
        raw = await redis.xrevrange(_AUDIT_PF_KEY, count=limit)
    finally:
        await redis.aclose()
    items: list[dict[str, Any]] = []
    for entry_id, fields in raw or []:
        decoded = {_decode(k): _decode(v) for k, v in (fields or {}).items()}
        items.append({"entry_id": _decode(entry_id), **decoded})
    return {"depth": depth, "returned": len(items), "items": items}


@router.get("/system/dlq/billing/permanently-failed")
async def billing_permanently_failed(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    _: Annotated[None, Depends(require_admin_role)] = None,
) -> dict[str, Any]:
    """List the most-recent ``limit`` permanently-failed billing entries."""
    redis = get_redis_client(settings.REDIS_URL, decode_responses=False)
    try:
        depth = int(await redis.llen(_BILLING_PF_KEY) or 0)
        raw = await redis.lrange(_BILLING_PF_KEY, 0, limit - 1)
    finally:
        await redis.aclose()
    items: list[dict[str, Any]] = []
    for raw_entry in raw or []:
        text = _decode(raw_entry)
        try:
            items.append(json.loads(text) if isinstance(text, str) else {"raw": str(text)})
        except (TypeError, ValueError):
            items.append({"raw": text if isinstance(text, str) else str(text)})
    return {"depth": depth, "returned": len(items), "items": items}
