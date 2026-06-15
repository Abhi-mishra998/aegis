"""
Sprint 6 — Shadow-mode policy API surface.

Endpoints (all tenant-scoped via JWT ``get_tenant_id``):

    POST   /audit/shadow/policies                       create draft
    GET    /audit/shadow/policies                       list
    GET    /audit/shadow/policies/{id}                  detail
    PATCH  /audit/shadow/policies/{id}                  edit draft
    DELETE /audit/shadow/policies/{id}                  archive

    POST   /audit/shadow/policies/{id}/promote          draft→shadow→enforce
    POST   /audit/shadow/policies/{id}/rollback         restore version N
    GET    /audit/shadow/policies/{id}/versions         history

    GET    /audit/shadow/policies/{id}/would-have-denied  report
    GET    /audit/shadow/policies/{id}/decisions          recent rows

    GET    /audit/shadow/online-eval                    get tenant config
    PUT    /audit/shadow/online-eval                    upsert tenant config

The promotion lifecycle is `draft → shadow → enforce → archived`. The
shadow→enforce transition is conceptual in this sprint: it marks the
policy as "operator-approved for promotion" and creates a version row.
Sprint 7's Policy Playground translates an `enforce`-mode shadow policy
into a deployable Rego bundle.

Cache invalidation: every state change calls
``shadow_eval_hook.invalidate_cache(tenant_id)`` so the gateway picks up
the new mode within one request, not 30 seconds.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from sdk.common.auth import verify_internal_secret
from sdk.common.config import settings as _settings
from sdk.common.db import get_db, get_tenant_id
from sdk.common.response import APIResponse
from services.audit.models import (
    OnlineEvalSampleConfig,
    ShadowDecision,
    ShadowPolicy,
    ShadowPolicyVersion,
)
from services.audit.rego_compiler import compile_and_validate
from services.audit.shadow_evaluator import would_have_blocked_benign
from services.audit.shadow_schemas import (
    OnlineEvalConfigBody,
    OnlineEvalConfigResponse,
    ShadowDecisionRow,
    ShadowPolicyCreate,
    ShadowPolicyEdit,
    ShadowPolicyResponse,
    ShadowPolicyVersionResponse,
    ShadowPromoteBody,
    ShadowRollbackBody,
    WouldHaveDeniedReport,
)

shadow_router = APIRouter(
    prefix="/shadow",
    tags=["shadow"],
    dependencies=[Depends(verify_internal_secret)],
)


_VALID_MODES = {"draft", "shadow", "enforce", "archived"}
_PROMOTION_PATHS = {
    ("draft",   "shadow"),
    ("shadow",  "enforce"),
    ("shadow",  "draft"),     # back to drawing board
    ("enforce", "shadow"),    # de-promote
    ("draft",   "archived"),
    ("shadow",  "archived"),
    ("enforce", "archived"),
}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _policy_to_response(row: ShadowPolicy) -> ShadowPolicyResponse:
    return ShadowPolicyResponse(
        id=str(row.id),
        tenant_id=str(row.tenant_id),
        agent_id=str(row.agent_id) if row.agent_id else None,
        name=row.name,
        version=int(row.version),
        mode=row.mode,
        rules_json=list(row.rules_json or []),
        description=row.description,
        sample_rate=float(row.sample_rate or 1.0),
        created_by=row.created_by,
        created_at=_iso(row.created_at) or "",
        promoted_at=_iso(row.promoted_at),
    )


def _version_to_response(row: ShadowPolicyVersion) -> ShadowPolicyVersionResponse:
    return ShadowPolicyVersionResponse(
        id=str(row.id),
        policy_id=str(row.policy_id),
        version=int(row.version),
        change_kind=row.change_kind,
        mode_before=row.mode_before,
        mode_after=row.mode_after,
        rules_json=list(row.rules_json or []),
        changed_by=row.changed_by,
        changed_at=_iso(row.changed_at) or "",
    )


def _decision_to_response(row: ShadowDecision) -> ShadowDecisionRow:
    return ShadowDecisionRow(
        id=str(row.id),
        policy_id=str(row.policy_id),
        policy_version=int(row.policy_version),
        request_id=row.request_id,
        audit_id=str(row.audit_id) if row.audit_id else None,
        tool=row.tool,
        real_action=row.real_action,
        shadow_action=row.shadow_action,
        matched_rule_index=row.matched_rule_index,
        matched_rule_description=row.matched_rule_description,
        payload_hash=row.payload_hash,
        risk_score=float(row.risk_score) if row.risk_score is not None else None,
        eval_latency_ms=float(row.eval_latency_ms),
        created_at=_iso(row.created_at) or "",
    )


def _invalidate_cache(tenant_id: uuid.UUID) -> None:
    """Drop the gateway's in-process cache for this tenant so the next
    request picks up the new mode immediately. Imported lazily so this
    router doesn't pull the gateway module in non-gateway processes.
    """
    try:
        from services.gateway.shadow_eval_hook import invalidate_cache
        invalidate_cache(tenant_id)
    except Exception:
        # Gateway hook may not be importable from every audit process;
        # cache will refresh on TTL expiry instead. Not load-bearing.
        pass


async def _push_to_opa_bundle(
    policy: ShadowPolicy,
    *,
    rego: str,
) -> tuple[bool, str | None]:
    """Forward the compiled Rego to the policy service so the OPA bundle
    server picks it up on the next reload. Returns (ok, error_or_none).

    Failures here MUST NOT roll back the promote — the operator already
    explicitly chose to enforce, and Sprint 6 keeps records of the
    intended Rego in shadow_policy_versions so a later sync can retry.
    Instead we surface the error in the API response so the dashboard
    can show "promoted, but bundle push failed — retry".
    """
    import httpx
    upload_url = (
        f"{_settings.POLICY_SERVICE_URL.rstrip('/')}/policy/upload"
    )
    body = {
        "name":        f"shadow_{policy.id.hex[:12]}",
        "rego":        rego,
        "description": (
            f"Sprint-7 promote of shadow policy {policy.name} "
            f"(id {policy.id}, v{policy.version})."
        ),
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                upload_url,
                json=body,
                headers={
                    "X-Internal-Secret": _settings.INTERNAL_SECRET,
                    "X-Tenant-ID":       str(policy.tenant_id),
                    "Content-Type":      "application/json",
                },
            )
        if resp.status_code in (200, 201, 204):
            return True, None
        return False, f"HTTP {resp.status_code}: {resp.text[:300]}"
    except Exception as exc:
        return False, f"upload error: {exc!s}"


async def _add_version(
    db: AsyncSession,
    policy: ShadowPolicy,
    *,
    change_kind: str,
    mode_before: str | None,
    mode_after: str,
    changed_by: str | None,
) -> None:
    db.add(
        ShadowPolicyVersion(
            id=uuid.uuid4(),
            policy_id=policy.id,
            tenant_id=policy.tenant_id,
            version=policy.version,
            change_kind=change_kind,
            mode_before=mode_before,
            mode_after=mode_after,
            rules_json=list(policy.rules_json or []),
            changed_by=changed_by,
        )
    )


# ---------------------------------------------------------------------------
# Policies — CRUD
# ---------------------------------------------------------------------------


@shadow_router.post(
    "/policies",
    response_model=APIResponse[ShadowPolicyResponse],
    summary="Create a draft shadow policy",
)
async def create_policy(
    body: ShadowPolicyCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[ShadowPolicyResponse]:
    agent_uuid: uuid.UUID | None = None
    if body.agent_id:
        try:
            agent_uuid = uuid.UUID(body.agent_id)
        except ValueError as exc:
            raise HTTPException(400, detail=f"invalid agent_id: {exc}")

    row = ShadowPolicy(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        agent_id=agent_uuid,
        name=body.name,
        version=1,
        mode="draft",
        rules_json=list(body.rules_json or []),
        description=body.description,
        sample_rate=float(body.sample_rate or 1.0),
    )
    db.add(row)
    await db.flush()
    await _add_version(
        db, row,
        change_kind="create",
        mode_before=None,
        mode_after="draft",
        changed_by=None,
    )
    await db.commit()
    await db.refresh(row)
    return APIResponse(data=_policy_to_response(row))


@shadow_router.get(
    "/policies",
    response_model=APIResponse[list[ShadowPolicyResponse]],
    summary="List shadow policies for the tenant",
)
async def list_policies(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    mode: str | None = Query(None),
    agent_id: str | None = Query(None),
) -> APIResponse[list[ShadowPolicyResponse]]:
    stmt = select(ShadowPolicy).where(ShadowPolicy.tenant_id == tenant_id)
    if mode:
        if mode not in _VALID_MODES:
            raise HTTPException(400, detail=f"invalid mode: {mode}")
        stmt = stmt.where(ShadowPolicy.mode == mode)
    if agent_id:
        try:
            stmt = stmt.where(ShadowPolicy.agent_id == uuid.UUID(agent_id))
        except ValueError as exc:
            raise HTTPException(400, detail=f"invalid agent_id: {exc}")
    stmt = stmt.order_by(desc(ShadowPolicy.created_at)).limit(500)
    rows = (await db.execute(stmt)).scalars().all()
    return APIResponse(data=[_policy_to_response(r) for r in rows])


@shadow_router.get(
    "/policies/{policy_id}",
    response_model=APIResponse[ShadowPolicyResponse],
)
async def get_policy(
    policy_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[ShadowPolicyResponse]:
    row = (
        await db.execute(
            select(ShadowPolicy).where(
                ShadowPolicy.id == policy_id,
                ShadowPolicy.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(404, detail="shadow policy not found")
    return APIResponse(data=_policy_to_response(row))


@shadow_router.patch(
    "/policies/{policy_id}",
    response_model=APIResponse[ShadowPolicyResponse],
    summary="Edit a DRAFT shadow policy (rules/name/description/sample_rate)",
)
async def edit_policy(
    policy_id: uuid.UUID,
    body: ShadowPolicyEdit,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[ShadowPolicyResponse]:
    row = (
        await db.execute(
            select(ShadowPolicy).where(
                ShadowPolicy.id == policy_id,
                ShadowPolicy.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(404, detail="shadow policy not found")
    if row.mode not in {"draft", "shadow"}:
        raise HTTPException(
            409,
            detail=f"cannot edit policy in mode={row.mode}; rollback or archive first",
        )

    if body.name is not None:
        row.name = body.name
    if body.description is not None:
        row.description = body.description
    if body.sample_rate is not None:
        row.sample_rate = float(body.sample_rate)
    if body.rules_json is not None:
        row.rules_json = list(body.rules_json)
        row.version = int(row.version) + 1
        await _add_version(
            db, row,
            change_kind="edit",
            mode_before=row.mode,
            mode_after=row.mode,
            changed_by=None,
        )

    await db.commit()
    await db.refresh(row)
    _invalidate_cache(tenant_id)
    return APIResponse(data=_policy_to_response(row))


@shadow_router.delete(
    "/policies/{policy_id}",
    response_model=APIResponse[ShadowPolicyResponse],
    summary="Archive a shadow policy",
)
async def archive_policy(
    policy_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[ShadowPolicyResponse]:
    row = (
        await db.execute(
            select(ShadowPolicy).where(
                ShadowPolicy.id == policy_id,
                ShadowPolicy.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(404, detail="shadow policy not found")
    prior = row.mode
    row.mode = "archived"
    row.version = int(row.version) + 1
    await _add_version(
        db, row,
        change_kind="archive",
        mode_before=prior,
        mode_after="archived",
        changed_by=None,
    )
    await db.commit()
    await db.refresh(row)
    _invalidate_cache(tenant_id)
    return APIResponse(data=_policy_to_response(row))


# ---------------------------------------------------------------------------
# Promotion + Rollback + Versions
# ---------------------------------------------------------------------------


@shadow_router.post(
    "/policies/{policy_id}/promote",
    response_model=APIResponse[ShadowPolicyResponse],
    summary="Transition a policy between draft/shadow/enforce/archived",
)
async def promote_policy(
    policy_id: uuid.UUID,
    body: ShadowPromoteBody,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[ShadowPolicyResponse]:
    target = body.target
    if target not in _VALID_MODES:
        raise HTTPException(400, detail=f"invalid target: {target}")
    row = (
        await db.execute(
            select(ShadowPolicy).where(
                ShadowPolicy.id == policy_id,
                ShadowPolicy.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(404, detail="shadow policy not found")
    if (row.mode, target) not in _PROMOTION_PATHS:
        raise HTTPException(
            409,
            detail=f"illegal transition: {row.mode} → {target}",
        )

    # Sprint 7 — when target is enforce, compile + validate + push to OPA
    # BEFORE we flip the row's mode. A validation failure aborts the
    # promote so the policy never lands in an enforce state with broken
    # Rego sitting in the bundle directory.
    bundle_push_warning: str | None = None
    if target == "enforce":
        compiled, validation = await compile_and_validate(
            list(row.rules_json or []),
            policy_name=row.name,
        )
        if not validation.valid:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "cannot promote to enforce — rules failed Rego validation",
                    "errors":   list(validation.errors),
                    "warnings": list(validation.warnings),
                    "rego":     compiled.rego,
                },
            )
        ok, err = await _push_to_opa_bundle(row, rego=compiled.rego)
        if not ok:
            # Soft-fail: continue the promote so the audit trail records
            # the operator's intent, but flag the bundle push so the UI
            # can prompt a retry.
            bundle_push_warning = err

    prior = row.mode
    row.mode = target
    row.version = int(row.version) + 1
    if target == "enforce":
        row.promoted_at = datetime.now(UTC)
    await _add_version(
        db, row,
        change_kind="promote",
        mode_before=prior,
        mode_after=target,
        changed_by=None,
    )
    await db.commit()
    await db.refresh(row)
    _invalidate_cache(tenant_id)

    response = _policy_to_response(row)
    if bundle_push_warning:
        # Surface the warning verbatim — the UI parses the description
        # field and shows a "Retry bundle push" affordance when present.
        response.description = (
            (response.description or "")
            + f"\n\n[Sprint 7] OPA bundle push failed: {bundle_push_warning}"
        )
    return APIResponse(data=response)


@shadow_router.post(
    "/policies/{policy_id}/rollback",
    response_model=APIResponse[ShadowPolicyResponse],
    summary="Restore a previous {rules_json, mode_after} from version history",
)
async def rollback_policy(
    policy_id: uuid.UUID,
    body: ShadowRollbackBody,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[ShadowPolicyResponse]:
    row = (
        await db.execute(
            select(ShadowPolicy).where(
                ShadowPolicy.id == policy_id,
                ShadowPolicy.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(404, detail="shadow policy not found")

    target = (
        await db.execute(
            select(ShadowPolicyVersion).where(
                ShadowPolicyVersion.policy_id == policy_id,
                ShadowPolicyVersion.version == body.target_version,
            )
        )
    ).scalar_one_or_none()
    if not target:
        raise HTTPException(404, detail=f"version {body.target_version} not found")

    prior_mode = row.mode
    row.rules_json = list(target.rules_json or [])
    row.mode = target.mode_after
    row.version = int(row.version) + 1
    await _add_version(
        db, row,
        change_kind="rollback",
        mode_before=prior_mode,
        mode_after=row.mode,
        changed_by=None,
    )
    await db.commit()
    await db.refresh(row)
    _invalidate_cache(tenant_id)
    return APIResponse(data=_policy_to_response(row))


@shadow_router.get(
    "/policies/{policy_id}/versions",
    response_model=APIResponse[list[ShadowPolicyVersionResponse]],
)
async def list_versions(
    policy_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[list[ShadowPolicyVersionResponse]]:
    rows = (
        await db.execute(
            select(ShadowPolicyVersion)
            .where(
                ShadowPolicyVersion.policy_id == policy_id,
                ShadowPolicyVersion.tenant_id == tenant_id,
            )
            .order_by(desc(ShadowPolicyVersion.version))
        )
    ).scalars().all()
    return APIResponse(data=[_version_to_response(r) for r in rows])


# ---------------------------------------------------------------------------
# Would-have-denied report + raw decisions
# ---------------------------------------------------------------------------


@shadow_router.get(
    "/policies/{policy_id}/would-have-denied",
    response_model=APIResponse[WouldHaveDeniedReport],
    summary="Drift + would-have-blocked-benign report for a candidate policy",
)
async def would_have_denied(
    policy_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    window_hours: int = Query(24, ge=1, le=720),
    sample_limit: int = Query(50, ge=1, le=500),
) -> APIResponse[WouldHaveDeniedReport]:
    policy = (
        await db.execute(
            select(ShadowPolicy).where(
                ShadowPolicy.id == policy_id,
                ShadowPolicy.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not policy:
        raise HTTPException(404, detail="shadow policy not found")

    since = datetime.now(UTC) - timedelta(hours=window_hours)
    rows = (
        await db.execute(
            select(ShadowDecision)
            .where(
                ShadowDecision.policy_id == policy_id,
                ShadowDecision.tenant_id == tenant_id,
                ShadowDecision.created_at >= since,
            )
            .order_by(desc(ShadowDecision.created_at))
            .limit(5000)
        )
    ).scalars().all()

    drift = [r for r in rows if r.real_action != r.shadow_action]
    blocked_benign = [
        r for r in drift
        if would_have_blocked_benign(r.real_action, r.shadow_action)
    ]
    would_have_denied_total = sum(
        1 for r in rows if r.shadow_action in {"deny", "throttle", "escalate"}
    )
    real_allow_total = sum(1 for r in rows if r.real_action == "allow")
    real_deny_total = sum(
        1 for r in rows if r.real_action in {"deny", "throttle", "escalate"}
    )

    return APIResponse(
        data=WouldHaveDeniedReport(
            policy_id=str(policy.id),
            policy_name=policy.name,
            window_hours=window_hours,
            decisions_seen=len(rows),
            drift_count=len(drift),
            would_have_denied_count=would_have_denied_total,
            would_have_blocked_benign_count=len(blocked_benign),
            real_allow_count=real_allow_total,
            real_deny_count=real_deny_total,
            sample_drift=[_decision_to_response(r) for r in drift[:sample_limit]],
        )
    )


@shadow_router.get(
    "/policies/{policy_id}/decisions",
    response_model=APIResponse[list[ShadowDecisionRow]],
    summary="Recent shadow decisions for a policy (most-recent first)",
)
async def list_decisions(
    policy_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    drift_only: bool = Query(False),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
) -> APIResponse[list[ShadowDecisionRow]]:
    stmt = select(ShadowDecision).where(
        ShadowDecision.policy_id == policy_id,
        ShadowDecision.tenant_id == tenant_id,
    )
    if drift_only:
        stmt = stmt.where(ShadowDecision.real_action != ShadowDecision.shadow_action)
    stmt = stmt.order_by(desc(ShadowDecision.created_at)).offset(offset).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return APIResponse(data=[_decision_to_response(r) for r in rows])


# ---------------------------------------------------------------------------
# Online-eval config
# ---------------------------------------------------------------------------


def _config_to_response(row: OnlineEvalSampleConfig) -> OnlineEvalConfigResponse:
    return OnlineEvalConfigResponse(
        id=str(row.id),
        tenant_id=str(row.tenant_id),
        enabled=bool(row.enabled),
        sample_rate=float(row.sample_rate),
        fp_threshold=float(row.fp_threshold),
        poll_interval_seconds=int(row.poll_interval_seconds),
        last_run_at=_iso(row.last_run_at),
        created_at=_iso(row.created_at) or "",
    )


@shadow_router.get(
    "/online-eval",
    response_model=APIResponse[OnlineEvalConfigResponse | None],
    summary="Get the tenant's online-eval drift config",
)
async def get_online_eval_config(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[OnlineEvalConfigResponse | None]:
    row = (
        await db.execute(
            select(OnlineEvalSampleConfig).where(
                OnlineEvalSampleConfig.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    return APIResponse(data=_config_to_response(row) if row else None)


@shadow_router.put(
    "/online-eval",
    response_model=APIResponse[OnlineEvalConfigResponse],
    summary="Upsert the tenant's online-eval drift config",
)
async def upsert_online_eval_config(
    body: OnlineEvalConfigBody,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[OnlineEvalConfigResponse]:
    row = (
        await db.execute(
            select(OnlineEvalSampleConfig).where(
                OnlineEvalSampleConfig.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = OnlineEvalSampleConfig(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            enabled=1 if body.enabled else 0,
            sample_rate=float(body.sample_rate),
            fp_threshold=float(body.fp_threshold),
            poll_interval_seconds=int(body.poll_interval_seconds),
        )
        db.add(row)
    else:
        row.enabled = 1 if body.enabled else 0
        row.sample_rate = float(body.sample_rate)
        row.fp_threshold = float(body.fp_threshold)
        row.poll_interval_seconds = int(body.poll_interval_seconds)
    await db.commit()
    await db.refresh(row)
    return APIResponse(data=_config_to_response(row))
