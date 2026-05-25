"""
Internal endpoint for durable billing DLQ writes.

When the normal billing path fails after retries, the gateway calls
POST /internal/billing-dlq to persist the event to the
pending_billing_events PostgreSQL table.  This provides crash-safe
durability that Redis alone cannot: a FLUSHDB or Redis node failure
will not lose queued billing events.

The background recovery worker (see services/usage/main.py) retries
rows with processed_at=NULL and retry_count < 5 every 60 seconds.
"""

from __future__ import annotations

import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from sdk.common.auth import verify_internal_secret
from sdk.common.db import get_db
from sdk.common.response import APIResponse
from services.usage.models.pending_billing import PendingBillingEvent

logger = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/internal",
    tags=["internal"],
    dependencies=[Depends(verify_internal_secret)],
)


class BillingDlqRequest(BaseModel):
    """Payload sent by the gateway when a billing event cannot be delivered."""

    tenant_id: str
    agent_id: str | None = None
    action: str
    tokens: int = 0
    audit_id: str | None = None
    error: str | None = None

    model_config = ConfigDict(extra="ignore")


@router.post(
    "/billing-dlq",
    response_model=APIResponse[dict],
    status_code=status.HTTP_201_CREATED,
    summary="Persist a failed billing event to the durable pending_billing_events table",
)
async def persist_billing_dlq(
    payload: BillingDlqRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> APIResponse[dict]:
    """
    Accepts a failed billing event from the gateway and writes it to
    pending_billing_events with processed_at=NULL so the recovery worker
    can retry it.

    Uses ON CONFLICT DO NOTHING on audit_id so duplicate gateway retries
    are idempotent.
    """
    event_id = uuid.uuid4()
    stmt = (
        pg_insert(PendingBillingEvent)
        .values(
            id=event_id,
            tenant_id=payload.tenant_id,
            agent_id=payload.agent_id,
            action=payload.action,
            tokens=max(payload.tokens, 0),
            audit_id=payload.audit_id,
            retry_count=0,
            last_error=payload.error,
            processed_at=None,
        )
        .on_conflict_do_nothing(constraint="uq_pending_billing_events_audit_id")
    )
    await db.execute(stmt)
    await db.commit()

    logger.info(
        "billing_dlq_event_persisted",
        event_id=str(event_id),
        tenant_id=payload.tenant_id,
        audit_id=payload.audit_id,
        action=payload.action,
    )

    return APIResponse(
        data={
            "id": str(event_id),
            "audit_id": payload.audit_id,
            "status": "pending",
        }
    )
