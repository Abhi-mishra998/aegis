"""Sprint 5 — Pure-Python scorer math.

Doesn't hit the DB. Builds fake EvalJobResult-shaped dicts and runs them
through the scorers so the math is correctness-checked independently of
the runner and the API surface.
"""
from __future__ import annotations

from services.audit.evaluation_scoring import (
    detection_rate,
    false_positive_rate,
    per_rule_efficacy,
    run_scorer,
)


def _row(
    case_id: str,
    case_kind: str,
    expected: str,
    actual: str,
    passed: bool,
    owasp_category: str = "LLM01",
    findings: list[str] | None = None,
    attrib: dict | None = None,
) -> dict:
    return {
        "case_id":               case_id,
        "case_kind":             case_kind,
        "owasp_category":        owasp_category,
        "expected_outcome":      expected,
        "actual_outcome":        actual,
        "passed":                passed,
        "findings":              findings or [],
        "rule_attribution_json": attrib or {},
    }


def test_detection_rate_all_caught() -> None:
    rows = [
        _row("a", "attack", "deny", "deny", True),
        _row("b", "attack", "deny", "deny", True),
        _row("c", "attack", "deny", "deny", True),
    ]
    res = detection_rate(rows)
    assert res.score == 1.0
    assert res.samples == 3
    assert res.failed_case_ids == []


def test_detection_rate_partial() -> None:
    rows = [
        _row("a", "attack", "deny", "deny", True),
        _row("b", "attack", "deny", "allow", False),
        _row("c", "attack", "deny", "deny", True),
        _row("d", "attack", "deny", "allow", False),
    ]
    res = detection_rate(rows)
    assert res.score == 0.5
    assert res.samples == 4
    assert set(res.failed_case_ids) == {"b", "d"}


def test_detection_rate_ignores_benign() -> None:
    rows = [
        _row("a", "attack",  "deny",  "deny",  True),
        _row("b", "benign",  "allow", "allow", True),
        _row("c", "benign",  "allow", "deny",  False),
    ]
    res = detection_rate(rows)
    # Benign rows don't influence detection_rate at all.
    assert res.score == 1.0
    assert res.samples == 1


def test_detection_rate_category_filter() -> None:
    rows = [
        _row("a", "attack", "deny", "deny",  True,  owasp_category="LLM01"),
        _row("b", "attack", "deny", "allow", False, owasp_category="LLM07"),
        _row("c", "attack", "deny", "deny",  True,  owasp_category="LLM07"),
    ]
    res = detection_rate(rows, owasp_category="LLM07")
    assert res.score == 0.5
    assert res.samples == 2


def test_detection_rate_empty() -> None:
    res = detection_rate([])
    assert res.score == 0.0
    assert res.samples == 0


def test_fp_rate_zero() -> None:
    rows = [
        _row("a", "benign", "allow", "allow", True),
        _row("b", "benign", "allow", "allow", True),
    ]
    assert false_positive_rate(rows).score == 0.0


def test_fp_rate_half() -> None:
    rows = [
        _row("a", "benign", "allow", "allow", True),
        _row("b", "benign", "allow", "deny",  False),
    ]
    res = false_positive_rate(rows)
    assert res.score == 0.5
    assert res.failed_case_ids == ["b"]


def test_fp_rate_counts_throttle_as_fp() -> None:
    rows = [
        _row("a", "benign", "allow", "throttle", False),
        _row("b", "benign", "allow", "allow",    True),
    ]
    assert false_positive_rate(rows).score == 0.5


def test_per_rule_efficacy_basic() -> None:
    rows = [
        _row(
            "a", "attack", "deny", "deny", True,
            attrib={"policy_rule_id": "sql_injection_stacked"},
        ),
        _row(
            "b", "attack", "deny", "allow", False,
            attrib={"policy_rule_id": "sql_injection_stacked"},
        ),
        _row(
            "c", "attack", "deny", "deny", True,
            attrib={"policy_rule_id": "policy_path_traversal"},
        ),
    ]
    res = per_rule_efficacy(rows)
    assert "sql_injection_stacked" in res.per_rule
    assert res.per_rule["sql_injection_stacked"]["efficacy"] == 0.5
    assert res.per_rule["policy_path_traversal"]["efficacy"] == 1.0
    # Weighted overall score: (0.5 * 2 + 1.0 * 1) / 3 = 0.6666...
    assert abs(res.score - 2 / 3) < 1e-6


def test_per_rule_efficacy_falls_back_to_findings() -> None:
    rows = [
        _row(
            "a", "attack", "deny", "deny", True,
            findings=["prompt_injection_detected"],
        ),
        _row(
            "b", "attack", "deny", "allow", False,
            findings=["prompt_injection_detected"],
        ),
    ]
    res = per_rule_efficacy(rows)
    assert "prompt_injection_detected" in res.per_rule
    assert res.per_rule["prompt_injection_detected"]["efficacy"] == 0.5


def test_per_rule_efficacy_tracks_fp() -> None:
    rows = [
        _row(
            "a", "attack", "deny", "deny", True,
            attrib={"policy_rule_id": "sql_injection_stacked"},
        ),
        _row(
            "b", "benign", "allow", "deny", False,
            attrib={"policy_rule_id": "sql_injection_stacked"},
        ),
    ]
    res = per_rule_efficacy(rows)
    bucket = res.per_rule["sql_injection_stacked"]
    assert bucket["fps"] == 1
    assert bucket["fp_rate"] == 0.5


def test_run_scorer_dispatch() -> None:
    rows = [_row("a", "attack", "deny", "deny", True)]
    out = run_scorer("detection_rate", rows)
    assert out.kind == "detection_rate"
    out2 = run_scorer("fp_rate", rows)
    assert out2.kind == "fp_rate"


def test_run_scorer_unknown_kind_raises() -> None:
    import pytest

    with pytest.raises(ValueError):
        run_scorer("invented_kind", [])


def test_to_dict_roundtrip() -> None:
    rows = [
        _row(
            "a", "attack", "deny", "deny", True,
            attrib={"policy_rule_id": "r1"},
        ),
    ]
    d = per_rule_efficacy(rows).to_dict()
    assert d["kind"] == "per_rule_efficacy"
    assert "per_rule" in d and "r1" in d["per_rule"]
    assert d["samples"] == 1
