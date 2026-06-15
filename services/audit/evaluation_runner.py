"""
Sprint 5 — Evaluation job runner.

Loop, single-process: claim a queued ``eval_jobs`` row, hydrate its cases,
replay each one through the REAL gateway ``/execute`` endpoint, and store
``eval_job_results`` rows. After every case is processed, score the job
with whichever ``eval_evaluators`` were attached and snapshot per-rule
scores so dashboards can render trend lines without a re-scan.

Auth model
----------
``/execute`` requires a user-scoped JWT. We never mint one here; instead
the operator provides credentials via env so the runner posts
``/auth/token`` once per job (token lives ~1 hour by default, plenty for
560 cases). Credentials come from SSM in prod per the standing convention.

Env vars
--------
``AEGIS_GATEWAY_URL``      e.g. https://dev.aegisagent.in
``AEGIS_EVAL_USER``        eval bot email (use a least-privilege account)
``AEGIS_EVAL_PASSWORD``    pulled from /aegis-playwright/E2E_PASSWORD or similar
``EVAL_RUNNER_BATCH_SIZE`` how many cases to send per inner loop (default 25)
``EVAL_RUNNER_TIMEOUT``    per-request seconds (default 10)
``EVAL_RUNNER_POLL_INTERVAL`` queue poll cadence in seconds (default 5)

Failure modes
-------------
* Token mint failure       → mark job ``failed`` + error_message, exit
* /execute network failure → record as ``error`` actual_outcome, passed=False
* DB write failure         → log, retry the case, runner stays up

Idempotency
-----------
The (eval_job_id, case_id) unique index on ``eval_job_results`` means a
restarted runner picking up an in-progress job NEVER double-writes; it
just re-tries the cases that don't yet have a result row.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from datetime import UTC, date, datetime
from typing import Any

import httpx
import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from services.audit.database import SessionLocal
from services.audit.evaluation_scoring import SCORERS, ScoreResult
from services.audit.models import (
    EvalDatasetCase,
    EvalJob,
    EvalJobResult,
    Evaluator,
    EvaluatorScoreSnapshot,
)

logger = structlog.get_logger(__name__)

GATEWAY_URL    = os.getenv("AEGIS_GATEWAY_URL", "http://gateway:8000")
EVAL_USER      = os.getenv("AEGIS_EVAL_USER", "")
EVAL_PASSWORD  = os.getenv("AEGIS_EVAL_PASSWORD", "")
BATCH_SIZE     = int(os.getenv("EVAL_RUNNER_BATCH_SIZE", "25"))
REQ_TIMEOUT    = float(os.getenv("EVAL_RUNNER_TIMEOUT", "10.0"))
POLL_INTERVAL  = float(os.getenv("EVAL_RUNNER_POLL_INTERVAL", "5.0"))


# ---------------------------------------------------------------------------
# /auth/token — single shared token per job (~1h TTL on the upstream JWT)
# ---------------------------------------------------------------------------


async def _mint_token(
    client: httpx.AsyncClient, tenant_id: uuid.UUID
) -> str:
    if not EVAL_USER or not EVAL_PASSWORD:
        raise RuntimeError(
            "AEGIS_EVAL_USER / AEGIS_EVAL_PASSWORD not set — eval runner "
            "cannot mint a /execute token. Populate from SSM as documented "
            "in scripts/ops/run_e2e.sh."
        )
    resp = await client.post(
        f"{GATEWAY_URL.rstrip('/')}/auth/token",
        json={"email": EVAL_USER, "password": EVAL_PASSWORD},
        headers={
            "X-Tenant-ID":   str(tenant_id),
            "Content-Type":  "application/json",
        },
        timeout=REQ_TIMEOUT,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"/auth/token failed: HTTP {resp.status_code} — {resp.text[:300]}"
        )
    body = resp.json()
    # Both envelope shapes seen in the wild (APIResponse + raw).
    token = (
        (body.get("data") or {}).get("access_token")
        or body.get("access_token")
    )
    if not token:
        raise RuntimeError(f"/auth/token returned no access_token: {body!r}")
    return str(token)


# ---------------------------------------------------------------------------
# /execute — replay one case
# ---------------------------------------------------------------------------


def _normalize_outcome(decision: str | None) -> str:
    if not decision:
        return "error"
    d = decision.lower()
    if d in {"allow", "monitor"}:
        return "allow"
    if d in {"deny", "kill", "redact"}:
        return "deny"
    if d == "throttle":
        return "throttle"
    if d == "escalate":
        return "escalate"
    return d


def _extract_findings(body: dict[str, Any]) -> list[str]:
    decision = (body.get("data") or {}).get("decision") or body.get("decision") or {}
    raw = decision.get("findings")
    if isinstance(raw, list):
        return [str(x) for x in raw if x]
    # legacy `reasons` fall-back
    raw2 = decision.get("reasons")
    if isinstance(raw2, list):
        return [str(x) for x in raw2 if x]
    return []


def _extract_attribution(body: dict[str, Any]) -> dict[str, Any]:
    """Harvest per-rule trace from the /execute response.

    Today the canonical findings ARE the rule key. As Sprint 5 hardening
    extends OPA/behavior/injection to emit structured rule ids, this
    function gets richer without changing callers.
    """
    decision = (body.get("data") or {}).get("decision") or body.get("decision") or {}
    metadata = decision.get("metadata") or {}
    return {
        "policy_rule_id":      metadata.get("policy_rule_id"),
        "behavior_heuristic":  metadata.get("behavior_heuristic"),
        "injection_pattern_id": metadata.get("injection_pattern_id"),
        "decision":            decision.get("action") or body.get("action"),
        "risk":                decision.get("risk"),
        "confidence":          decision.get("confidence"),
    }


async def _replay_case(
    client: httpx.AsyncClient,
    token: str,
    tenant_id: uuid.UUID,
    case: EvalDatasetCase,
) -> tuple[str, list[str], dict[str, Any], float, str | None]:
    """Send the case payload to /execute. Returns
    (actual_outcome, findings, attribution, latency_ms, error_message)."""
    payload_obj = case.payload_json or {}
    body = {
        "tool":    payload_obj.get("tool"),
        "payload": payload_obj.get("payload"),
        "agent_id": str(uuid.uuid4()),  # ephemeral agent per case
        "_eval": {
            "dataset_id": str(case.dataset_id),
            "case_id":    str(case.id),
            "mutation":   case.mutation,
        },
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Tenant-ID":   str(tenant_id),
        "Content-Type":  "application/json",
    }
    t0 = time.perf_counter()
    try:
        resp = await client.post(
            f"{GATEWAY_URL.rstrip('/')}/execute",
            json=body,
            headers=headers,
            timeout=REQ_TIMEOUT,
        )
    except (httpx.RequestError, asyncio.TimeoutError) as exc:
        elapsed = (time.perf_counter() - t0) * 1000.0
        return "error", [], {}, elapsed, f"network: {exc!s}"

    elapsed = (time.perf_counter() - t0) * 1000.0
    try:
        parsed = resp.json()
    except json.JSONDecodeError:
        return "error", [], {}, elapsed, f"non-json {resp.status_code}: {resp.text[:200]}"

    # 200 with decision body — happy path.
    if resp.status_code == 200:
        action = (
            (parsed.get("data") or {}).get("decision", {}).get("action")
            or (parsed.get("data") or {}).get("action")
            or parsed.get("action")
        )
        actual = _normalize_outcome(action)
        return actual, _extract_findings(parsed), _extract_attribution(parsed), elapsed, None

    # 403 = the contract's "deny / approval-required" return path.
    if resp.status_code == 403:
        return "deny", _extract_findings(parsed), _extract_attribution(parsed), elapsed, None

    # 429 = throttled.
    if resp.status_code == 429:
        return "throttle", _extract_findings(parsed), _extract_attribution(parsed), elapsed, None

    # Everything else = error (504, 502, 500, etc).
    return (
        "error",
        _extract_findings(parsed),
        _extract_attribution(parsed),
        elapsed,
        f"http {resp.status_code}: {str(parsed)[:200]}",
    )


# ---------------------------------------------------------------------------
# Per-job scoring + snapshot write
# ---------------------------------------------------------------------------


async def _score_job(
    db: AsyncSession,
    job: EvalJob,
) -> dict[str, Any]:
    """Run every attached Evaluator over the job's results and write
    snapshot rows. Returns the summary_json that gets persisted to the
    job row.
    """
    rows = (
        await db.execute(
            select(EvalJobResult).where(EvalJobResult.eval_job_id == job.id)
        )
    ).scalars().all()

    summary: dict[str, Any] = {
        "cases_total":   len(rows),
        "passed":        sum(1 for r in rows if r.passed),
        "failed":        sum(1 for r in rows if not r.passed),
        "errors":        sum(
            1 for r in rows if r.actual_outcome == "error"
        ),
        "evaluators":    {},
    }

    evaluator_ids = [uuid.UUID(s) for s in (job.evaluator_ids or [])]
    if not evaluator_ids:
        return summary

    evaluators = (
        await db.execute(
            select(Evaluator).where(Evaluator.id.in_(evaluator_ids))
        )
    ).scalars().all()

    today = date.today()
    snapshot_rows: list[EvaluatorScoreSnapshot] = []
    for ev in evaluators:
        scorer = SCORERS.get(ev.kind)
        if scorer is None:
            continue
        kwargs: dict[str, Any] = {"name": ev.name}
        cfg = ev.config_json or {}
        if ev.kind == "detection_rate" and cfg.get("owasp_category"):
            kwargs["owasp_category"] = cfg["owasp_category"]
        res: ScoreResult = scorer(rows, **kwargs)  # type: ignore[arg-type]
        summary["evaluators"][str(ev.id)] = res.to_dict()
        if res.per_rule:
            for rule_id, bucket in res.per_rule.items():
                snapshot_rows.append(
                    EvaluatorScoreSnapshot(
                        id=uuid.uuid4(),
                        tenant_id=job.tenant_id,
                        evaluator_id=ev.id,
                        rule_id=rule_id,
                        snapshot_date=today,
                        score=float(bucket.get("efficacy", 0.0)),
                        samples=int(bucket.get("hits", 0)),
                        eval_job_id=job.id,
                    )
                )
        else:
            snapshot_rows.append(
                EvaluatorScoreSnapshot(
                    id=uuid.uuid4(),
                    tenant_id=job.tenant_id,
                    evaluator_id=ev.id,
                    rule_id=None,
                    snapshot_date=today,
                    score=res.score,
                    samples=res.samples,
                    eval_job_id=job.id,
                )
            )

    for row in snapshot_rows:
        # Upsert-style: skip if today's row for the same (tenant, evaluator,
        # rule) already exists (e.g. a re-run on the same day).
        existing = (
            await db.execute(
                select(EvaluatorScoreSnapshot.id).where(
                    EvaluatorScoreSnapshot.tenant_id == row.tenant_id,
                    EvaluatorScoreSnapshot.evaluator_id == row.evaluator_id,
                    EvaluatorScoreSnapshot.rule_id == row.rule_id,
                    EvaluatorScoreSnapshot.snapshot_date == row.snapshot_date,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            db.add(row)
    return summary


# ---------------------------------------------------------------------------
# Job loop
# ---------------------------------------------------------------------------


async def _claim_job(db: AsyncSession) -> EvalJob | None:
    """Pick the oldest queued job atomically; mark it running."""
    candidate = (
        await db.execute(
            select(EvalJob)
            .where(EvalJob.status == "queued")
            .order_by(EvalJob.queued_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
    ).scalar_one_or_none()
    if candidate is None:
        return None
    candidate.status = "running"
    candidate.started_at = datetime.now(tz=UTC)
    await db.commit()
    await db.refresh(candidate)
    return candidate


async def _run_job(job: EvalJob) -> None:
    async with SessionLocal() as db:
        cases = (
            await db.execute(
                select(EvalDatasetCase).where(
                    EvalDatasetCase.dataset_id == job.dataset_id,
                    EvalDatasetCase.tenant_id == job.tenant_id,
                )
            )
        ).scalars().all()
        already = {
            r.case_id
            for r in (
                await db.execute(
                    select(EvalJobResult.case_id).where(
                        EvalJobResult.eval_job_id == job.id
                    )
                )
            ).scalars()
        }

    pending = [c for c in cases if c.id not in already]

    async with httpx.AsyncClient() as client:
        try:
            token = await _mint_token(client, job.tenant_id)
        except Exception as exc:
            logger.exception("eval_runner_token_failure", job_id=str(job.id))
            async with SessionLocal() as db:
                await db.execute(
                    update(EvalJob)
                    .where(EvalJob.id == job.id)
                    .values(
                        status="failed",
                        error_message=str(exc),
                        finished_at=datetime.now(tz=UTC),
                    )
                )
                await db.commit()
            return

        for batch_start in range(0, len(pending), BATCH_SIZE):
            batch = pending[batch_start : batch_start + BATCH_SIZE]
            sem = asyncio.Semaphore(BATCH_SIZE)

            async def _bound(case: EvalDatasetCase):
                async with sem:
                    return case, await _replay_case(
                        client, token, job.tenant_id, case
                    )

            results = await asyncio.gather(*(_bound(c) for c in batch))

            async with SessionLocal() as db:
                for case, (actual, findings, attrib, latency_ms, err) in results:
                    passed = _grade(case, actual)
                    db.add(
                        EvalJobResult(
                            id=uuid.uuid4(),
                            eval_job_id=job.id,
                            case_id=case.id,
                            tenant_id=job.tenant_id,
                            owasp_category=case.owasp_category,
                            case_kind=case.case_kind,
                            expected_outcome=case.expected_outcome,
                            actual_outcome=actual,
                            passed=1 if passed else 0,
                            findings=findings,
                            rule_attribution_json=attrib or {},
                            latency_ms=latency_ms,
                            error_message=err,
                        )
                    )
                await db.execute(
                    update(EvalJob)
                    .where(EvalJob.id == job.id)
                    .values(cases_done=EvalJob.cases_done + len(batch))
                )
                await db.commit()

    # Score + finalise
    async with SessionLocal() as db:
        # Re-fetch with current state so SQLAlchemy doesn't clobber cases_done.
        fresh = (
            await db.execute(select(EvalJob).where(EvalJob.id == job.id))
        ).scalar_one()
        summary = await _score_job(db, fresh)
        fresh.summary_json = summary
        fresh.status = "completed"
        fresh.finished_at = datetime.now(tz=UTC)
        await db.commit()
        logger.info(
            "eval_job_completed",
            job_id=str(fresh.id),
            cases=summary.get("cases_total"),
            passed=summary.get("passed"),
            failed=summary.get("failed"),
        )


def _grade(case: EvalDatasetCase, actual: str) -> bool:
    """Did the pipeline behave correctly on this case?

    * attack expects ``deny`` — denying, redacting, killing, or escalating
      all count as a correct catch.
    * benign expects ``allow`` — only an actual allow counts.
    * everything else counts as a miss.
    """
    if case.case_kind == "attack":
        return actual in {"deny", "throttle", "escalate"}
    if case.case_kind == "benign":
        return actual == "allow"
    return False


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


async def run_forever() -> None:
    logger.info(
        "eval_runner_starting",
        gateway=GATEWAY_URL,
        user=EVAL_USER,
        batch=BATCH_SIZE,
        poll=POLL_INTERVAL,
    )
    while True:
        try:
            async with SessionLocal() as db:
                job = await _claim_job(db)
            if job is None:
                await asyncio.sleep(POLL_INTERVAL)
                continue
            await _run_job(job)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("eval_runner_loop_error")
            await asyncio.sleep(POLL_INTERVAL)


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
