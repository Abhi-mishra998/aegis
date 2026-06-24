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
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from redis.asyncio import Redis as _Redis
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from sdk.common.auth import verify_internal_secret
from sdk.common.background import safe_bg as _safe_bg
from sdk.common.db import get_db, get_tenant_id
from sdk.common.redis import get_redis_client
from sdk.common.response import APIResponse
from services.autonomy.enforcement import ContractView, evaluate

_redis_client: _Redis | None = None
_logger = structlog.get_logger(__name__)


async def _invalidate_autonomy_cache(tenant_id, agent_id) -> None:
    """Drop every cached autonomy-check entry for (tenant, agent). Fire-and-forget."""
    global _redis_client
    try:
        if _redis_client is None:
            _redis_client = get_redis_client(decode_responses=False)
        pattern = f"acp:autonomy_check:{tenant_id}:{agent_id}:*"
        async for k in _redis_client.scan_iter(match=pattern, count=200):
            await _redis_client.delete(k)
    except Exception as exc:
        _logger.warning("autonomy_cache_invalidation_failed", error=str(exc))


_rl_redis_client: _Redis | None = None


async def _rl_redis() -> _Redis:
    """Return a dedicated module-level Redis client for rate-limiting.

    Kept separate from _redis_client (which mixes decode_responses modes between
    _invalidate_autonomy_cache and _get_redis) so DOS limits never depend on
    whichever caller initialized the shared client first.
    """
    global _rl_redis_client
    if _rl_redis_client is None:
        _rl_redis_client = get_redis_client(decode_responses=False)
    return _rl_redis_client


async def _check_rate(redis: _Redis, key: str, limit: int, window: int = 60) -> None:
    """Per-tenant DOS ceiling on state-mutating routes (token-bucket-lite)."""
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, window)
    if count > limit:
        ttl = await redis.ttl(key)
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(max(1, ttl))},
        )


from services.autonomy.models import (
    AutonomyContract,
    AutonomyViolation,
    HumanOverrideEvent,
)
from services.autonomy.schemas import (
    CheckRequest,
    CheckResult,
    ContractIn,
    ContractOut,
    OverrideIn,
    OverrideOut,
    ViolationOut,
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
    await _check_rate(
        await _rl_redis(),
        f"acp:ratelimit:autonomy:contracts_create:{tenant_id}",
        limit=60,
    )
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
    await _check_rate(
        await _rl_redis(),
        f"acp:ratelimit:autonomy:contracts_update:{tenant_id}",
        limit=60,
    )
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
    await _check_rate(
        await _rl_redis(),
        f"acp:ratelimit:autonomy:contracts_delete:{tenant_id}",
        limit=60,
    )
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
    since = datetime.now(tz=UTC) - timedelta(minutes=minutes)
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
    since = datetime.now(tz=UTC) - timedelta(minutes=minutes)
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
    x_acp_actor: Annotated[str | None, Header(alias="X-ACP-Actor")] = None,
    x_acp_role: Annotated[str | None, Header(alias="X-ACP-Role")] = None,
) -> APIResponse[OverrideOut]:
    await _check_rate(
        await _rl_redis(),
        f"acp:ratelimit:autonomy:overrides_create:{tenant_id}",
        limit=60,
    )
    # Actor attribution: prefer gateway-injected headers (JWT-validated `sub`
    # and `role`) over client-supplied body fields. The gateway sets
    # X-ACP-Actor/X-ACP-Role from request.state after auth (services/gateway/
    # _mw_auth.py); they cannot be spoofed by the browser. Body fields remain
    # the fallback for direct service-to-service calls that bypass the gateway.
    actor = x_acp_actor or payload.actor
    actor_role = x_acp_role or payload.actor_role
    ev = HumanOverrideEvent(
        tenant_id=tenant_id, org_id=tenant_id,
        actor=actor, actor_role=actor_role,
        event_type=payload.event_type,
        target_kind=payload.target_kind, target_id=payload.target_id,
        request_id=payload.request_id, reason=payload.reason,
        metadata_json=payload.metadata,
    )
    db.add(ev)
    await db.commit()

    # Observe wall-clock-to-resolve into the customer-SLO histogram. No-op
    # when event_type is not "approval" or when the request_id has no matching
    # autonomy_contract_violation. Lookup failures are swallowed inside
    # observe_approval_resolution so they cannot roll back the override the
    # DB already committed.
    from services.autonomy.metrics import observe_approval_resolution
    await observe_approval_resolution(
        db,
        tenant_id=tenant_id,
        request_id=payload.request_id,
        event_type=payload.event_type,
    )

    # Emit `approval_resolved` on the per-tenant pubsub channel so the
    # LiveFeed + pending-approvals inbox reflect the human action in
    # real time. Uses the same channel naming as
    # services/gateway/_helpers.publish_event so the gateway's SSE
    # fan-out picks it up. Fire-and-forget — a Redis stall here must
    # not roll back the override the DB already accepted.
    asyncio.create_task(_safe_bg(_publish_approval_resolved(
        tenant_id=str(tenant_id),
        agent_id=str(payload.target_id) if payload.target_kind == "agent" else None,
        event_type=payload.event_type,
        target_kind=payload.target_kind,
        target_id=str(payload.target_id),
        request_id=payload.request_id,
        actor=actor,
        actor_role=actor_role,
        reason=payload.reason,
    )))
    return APIResponse(data=OverrideOut.model_validate(ev))


async def _publish_approval_resolved(
    *,
    tenant_id: str,
    agent_id: str | None,
    event_type: str,
    target_kind: str,
    target_id: str,
    request_id: str | None,
    actor: str | None,
    actor_role: str | None,
    reason: str | None,
) -> None:
    """Publish an ``approval_resolved`` SSE event mirroring the channel
    convention used by services/gateway/_helpers.publish_event.

    N2 (2026-06-21) — payload carries top-level ``tenant_id`` so the
    gateway SSE generator can verify the message was intended for the
    authenticated client. Trusting the channel name is not enough.
    """
    import json as _json
    import time as _time
    global _redis_client
    if _redis_client is None:
        _redis_client = get_redis_client(decode_responses=False)
    payload = _json.dumps({
        "tenant_id": tenant_id,
        "type": "approval_resolved",
        "data": {
            "event_type":  event_type,
            "target_kind": target_kind,
            "target_id":   target_id,
            "agent_id":    agent_id,
            "request_id":  request_id,
            "actor":       actor,
            "actor_role":  actor_role,
            "reason":      reason,
        },
        "ts": int(_time.time()),
    })
    try:
        await _redis_client.publish(f"acp:events:{tenant_id}", payload)
    except Exception as exc:
        _logger.warning("approval_resolved_publish_failed", error=str(exc))
    if agent_id:
        try:
            await _redis_client.publish(f"acp:events:{tenant_id}:{agent_id}", payload)
        except Exception as exc:
            _logger.warning(
                "approval_resolved_publish_agent_channel_failed",
                error=str(exc), agent_id=agent_id,
            )


# ---------------------------------------------------------------------------
# Playbooks Engine (Day 13-14)
# ---------------------------------------------------------------------------
from typing import Any  # noqa: E402

from pydantic import BaseModel, Field  # noqa: E402

from services.autonomy.playbooks import (  # noqa: E402
    Playbook,
    PlaybookRun,
    execute_playbook,
    get_playbook_templates,
)

# ── Pydantic schemas ──────────────────────────────────────────────────────

class PlaybookCreate(BaseModel):
    name: str
    description: str | None = None
    trigger_conditions: dict[str, Any] = Field(default_factory=dict)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    mode: str = "auto"
    is_active: bool = True


class PlaybookOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    description: str | None = None
    trigger_conditions: dict[str, Any]
    steps: list[dict[str, Any]]
    mode: str
    is_active: bool
    run_count: int
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class PlaybookRunOut(BaseModel):
    id: uuid.UUID
    playbook_id: uuid.UUID
    triggered_by: str
    status: str
    steps_executed: list[dict[str, Any]]
    result: dict[str, Any]
    started_at: datetime
    finished_at: datetime | None = None

    model_config = {"from_attributes": True}


class PlaybookTriggerIn(BaseModel):
    context: dict[str, Any] = Field(default_factory=dict)


# ── Playbook CRUD routes ──────────────────────────────────────────────────

_playbooks_router = APIRouter()  # nested, inherits parent prefix/deps


@router.get("/playbooks/templates", response_model=APIResponse[list[dict]])
async def list_playbook_templates() -> APIResponse[list[dict]]:
    """Return 4 pre-built remediation templates. No auth required."""
    return APIResponse(data=get_playbook_templates())


@router.get("/playbooks/autotrigger-stats", response_model=APIResponse[list[dict]])
async def get_autotrigger_stats(
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> APIResponse[list[dict]]:
    """Return per-playbook auto-trigger counts (runs with triggered_by='auto').

    Declared *before* /playbooks/{playbook_id} so the literal "autotrigger-stats"
    segment is not parsed as a UUID path parameter.
    """
    from sqlalchemy import func, select  # noqa: F811
    stmt = (
        select(
            PlaybookRun.playbook_id,
            func.count().label("auto_count"),
            func.max(PlaybookRun.started_at).label("last_auto_at"),
        )
        .where(
            PlaybookRun.tenant_id == tenant_id,
            PlaybookRun.triggered_by == "auto",
        )
        .group_by(PlaybookRun.playbook_id)
    )
    rows = (await db.execute(stmt)).all()
    return APIResponse(data=[
        {
            "playbook_id":  str(r.playbook_id),
            "auto_count":   r.auto_count,
            "last_auto_at": r.last_auto_at.isoformat() if r.last_auto_at else None,
        }
        for r in rows
    ])


@router.get("/playbooks", response_model=APIResponse[list[PlaybookOut]])
async def list_playbooks(
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> APIResponse[list[PlaybookOut]]:
    from sqlalchemy import desc, select  # noqa: F811
    stmt = (
        select(Playbook)
        .where(Playbook.tenant_id == tenant_id)
        .order_by(desc(Playbook.created_at))
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return APIResponse(data=[PlaybookOut.model_validate(r) for r in rows])


@router.post("/playbooks", response_model=APIResponse[PlaybookOut])
async def create_playbook(
    payload: PlaybookCreate,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> APIResponse[PlaybookOut]:
    await _check_rate(
        await _rl_redis(),
        f"acp:ratelimit:autonomy:playbooks_create:{tenant_id}",
        limit=60,
    )
    pb = Playbook(
        tenant_id=tenant_id,
        name=payload.name,
        description=payload.description,
        trigger_conditions=payload.trigger_conditions,
        steps=payload.steps,
        mode=payload.mode,
        is_active=payload.is_active,
        run_count=0,
    )
    db.add(pb)
    await db.commit()
    await db.refresh(pb)
    return APIResponse(data=PlaybookOut.model_validate(pb))


@router.get("/playbooks/{playbook_id}", response_model=APIResponse[PlaybookOut])
async def get_playbook(
    playbook_id: uuid.UUID,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> APIResponse[PlaybookOut]:
    from sqlalchemy import select  # noqa: F811
    pb = (await db.execute(
        select(Playbook).where(
            Playbook.id == playbook_id,
            Playbook.tenant_id == tenant_id,
        )
    )).scalar_one_or_none()
    if pb is None:
        raise HTTPException(status_code=404, detail="Playbook not found")
    return APIResponse(data=PlaybookOut.model_validate(pb))


@router.patch("/playbooks/{playbook_id}", response_model=APIResponse[PlaybookOut])
async def update_playbook(
    playbook_id: uuid.UUID,
    payload: PlaybookCreate,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> APIResponse[PlaybookOut]:
    await _check_rate(
        await _rl_redis(),
        f"acp:ratelimit:autonomy:playbooks_update:{tenant_id}",
        limit=60,
    )
    from sqlalchemy import select  # noqa: F811
    pb = (await db.execute(
        select(Playbook).where(
            Playbook.id == playbook_id,
            Playbook.tenant_id == tenant_id,
        )
    )).scalar_one_or_none()
    if pb is None:
        raise HTTPException(status_code=404, detail="Playbook not found")
    pb.name               = payload.name
    pb.description        = payload.description
    pb.trigger_conditions = payload.trigger_conditions
    pb.steps              = payload.steps
    pb.mode               = payload.mode
    pb.is_active          = payload.is_active
    await db.commit()
    await db.refresh(pb)
    return APIResponse(data=PlaybookOut.model_validate(pb))


@router.delete("/playbooks/{playbook_id}", response_model=APIResponse[dict])
async def delete_playbook(
    playbook_id: uuid.UUID,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> APIResponse[dict]:
    await _check_rate(
        await _rl_redis(),
        f"acp:ratelimit:autonomy:playbooks_delete:{tenant_id}",
        limit=60,
    )
    from sqlalchemy import select  # noqa: F811
    pb = (await db.execute(
        select(Playbook).where(
            Playbook.id == playbook_id,
            Playbook.tenant_id == tenant_id,
        )
    )).scalar_one_or_none()
    if pb is None:
        raise HTTPException(status_code=404, detail="Playbook not found")
    pb.is_active = False
    await db.commit()
    return APIResponse(data={"id": str(playbook_id), "is_active": False})


@router.post("/playbooks/{playbook_id}/trigger", response_model=APIResponse[dict])
async def trigger_playbook(
    playbook_id: uuid.UUID,
    payload: PlaybookTriggerIn,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> APIResponse[dict]:
    """
    Manually trigger a playbook run.
    The run is executed in the background; response returns the run_id immediately.
    """
    await _check_rate(
        await _rl_redis(),
        f"acp:ratelimit:autonomy:playbooks_trigger:{tenant_id}",
        limit=60,
    )
    from sqlalchemy import select  # noqa: F811
    # Verify playbook exists and belongs to tenant
    pb = (await db.execute(
        select(Playbook).where(
            Playbook.id == playbook_id,
            Playbook.tenant_id == tenant_id,
        )
    )).scalar_one_or_none()
    if pb is None:
        raise HTTPException(status_code=404, detail="Playbook not found")

    # Create a pending run record to return the run_id immediately
    run = PlaybookRun(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        playbook_id=playbook_id,
        triggered_by="manual",
        status="pending",
        steps_executed=[],
        result={"context": payload.context},
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    run_id = run.id

    # Fire-and-forget background execution
    async def _bg_execute() -> None:
        from sdk.common.db import get_session_factory  # noqa: F811
        session_factory = get_session_factory()
        async with session_factory() as bg_db:
            try:
                await execute_playbook(
                    playbook_id=playbook_id,
                    context=payload.context,
                    db=bg_db,
                    tenant_id=tenant_id,
                    triggered_by="manual",
                )
            except Exception as exc:
                _logger.error(
                    "playbook_bg_execute_failed",
                    playbook_id=str(playbook_id),
                    run_id=str(run_id),
                    error=str(exc),
                )

    asyncio.create_task(_safe_bg(_bg_execute()))

    return APIResponse(data={"run_id": str(run_id), "status": "triggered"})


@router.get("/playbooks/{playbook_id}/runs", response_model=APIResponse[list[PlaybookRunOut]])
async def list_playbook_runs(
    playbook_id: uuid.UUID,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(20, ge=1, le=100),
) -> APIResponse[list[PlaybookRunOut]]:
    from sqlalchemy import desc, select  # noqa: F811
    # Verify playbook belongs to tenant
    pb = (await db.execute(
        select(Playbook).where(
            Playbook.id == playbook_id,
            Playbook.tenant_id == tenant_id,
        )
    )).scalar_one_or_none()
    if pb is None:
        raise HTTPException(status_code=404, detail="Playbook not found")

    stmt = (
        select(PlaybookRun)
        .where(
            PlaybookRun.playbook_id == playbook_id,
            PlaybookRun.tenant_id == tenant_id,
        )
        .order_by(desc(PlaybookRun.started_at))
        .limit(limit)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return APIResponse(data=[PlaybookRunOut.model_validate(r) for r in rows])


# ---------------------------------------------------------------------------
# Webhook Settings (SEND_ALERT / WEBHOOK config per-tenant)
# Stored in Redis hash  acp:webhooks:{tenant_id}
# ---------------------------------------------------------------------------


from services.autonomy.webhook_executor import (  # noqa: E402
    fire_generic_webhook,
    fire_pagerduty,
    fire_slack,
)

_WEBHOOK_KEY_TTL: int | None = None  # persistent — no TTL


def _mask(value: str) -> str:
    """Return a masked representation of a secret string."""
    if not value:
        return ""
    return value[:4] + "***" if len(value) > 4 else "***"


async def _get_redis() -> _Redis:
    """Return (or lazily create) a module-level Redis client."""
    global _redis_client
    if _redis_client is None:
        _redis_client = get_redis_client(decode_responses=True)
    return _redis_client


class WebhookConfigIn(BaseModel):
    slack_url:       str = ""
    pagerduty_key:   str = ""
    generic_url:     str = ""


class WebhookConfigOut(BaseModel):
    slack_url:       str = ""
    pagerduty_key:   str = ""
    generic_url:     str = ""


class WebhookTestRequest(BaseModel):
    message: str = "Aegis test alert — webhook configuration verified"
    url:     str = ""
    method:  str = "POST"
    payload: dict = Field(default_factory=dict)
    headers: dict = Field(default_factory=dict)


@router.get("/webhooks/config", response_model=APIResponse[WebhookConfigOut])
async def get_webhook_config(
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[WebhookConfigOut]:
    """Return the stored webhook configuration with secret values masked."""
    redis = await _get_redis()
    key = f"acp:webhooks:{tenant_id}"
    raw: dict[str, str] = await redis.hgetall(key)
    return APIResponse(data=WebhookConfigOut(
        slack_url=_mask(raw.get("slack_url", "")),
        pagerduty_key=_mask(raw.get("pagerduty_key", "")),
        generic_url=raw.get("generic_url", ""),  # URL is not a secret, don't mask
    ))


@router.post("/webhooks/config", response_model=APIResponse[dict])
async def save_webhook_config(
    payload: WebhookConfigIn,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[dict]:
    """Persist webhook configuration for the tenant in Redis."""
    await _check_rate(
        await _rl_redis(),
        f"acp:ratelimit:autonomy:webhooks_config:{tenant_id}",
        limit=60,
    )
    redis = await _get_redis()
    key = f"acp:webhooks:{tenant_id}"
    mapping: dict[str, str] = {}
    if payload.slack_url:
        mapping["slack_url"] = payload.slack_url
    if payload.pagerduty_key:
        mapping["pagerduty_key"] = payload.pagerduty_key
    if payload.generic_url:
        mapping["generic_url"] = payload.generic_url
    if mapping:
        await redis.hset(key, mapping=mapping)
    return APIResponse(data={"saved": True, "fields": list(mapping.keys())})


@router.post("/webhooks/test/slack", response_model=APIResponse[dict])
async def test_slack_webhook(
    req: WebhookTestRequest,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[dict]:
    """Fire a test Slack message using the configured (or supplied) webhook URL."""
    await _check_rate(
        await _rl_redis(),
        f"acp:ratelimit:autonomy:webhooks_test_slack:{tenant_id}",
        limit=60,
    )
    redis = await _get_redis()
    key = f"acp:webhooks:{tenant_id}"
    raw: dict[str, str] = await redis.hgetall(key)
    # Supplied URL takes precedence over stored config
    webhook_url = req.url or raw.get("slack_url", "")
    result = await fire_slack(
        message=req.message,
        webhook_url=webhook_url,
        context={"tenant_id": str(tenant_id), "test": "true"},
    )
    return APIResponse(data=result)


@router.post("/webhooks/test/pagerduty", response_model=APIResponse[dict])
async def test_pagerduty_webhook(
    req: WebhookTestRequest,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[dict]:
    """Fire a test PagerDuty alert using the configured (or supplied) routing key."""
    await _check_rate(
        await _rl_redis(),
        f"acp:ratelimit:autonomy:webhooks_test_pagerduty:{tenant_id}",
        limit=60,
    )
    redis = await _get_redis()
    key = f"acp:webhooks:{tenant_id}"
    raw: dict[str, str] = await redis.hgetall(key)
    routing_key = raw.get("pagerduty_key", "")
    result = await fire_pagerduty(
        summary=req.message,
        severity="info",
        routing_key=routing_key,
        dedup_key=f"aegis-test-{tenant_id}",
    )
    return APIResponse(data=result)


@router.post("/webhooks/test/webhook", response_model=APIResponse[dict])
async def test_generic_webhook(
    req: WebhookTestRequest,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[dict]:
    """Fire a test generic webhook using the configured (or supplied) URL."""
    await _check_rate(
        await _rl_redis(),
        f"acp:ratelimit:autonomy:webhooks_test_generic:{tenant_id}",
        limit=60,
    )
    redis = await _get_redis()
    key = f"acp:webhooks:{tenant_id}"
    raw: dict[str, str] = await redis.hgetall(key)
    url = req.url or raw.get("generic_url", "")
    result = await fire_generic_webhook(
        url=url,
        payload={**req.payload, "aegis_context": {"tenant_id": str(tenant_id), "test": "true"}},
        method=req.method,
        headers=req.headers,
    )
    return APIResponse(data=result)
