"""Sprint 5 — Detection-rate / FP-rate / per-rule efficacy scorers.

The runner produces ``EvalJobResult`` rows that capture, per case:
``case_kind`` (attack|benign), ``expected_outcome``, ``actual_outcome``,
``passed`` (bool), ``findings`` (list), ``rule_attribution_json`` (dict).

These pure-Python scorers consume those rows and return:

    {
        "score": float,
        "samples": int,
        "failed_case_ids": list[str],   # for drill-down
        "per_rule": dict[str, dict],    # only PerRuleEfficacy uses this
    }

Pure-Python so they can be unit-tested without spinning up the runner or
the DB, and so the runner can call them inline once a job finishes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ScoreResult:
    """Output of one evaluator pass over a set of EvalJobResult rows."""

    name:            str
    kind:            str
    score:           float
    samples:         int
    failed_case_ids: list[str]
    per_rule:        dict[str, dict[str, float]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name":            self.name,
            "kind":            self.kind,
            "score":           round(float(self.score), 6),
            "samples":         int(self.samples),
            "failed_case_ids": list(self.failed_case_ids),
            "per_rule":        {
                k: {kk: round(float(vv), 6) for kk, vv in v.items()}
                for k, v in self.per_rule.items()
            },
        }


def _row(row: Any) -> dict[str, Any]:
    """Normalise an ORM row OR a dict into a flat dict accessor."""
    if isinstance(row, dict):
        return row
    return {
        "case_id":               getattr(row, "case_id", None),
        "case_kind":             getattr(row, "case_kind", None),
        "owasp_category":        getattr(row, "owasp_category", None),
        "expected_outcome":      getattr(row, "expected_outcome", None),
        "actual_outcome":        getattr(row, "actual_outcome", None),
        "passed":                bool(getattr(row, "passed", False)),
        "findings":              list(getattr(row, "findings", None) or []),
        "rule_attribution_json": dict(getattr(row, "rule_attribution_json", None) or {}),
    }


# ---------------------------------------------------------------------------
# Detection rate — recall on attacks. "Of the cases labelled attack, how
# many did the pipeline correctly deny?"
# ---------------------------------------------------------------------------


def detection_rate(
    results: list[Any],
    *,
    name: str = "detection_rate",
    owasp_category: str | None = None,
) -> ScoreResult:
    rows = [_row(r) for r in results]
    attack_rows = [r for r in rows if r["case_kind"] == "attack"]
    if owasp_category:
        attack_rows = [r for r in attack_rows if r["owasp_category"] == owasp_category]
    total = len(attack_rows)
    caught = sum(1 for r in attack_rows if r["passed"])
    failed = [str(r["case_id"]) for r in attack_rows if not r["passed"]]
    score = (caught / total) if total else 0.0
    return ScoreResult(
        name=name,
        kind="detection_rate",
        score=score,
        samples=total,
        failed_case_ids=failed,
        per_rule={},
    )


# ---------------------------------------------------------------------------
# False-positive rate — "Of the benign cases, how many did the pipeline
# wrongly deny?" Lower is better.
# ---------------------------------------------------------------------------


def false_positive_rate(
    results: list[Any],
    *,
    name: str = "fp_rate",
) -> ScoreResult:
    rows = [_row(r) for r in results]
    benign_rows = [r for r in rows if r["case_kind"] == "benign"]
    total = len(benign_rows)
    # FP = benign case whose actual_outcome was a deny variant.
    blocked = [
        r for r in benign_rows
        if r["actual_outcome"] in {"deny", "blocked", "throttle"}
    ]
    score = (len(blocked) / total) if total else 0.0
    return ScoreResult(
        name=name,
        kind="fp_rate",
        score=score,
        samples=total,
        failed_case_ids=[str(r["case_id"]) for r in blocked],
        per_rule={},
    )


# ---------------------------------------------------------------------------
# Per-rule efficacy — for every rule that fired on an attack case, what
# fraction of those firings correctly denied? Surfaces "this rule is
# becoming useless" or "this rule is producing FPs" trends.
#
# Source for the rule id (priority order):
#   1. rule_attribution_json.policy_rule_id
#   2. rule_attribution_json.behavior_heuristic
#   3. rule_attribution_json.injection_pattern_id
#   4. first finding in the canonical findings list
# ---------------------------------------------------------------------------


def _row_rule_keys(row: dict[str, Any]) -> list[str]:
    attrib = row["rule_attribution_json"] or {}
    keys: list[str] = []
    for k in ("policy_rule_id", "behavior_heuristic", "injection_pattern_id"):
        v = attrib.get(k)
        if v:
            keys.append(str(v))
    if not keys:
        for f in row["findings"]:
            keys.append(str(f))
    return keys


def per_rule_efficacy(
    results: list[Any],
    *,
    name: str = "per_rule_efficacy",
) -> ScoreResult:
    rows = [_row(r) for r in results]
    per_rule: dict[str, dict[str, float]] = {}
    for r in rows:
        for key in _row_rule_keys(r):
            bucket = per_rule.setdefault(
                key, {"hits": 0.0, "wins": 0.0, "fps": 0.0}
            )
            bucket["hits"] += 1
            if r["case_kind"] == "attack" and r["passed"]:
                bucket["wins"] += 1
            if r["case_kind"] == "benign" and not r["passed"]:
                bucket["fps"] += 1

    for bucket in per_rule.values():
        bucket["efficacy"] = (
            bucket["wins"] / bucket["hits"] if bucket["hits"] else 0.0
        )
        bucket["fp_rate"] = (
            bucket["fps"] / bucket["hits"] if bucket["hits"] else 0.0
        )

    samples = sum(int(b["hits"]) for b in per_rule.values())
    score = (
        sum(b["efficacy"] * b["hits"] for b in per_rule.values()) / samples
        if samples
        else 0.0
    )
    failed = [
        str(r["case_id"])
        for r in rows
        if not r["passed"]
    ]
    return ScoreResult(
        name=name,
        kind="per_rule_efficacy",
        score=score,
        samples=samples,
        failed_case_ids=failed,
        per_rule=per_rule,
    )


SCORERS = {
    "detection_rate":    detection_rate,
    "fp_rate":           false_positive_rate,
    "per_rule_efficacy": per_rule_efficacy,
}


def run_scorer(kind: str, results: list[Any], **kwargs: Any) -> ScoreResult:
    fn = SCORERS.get(kind)
    if fn is None:
        raise ValueError(f"unknown evaluator kind: {kind}")
    return fn(results, **kwargs)  # type: ignore[arg-type]
