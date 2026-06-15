"""Sprint 7 — Pure tests for the rules_json → Rego compiler.

These cover the compile_rules() path only — the validate_rego() side
is HTTP-bound and lives in an integration test. We assert:

  * Compiled output declares a valid Rego package and rule bodies
  * payload_substring → contains(lower(...)) emits the canonical form
  * Numeric operators map to the right symbols
  * Description comments survive escaping
  * Unknown actions / operators surface as warnings, not silent denies
"""
from __future__ import annotations

import re

from services.audit.rego_compiler import (
    CompiledRego,
    _safe_package,
    _quote,
    compile_rules,
)


def test_safe_package_strips_unsafe_chars() -> None:
    assert _safe_package("block-rm-rf") == "block_rm_rf"
    assert _safe_package("Block RM -- RF!") == "block_rm____rf"
    assert _safe_package("") == "aegis_policy"
    assert _safe_package("123abc") == "p_123abc"


def test_quote_string_and_numeric() -> None:
    assert _quote(42) == "42"
    assert _quote(0.5) == "0.5"
    assert _quote("hello") == "\"hello\""
    # Numeric-looking strings stay bare (matches the UI generator's behavior).
    assert _quote("3.14") == "3.14"
    # Embedded double-quote gets escaped.
    assert _quote('he said "hi"') == '"he said \\"hi\\""'
    # Newlines escape.
    assert _quote("a\nb") == "\"a\\nb\""


def test_minimal_compile_has_package_and_imports() -> None:
    res = compile_rules([], policy_name="my_test_policy")
    assert isinstance(res, CompiledRego)
    assert res.package_name == "my_test_policy"
    assert "package aegis_policies.my_test_policy" in res.rego
    assert "import rego.v1" in res.rego
    assert "default allow := true" in res.rego
    assert res.rule_count == 0


def test_compile_tool_eq_emits_input_tool_name() -> None:
    rules = [{
        "conditions": [
            {"field": "tool", "operator": "eq", "value": "tool.shell"},
        ],
        "action": "deny",
        "description": "block shell",
    }]
    res = compile_rules(rules, policy_name="block_shell")
    assert res.rule_count == 1
    assert "deny if {" in res.rego
    assert "input.tool_name == \"tool.shell\"" in res.rego
    # The description survives as a comment.
    assert "# rule[0]: block shell" in res.rego


def test_compile_numeric_operators() -> None:
    rules = [{
        "conditions": [
            {"field": "risk_score", "operator": "gt", "value": "0.8"},
        ],
        "action": "deny",
    }]
    res = compile_rules(rules)
    assert "input.risk_score > 0.8" in res.rego


def test_compile_payload_substring_contains() -> None:
    rules = [{
        "conditions": [
            {
                "field": "payload_substring",
                "operator": "contains",
                "value": "rm -rf",
            },
        ],
        "action": "deny",
    }]
    res = compile_rules(rules)
    assert "contains(lower(input.payload), lower(\"rm -rf\"))" in res.rego


def test_compile_payload_substring_not_contains() -> None:
    rules = [{
        "conditions": [
            {
                "field": "payload_substring",
                "operator": "not_contains",
                "value": "approved",
            },
        ],
        "action": "deny",
    }]
    res = compile_rules(rules)
    assert "not contains(lower(input.payload), lower(\"approved\"))" in res.rego


def test_compile_payload_substring_unknown_op_is_warning_not_deny() -> None:
    rules = [{
        "conditions": [
            {
                "field": "payload_substring",
                "operator": "matches_vibe",
                "value": "x",
            },
        ],
        "action": "deny",
    }]
    res = compile_rules(rules)
    assert any("payload_substring" in w for w in res.warnings)
    # The invalid condition becomes `false` so the rule is unreachable
    # rather than silently matching everything.
    assert "false" in res.rego


def test_compile_unknown_action_skipped() -> None:
    rules = [
        {"conditions": [], "action": "warp_speed"},
        {"conditions": [], "action": "deny"},
    ]
    res = compile_rules(rules)
    assert res.rule_count == 1
    assert any("warp_speed" in w for w in res.warnings)
    # The legitimate rule still compiles.
    assert "deny if {" in res.rego


def test_action_aliasing() -> None:
    # monitor → allow, kill → deny
    rules = [{"conditions": [], "action": "monitor"}]
    assert "allow if {" in compile_rules(rules).rego
    rules = [{"conditions": [], "action": "kill"}]
    assert "deny if {" in compile_rules(rules).rego


def test_compound_conditions_emitted_in_order() -> None:
    rules = [{
        "conditions": [
            {"field": "tool", "operator": "eq", "value": "tool.shell"},
            {"field": "risk_score", "operator": "gte", "value": "0.5"},
            {
                "field": "payload_substring",
                "operator": "contains",
                "value": "rm -rf",
            },
        ],
        "action": "deny",
        "description": "destructive shell",
    }]
    res = compile_rules(rules)
    # All three conditions appear in the same `if { ... }` block.
    block = re.search(r"deny if \{(.*?)\}", res.rego, flags=re.S)
    assert block is not None
    body = block.group(1)
    assert "input.tool_name == \"tool.shell\"" in body
    assert "input.risk_score >= 0.5" in body
    assert "contains(lower(input.payload), lower(\"rm -rf\"))" in body


def test_rules_with_no_conditions_compile_to_true_branch() -> None:
    rules = [{"conditions": [], "action": "deny", "description": "fallback"}]
    res = compile_rules(rules)
    assert "deny if {" in res.rego
    assert "true" in res.rego.split("deny if {", 1)[1]


def test_quote_escapes_backslash() -> None:
    rules = [{
        "conditions": [
            {"field": "tool", "operator": "eq", "value": 'a\\b"c'},
        ],
        "action": "deny",
    }]
    res = compile_rules(rules)
    assert "input.tool_name == \"a\\\\b\\\"c\"" in res.rego


def test_compiled_output_is_deterministic() -> None:
    rules = [{
        "conditions": [
            {"field": "risk_score", "operator": "gt", "value": "0.7"},
        ],
        "action": "deny",
        "description": "high risk",
    }]
    a = compile_rules(rules, policy_name="det_test").rego
    b = compile_rules(rules, policy_name="det_test").rego
    assert a == b
