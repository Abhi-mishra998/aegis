"""Sprint 6 — Pure shadow-policy rule evaluator tests.

These run in isolation: no DB, no gateway, no event loop. They cover the
contract every shadow policy depends on:

  * First-matching-rule wins (same as /policy/simulate).
  * Default action is allow.
  * is_drift / would_have_blocked_benign return the right go/no-go signal.
  * Unknown fields, unknown operators, unknown actions never crash and
    never silently match.
"""
from __future__ import annotations

import pytest

from services.audit.shadow_evaluator import (
    ShadowEvalResult,
    evaluate_rules,
    is_drift,
    would_have_blocked_benign,
)


# ---------------------------------------------------------------------------
# Default + empty rules
# ---------------------------------------------------------------------------


def test_no_rules_means_allow() -> None:
    res = evaluate_rules([], {"tool": "tool.shell", "payload": ""})
    assert res.action == "allow"
    assert res.matched_rule_index is None
    assert res.matched_rule_description == ""


def test_empty_conditions_acts_as_default_branch() -> None:
    rules = [
        {"conditions": [], "action": "deny", "description": "fallback deny"},
    ]
    res = evaluate_rules(rules, {"tool": "tool.shell"})
    assert res.action == "deny"
    assert res.matched_rule_index == 0
    assert res.matched_rule_description == "fallback deny"


# ---------------------------------------------------------------------------
# Tool-equality rules
# ---------------------------------------------------------------------------


def test_tool_eq_matches() -> None:
    rules = [
        {
            "conditions": [
                {"field": "tool", "operator": "eq", "value": "tool.shell"},
            ],
            "action":      "deny",
            "description": "block shell",
        },
    ]
    res = evaluate_rules(rules, {"tool": "tool.shell"})
    assert res.action == "deny"
    assert res.matched_rule_index == 0


def test_tool_eq_does_not_match_other_tool() -> None:
    rules = [
        {
            "conditions": [
                {"field": "tool", "operator": "eq", "value": "tool.shell"},
            ],
            "action": "deny",
        },
    ]
    res = evaluate_rules(rules, {"tool": "tool.sql_query"})
    assert res.action == "allow"
    assert res.matched_rule_index is None


def test_tool_neq() -> None:
    rules = [
        {
            "conditions": [
                {"field": "tool", "operator": "neq", "value": "tool.read_file"},
            ],
            "action": "deny",
        },
    ]
    assert evaluate_rules(rules, {"tool": "tool.shell"}).action == "deny"
    assert evaluate_rules(rules, {"tool": "tool.read_file"}).action == "allow"


# ---------------------------------------------------------------------------
# Risk-numeric rules
# ---------------------------------------------------------------------------


def test_risk_gt() -> None:
    rules = [
        {
            "conditions": [
                {"field": "risk_score", "operator": "gt", "value": "0.7"},
            ],
            "action": "deny",
        },
    ]
    assert evaluate_rules(rules, {"risk_score": 0.9}).action == "deny"
    assert evaluate_rules(rules, {"risk_score": 0.5}).action == "allow"
    assert evaluate_rules(rules, {"risk_score": 0.7}).action == "allow"  # gt not gte


def test_risk_gte() -> None:
    rules = [
        {
            "conditions": [
                {"field": "risk_score", "operator": "gte", "value": "0.7"},
            ],
            "action": "deny",
        },
    ]
    assert evaluate_rules(rules, {"risk_score": 0.7}).action == "deny"
    assert evaluate_rules(rules, {"risk_score": 0.69}).action == "allow"


def test_risk_missing_field_is_no_match() -> None:
    rules = [
        {
            "conditions": [
                {"field": "risk_score", "operator": "gt", "value": "0.7"},
            ],
            "action": "deny",
        },
    ]
    res = evaluate_rules(rules, {"tool": "tool.shell"})
    assert res.action == "allow"


# ---------------------------------------------------------------------------
# Payload-substring (new shadow-only field)
# ---------------------------------------------------------------------------


def test_payload_contains() -> None:
    rules = [
        {
            "conditions": [
                {
                    "field": "payload_substring",
                    "operator": "contains",
                    "value": "rm -rf",
                },
            ],
            "action": "deny",
            "description": "destructive shell",
        },
    ]
    res = evaluate_rules(
        rules,
        {"tool": "tool.shell", "payload": "sudo rm -rf /var/lib"},
    )
    assert res.action == "deny"
    assert res.matched_rule_description == "destructive shell"


def test_payload_contains_is_case_insensitive() -> None:
    rules = [
        {
            "conditions": [
                {
                    "field": "payload_substring",
                    "operator": "contains",
                    "value": "drop table",
                },
            ],
            "action": "deny",
        },
    ]
    assert evaluate_rules(rules, {"payload": "DROP TABLE users"}).action == "deny"


def test_payload_not_contains() -> None:
    rules = [
        {
            "conditions": [
                {
                    "field": "payload_substring",
                    "operator": "not_contains",
                    "value": "approved",
                },
            ],
            "action": "deny",
            "description": "must include approval marker",
        },
    ]
    assert evaluate_rules(rules, {"payload": "do thing"}).action == "deny"
    assert evaluate_rules(rules, {"payload": "approved: do thing"}).action == "allow"


# ---------------------------------------------------------------------------
# Compound rules (all conditions must match)
# ---------------------------------------------------------------------------


def test_compound_all_match() -> None:
    rules = [
        {
            "conditions": [
                {"field": "tool", "operator": "eq", "value": "tool.shell"},
                {"field": "risk_score", "operator": "gt", "value": "0.5"},
            ],
            "action": "deny",
        },
    ]
    assert evaluate_rules(
        rules, {"tool": "tool.shell", "risk_score": 0.9}
    ).action == "deny"
    assert evaluate_rules(
        rules, {"tool": "tool.shell", "risk_score": 0.1}
    ).action == "allow"
    assert evaluate_rules(
        rules, {"tool": "tool.read_file", "risk_score": 0.9}
    ).action == "allow"


def test_first_matching_rule_wins() -> None:
    rules = [
        {
            "conditions": [
                {"field": "tool", "operator": "eq", "value": "tool.shell"},
            ],
            "action": "throttle",
            "description": "shell throttle",
        },
        {
            "conditions": [
                {"field": "tool", "operator": "eq", "value": "tool.shell"},
            ],
            "action": "deny",
            "description": "shell deny",
        },
    ]
    res = evaluate_rules(rules, {"tool": "tool.shell"})
    assert res.action == "throttle"
    assert res.matched_rule_index == 0
    assert res.matched_rule_description == "shell throttle"


# ---------------------------------------------------------------------------
# Action normalisation
# ---------------------------------------------------------------------------


def test_action_aliases() -> None:
    # monitor -> allow at the shadow surface.
    rules = [{"conditions": [], "action": "monitor"}]
    assert evaluate_rules(rules, {}).action == "allow"
    # kill -> deny.
    rules = [{"conditions": [], "action": "kill"}]
    assert evaluate_rules(rules, {}).action == "deny"
    # uppercase + whitespace
    rules = [{"conditions": [], "action": "  DENY  "}]
    assert evaluate_rules(rules, {}).action == "deny"


def test_unknown_action_falls_back_to_allow() -> None:
    rules = [{"conditions": [], "action": "warp_speed"}]
    assert evaluate_rules(rules, {}).action == "allow"


# ---------------------------------------------------------------------------
# Robustness: unknown fields / operators never crash + never match
# ---------------------------------------------------------------------------


def test_unknown_field_does_not_match() -> None:
    rules = [
        {
            "conditions": [
                {"field": "made_up_field", "operator": "eq", "value": "x"},
            ],
            "action": "deny",
        },
    ]
    assert evaluate_rules(rules, {"made_up_field": "x"}).action == "allow"


def test_unknown_operator_does_not_match() -> None:
    rules = [
        {
            "conditions": [
                {"field": "risk_score", "operator": "matches_vibe", "value": "0.5"},
            ],
            "action": "deny",
        },
    ]
    assert evaluate_rules(rules, {"risk_score": 0.9}).action == "allow"


def test_garbage_value_does_not_crash() -> None:
    rules = [
        {
            "conditions": [
                {"field": "risk_score", "operator": "gt", "value": "not_a_number"},
            ],
            "action": "deny",
        },
    ]
    res = evaluate_rules(rules, {"risk_score": 0.9})
    assert res.action == "allow"


# ---------------------------------------------------------------------------
# Drift detection helpers — what the UI grades policies on
# ---------------------------------------------------------------------------


def test_is_drift_basic() -> None:
    assert is_drift("allow", "deny") is True
    assert is_drift("deny", "allow") is True
    assert is_drift("allow", "monitor") is False  # both normalise to allow
    assert is_drift("DENY", "kill") is False       # both normalise to deny
    assert is_drift("throttle", "throttle") is False


def test_would_have_blocked_benign() -> None:
    # The headline FP signal — real allowed, shadow denied benign.
    assert would_have_blocked_benign("allow", "deny") is True
    assert would_have_blocked_benign("allow", "throttle") is True
    assert would_have_blocked_benign("allow", "escalate") is True
    # Not an FP — real denied too.
    assert would_have_blocked_benign("deny", "deny") is False
    # Not an FP — shadow agreed it's allowed.
    assert would_have_blocked_benign("allow", "allow") is False


def test_latency_recorded() -> None:
    rules = [{"conditions": [], "action": "deny"}]
    res = evaluate_rules(rules, {})
    assert isinstance(res, ShadowEvalResult)
    assert res.latency_ms >= 0.0
