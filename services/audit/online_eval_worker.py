"""
Sprint 6 — Online evaluation worker.

Polls per-tenant configs and samples the last ``poll_interval_seconds``
window of shadow_decisions for each tenant that has shadow policies
enabled. Computes detection-rate, FP-rate, and per-rule efficacy on the
sampled set using the Sprint-5 scorers, snapshots them into
``eval_evaluator_score_snapshots``, and fires a notification if the FP
rate crossed the tenant's threshold.

Why this lives next to the audit DB
===================================
Online evaluation has no hot-path budget — it runs at the cadence of
``poll_interval_seconds`` (15 min default). Putting it in the audit
service means it reads from ``shadow_decisions`` and ``audit_logs`` in
the same DB it writes to, with no cross-service round-trip.

Drift contract
==============
A "drift" is the moment the rolling FP rate on a single (policy, tenant)
exceeds ``fp_threshold``. Each crossing fires exactly one notification —
we deduplicate per (tenant, policy, day) so the inbox doesn't spam if
the metric flickers around the threshold.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from services.audit.database import SessionLocal
from services.audit.evaluation_scoring import per_rule_efficacy
from services.audit.models import (
    EvaluatorScoreSnapshot,
    OnlineEvalSampleConfig,
    ShadowDecision,
    ShadowPolicy,
)
from services.audit.notifications import Notification, create_notification
from services.audit.shadow_evaluator import would_have_blocked_benign

logger = structlog.get_logger(__name__)

POLL_LOOP_SLEEP_SECONDS = float(os.getenv("ONLINE_EVAL_LOOP_SLEEP", "60"))


def _today_utc() -> date:
    return datetime.now(UTC).date()


async def _due_configs(db: AsyncSession) -> list[OnlineEvalSampleConfig]:
    """Return configs whose `last_run_at` is older than `poll_interval_seconds`."""
    now = datetime.now(UTC)
    rows = (
        await db.execute(
            select(OnlineEvalSampleConfig).where(
                OnlineEvalSampleConfig.enabled == 1,
            )
        )
    ).scalars().all()
    due: list[OnlineEvalSampleConfig] = []
    for cfg in rows:
        last = cfg.last_run_at
        if last is None:
            due.append(cfg)
            continue
        if last + timedelta(seconds=cfg.poll_interval_seconds) <= now:
            due.append(cfg)
    return due


async def _sample_shadow_window(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    since: datetime,
) -> list[ShadowDecision]:
    rows = (
        await db.execute(
            select(ShadowDecision)
            .where(
                ShadowDecision.tenant_id == tenant_id,
                ShadowDecision.created_at >= since,
            )
            .order_by(desc(ShadowDecision.created_at))
            .limit(5000)
        )
    ).scalars().all()
    return list(rows)


def _shadow_to_eval_row(d: ShadowDecision) -> dict[str, Any]:
    """Turn a ShadowDecision row into the shape per_rule_efficacy() expects.

    The Sprint-5 scorer reads case_kind / passed / rule_attribution_json,
    so we synthesise those: shadow drift on a real-allow request counts
    as a "benign" case the candidate policy failed (FP); shadow agreement
    counts as "passed".
    """
    real = d.real_action
    shadow = d.shadow_action
    fp = would_have_blocked_benign(real, shadow)
    return {
        "case_id":               str(d.id),
        "case_kind":             "benign" if real == "allow" else "attack",
        "owasp_category":        "online",
        "expected_outcome":      real,
        "actual_outcome":        shadow,
        "passed":                not fp if real == "allow" else (shadow == "deny"),
        "findings":              [],
        "rule_attribution_json": {
            "policy_rule_id": d.matched_rule_description or "",
            "behavior_heuristic": None,
            "injection_pattern_id": None,
        },
    }


async def _persist_snapshot(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    policy: ShadowPolicy,
    score: float,
    samples: int,
) -> None:
    today = _today_utc()
    existing = (
        await db.execute(
            select(EvaluatorScoreSnapshot.id).where(
                EvaluatorScoreSnapshot.tenant_id == tenant_id,
                EvaluatorScoreSnapshot.evaluator_id == policy.id,
                EvaluatorScoreSnapshot.rule_id.is_(None),
                EvaluatorScoreSnapshot.snapshot_date == today,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        await db.execute(
            update(EvaluatorScoreSnapshot)
            .where(EvaluatorScoreSnapshot.id == existing)
            .values(score=score, samples=samples)
        )
        return
    db.add(
        EvaluatorScoreSnapshot(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            evaluator_id=policy.id,  # shadow policy treated as evaluator key
            rule_id=None,
            snapshot_date=today,
            score=score,
            samples=samples,
            eval_job_id=policy.id,
        )
    )


async def _maybe_alert(
    db: AsyncSession,
    cfg: OnlineEvalSampleConfig,
    policy: ShadowPolicy,
    fp_rate: float,
    sampled: int,
) -> None:
    """One alert per (tenant, policy, day) when FP rate crosses threshold."""
    if fp_rate < cfg.fp_threshold:
        return
    today = _today_utc()
    dup_marker = f"shadow_drift|{policy.id}|{today.isoformat()}"
    existing = (
        await db.execute(
            select(Notification.id).where(
                Notification.tenant_id == str(policy.tenant_id),
                Notification.title.like(dup_marker + "%"),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return
    title = (
        f"{dup_marker} — shadow policy '{policy.name}' would block "
        f"{fp_rate * 100:.1f}% of allowed traffic"
    )
    body = (
        f"Shadow policy '{policy.name}' (id {policy.id}) "
        f"crossed the configured FP threshold ({cfg.fp_threshold * 100:.1f}%) "
        f"over the last {sampled} sampled decisions. "
        f"Do NOT promote until you review the would-have-denied list."
    )
    await create_notification(
        db=db,
        tenant_id=str(policy.tenant_id),
        title=title,
        body=body,
        level="warning",
        category="policy",
        link=f"/shadow-mode?policy={policy.id}",
    )


async def _process_config(cfg: OnlineEvalSampleConfig) -> None:
    """One pass over one tenant's shadow window."""
    async with SessionLocal() as db:
        since = datetime.now(UTC) - timedelta(seconds=cfg.poll_interval_seconds * 4)
        rows = await _sample_shadow_window(db, cfg.tenant_id, since)
        if not rows:
            await db.execute(
                update(OnlineEvalSampleConfig)
                .where(OnlineEvalSampleConfig.id == cfg.id)
                .values(last_run_at=datetime.now(UTC))
            )
            await db.commit()
            return

        # Group by policy_id so the snapshot + alert key off the policy.
        by_policy: dict[uuid.UUID, list[ShadowDecision]] = {}
        for r in rows:
            by_policy.setdefault(r.policy_id, []).append(r)

        policies = (
            await db.execute(
                select(ShadowPolicy).where(
                    ShadowPolicy.id.in_(list(by_policy.keys()))
                )
            )
        ).scalars().all()
        policy_map = {p.id: p for p in policies}

        for policy_id, group in by_policy.items():
            policy = policy_map.get(policy_id)
            if policy is None:
                continue

            sample = max(1, int(len(group) * cfg.sample_rate))
            sampled_rows = group[:sample]

            eval_rows = [_shadow_to_eval_row(d) for d in sampled_rows]
            score_result = per_rule_efficacy(eval_rows)

            await _persist_snapshot(
                db=db,
                tenant_id=cfg.tenant_id,
                policy=policy,
                score=score_result.score,
                samples=score_result.samples,
            )

            # Headline FP rate: fraction of real-allow rows the shadow
            # would have blocked. This is the buyer's go/no-go number.
            allow_rows = [
                d for d in sampled_rows if d.real_action == "allow"
            ]
            fp_count = sum(
                1
                for d in allow_rows
                if would_have_blocked_benign(d.real_action, d.shadow_action)
            )
            fp_rate = (fp_count / len(allow_rows)) if allow_rows else 0.0

            await _maybe_alert(
                db=db,
                cfg=cfg,
                policy=policy,
                fp_rate=fp_rate,
                sampled=len(sampled_rows),
            )

        await db.execute(
            update(OnlineEvalSampleConfig)
            .where(OnlineEvalSampleConfig.id == cfg.id)
            .values(last_run_at=datetime.now(UTC))
        )
        await db.commit()


async def run_forever() -> None:
    logger.info("online_eval_worker_starting", loop_sleep=POLL_LOOP_SLEEP_SECONDS)
    while True:
        try:
            async with SessionLocal() as db:
                due = await _due_configs(db)
            for cfg in due:
                await _process_config(cfg)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("online_eval_worker_loop_error")
        await asyncio.sleep(POLL_LOOP_SLEEP_SECONDS)
