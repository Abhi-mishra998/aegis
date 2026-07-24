"""ATF v3.2 §5.4 — policy testing suite.

Three modes, all pure functions so a CI job can call them without a
running gateway:

    run_unit_assertions(bundle, cases)     → per-rule pass/fail
    replay(bundle, historical_entries)     → decision divergence report
    coverage(bundle, entries, window_days) → which rules fired, dead rules

'bundle' is any callable `(input_doc) -> (allow: bool, tier: str, findings: list[str])`.
The runner doesn't care whether it's rego via OPA or a local Python
evaluator — the test harness is decoupled from the enforcement path.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

Bundle = Callable[[dict[str, Any]], tuple[bool, str, list[str]]]


@dataclass
class AssertionCase:
    name: str
    input_doc: dict[str, Any]
    expect_allow: bool
    expect_tier: str | None = None
    expect_finding: str | None = None


@dataclass
class AssertionResult:
    name: str
    passed: bool
    reason: str = ""


@dataclass
class ReplayDivergence:
    entry_id: str
    was_decision: str      # historical decision recorded in the ledger
    now_decision: str      # decision the candidate bundle produces
    reason: str = ""


@dataclass
class ReplayReport:
    total_entries: int
    divergences: list[ReplayDivergence] = field(default_factory=list)

    @property
    def divergence_ratio(self) -> float:
        return 0.0 if self.total_entries == 0 else len(self.divergences) / self.total_entries


@dataclass
class CoverageReport:
    total_entries: int
    per_rule_fire_counts: Counter[str]
    dead_rules: list[str]      # rules present in bundle but never fired
    default_only_ratio: float  # entries decided only by `default` rule


def run_unit_assertions(bundle: Bundle, cases: list[AssertionCase]) -> list[AssertionResult]:
    """Every candidate policy bundle carries assertion cases; a bundle
    without passing cases cannot reach PUBLISHED (§5.4)."""
    out: list[AssertionResult] = []
    for c in cases:
        allow, tier, findings = bundle(c.input_doc)
        if allow != c.expect_allow:
            out.append(AssertionResult(c.name, False,
                                       f"allow={allow!r}, expected {c.expect_allow!r}"))
            continue
        if c.expect_tier is not None and tier != c.expect_tier:
            out.append(AssertionResult(c.name, False,
                                       f"tier={tier!r}, expected {c.expect_tier!r}"))
            continue
        if c.expect_finding is not None and c.expect_finding not in findings:
            out.append(AssertionResult(c.name, False,
                                       f"finding {c.expect_finding!r} missing from {findings}"))
            continue
        out.append(AssertionResult(c.name, True))
    return out


def replay(bundle: Bundle, historical_entries: list[dict[str, Any]]) -> ReplayReport:
    """Evaluate candidate against tenant's own historical ledger entries
    — 'under v18, X% of last month's ALLOWs become DENYs' (§5.4)."""
    divergences: list[ReplayDivergence] = []
    for entry in historical_entries:
        was = str(entry.get("decision") or "")
        input_doc = entry.get("input_doc") or {}
        allow, tier, _ = bundle(input_doc)
        now = "allow" if allow else (tier or "deny")
        if was != now:
            divergences.append(ReplayDivergence(
                entry_id=str(entry.get("entry_id") or entry.get("id") or ""),
                was_decision=was, now_decision=now,
                reason=str(entry.get("reason") or ""),
            ))
    return ReplayReport(total_entries=len(historical_entries), divergences=divergences)


def coverage(
    bundle_rules: list[str],
    fired_rule_ids: list[str],
    default_rule_id: str = "default",
) -> CoverageReport:
    """Which rules fired, which are dead, and how much traffic fell to
    the default rule (§5.4 coverage report)."""
    counts = Counter(fired_rule_ids)
    total = sum(counts.values())
    dead = [r for r in bundle_rules if r not in counts and r != default_rule_id]
    default_ratio = 0.0 if total == 0 else counts.get(default_rule_id, 0) / total
    return CoverageReport(
        total_entries=total,
        per_rule_fire_counts=counts,
        dead_rules=dead,
        default_only_ratio=default_ratio,
    )


if __name__ == "__main__":
    def bundle_v1(doc: dict[str, Any]) -> tuple[bool, str, list[str]]:
        amount = doc.get("amount", 0)
        if amount > 10_000:
            return False, "deny", ["over_hard_max"]
        if amount > 1_000:
            return False, "escalate", ["over_auto_approve"]
        return True, "allow", []

    def bundle_v2(doc: dict[str, Any]) -> tuple[bool, str, list[str]]:
        # Tighter: hard max halved
        amount = doc.get("amount", 0)
        if amount > 5_000:
            return False, "deny", ["over_hard_max"]
        if amount > 500:
            return False, "escalate", ["over_auto_approve"]
        return True, "allow", []

    cases = [
        AssertionCase("small_ok",       {"amount": 100},    True,  "allow"),
        AssertionCase("borderline_ok",  {"amount": 700},    True,  "allow"),
        AssertionCase("big_denies",     {"amount": 50_000}, False, "deny", "over_hard_max"),
    ]
    r = run_unit_assertions(bundle_v1, cases)
    assert all(res.passed for res in r), r

    # v2 tightens the escalate threshold from 1_000 to 500 → borderline_ok
    # (amount=700) now escalates, breaking that assertion.
    r2 = run_unit_assertions(bundle_v2, cases)
    failures = [res for res in r2 if not res.passed]
    assert len(failures) == 1 and failures[0].name == "borderline_ok", r2

    hist = [
        {"entry_id": "el_1", "decision": "allow", "input_doc": {"amount": 200}},
        {"entry_id": "el_2", "decision": "allow", "input_doc": {"amount": 900}},
        {"entry_id": "el_3", "decision": "escalate", "input_doc": {"amount": 3_000}},
    ]
    rep = replay(bundle_v2, hist)
    assert rep.total_entries == 3
    # 'allow amount=900' now becomes 'escalate' under v2; el_3 stays escalate
    assert len(rep.divergences) == 1
    assert rep.divergences[0].entry_id == "el_2"

    cov = coverage(
        bundle_rules=["small_ok", "mid_escalate", "big_deny", "default"],
        fired_rule_ids=["small_ok", "small_ok", "mid_escalate", "default"],
    )
    assert cov.dead_rules == ["big_deny"]
    assert cov.default_only_ratio == 0.25

    print("policy_test_runner OK")
