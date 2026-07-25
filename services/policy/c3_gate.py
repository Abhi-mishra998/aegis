"""ATF v3.2 §9.3 — C3 consistency-sampling gate.

Small helper that decides whether a specific request should be
consistency-sampled (based on tenant opt-in + action class) and, if so,
runs the sampling loop.

Sampling logic itself lives in `sdk/common/consistency_sampling`; this
module is the ATF-shaped WIRE — reads config, invokes the planner N
times, projects each plan to a constraint-relevant fingerprint,
returns a decision the caller can turn into 200 / 403.

Kept small deliberately. Not a decorator, not a middleware — a plain
async helper. Callers own their planner + integration.
"""
from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from sdk.common.consistency_sampling import (
    ConsistencyResult,
    sample_and_check,
)

GateDecision = Literal["ALLOW", "BLOCK"]
Planner = Callable[[], Awaitable[dict[str, Any]]]


@dataclass
class C3GateResult:
    decision: GateDecision
    verdict: ConsistencyResult
    winning_plan: dict[str, Any] | None
    reason: str


def should_sample(action_class: str, tenant_id: str) -> bool:
    """True iff the tenant has opted into C3 sampling AND the action is C3.

    ENV-VAR PATH (historical): tenant in
        ACP_C3_SAMPLING_TENANTS=tenant-a,tenant-b
    Kept for backward compat + ops-owned deployments.

    Per-tenant Redis override (Sprint UI-3): use
    ``should_sample_async(redis, action_class, tenant_id)`` from the
    request path — it consults the UI-set per-tenant flag first, then
    falls back to this env-var check. A live UI toggle in Settings
    writes the Redis flag; ops env-var still works for legacy setups.
    """
    if action_class != "C3":
        return False
    raw = os.getenv("ACP_C3_SAMPLING_TENANTS", "")
    enabled = {t.strip() for t in raw.split(",") if t.strip()}
    return tenant_id in enabled


async def should_sample_async(redis: Any, action_class: str, tenant_id: str) -> bool:
    """Async form that consults the per-tenant UI toggle first.

    Callers in an async context (e.g. gateway messages proxy) should
    prefer this — the env-var version misses admin-set overrides.
    """
    if action_class != "C3":
        return False
    from sdk.common.tenant_settings import get_flag
    return await get_flag(
        redis, tenant_id, "c3_sampling",
        env_var="ACP_C3_SAMPLING_TENANTS",
    )


async def evaluate(
    planner: Planner,
    *,
    samples: int = 3,
    quorum: int = 2,
) -> C3GateResult:
    """Run the planner ``samples`` times, require ``quorum`` matching plans.

    - CONSISTENT + quorum met  → ALLOW with the winning plan.
    - NEEDS_HUMAN (all differ)  → BLOCK (auditor sees "no signal").
    - INCONSISTENT (plurality
      but under quorum)         → BLOCK (unstable reasoning under threshold).

    The planner MUST be idempotent — same context in, same-ish plan out.
    Retryable HTTP failures inside the planner should bubble up; we do
    NOT swallow, because a silent partial sample would defeat the point.
    """
    plans: list[dict[str, Any]] = []
    for _ in range(samples):
        plans.append(await planner())

    # sample_and_check wants a sync callable that returns the next plan.
    _iter = iter(plans)
    verdict = sample_and_check(lambda: next(_iter), samples=samples, quorum=quorum)

    if verdict.verdict == "CONSISTENT":
        # Find the first plan whose fingerprint matches the dominant one.
        from sdk.common.consistency_sampling import _fingerprint_plan
        winning = next(
            p for p in plans
            if _fingerprint_plan(p) == verdict.dominant_plan_fingerprint
        )
        return C3GateResult(
            decision="ALLOW",
            verdict=verdict,
            winning_plan=winning,
            reason=verdict.reason,
        )

    return C3GateResult(
        decision="BLOCK",
        verdict=verdict,
        winning_plan=None,
        reason=verdict.reason,
    )
