"""ATF v3.2 §9.3 — Consistency Sampling for C3.

Sample the agent's plan 3× and require 2/3 constraint-consistency
before the Gate forwards. Detects INSTABILITY, not INCORRECTNESS —
three consistent samples of flawed reasoning pass unanimously.

Documented property. Cost: 3× inference on C3 planning steps only
(typically <1% of actions).
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

SamplingVerdict = Literal["CONSISTENT", "INCONSISTENT", "NEEDS_HUMAN"]


@dataclass
class ConsistencyResult:
    verdict: SamplingVerdict
    dominant_plan_fingerprint: str
    dominant_count: int
    sample_count: int
    reason: str = ""


def _fingerprint_plan(plan: dict[str, Any]) -> str:
    """Compact stable key over the constraint-relevant plan fields.
    Reasoning text differs across samples; the constraint values must
    not. Tenant policy controls which keys are constraint-relevant.
    """
    import hashlib
    import json
    keys = sorted(plan.keys())
    body = json.dumps({k: plan[k] for k in keys}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def sample_and_check(
    planner: Callable[[], dict[str, Any]],
    samples: int = 3,
    quorum: int = 2,
) -> ConsistencyResult:
    """Call planner ``samples`` times, require ``quorum`` matching fingerprints."""
    if samples < 1 or quorum < 1 or quorum > samples:
        raise ValueError(f"invalid samples/quorum: {samples}/{quorum}")

    plans: list[dict[str, Any]] = []
    for _ in range(samples):
        plans.append(planner())

    fingerprints = [_fingerprint_plan(p) for p in plans]
    counts = Counter(fingerprints)
    dominant_fp, dominant_count = counts.most_common(1)[0]

    if dominant_count >= quorum:
        return ConsistencyResult(
            "CONSISTENT", dominant_fp, dominant_count, samples,
            reason=f"{dominant_count}/{samples} agreed",
        )
    # Fewer than quorum matched — INCONSISTENT if there's still a plurality,
    # NEEDS_HUMAN if it's an even three-way split (no signal at all).
    if len(counts) == samples:
        return ConsistencyResult(
            "NEEDS_HUMAN", dominant_fp, dominant_count, samples,
            reason="all samples diverged — no signal, human required",
        )
    return ConsistencyResult(
        "INCONSISTENT", dominant_fp, dominant_count, samples,
        reason=f"quorum {quorum} not reached (max cluster {dominant_count})",
    )


if __name__ == "__main__":
    # All 3 identical → CONSISTENT
    stable = {"amount": 1000, "recipient": "acme"}
    r = sample_and_check(lambda: dict(stable))
    assert r.verdict == "CONSISTENT"
    assert r.dominant_count == 3

    # 2 of 3 identical → CONSISTENT (quorum met)
    plans = iter([
        {"amount": 1000, "recipient": "acme"},
        {"amount": 1000, "recipient": "acme"},
        {"amount": 1000, "recipient": "typo-corp"},
    ])
    r = sample_and_check(lambda: next(plans))
    assert r.verdict == "CONSISTENT"
    assert r.dominant_count == 2

    # All 3 different → NEEDS_HUMAN
    diverging = iter([
        {"amount": 1000},
        {"amount": 2000},
        {"amount": 3000},
    ])
    r = sample_and_check(lambda: next(diverging))
    assert r.verdict == "NEEDS_HUMAN"

    # Even split at higher sample count (4 samples, quorum 3, best cluster 2) → INCONSISTENT
    split = iter([
        {"amount": 1000}, {"amount": 1000},
        {"amount": 2000}, {"amount": 2000},
    ])
    r = sample_and_check(lambda: next(split), samples=4, quorum=3)
    assert r.verdict == "INCONSISTENT"

    print("consistency_sampling OK")
