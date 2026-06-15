"""Sprint 7 — Pure tests for the Policy Playground replay engine.

These cover the parts that don't touch the DB: per-row diff math,
bucket classification, and the Sprint-5 evaluator projection. We mint
fake AuditLog-shaped objects with SimpleNamespace so the engine
doesn't import the SQLAlchemy ORM.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from services.audit.playground_engine import (
    ReplayDiff,
    _normalise_decision,
    run_replay,
    score_replay,
)


def _audit(
    *,
    decision: str,
    tool: str = "tool.shell",
    payload: str = "",
    risk: float | None = None,
    aid: uuid.UUID | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        agent_id=aid or uuid.uuid4(),
        tool=tool,
        decision=decision,
        timestamp=datetime.now(UTC),
        metadata_json={
            "payload":    payload,
            "risk_score": risk,
        },
    )


# ---------------------------------------------------------------------------
# Decision normalisation
# ---------------------------------------------------------------------------


def test_normalise_decision_buckets() -> None:
    assert _normalise_decision("allow") == "allow"
    assert _normalise_decision("ALLOW") == "allow"
    assert _normalise_decision("monitor") == "allow"
    assert _normalise_decision("deny") == "deny"
    assert _normalise_decision("kill") == "deny"
    assert _normalise_decision("redact") == "deny"
    assert _normalise_decision("blocked") == "deny"
    assert _normalise_decision("throttle") == "throttle"
    assert _normalise_decision("escalate") == "escalate"
    assert _normalise_decision(None) == "allow"
    assert _normalise_decision("") == "allow"


# ---------------------------------------------------------------------------
# run_replay — aggregate counts
# ---------------------------------------------------------------------------


def test_replay_empty() -> None:
    diff, replays = run_replay([], [])
    assert diff.total_audits == 0
    assert replays == []
    assert diff.agreement_count == 0
    assert diff.newly_denied_count == 0
    assert diff.newly_allowed_count == 0


def test_replay_agreement_only() -> None:
    rules = [{"conditions": [], "action": "allow"}]
    rows = [
        _audit(decision="allow"),
        _audit(decision="allow"),
        _audit(decision="allow"),
    ]
    diff, _ = run_replay(rules, rows)
    assert diff.total_audits == 3
    assert diff.agreement_count == 3
    assert diff.drift_count == 0
    assert diff.real_allow_count == 3
    assert diff.real_deny_count == 0


def test_replay_newly_denied() -> None:
    # Rule: deny everything. Historical: all allowed → 100% newly_denied.
    rules = [{"conditions": [], "action": "deny"}]
    rows = [_audit(decision="allow") for _ in range(4)]
    diff, replays = run_replay(rules, rows)
    assert diff.total_audits == 4
    assert diff.newly_denied_count == 4
    assert diff.newly_allowed_count == 0
    assert all(r.bucket == "newly_denied" for r in replays)


def test_replay_newly_allowed() -> None:
    rules = [{"conditions": [], "action": "allow"}]
    rows = [_audit(decision="deny") for _ in range(3)]
    diff, replays = run_replay(rules, rows)
    assert diff.newly_allowed_count == 3
    assert diff.newly_denied_count == 0
    assert all(r.bucket == "newly_allowed" for r in replays)


def test_replay_mixed_with_condition_match() -> None:
    rules = [
        {
            "conditions": [
                {"field": "tool", "operator": "eq", "value": "tool.shell"},
            ],
            "action": "deny",
            "description": "block shell",
        },
    ]
    rows = [
        _audit(decision="allow", tool="tool.shell"),    # newly_denied
        _audit(decision="allow", tool="tool.sql_query"),  # agreement (allow == allow)
        _audit(decision="deny",  tool="tool.shell"),    # agreement (deny == deny)
    ]
    diff, replays = run_replay(rules, rows)
    buckets = [r.bucket for r in replays]
    assert buckets.count("newly_denied") == 1
    assert buckets.count("agreement") == 2


def test_replay_sample_caps() -> None:
    rules = [{"conditions": [], "action": "deny"}]
    rows = [_audit(decision="allow") for _ in range(120)]
    diff, _ = run_replay(rules, rows, sample_limit=10)
    assert diff.newly_denied_count == 120
    assert len(diff.sample_newly_denied) == 10
    assert len(diff.sample_drift) == 10


def test_replay_matched_rule_description_propagates() -> None:
    rules = [
        {
            "conditions": [
                {"field": "tool", "operator": "eq", "value": "tool.shell"},
            ],
            "action": "deny",
            "description": "block shell",
        },
    ]
    rows = [_audit(decision="allow", tool="tool.shell")]
    _, replays = run_replay(rules, rows)
    assert replays[0].matched_rule_description == "block shell"
    assert replays[0].matched_rule_index == 0


# ---------------------------------------------------------------------------
# score_replay — Sprint 5 evaluator projection
# ---------------------------------------------------------------------------


def test_score_replay_all_attack_caught() -> None:
    rules = [{"conditions": [], "action": "deny"}]
    rows = [_audit(decision="deny") for _ in range(5)]
    _, replays = run_replay(rules, rows)
    scores = score_replay(replays)
    assert scores.detection_rate == 1.0
    assert scores.samples == 5


def test_score_replay_all_attack_missed() -> None:
    rules = [{"conditions": [], "action": "allow"}]
    rows = [_audit(decision="deny") for _ in range(4)]
    _, replays = run_replay(rules, rows)
    scores = score_replay(replays)
    assert scores.detection_rate == 0.0


def test_score_replay_fp_rate() -> None:
    # Real allowed all 4 rows; draft denies all of them → FP rate 100%.
    rules = [{"conditions": [], "action": "deny"}]
    rows = [_audit(decision="allow") for _ in range(4)]
    _, replays = run_replay(rules, rows)
    scores = score_replay(replays)
    assert scores.fp_rate == 1.0


def test_score_replay_mixed() -> None:
    rules = [
        {
            "conditions": [
                {"field": "tool", "operator": "eq", "value": "tool.shell"},
            ],
            "action": "deny",
        },
    ]
    rows = [
        _audit(decision="deny",  tool="tool.shell"),     # caught attack
        _audit(decision="deny",  tool="tool.shell"),     # caught attack
        _audit(decision="allow", tool="tool.read_file"), # passed benign
        _audit(decision="allow", tool="tool.shell"),     # FP — benign blocked
    ]
    _, replays = run_replay(rules, rows)
    scores = score_replay(replays)
    # 2/2 attacks caught.
    assert scores.detection_rate == 1.0
    # 1/2 benigns wrongly blocked.
    assert scores.fp_rate == 0.5


# ---------------------------------------------------------------------------
# Diff shape sanity
# ---------------------------------------------------------------------------


def test_diff_is_immutable() -> None:
    rules = [{"conditions": [], "action": "allow"}]
    diff, _ = run_replay(rules, [])
    assert isinstance(diff, ReplayDiff)
    with pytest.raises(Exception):
        diff.agreement_count = 99  # type: ignore[misc]


def test_total_audits_equals_agreement_plus_drift() -> None:
    rules = [
        {
            "conditions": [
                {"field": "tool", "operator": "eq", "value": "tool.shell"},
            ],
            "action": "deny",
        },
    ]
    rows = (
        [_audit(decision="allow", tool="tool.shell") for _ in range(3)]
        + [_audit(decision="deny",  tool="tool.shell") for _ in range(2)]
        + [_audit(decision="allow", tool="tool.read_file") for _ in range(4)]
    )
    diff, _ = run_replay(rules, rows)
    assert diff.total_audits == 9
    assert diff.agreement_count + diff.drift_count == 9
