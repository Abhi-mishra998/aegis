import pytest

from sdk.acp_client import validate_policy
from sdk.acp_client.errors import PolicyError
from sdk.acp_client.policy import _rule_matches, Rule


def test_minimal_policy_parses():
    p = validate_policy({"version": 1, "agent": "a1", "allow": [{"tool": "search"}]})
    assert p.agent == "a1"
    assert len(p.allow) == 1


def test_unknown_top_level_key_rejected():
    with pytest.raises(PolicyError, match="unknown top-level keys"):
        validate_policy({"version": 1, "agent": "a", "rogue": True})


def test_invalid_version_rejected():
    with pytest.raises(PolicyError, match="unsupported policy version"):
        validate_policy({"version": 99, "agent": "a"})


def test_missing_agent_rejected():
    with pytest.raises(PolicyError, match="agent"):
        validate_policy({"version": 1})


def test_invalid_regex_in_when_rejected():
    with pytest.raises(PolicyError, match="invalid regex"):
        validate_policy({
            "version": 1,
            "agent": "a",
            "allow": [{"tool": "x", "when": {"payload.foo": "([unclosed"}}],
        })


def test_rule_matches_with_predicate():
    rule = Rule(tool="db.query", when={"payload.args.0": "^SELECT"})
    assert _rule_matches(rule, "db.query", {"args": {"0": "SELECT 1"}})
    assert not _rule_matches(rule, "db.query", {"args": {"0": "DELETE 1"}})
    assert not _rule_matches(rule, "other.tool", {"args": {"0": "SELECT 1"}})


def test_wildcard_tool_matches_any():
    rule = Rule(tool="*", when={})
    assert _rule_matches(rule, "anything", {})
