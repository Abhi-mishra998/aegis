"""Sprint 5 — Pure-function unit tests for the eval runner.

These cover the parts that don't need the database or the network:

* outcome normalization (allow vs deny vs throttle vs escalate)
* per-case grading (attack expects deny-variant, benign expects allow)
* findings + attribution extraction from /execute response envelopes
"""
from __future__ import annotations

from types import SimpleNamespace

from services.audit.evaluation_runner import (
    _extract_attribution,
    _extract_findings,
    _grade,
    _normalize_outcome,
)


def test_normalize_outcome_allow_variants() -> None:
    assert _normalize_outcome("allow") == "allow"
    assert _normalize_outcome("ALLOW") == "allow"
    assert _normalize_outcome("monitor") == "allow"


def test_normalize_outcome_deny_variants() -> None:
    assert _normalize_outcome("deny") == "deny"
    assert _normalize_outcome("kill") == "deny"
    assert _normalize_outcome("redact") == "deny"


def test_normalize_outcome_other_paths() -> None:
    assert _normalize_outcome("throttle") == "throttle"
    assert _normalize_outcome("escalate") == "escalate"
    assert _normalize_outcome(None) == "error"
    assert _normalize_outcome("") == "error"


def _case(kind: str, expected: str = "deny") -> SimpleNamespace:
    return SimpleNamespace(case_kind=kind, expected_outcome=expected)


def test_grade_attack() -> None:
    assert _grade(_case("attack"), "deny") is True
    assert _grade(_case("attack"), "throttle") is True
    assert _grade(_case("attack"), "escalate") is True
    assert _grade(_case("attack"), "allow") is False
    assert _grade(_case("attack"), "error") is False


def test_grade_benign() -> None:
    assert _grade(_case("benign", "allow"), "allow") is True
    assert _grade(_case("benign", "allow"), "deny") is False
    assert _grade(_case("benign", "allow"), "error") is False


def test_extract_findings_envelope_shapes() -> None:
    # APIResponse envelope
    body1 = {"data": {"decision": {"findings": ["prompt_injection_detected"]}}}
    assert _extract_findings(body1) == ["prompt_injection_detected"]
    # Raw decision shape
    body2 = {"decision": {"findings": ["sql_injection_detected"]}}
    assert _extract_findings(body2) == ["sql_injection_detected"]
    # Legacy reasons fallback
    body3 = {"data": {"decision": {"reasons": ["legacy_reason"]}}}
    assert _extract_findings(body3) == ["legacy_reason"]
    # Empty / missing
    assert _extract_findings({}) == []
    assert _extract_findings({"data": {"decision": {}}}) == []


def test_extract_attribution_pulls_metadata() -> None:
    body = {
        "data": {
            "decision": {
                "action": "deny",
                "risk": 0.92,
                "confidence": 0.81,
                "metadata": {
                    "policy_rule_id": "policy_path_traversal",
                    "behavior_heuristic": None,
                    "injection_pattern_id": None,
                },
            },
        },
    }
    out = _extract_attribution(body)
    assert out["policy_rule_id"] == "policy_path_traversal"
    assert out["decision"] == "deny"
    assert out["risk"] == 0.92
    assert out["confidence"] == 0.81
