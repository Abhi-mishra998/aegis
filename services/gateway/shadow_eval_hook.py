"""
Sprint 6 — Gateway-side shadow-mode hook.

Called from services/gateway/middleware.py AFTER the real enforcement
decision has been written to ``request.state.decision`` — the contract
is that this hook NEVER alters that result, NEVER blocks the response,
and NEVER raises into the request handler.

Lifecycle
=========
1. Gateway middleware computes `decision = Decision(...)` and continues
   the request.
2. Right before returning the response, it dispatches
   ``schedule_shadow_eval(...)`` via ``asyncio.create_task`` so the
   hook runs after the response is on the wire.
3. The hook loads cached shadow policies for (tenant_id, agent_id),
   evaluates them against the request context, and writes one
   ``shadow_decisions`` row per matched policy.

Caching
=======
Shadow policies change on the order of human edits (minutes), not
requests (milliseconds). We cache the (tenant, agent_id?) → list[Policy]
lookup with a 30-second TTL so each ``/execute`` adds at most one DB
fetch every 30s per (tenant, agent_id) bucket — not per request.

The cache TTL is intentionally short: a buyer promotes a policy from
shadow → enforce expects the hot path to stop logging shadow rows in
under a minute, not five.

Latency budget
==============
The whole hook has a ~5 ms soft budget per call. If the policy list is
empty (common case) the hook short-circuits at sub-millisecond cost.
If the rule eval takes longer than the budget for any policy, we still
record the row — but ``eval_latency_ms`` makes the slow case visible to
operators.
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid

import structlog
from sqlalchemy import or_, select

from services.audit.database import SessionLocal
from services.audit.models import ShadowDecision, ShadowPolicy
from services.audit.shadow_evaluator import evaluate_rules

logger = structlog.get_logger(__name__)

# Per-call deadline — shadow eval is fire-and-forget but we still want
# stuck DB sessions to give up quickly so the audit pool isn't drained.
_SHADOW_HOOK_DEADLINE_SECONDS = float(
    os.getenv("AEGIS_SHADOW_HOOK_DEADLINE", "2.0")
)
_CACHE_TTL_SECONDS = float(os.getenv("AEGIS_SHADOW_CACHE_TTL", "30"))

# (tenant_id, agent_id_or_none) -> (expiry_epoch, list[policy_snapshot])
_POLICY_CACHE: dict[tuple[str, str | None], tuple[float, list[dict]]] = {}


def _cache_key(tenant_id: uuid.UUID, agent_id: uuid.UUID | None) -> tuple[str, str | None]:
    return (str(tenant_id), str(agent_id) if agent_id else None)


async def _load_shadow_policies(
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID | None,
) -> list[dict]:
    """Return the list of in-`shadow`-mode policies for (tenant, agent?)."""
    key = _cache_key(tenant_id, agent_id)
    now = time.monotonic()
    entry = _POLICY_CACHE.get(key)
    if entry and entry[0] > now:
        return entry[1]

    try:
        async with SessionLocal() as db:
            stmt = select(ShadowPolicy).where(
                ShadowPolicy.tenant_id == tenant_id,
                ShadowPolicy.mode == "shadow",
            )
            if agent_id is None:
                stmt = stmt.where(ShadowPolicy.agent_id.is_(None))
            else:
                # Match agent-scoped AND tenant-wide (NULL agent_id).
                stmt = stmt.where(
                    or_(
                        ShadowPolicy.agent_id == agent_id,
                        ShadowPolicy.agent_id.is_(None),
                    )
                )
            rows = (await db.execute(stmt)).scalars().all()
    except Exception as exc:
        # If the audit DB is unreachable the hot path MUST NOT see it.
        logger.warning("shadow_policy_load_failed", error=str(exc))
        _POLICY_CACHE[key] = (now + _CACHE_TTL_SECONDS, [])
        return []

    snapshots = [
        {
            "id":          str(r.id),
            "version":     int(r.version),
            "rules_json":  list(r.rules_json or []),
            "sample_rate": float(r.sample_rate or 1.0),
            "agent_id":    str(r.agent_id) if r.agent_id else None,
        }
        for r in rows
    ]
    _POLICY_CACHE[key] = (now + _CACHE_TTL_SECONDS, snapshots)
    return snapshots


def _should_sample(sample_rate: float, request_id: str | None) -> bool:
    """Deterministic sampling — same request_id always lands the same way.

    Using request_id (rather than ``random()``) makes the dashboard
    drill-down reproducible: an operator who follows the link in a
    notification to a specific shadow decision will always see the same
    set of rows.
    """
    if sample_rate >= 1.0:
        return True
    if sample_rate <= 0.0:
        return False
    if not request_id:
        return True
    # Stable, fast, no allocations of a Random instance.
    bucket = (hash(request_id) % 10_000) / 10_000.0
    return bucket < sample_rate


async def _write_shadow_row(
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID | None,
    policy_id: uuid.UUID,
    policy_version: int,
    request_id: str | None,
    audit_id: uuid.UUID | None,
    tool: str | None,
    real_action: str,
    shadow_action: str,
    matched_rule_index: int | None,
    matched_rule_description: str,
    payload_hash: str | None,
    risk_score: float | None,
    eval_latency_ms: float,
) -> None:
    try:
        async with SessionLocal() as db:
            db.add(
                ShadowDecision(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                    policy_id=policy_id,
                    policy_version=policy_version,
                    request_id=request_id,
                    audit_id=audit_id,
                    tool=tool,
                    real_action=real_action,
                    shadow_action=shadow_action,
                    matched_rule_index=matched_rule_index,
                    matched_rule_description=(
                        matched_rule_description[:255]
                        if matched_rule_description else None
                    ),
                    payload_hash=payload_hash,
                    risk_score=risk_score,
                    eval_latency_ms=eval_latency_ms,
                )
            )
            await db.commit()
    except Exception:
        logger.exception("shadow_decision_write_failed", policy_id=str(policy_id))


async def evaluate_and_record(
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID | None,
    request_id: str | None,
    audit_id: uuid.UUID | None,
    tool: str | None,
    payload: str | None,
    payload_hash: str | None,
    real_action: str,
    risk_score: float | None,
    inference_risk: float | None = None,
    behavior_risk: float | None = None,
) -> int:
    """Run all shadow policies for this request. Returns number of rows written.

    Intended call site is ``asyncio.create_task(safe_bg(...))`` from the
    gateway middleware AFTER the real decision is sent to the client. The
    return value exists for tests; the gateway ignores it.
    """
    deadline = time.monotonic() + _SHADOW_HOOK_DEADLINE_SECONDS
    policies = await _load_shadow_policies(tenant_id, agent_id)
    if not policies:
        return 0

    context = {
        "tool":              tool or "",
        "payload":           payload or "",
        "agent_id":          str(agent_id) if agent_id else "",
        "tenant_id":         str(tenant_id),
        "risk_score":        risk_score,
        "inference_risk":    inference_risk if inference_risk is not None else risk_score,
        "behavior_risk":     behavior_risk,
        "anomaly_score":     None,
    }

    written = 0
    for snapshot in policies:
        if time.monotonic() > deadline:
            logger.warning("shadow_eval_deadline_exceeded",
                           tenant=str(tenant_id))
            break
        if not _should_sample(snapshot["sample_rate"], request_id):
            continue
        eval_res = evaluate_rules(snapshot["rules_json"], context)
        await _write_shadow_row(
            tenant_id=tenant_id,
            agent_id=agent_id,
            policy_id=uuid.UUID(snapshot["id"]),
            policy_version=snapshot["version"],
            request_id=request_id,
            audit_id=audit_id,
            tool=tool,
            real_action=real_action,
            shadow_action=eval_res.action,
            matched_rule_index=eval_res.matched_rule_index,
            matched_rule_description=eval_res.matched_rule_description,
            payload_hash=payload_hash,
            risk_score=risk_score,
            eval_latency_ms=eval_res.latency_ms,
        )
        written += 1
    return written


def schedule(
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID | None,
    request_id: str | None,
    audit_id: uuid.UUID | None,
    tool: str | None,
    payload: str | None,
    payload_hash: str | None,
    real_action: str,
    risk_score: float | None,
    inference_risk: float | None = None,
    behavior_risk: float | None = None,
) -> asyncio.Task | None:
    """Fire the shadow evaluator as a background task. NEVER raises.

    The middleware calls this — it must not change request-handler
    behaviour even if the audit DB is down. Returns the task handle
    (or None if there is no running event loop) so tests can `await` it.
    """
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return None

    async def _runner() -> None:
        try:
            await evaluate_and_record(
                tenant_id=tenant_id,
                agent_id=agent_id,
                request_id=request_id,
                audit_id=audit_id,
                tool=tool,
                payload=payload,
                payload_hash=payload_hash,
                real_action=real_action,
                risk_score=risk_score,
                inference_risk=inference_risk,
                behavior_risk=behavior_risk,
            )
        except Exception:
            logger.exception("shadow_eval_runner_unexpected")

    return loop.create_task(_runner())


def invalidate_cache(tenant_id: uuid.UUID | None = None) -> None:
    """Drop cached snapshots — called by the promote / rollback APIs so the
    hot path picks up the new mode within one request.
    """
    if tenant_id is None:
        _POLICY_CACHE.clear()
        return
    tid = str(tenant_id)
    for key in [k for k in _POLICY_CACHE if k[0] == tid]:
        _POLICY_CACHE.pop(key, None)
