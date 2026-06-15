"""
Sprint 7 — Policy Playground replay + outcome diff + evaluator scoring.

The Playground takes a candidate `rules_json` and replays it against any
historical window of REAL audit_logs. For each historical row we produce:

    real_decision    — what the pipeline actually returned at the time
    draft_decision   — what the candidate rules WOULD return today
    drift            — bool, real != draft
    bucket           — agreement | newly_denied | newly_allowed | error

Aggregate counts are surfaced as a ReplayDiff; evaluator scores
(detection_rate, fp_rate) are projected onto the historical truth by
treating real-deny rows as "attacks" and real-allow rows as "benign".

Pure Python — no HTTP, no OPA. Reuses Sprint 6 shadow_evaluator for the
per-row rule eval so the math is identical to what the gateway records.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.audit.evaluation_scoring import (
    detection_rate,
    false_positive_rate,
)
from services.audit.models import AuditLog
from services.audit.shadow_evaluator import (
    ShadowEvalResult,
    evaluate_rules,
)


_BUCKET_AGREEMENT     = "agreement"
_BUCKET_NEWLY_DENIED  = "newly_denied"
_BUCKET_NEWLY_ALLOWED = "newly_allowed"


@dataclass(frozen=True)
class ReplayRow:
    """Per-audit replay record."""

    audit_id:        str
    timestamp:       str | None
    agent_id:        str | None
    tool:            str | None
    real_decision:   str
    draft_decision:  str
    matched_rule_index: int | None
    matched_rule_description: str
    bucket:          str


@dataclass(frozen=True)
class ReplayDiff:
    """Aggregate replay outcome — what the dashboard renders."""

    total_audits:       int
    agreement_count:    int
    newly_denied_count: int
    newly_allowed_count: int
    drift_count:        int
    real_allow_count:   int
    real_deny_count:    int
    sample_drift:       list[ReplayRow]
    sample_newly_denied:  list[ReplayRow]
    sample_newly_allowed: list[ReplayRow]


@dataclass(frozen=True)
class ReplayScores:
    """Sprint 5 evaluators projected onto historical truth."""

    detection_rate: float
    fp_rate:        float
    samples:        int


def _normalise_decision(raw: Any) -> str:
    """Audit decision values vary in case + verb. Collapse to allow/deny."""
    if raw is None:
        return "allow"
    s = str(raw).strip().lower()
    if s in {"deny", "blocked", "kill", "redact", "throttle", "escalate"}:
        return "deny" if s in {"deny", "blocked", "kill", "redact"} else s
    return "allow"


def _row_to_context(row: AuditLog) -> dict[str, Any]:
    """Build the evaluator context dict from one audit row.

    Mirrors what the gateway feeds shadow eval at request time, so a
    replay decision matches what a live shadow decision would have
    produced at the time of the original request.
    """
    metadata = row.metadata_json or {}
    return {
        "tool":           getattr(row, "tool", None) or "",
        "payload":        str(metadata.get("payload") or metadata.get("input") or ""),
        "agent_id":       str(getattr(row, "agent_id", "") or ""),
        "tenant_id":      str(getattr(row, "tenant_id", "") or ""),
        "risk_score":     metadata.get("risk_score"),
        "inference_risk": metadata.get("inference_risk"),
        "behavior_risk":  metadata.get("behavior_risk"),
        "anomaly_score":  metadata.get("anomaly_score"),
    }


def _bucket_for(real: str, draft: str) -> str:
    """Classify each audit into the dashboard's drill-down bucket."""
    if real == draft:
        return _BUCKET_AGREEMENT
    if real == "allow" and draft in {"deny", "throttle", "escalate"}:
        return _BUCKET_NEWLY_DENIED
    if real in {"deny", "throttle", "escalate"} and draft == "allow":
        return _BUCKET_NEWLY_ALLOWED
    return _BUCKET_NEWLY_DENIED if draft == "deny" else _BUCKET_NEWLY_ALLOWED


def _row_to_replay(
    row: AuditLog, eval_res: ShadowEvalResult, real: str, draft: str
) -> ReplayRow:
    timestamp = getattr(row, "timestamp", None) or getattr(row, "created_at", None)
    return ReplayRow(
        audit_id=str(row.id),
        timestamp=timestamp.isoformat() if timestamp else None,
        agent_id=str(row.agent_id) if getattr(row, "agent_id", None) else None,
        tool=getattr(row, "tool", None),
        real_decision=real,
        draft_decision=draft,
        matched_rule_index=eval_res.matched_rule_index,
        matched_rule_description=eval_res.matched_rule_description,
        bucket=_bucket_for(real, draft),
    )


async def fetch_history(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID | None,
    since: datetime,
    until: datetime,
    limit: int,
) -> list[AuditLog]:
    """Pull audit_logs in the window, tenant-scoped + optional agent filter."""
    stmt = select(AuditLog).where(
        AuditLog.tenant_id == tenant_id,
        AuditLog.timestamp >= since,
        AuditLog.timestamp <= until,
    )
    if agent_id is not None:
        stmt = stmt.where(AuditLog.agent_id == agent_id)
    stmt = stmt.order_by(desc(AuditLog.timestamp)).limit(limit)
    return list((await db.execute(stmt)).scalars().all())


def run_replay(
    rules: list[dict[str, Any]],
    rows: list[AuditLog],
    *,
    sample_limit: int = 50,
) -> tuple[ReplayDiff, list[ReplayRow]]:
    """Replay the candidate rules against the supplied audit rows.

    Pure function — testable without DB. Returns the aggregate diff plus
    the full per-row list (callers decide whether to persist or just
    surface a sample).
    """
    replays: list[ReplayRow] = []
    agreement = 0
    newly_denied = 0
    newly_allowed = 0
    real_allow_total = 0
    real_deny_total = 0
    sample_drift: list[ReplayRow] = []
    sample_nd: list[ReplayRow] = []
    sample_na: list[ReplayRow] = []

    for row in rows:
        real = _normalise_decision(getattr(row, "decision", None))
        eval_res = evaluate_rules(rules, _row_to_context(row))
        draft = eval_res.action
        replay = _row_to_replay(row, eval_res, real, draft)
        replays.append(replay)

        if real == "allow":
            real_allow_total += 1
        else:
            real_deny_total += 1

        if replay.bucket == _BUCKET_AGREEMENT:
            agreement += 1
        elif replay.bucket == _BUCKET_NEWLY_DENIED:
            newly_denied += 1
            if len(sample_nd) < sample_limit:
                sample_nd.append(replay)
        else:
            newly_allowed += 1
            if len(sample_na) < sample_limit:
                sample_na.append(replay)

        if real != draft and len(sample_drift) < sample_limit:
            sample_drift.append(replay)

    diff = ReplayDiff(
        total_audits=len(replays),
        agreement_count=agreement,
        newly_denied_count=newly_denied,
        newly_allowed_count=newly_allowed,
        drift_count=newly_denied + newly_allowed,
        real_allow_count=real_allow_total,
        real_deny_count=real_deny_total,
        sample_drift=sample_drift,
        sample_newly_denied=sample_nd,
        sample_newly_allowed=sample_na,
    )
    return diff, replays


def score_replay(replays: list[ReplayRow]) -> ReplayScores:
    """Project Sprint 5 evaluators onto historical truth.

    Treat real-deny rows as "attacks" the draft should also deny;
    real-allow rows as "benign" the draft must not block. The
    detection_rate scorer answers "how many of the historical denies
    does the draft also catch?" and fp_rate answers "how many of the
    historical allows does the draft wrongly block?".
    """
    eval_rows: list[dict[str, Any]] = []
    for r in replays:
        attack = r.real_decision in {"deny", "throttle", "escalate"}
        passed = (
            r.draft_decision in {"deny", "throttle", "escalate"} if attack
            else r.draft_decision == "allow"
        )
        eval_rows.append({
            "case_id":          r.audit_id,
            "case_kind":        "attack" if attack else "benign",
            "owasp_category":   "historical",
            "expected_outcome": r.real_decision,
            "actual_outcome":   r.draft_decision,
            "passed":           passed,
            "findings":         [],
            "rule_attribution_json": {
                "policy_rule_id": r.matched_rule_description or "",
            },
        })

    det = detection_rate(eval_rows)
    fp = false_positive_rate(eval_rows)
    return ReplayScores(
        detection_rate=det.score,
        fp_rate=fp.score,
        samples=len(eval_rows),
    )
