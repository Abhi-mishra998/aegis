"""
Sprint 5 — Attack Evaluation Suite API surface.

Endpoints (all tenant-scoped via JWT ``get_tenant_id``):

    POST   /audit/evaluation/datasets
    GET    /audit/evaluation/datasets
    GET    /audit/evaluation/datasets/{dataset_id}
    POST   /audit/evaluation/datasets/{dataset_id}/cases
    GET    /audit/evaluation/datasets/{dataset_id}/cases

    POST   /audit/evaluation/evaluators
    GET    /audit/evaluation/evaluators

    POST   /audit/evaluation/jobs                 # enqueue
    GET    /audit/evaluation/jobs                 # list
    GET    /audit/evaluation/jobs/{job_id}        # status + summary
    GET    /audit/evaluation/jobs/{job_id}/results

    GET    /audit/evaluation/efficacy/overview
    GET    /audit/evaluation/efficacy/trend       # per-rule trend (sparkline)

The runner is in ``evaluation_runner.py`` — this router never calls
/execute itself, it only writes rows the runner picks up.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sdk.common.auth import verify_internal_secret
from sdk.common.db import get_db, get_tenant_id
from sdk.common.response import APIResponse
from services.audit.evaluation_schemas import (
    DatasetCaseBody,
    DatasetCaseResponse,
    DatasetCreateBody,
    DatasetResponse,
    EfficacyOverview,
    EfficacyTrendPoint,
    EvalJobCreateBody,
    EvalJobResponse,
    EvalJobResultRow,
    EvaluatorCreateBody,
    EvaluatorResponse,
)
from services.audit.models import (
    EvalDataset,
    EvalDatasetCase,
    EvalJob,
    EvalJobResult,
    Evaluator,
    EvaluatorScoreSnapshot,
)

evaluation_router = APIRouter(
    prefix="/evaluation",
    tags=["evaluation"],
    dependencies=[Depends(verify_internal_secret)],
)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _dataset_to_response(row: EvalDataset) -> DatasetResponse:
    return DatasetResponse(
        id=str(row.id),
        tenant_id=str(row.tenant_id),
        name=row.name,
        kind=row.kind,
        version=row.version,
        description=row.description,
        case_count=row.case_count,
        created_by=row.created_by,
        created_at=_iso(row.created_at) or "",
    )


def _case_to_response(row: EvalDatasetCase) -> DatasetCaseResponse:
    return DatasetCaseResponse(
        id=str(row.id),
        dataset_id=str(row.dataset_id),
        tenant_id=str(row.tenant_id),
        case_kind=row.case_kind,
        owasp_category=row.owasp_category,
        base_id=row.base_id,
        mutation=row.mutation,
        payload_json=row.payload_json or {},
        expected_outcome=row.expected_outcome,
        expected_findings=row.expected_findings or [],
        notes=row.notes,
        created_at=_iso(row.created_at) or "",
    )


def _evaluator_to_response(row: Evaluator) -> EvaluatorResponse:
    return EvaluatorResponse(
        id=str(row.id),
        tenant_id=str(row.tenant_id),
        name=row.name,
        kind=row.kind,
        config_json=row.config_json or {},
        description=row.description,
        enabled=bool(row.enabled),
        created_at=_iso(row.created_at) or "",
    )


def _job_to_response(row: EvalJob) -> EvalJobResponse:
    return EvalJobResponse(
        id=str(row.id),
        tenant_id=str(row.tenant_id),
        dataset_id=str(row.dataset_id),
        evaluator_ids=[str(e) for e in (row.evaluator_ids or [])],
        schedule=row.schedule,
        status=row.status,
        cases_total=row.cases_total,
        cases_done=row.cases_done,
        summary_json=row.summary_json or {},
        error_message=row.error_message,
        created_by=row.created_by,
        queued_at=_iso(row.queued_at) or "",
        started_at=_iso(row.started_at),
        finished_at=_iso(row.finished_at),
    )


def _result_to_response(row: EvalJobResult) -> EvalJobResultRow:
    return EvalJobResultRow(
        id=str(row.id),
        eval_job_id=str(row.eval_job_id),
        case_id=str(row.case_id),
        owasp_category=row.owasp_category,
        case_kind=row.case_kind,
        expected_outcome=row.expected_outcome,
        actual_outcome=row.actual_outcome,
        passed=bool(row.passed),
        findings=row.findings or [],
        rule_attribution_json=row.rule_attribution_json or {},
        latency_ms=float(row.latency_ms),
        error_message=row.error_message,
        created_at=_iso(row.created_at) or "",
    )


# ---------------------------------------------------------------------------
# Datasets
# ---------------------------------------------------------------------------


@evaluation_router.post(
    "/datasets",
    response_model=APIResponse[DatasetResponse],
    summary="Create a new evaluation dataset (attack | benign | mixed)",
)
async def create_dataset(
    body: DatasetCreateBody,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[DatasetResponse]:
    if body.kind not in {"attack", "benign", "mixed"}:
        raise HTTPException(400, detail=f"invalid kind: {body.kind}")
    row = EvalDataset(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name=body.name,
        kind=body.kind,
        version=body.version,
        description=body.description,
        case_count=0,
        created_by=None,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return APIResponse(data=_dataset_to_response(row))


@evaluation_router.get(
    "/datasets",
    response_model=APIResponse[list[DatasetResponse]],
    summary="List datasets for the current tenant",
)
async def list_datasets(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    kind: str | None = Query(None),
) -> APIResponse[list[DatasetResponse]]:
    stmt = select(EvalDataset).where(EvalDataset.tenant_id == tenant_id)
    if kind:
        stmt = stmt.where(EvalDataset.kind == kind)
    stmt = stmt.order_by(desc(EvalDataset.created_at)).limit(500)
    rows = (await db.execute(stmt)).scalars().all()
    return APIResponse(data=[_dataset_to_response(r) for r in rows])


@evaluation_router.get(
    "/datasets/{dataset_id}",
    response_model=APIResponse[DatasetResponse],
    summary="Dataset detail",
)
async def get_dataset(
    dataset_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[DatasetResponse]:
    stmt = select(EvalDataset).where(
        EvalDataset.id == dataset_id, EvalDataset.tenant_id == tenant_id
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if not row:
        raise HTTPException(404, detail="dataset not found")
    return APIResponse(data=_dataset_to_response(row))


@evaluation_router.post(
    "/datasets/{dataset_id}/cases",
    response_model=APIResponse[DatasetCaseResponse],
    summary="Append a single case to a dataset",
)
async def add_case(
    dataset_id: uuid.UUID,
    body: DatasetCaseBody,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[DatasetCaseResponse]:
    if body.case_kind not in {"attack", "benign"}:
        raise HTTPException(400, detail=f"invalid case_kind: {body.case_kind}")
    if body.expected_outcome not in {"deny", "allow"}:
        raise HTTPException(
            400, detail=f"invalid expected_outcome: {body.expected_outcome}"
        )
    parent = (
        await db.execute(
            select(EvalDataset).where(
                EvalDataset.id == dataset_id, EvalDataset.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if not parent:
        raise HTTPException(404, detail="dataset not found")

    row = EvalDatasetCase(
        id=uuid.uuid4(),
        dataset_id=dataset_id,
        tenant_id=tenant_id,
        case_kind=body.case_kind,
        owasp_category=body.owasp_category,
        base_id=body.base_id,
        mutation=body.mutation,
        payload_json=body.payload_json,
        expected_outcome=body.expected_outcome,
        expected_findings=body.expected_findings,
        notes=body.notes,
    )
    db.add(row)
    parent.case_count = (parent.case_count or 0) + 1
    await db.commit()
    await db.refresh(row)
    return APIResponse(data=_case_to_response(row))


@evaluation_router.get(
    "/datasets/{dataset_id}/cases",
    response_model=APIResponse[list[DatasetCaseResponse]],
    summary="Paginated case browser",
)
async def list_cases(
    dataset_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    case_kind: str | None = Query(None),
    owasp_category: str | None = Query(None),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
) -> APIResponse[list[DatasetCaseResponse]]:
    stmt = select(EvalDatasetCase).where(
        EvalDatasetCase.dataset_id == dataset_id,
        EvalDatasetCase.tenant_id == tenant_id,
    )
    if case_kind:
        stmt = stmt.where(EvalDatasetCase.case_kind == case_kind)
    if owasp_category:
        stmt = stmt.where(EvalDatasetCase.owasp_category == owasp_category)
    stmt = stmt.order_by(EvalDatasetCase.owasp_category, EvalDatasetCase.base_id)
    stmt = stmt.offset(offset).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return APIResponse(data=[_case_to_response(r) for r in rows])


# ---------------------------------------------------------------------------
# Evaluators
# ---------------------------------------------------------------------------


_EVALUATOR_KINDS = {"detection_rate", "fp_rate", "per_rule_efficacy"}


@evaluation_router.post(
    "/evaluators",
    response_model=APIResponse[EvaluatorResponse],
    summary="Create a named scorer config",
)
async def create_evaluator(
    body: EvaluatorCreateBody,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[EvaluatorResponse]:
    if body.kind not in _EVALUATOR_KINDS:
        raise HTTPException(400, detail=f"invalid kind: {body.kind}")
    row = Evaluator(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name=body.name,
        kind=body.kind,
        config_json=body.config_json,
        description=body.description,
        enabled=1 if body.enabled else 0,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return APIResponse(data=_evaluator_to_response(row))


@evaluation_router.get(
    "/evaluators",
    response_model=APIResponse[list[EvaluatorResponse]],
    summary="List evaluators for the current tenant",
)
async def list_evaluators(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    kind: str | None = Query(None),
) -> APIResponse[list[EvaluatorResponse]]:
    stmt = select(Evaluator).where(Evaluator.tenant_id == tenant_id)
    if kind:
        stmt = stmt.where(Evaluator.kind == kind)
    stmt = stmt.order_by(Evaluator.name)
    rows = (await db.execute(stmt)).scalars().all()
    return APIResponse(data=[_evaluator_to_response(r) for r in rows])


# ---------------------------------------------------------------------------
# Eval Jobs + Results
# ---------------------------------------------------------------------------


@evaluation_router.post(
    "/jobs",
    response_model=APIResponse[EvalJobResponse],
    summary="Enqueue a new evaluation run",
)
async def enqueue_job(
    body: EvalJobCreateBody,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[EvalJobResponse]:
    try:
        dataset_uuid = uuid.UUID(body.dataset_id)
    except ValueError as exc:
        raise HTTPException(400, detail=f"invalid dataset_id: {exc}")

    parent = (
        await db.execute(
            select(EvalDataset).where(
                EvalDataset.id == dataset_uuid, EvalDataset.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if not parent:
        raise HTTPException(404, detail="dataset not found")

    cases_total = (
        await db.execute(
            select(func.count()).select_from(EvalDatasetCase).where(
                EvalDatasetCase.dataset_id == dataset_uuid,
                EvalDatasetCase.tenant_id == tenant_id,
            )
        )
    ).scalar_one()

    evaluator_ids: list[str] = []
    for raw in body.evaluator_ids or []:
        try:
            evaluator_ids.append(str(uuid.UUID(raw)))
        except ValueError as exc:
            raise HTTPException(400, detail=f"invalid evaluator_id {raw}: {exc}")

    row = EvalJob(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        dataset_id=dataset_uuid,
        evaluator_ids=evaluator_ids,
        schedule=body.schedule or "manual",
        status="queued",
        cases_total=int(cases_total or 0),
        cases_done=0,
        summary_json={},
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return APIResponse(data=_job_to_response(row))


@evaluation_router.get(
    "/jobs",
    response_model=APIResponse[list[EvalJobResponse]],
    summary="List evaluation jobs for the tenant (most-recent first)",
)
async def list_jobs(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    status: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
) -> APIResponse[list[EvalJobResponse]]:
    stmt = select(EvalJob).where(EvalJob.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(EvalJob.status == status)
    stmt = stmt.order_by(desc(EvalJob.queued_at)).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return APIResponse(data=[_job_to_response(r) for r in rows])


@evaluation_router.get(
    "/jobs/{job_id}",
    response_model=APIResponse[EvalJobResponse],
    summary="Eval job status + summary",
)
async def get_job(
    job_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[EvalJobResponse]:
    row = (
        await db.execute(
            select(EvalJob).where(
                EvalJob.id == job_id, EvalJob.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(404, detail="job not found")
    return APIResponse(data=_job_to_response(row))


@evaluation_router.get(
    "/jobs/{job_id}/results",
    response_model=APIResponse[list[EvalJobResultRow]],
    summary="Per-case results — drill-down for failed cases",
)
async def list_results(
    job_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    only_failed: bool = Query(False),
    owasp_category: str | None = Query(None),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
) -> APIResponse[list[EvalJobResultRow]]:
    stmt = select(EvalJobResult).where(
        EvalJobResult.eval_job_id == job_id,
        EvalJobResult.tenant_id == tenant_id,
    )
    if only_failed:
        stmt = stmt.where(EvalJobResult.passed == 0)
    if owasp_category:
        stmt = stmt.where(EvalJobResult.owasp_category == owasp_category)
    stmt = stmt.order_by(EvalJobResult.created_at).offset(offset).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return APIResponse(data=[_result_to_response(r) for r in rows])


# ---------------------------------------------------------------------------
# Efficacy — dashboard overview + per-rule trend
# ---------------------------------------------------------------------------


@evaluation_router.get(
    "/efficacy/overview",
    response_model=APIResponse[EfficacyOverview],
    summary="Overall detection + FP rate from the most recent completed run",
)
async def efficacy_overview(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[EfficacyOverview]:
    last_job = (
        await db.execute(
            select(EvalJob).where(
                EvalJob.tenant_id == tenant_id, EvalJob.status == "completed"
            ).order_by(desc(EvalJob.finished_at)).limit(1)
        )
    ).scalar_one_or_none()

    if last_job is None:
        return APIResponse(
            data=EfficacyOverview(
                detection_rate=0.0,
                fp_rate=0.0,
                cases_evaluated=0,
                attack_cases=0,
                benign_cases=0,
                last_run_at=None,
                per_owasp_category={},
                per_rule={},
            )
        )

    rows = (
        await db.execute(
            select(EvalJobResult).where(
                EvalJobResult.tenant_id == tenant_id,
                EvalJobResult.eval_job_id == last_job.id,
            )
        )
    ).scalars().all()

    attack_total = sum(1 for r in rows if r.case_kind == "attack")
    benign_total = sum(1 for r in rows if r.case_kind == "benign")
    attack_caught = sum(
        1 for r in rows if r.case_kind == "attack" and bool(r.passed)
    )
    benign_blocked = sum(
        1
        for r in rows
        if r.case_kind == "benign" and r.actual_outcome == "deny"
    )

    detection_rate = (attack_caught / attack_total) if attack_total else 0.0
    fp_rate = (benign_blocked / benign_total) if benign_total else 0.0

    per_owasp: dict[str, dict[str, float]] = {}
    for r in rows:
        if r.case_kind != "attack":
            continue
        bucket = per_owasp.setdefault(
            r.owasp_category, {"total": 0.0, "caught": 0.0}
        )
        bucket["total"] += 1
        if bool(r.passed):
            bucket["caught"] += 1
    for bucket in per_owasp.values():
        bucket["detection_rate"] = (
            bucket["caught"] / bucket["total"] if bucket["total"] else 0.0
        )

    per_rule: dict[str, dict[str, float]] = {}
    for r in rows:
        attrib: dict[str, Any] = r.rule_attribution_json or {}
        for rule_key in (
            attrib.get("policy_rule_id"),
            attrib.get("behavior_heuristic"),
            attrib.get("injection_pattern_id"),
        ):
            if not rule_key:
                continue
            bucket = per_rule.setdefault(
                str(rule_key), {"hits": 0.0, "wins": 0.0}
            )
            bucket["hits"] += 1
            if bool(r.passed):
                bucket["wins"] += 1
    for bucket in per_rule.values():
        bucket["efficacy"] = (
            bucket["wins"] / bucket["hits"] if bucket["hits"] else 0.0
        )

    return APIResponse(
        data=EfficacyOverview(
            detection_rate=round(detection_rate, 4),
            fp_rate=round(fp_rate, 4),
            cases_evaluated=len(rows),
            attack_cases=attack_total,
            benign_cases=benign_total,
            last_run_at=_iso(last_job.finished_at),
            per_owasp_category=per_owasp,
            per_rule=per_rule,
        )
    )


@evaluation_router.get(
    "/efficacy/trend",
    response_model=APIResponse[list[EfficacyTrendPoint]],
    summary="Per-rule efficacy trend — the 'biggest score changes' panel",
)
async def efficacy_trend(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    rule_id: str | None = Query(None),
    days: int = Query(30, ge=1, le=365),
) -> APIResponse[list[EfficacyTrendPoint]]:
    stmt = select(EvaluatorScoreSnapshot).where(
        EvaluatorScoreSnapshot.tenant_id == tenant_id
    )
    if rule_id:
        stmt = stmt.where(EvaluatorScoreSnapshot.rule_id == rule_id)
    stmt = stmt.order_by(EvaluatorScoreSnapshot.snapshot_date).limit(days * 50)
    rows = (await db.execute(stmt)).scalars().all()
    points = [
        EfficacyTrendPoint(
            evaluator_id=str(r.evaluator_id),
            rule_id=r.rule_id,
            snapshot_date=r.snapshot_date.isoformat(),
            score=float(r.score),
            samples=int(r.samples),
        )
        for r in rows
    ]
    return APIResponse(data=points)
