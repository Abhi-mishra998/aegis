"""
Autonomy Contracts + Human Override Timeline — REST API

Contracts (F3):
  GET    /autonomy/contracts                 — list for tenant
  GET    /autonomy/contracts/{id}            — single
  POST   /autonomy/contracts                 — create
  PATCH  /autonomy/contracts/{id}            — update (bumps version)
  DELETE /autonomy/contracts/{id}            — disable
  POST   /autonomy/check                     — evaluate without DB writes
  GET    /autonomy/violations                — recent contract violations

Human override timeline (F6):
  GET    /autonomy/overrides                 — list (paginated, filtered)
  POST   /autonomy/overrides                 — append a human event

2026-05-13: every mutation invalidates the gateway's per-(tenant, agent)
autonomy-check cache so new contracts are enforced immediately.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

import structlog

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from redis.asyncio import Redis as _Redis

from sdk.common.auth import verify_internal_secret
from sdk.common.background import safe_bg as _safe_bg
from sdk.common.db import get_db, get_tenant_id
from sdk.common.response import APIResponse
from services.autonomy.enforcement import ContractView, evaluate

_REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
_redis_client: _Redis | None = None
_logger = structlog.get_logger(__name__)


async def _invalidate_autonomy_cache(tenant_id, agent_id) -> None:
    """Drop every cached autonomy-check entry for (tenant, agent). Fire-and-forget."""
    global _redis_client
    try:
        if _redis_client is None:
            _redis_client = _Redis.from_url(_REDIS_URL, decode_responses=False)
        pattern = f"acp:autonomy_check:{tenant_id}:{agent_id}:*"
        async for k in _redis_client.scan_iter(match=pattern, count=200):
            await _redis_client.delete(k)
    except Exception as exc:
        _logger.warning("autonomy_cache_invalidation_failed", error=str(exc))
from services.autonomy.models import (
    AutonomyContract, AutonomyViolation, HumanOverrideEvent,
)
from services.autonomy.schemas import (
    CheckRequest, CheckResult, ContractIn, ContractOut, OverrideIn,
    OverrideOut, ViolationOut,
)

router = APIRouter(
    prefix="/autonomy",
    tags=["autonomy"],
    dependencies=[Depends(verify_internal_secret)],
)


def _to_view(c: AutonomyContract) -> ContractView:
    return ContractView(
        id=c.id, version=c.version, enabled=c.enabled,
        allowed_actions=list(c.allowed_actions or []),
        denied_actions=list(c.denied_actions or []),
        approval_required=list(c.approval_required or []),
        max_runtime_seconds=c.max_runtime_seconds,
        max_tool_calls=c.max_tool_calls,
        max_cost_usd=c.max_cost_usd,
        max_autonomy_level=c.max_autonomy_level,
        escalation_triggers=list(c.escalation_triggers or []),
    )


@router.get("/contracts", response_model=APIResponse[list[ContractOut]])
async def list_contracts(
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    agent_id: uuid.UUID | None = Query(None),
) -> APIResponse[list[ContractOut]]:
    stmt = select(AutonomyContract).where(AutonomyContract.tenant_id == tenant_id)
    if agent_id is not None:
        stmt = stmt.where(AutonomyContract.agent_id == agent_id)
    stmt = stmt.order_by(desc(AutonomyContract.updated_at))
    rows = list((await db.execute(stmt)).scalars().all())
    return APIResponse(data=[ContractOut.model_validate(r) for r in rows])


@router.get("/contracts/{contract_id}", response_model=APIResponse[ContractOut])
async def get_contract(
    contract_id: uuid.UUID,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> APIResponse[ContractOut]:
    c = (await db.execute(
        select(AutonomyContract).where(
            AutonomyContract.id == contract_id,
            AutonomyContract.tenant_id == tenant_id,
        )
    )).scalar_one_or_none()
    if c is None:
        raise HTTPException(status_code=404, detail="Contract not found")
    return APIResponse(data=ContractOut.model_validate(c))


@router.post("/contracts", response_model=APIResponse[ContractOut])
async def create_contract(
    payload: ContractIn,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> APIResponse[ContractOut]:
    c = AutonomyContract(
        tenant_id=tenant_id, org_id=tenant_id,
        agent_id=payload.agent_id, name=payload.name,
        enabled=payload.enabled,
        allowed_actions=payload.allowed_actions,
        denied_actions=payload.denied_actions,
        approval_required=payload.approval_required,
        max_runtime_seconds=payload.max_runtime_seconds,
        max_tool_calls=payload.max_tool_calls,
        max_cost_usd=payload.max_cost_usd,
        max_autonomy_level=payload.max_autonomy_level,
        escalation_triggers=payload.escalation_triggers,
        notes=payload.notes,
    )
    db.add(c)
    await db.commit()
    # 2026-05-13 BUGFIX: refresh so server-side defaults (gen_random_uuid id,
    # created_at, version) are hydrated before serialization. Without this the
    # response is "{id: null, name: null}".
    await db.refresh(c)
    # Invalidate the per-agent cache so the new contract is enforced immediately.
    asyncio.create_task(_safe_bg(_invalidate_autonomy_cache(tenant_id, payload.agent_id)))
    return APIResponse(data=ContractOut.model_validate(c))


@router.patch("/contracts/{contract_id}", response_model=APIResponse[ContractOut])
async def update_contract(
    contract_id: uuid.UUID,
    payload: ContractIn,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> APIResponse[ContractOut]:
    c = (await db.execute(
        select(AutonomyContract).where(
            AutonomyContract.id == contract_id,
            AutonomyContract.tenant_id == tenant_id,
        )
    )).scalar_one_or_none()
    if c is None:
        raise HTTPException(status_code=404, detail="Contract not found")
    c.agent_id = payload.agent_id
    c.name = payload.name
    c.enabled = payload.enabled
    c.allowed_actions = payload.allowed_actions
    c.denied_actions = payload.denied_actions
    c.approval_required = payload.approval_required
    c.max_runtime_seconds = payload.max_runtime_seconds
    c.max_tool_calls = payload.max_tool_calls
    c.max_cost_usd = payload.max_cost_usd
    c.max_autonomy_level = payload.max_autonomy_level
    c.escalation_triggers = payload.escalation_triggers
    c.notes = payload.notes
    c.version = (c.version or 1) + 1
    await db.commit()
    asyncio.create_task(_safe_bg(_invalidate_autonomy_cache(tenant_id, c.agent_id)))
    return APIResponse(data=ContractOut.model_validate(c))


@router.delete("/contracts/{contract_id}", response_model=APIResponse[dict])
async def disable_contract(
    contract_id: uuid.UUID,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> APIResponse[dict]:
    c = (await db.execute(
        select(AutonomyContract).where(
            AutonomyContract.id == contract_id,
            AutonomyContract.tenant_id == tenant_id,
        )
    )).scalar_one_or_none()
    if c is None:
        raise HTTPException(status_code=404, detail="Contract not found")
    c.enabled = False
    await db.commit()
    asyncio.create_task(_safe_bg(_invalidate_autonomy_cache(tenant_id, c.agent_id)))
    return APIResponse(data={"id": str(contract_id), "enabled": False})


@router.post("/check", response_model=APIResponse[CheckResult])
async def check_action(
    req: CheckRequest,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> APIResponse[CheckResult]:
    """
    Evaluate `req.action` against the agent's enabled contracts.
    Multiple contracts can apply per agent — the result is the strictest:
    any deny dominates, approval_required carries through.
    """
    contracts = list((await db.execute(
        select(AutonomyContract).where(
            AutonomyContract.tenant_id == tenant_id,
            AutonomyContract.agent_id == req.agent_id,
            AutonomyContract.enabled.is_(True),
        )
    )).scalars().all())
    if not contracts:
        return APIResponse(data=CheckResult(allowed=True, reason="no_contract"))

    final_allowed = True
    final_requires_approval = False
    final_violations: list[str] = []
    chosen_contract: AutonomyContract | None = None
    for c in contracts:
        r = evaluate(
            _to_view(c), req.action,
            cost_estimate_usd=req.cost_estimate_usd,
            runtime_estimate_seconds=req.runtime_estimate_seconds,
            tool_calls_so_far=req.tool_calls_so_far,
        )
        if not r["allowed"]:
            final_allowed = False
            for v in r["violated_rules"]:
                final_violations.append(v)
                db.add(AutonomyViolation(
                    tenant_id=tenant_id, org_id=tenant_id,
                    contract_id=c.id, agent_id=req.agent_id,
                    request_id=req.request_id, rule=v,
                    detail={"action": req.action, "cost": req.cost_estimate_usd},
                ))
            chosen_contract = c
        if r["requires_approval"]:
            final_requires_approval = True
            chosen_contract = chosen_contract or c
    await db.commit()
    return APIResponse(data=CheckResult(
        allowed=final_allowed,
        requires_approval=final_requires_approval,
        violated_rules=final_violations,
        contract_id=chosen_contract.id if chosen_contract else None,
        contract_version=chosen_contract.version if chosen_contract else None,
        reason=(
            "; ".join(sorted(set(final_violations))) if not final_allowed
            else ("approval_required" if final_requires_approval else None)
        ),
    ))


@router.get("/violations", response_model=APIResponse[list[ViolationOut]])
async def list_violations(
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    minutes: int = Query(1440, ge=1, le=43200),
    limit: int = Query(200, ge=1, le=1000),
) -> APIResponse[list[ViolationOut]]:
    since = datetime.now(tz=timezone.utc) - timedelta(minutes=minutes)
    stmt = (
        select(AutonomyViolation)
        .where(
            AutonomyViolation.tenant_id == tenant_id,
            AutonomyViolation.detected_at >= since,
        )
        .order_by(desc(AutonomyViolation.detected_at))
        .limit(limit)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return APIResponse(data=[ViolationOut.model_validate(r) for r in rows])


# ---------------------------------------------------------------------------
# Human Override Timeline (F6)
# ---------------------------------------------------------------------------
@router.get("/overrides", response_model=APIResponse[list[OverrideOut]])
async def list_overrides(
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    minutes: int = Query(10080, ge=1, le=43200),
    target_kind: str | None = Query(None),
    target_id: str | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
) -> APIResponse[list[OverrideOut]]:
    since = datetime.now(tz=timezone.utc) - timedelta(minutes=minutes)
    stmt = (
        select(HumanOverrideEvent)
        .where(
            HumanOverrideEvent.tenant_id == tenant_id,
            HumanOverrideEvent.occurred_at >= since,
        )
        .order_by(desc(HumanOverrideEvent.occurred_at))
        .limit(limit)
    )
    if target_kind:
        stmt = stmt.where(HumanOverrideEvent.target_kind == target_kind)
    if target_id:
        stmt = stmt.where(HumanOverrideEvent.target_id == target_id)
    rows = list((await db.execute(stmt)).scalars().all())
    return APIResponse(data=[OverrideOut.model_validate(r) for r in rows])


@router.post("/overrides", response_model=APIResponse[OverrideOut])
async def add_override(
    payload: OverrideIn,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> APIResponse[OverrideOut]:
    ev = HumanOverrideEvent(
        tenant_id=tenant_id, org_id=tenant_id,
        actor=payload.actor, actor_role=payload.actor_role,
        event_type=payload.event_type,
        target_kind=payload.target_kind, target_id=payload.target_id,
        request_id=payload.request_id, reason=payload.reason,
        metadata_json=payload.metadata,
    )
    db.add(ev)
    await db.commit()
    return APIResponse(data=OverrideOut.model_validate(ev))
