"""
Sprint 7 — Policy Playground API surface.

Endpoints (all tenant-scoped via JWT ``get_tenant_id``):

    POST   /audit/playground/validate
           Compile rules_json → Rego and validate against OPA. Returns
           the compiled Rego, any warnings, and parse errors if any.

    POST   /audit/playground/replay
           Replay a candidate rules_json against a historical window of
           audit_logs. Returns diff counts + per-bucket sample drill-down
           + Sprint-5 evaluator scores.

    POST   /audit/playground/publish
           One-click: take a candidate, create or update a Sprint-6
           ShadowPolicy in draft mode (target=shadow optional) — the
           operator then promotes from draft → shadow → enforce via the
           existing /audit/shadow/policies/{id}/promote endpoint.

The Playground is read-mostly: only `publish` writes to the database.
`validate` is pure compute + an HTTP round-trip to OPA. `replay` reads
audit_logs but never writes.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)

from sdk.common.auth import verify_internal_secret
from sdk.common.db import get_db, get_tenant_id
from sdk.common.response import APIResponse
from services.audit.models import (
    ShadowPolicy,
    ShadowPolicyVersion,
)
from services.audit.playground_engine import (
    fetch_history,
    run_replay,
    score_replay,
)
from services.audit.rego_compiler import compile_and_validate


playground_router = APIRouter(
    prefix="/playground",
    tags=["playground"],
    dependencies=[Depends(verify_internal_secret)],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ValidateRequest(BaseModel):
    rules:       list[dict[str, Any]]
    policy_name: str = Field("aegis_policy")


class ValidateResponse(BaseModel):
    valid:          bool
    package_name:   str
    rule_count:     int
    rego:           str
    warnings:       list[str]
    errors:         list[str]


class ReplayRequest(BaseModel):
    rules:        list[dict[str, Any]]
    window_hours: int = Field(24, ge=1, le=720)
    agent_id:     str | None = None
    limit:        int = Field(1000, ge=1, le=5000)
    sample_limit: int = Field(50, ge=1, le=500)


class ReplayRowResponse(BaseModel):
    audit_id:                 str
    timestamp:                str | None
    agent_id:                 str | None
    tool:                     str | None
    real_decision:            str
    draft_decision:           str
    matched_rule_index:       int | None
    matched_rule_description: str
    bucket:                   str


class ReplayResponse(BaseModel):
    window_hours:         int
    total_audits:         int
    agreement_count:      int
    newly_denied_count:   int
    newly_allowed_count:  int
    drift_count:          int
    real_allow_count:     int
    real_deny_count:      int
    detection_rate:       float
    fp_rate:              float
    sample_drift:         list[ReplayRowResponse]
    sample_newly_denied:  list[ReplayRowResponse]
    sample_newly_allowed: list[ReplayRowResponse]


class PublishRequest(BaseModel):
    name:         str
    rules:        list[dict[str, Any]]
    description:  str | None = None
    agent_id:     str | None = None
    sample_rate:  float = Field(1.0, ge=0.0, le=1.0)
    start_in:     str = Field("draft", description="draft | shadow")
    """`draft` is the safer default — the operator promotes to shadow
    after a final visual check; `shadow` starts evaluating live traffic
    immediately."""


class PublishResponse(BaseModel):
    policy_id:    str
    version:      int
    mode:         str
    rego:         str
    warnings:     list[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_replay_response(row) -> ReplayRowResponse:
    return ReplayRowResponse(
        audit_id=row.audit_id,
        timestamp=row.timestamp,
        agent_id=row.agent_id,
        tool=row.tool,
        real_decision=row.real_decision,
        draft_decision=row.draft_decision,
        matched_rule_index=row.matched_rule_index,
        matched_rule_description=row.matched_rule_description,
        bucket=row.bucket,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@playground_router.post(
    "/validate",
    response_model=APIResponse[ValidateResponse],
    summary="Compile rules_json → Rego and validate against OPA",
)
async def validate(
    body: ValidateRequest,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[ValidateResponse]:
    compiled, validation = await compile_and_validate(
        body.rules, policy_name=body.policy_name,
    )
    return APIResponse(
        data=ValidateResponse(
            valid=validation.valid,
            package_name=compiled.package_name,
            rule_count=compiled.rule_count,
            rego=compiled.rego,
            warnings=list(validation.warnings),
            errors=list(validation.errors),
        )
    )


@playground_router.post(
    "/replay",
    response_model=APIResponse[ReplayResponse],
    summary="Replay candidate rules against historical audit_logs",
)
async def replay(
    body: ReplayRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[ReplayResponse]:
    until = datetime.now(UTC)
    since = until - timedelta(hours=body.window_hours)
    agent_uuid: uuid.UUID | None = None
    if body.agent_id:
        try:
            agent_uuid = uuid.UUID(body.agent_id)
        except ValueError as exc:
            raise HTTPException(400, detail=f"invalid agent_id: {exc}")

    rows = await fetch_history(
        db,
        tenant_id=tenant_id,
        agent_id=agent_uuid,
        since=since,
        until=until,
        limit=body.limit,
    )
    diff, replays = run_replay(
        body.rules, rows, sample_limit=body.sample_limit,
    )
    scores = score_replay(replays)
    return APIResponse(
        data=ReplayResponse(
            window_hours=body.window_hours,
            total_audits=diff.total_audits,
            agreement_count=diff.agreement_count,
            newly_denied_count=diff.newly_denied_count,
            newly_allowed_count=diff.newly_allowed_count,
            drift_count=diff.drift_count,
            real_allow_count=diff.real_allow_count,
            real_deny_count=diff.real_deny_count,
            detection_rate=round(scores.detection_rate, 4),
            fp_rate=round(scores.fp_rate, 4),
            sample_drift=[_row_to_replay_response(r) for r in diff.sample_drift],
            sample_newly_denied=[_row_to_replay_response(r) for r in diff.sample_newly_denied],
            sample_newly_allowed=[_row_to_replay_response(r) for r in diff.sample_newly_allowed],
        )
    )


@playground_router.post(
    "/publish",
    response_model=APIResponse[PublishResponse],
    summary="Compile + validate + persist as a Sprint-6 ShadowPolicy",
)
async def publish(
    body: PublishRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[PublishResponse]:
    if body.start_in not in {"draft", "shadow"}:
        raise HTTPException(
            400, detail="start_in must be 'draft' or 'shadow'",
        )

    compiled, validation = await compile_and_validate(
        body.rules, policy_name=body.name,
    )
    if not validation.valid:
        raise HTTPException(
            400,
            detail={
                "message": "rules failed Rego validation",
                "errors": list(validation.errors),
                "rego":   compiled.rego,
            },
        )

    agent_uuid: uuid.UUID | None = None
    if body.agent_id:
        try:
            agent_uuid = uuid.UUID(body.agent_id)
        except ValueError as exc:
            raise HTTPException(400, detail=f"invalid agent_id: {exc}")

    policy = ShadowPolicy(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        agent_id=agent_uuid,
        name=body.name,
        version=1,
        mode=body.start_in,
        rules_json=list(body.rules or []),
        description=body.description,
        sample_rate=float(body.sample_rate or 1.0),
    )
    db.add(policy)
    await db.flush()
    db.add(
        ShadowPolicyVersion(
            id=uuid.uuid4(),
            policy_id=policy.id,
            tenant_id=tenant_id,
            version=1,
            change_kind="publish",
            mode_before=None,
            mode_after=body.start_in,
            rules_json=list(body.rules or []),
        )
    )
    await db.commit()
    await db.refresh(policy)

    # Invalidate the gateway's in-process cache so any caller in shadow
    # mode picks up the new policy on the next request, not 30s later.
    try:
        from services.gateway.shadow_eval_hook import invalidate_cache
        invalidate_cache(tenant_id)
    except Exception as exc:
        logger.warning("shadow_cache_invalidate_failed", tenant_id=str(tenant_id), error=str(exc))

    return APIResponse(
        data=PublishResponse(
            policy_id=str(policy.id),
            version=policy.version,
            mode=policy.mode,
            rego=compiled.rego,
            warnings=list(validation.warnings),
        )
    )
