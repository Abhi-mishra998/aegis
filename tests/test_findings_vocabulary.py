"""Unit tests for the canonical findings vocabulary (Sprint 2.2, 2026-05-15).

Three invariants enforced:

1. Every string the DecisionEngine emits into `Decision.findings` is in
   `CANONICAL_FINDINGS`. We exercise this on a representative
   cross-product of contexts (clean / each signal triggered / policy
   deny) so a regression that re-adds a free-form string ("Behavioral
   loop detected" or similar) fails CI immediately.

2. A clean low-score context produces `findings == []` — the bug
   reported by customer security teams (mystery strings on clean
   reads) cannot recur.

3. `signals_evaluated` always lists every classifier with its score +
   threshold + triggered bit, even on the clean path. That field is the
   diagnostic surface; missing entries would defeat the audit story.

4. SDK + server vocabularies stay in lockstep (drift between
   sdk/acp_client/findings.py and services/decision/findings.py would
   silently break customer code).
"""

from __future__ import annotations

import uuid

import pytest

from services.decision.engine import DecisionEngine
from services.decision.findings import (
    CANONICAL_FINDINGS,
    FINDING_ANOMALOUS_BEHAVIOR_DETECTED,
    FINDING_AUTONOMY_MAX_COST_EXCEEDED,
    FINDING_POLICY_DENY,
    FINDING_PROMPT_INJECTION_DETECTED,
    SIGNAL_THRESHOLDS,
    SIGNAL_TO_FINDING,
    validate_findings,
)
from services.decision.schemas import DecisionContext


def _ctx(**overrides) -> DecisionContext:
    defaults = dict(
        tenant_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        tool="read_file",
        policy_allowed=True,
        inference_risk=0.0,
        behavior_risk=0.0,
        anomaly_score=0.0,
        cost_risk=0.0,
        cross_agent_risk=0.0,
        confidence=1.0,
        false_positive_rate=0.0,
    )
    defaults.update(overrides)
    return DecisionContext(**defaults)


@pytest.fixture
def engine() -> DecisionEngine:
    return DecisionEngine()


# --------------------------------------------------------------------------- #
# Invariant #1 — every emitted finding is canonical                           #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("scenario", [
    pytest.param({"inference_risk":   0.95}, id="inference_high"),
    pytest.param({"behavior_risk":    0.95}, id="behavior_high"),
    pytest.param({"anomaly_score":    0.95}, id="anomaly_high"),
    pytest.param({"cost_risk":        0.95}, id="cost_high"),
    pytest.param({"cross_agent_risk": 0.95}, id="cross_agent_high"),
    pytest.param({"policy_allowed":   False, "policy_reason": "tool_not_allowed"},
                 id="policy_denied"),
    pytest.param({"inference_risk": 0.95, "policy_allowed": False,
                  "policy_reason": "tool_not_allowed"},
                 id="multi_signal_plus_policy_deny"),
])
def test_engine_emits_only_canonical_findings(engine, scenario):
    decision = engine.evaluate(_ctx(**scenario))
    # validate_findings raises on the first non-canonical entry — if any
    # snuck in this test fails with a clear diff in the message.
    validate_findings(decision.findings)
    # And explicitly:
    for f in decision.findings:
        assert f in CANONICAL_FINDINGS, f"non-canonical finding {f!r}"


# --------------------------------------------------------------------------- #
# Invariant #2 — clean call → empty findings                                  #
# --------------------------------------------------------------------------- #


def test_clean_low_score_call_has_empty_findings(engine):
    """The customer bug we're fixing: a clean read of a public file
    must NOT carry mystery strings in findings/reasons."""
    decision = engine.evaluate(_ctx())  # all signals 0.0
    assert decision.findings == [], (
        f"clean call leaked findings: {decision.findings!r}. "
        "If a regression adds a free-form diagnostic string here, this "
        "test trips first."
    )
    # reasons is the deprecated alias of findings — same invariant.
    assert decision.reasons == []
    # Action should be ALLOW for a clean context.
    assert decision.action.value == "allow"


def test_clean_call_signals_evaluated_lists_every_classifier(engine):
    decision = engine.evaluate(_ctx())
    assert set(decision.signals_evaluated.keys()) == set(SIGNAL_THRESHOLDS.keys())
    for sig_name, entry in decision.signals_evaluated.items():
        assert entry.threshold == SIGNAL_THRESHOLDS[sig_name]
        assert entry.score == 0.0
        assert entry.triggered is False


# --------------------------------------------------------------------------- #
# Invariant #3 — signals_evaluated structure across signal triggers           #
# --------------------------------------------------------------------------- #


def test_inference_threshold_emits_prompt_injection(engine):
    decision = engine.evaluate(_ctx(inference_risk=0.61))
    assert FINDING_PROMPT_INJECTION_DETECTED in decision.findings
    inf = decision.signals_evaluated["inference"]
    assert inf.triggered is True and inf.score == 0.61


def test_behavior_and_anomaly_dedup_to_one_anomalous_behavior(engine):
    """Both `behavior` and `anomaly` map to anomalous_behavior_detected.
    They must appear ONCE, not twice."""
    decision = engine.evaluate(_ctx(behavior_risk=0.95, anomaly_score=0.95))
    assert decision.findings.count(FINDING_ANOMALOUS_BEHAVIOR_DETECTED) == 1


def test_cost_threshold_emits_autonomy_max_cost_exceeded(engine):
    decision = engine.evaluate(_ctx(cost_risk=0.55))
    assert FINDING_AUTONOMY_MAX_COST_EXCEEDED in decision.findings


def test_policy_deny_emits_policy_deny_finding(engine):
    decision = engine.evaluate(_ctx(policy_allowed=False, policy_reason="tool_not_allowed"))
    assert FINDING_POLICY_DENY in decision.findings
    # The free-form reason detail is preserved in metadata, NOT findings.
    assert decision.metadata.get("policy_reason") == "tool_not_allowed"


# --------------------------------------------------------------------------- #
# Invariant #4 — raw upstream flags must not leak into findings               #
# --------------------------------------------------------------------------- #


def test_raw_behavior_flags_do_not_leak_into_findings(engine):
    """Sprint 2.2 fix: previously `behavior_flags` like 'data_exfiltration_risk'
    were appended directly to findings even when no classifier triggered."""
    ctx = _ctx(
        behavior_flags=["data_exfiltration_risk", "token", "something_diagnostic"],
        inference_flags=["spelling_typo"],
    )
    decision = engine.evaluate(ctx)
    assert decision.findings == [], (
        "raw behavior_flags / inference_flags must NOT enter findings"
    )
    # They MUST still be visible somewhere for forensics — metadata.
    diag = decision.metadata.get("diagnostic_flags") or []
    assert "data_exfiltration_risk" in diag
    assert "spelling_typo" in diag


def test_validate_findings_raises_on_non_canonical():
    with pytest.raises(ValueError) as exc:
        validate_findings(["policy_deny", "totally_made_up_finding"])
    assert "non-canonical" in str(exc.value)
    assert "totally_made_up_finding" in str(exc.value)


def test_validate_findings_passes_empty():
    assert validate_findings([]) == []


# --------------------------------------------------------------------------- #
# Invariant #5 — SDK + server vocabularies stay in lockstep                   #
# --------------------------------------------------------------------------- #


def test_sdk_vocabulary_matches_server():
    """Drift between sdk/acp_client/findings.py and the server module
    would silently break customer code (they'd reference a constant the
    server still emits but they don't recognise, or vice versa)."""
    from sdk.acp_client.findings import CANONICAL_FINDINGS as SDK_CANON
    assert SDK_CANON == CANONICAL_FINDINGS, (
        f"SDK vocabulary drifted from server:\n"
        f"  only in server: {sorted(CANONICAL_FINDINGS - SDK_CANON)}\n"
        f"  only in SDK:    {sorted(SDK_CANON - CANONICAL_FINDINGS)}"
    )


def test_sdk_findings_namespace_exposes_every_constant():
    """`FINDINGS.PROMPT_INJECTION_DETECTED` style accessor must cover
    every canonical name so customer code never has to fall back to a
    raw string literal."""
    from sdk.acp_client.findings import FINDINGS
    namespace_values = {v for v in vars(FINDINGS).values() if isinstance(v, str)}
    assert namespace_values == CANONICAL_FINDINGS


# --------------------------------------------------------------------------- #
# Back-compat sanity                                                          #
# --------------------------------------------------------------------------- #


def test_reasons_alias_matches_findings(engine):
    """Deprecated `reasons` is set as a copy of `findings` for one release."""
    decision = engine.evaluate(_ctx(inference_risk=0.95))
    assert decision.reasons == decision.findings
    # Mutating one must not retroactively change the other.
    assert decision.reasons is not decision.findings


def test_signal_to_finding_complete():
    """Every signal in SIGNAL_THRESHOLDS has a finding mapping."""
    assert set(SIGNAL_TO_FINDING.keys()) == set(SIGNAL_THRESHOLDS.keys())
    for finding in SIGNAL_TO_FINDING.values():
        assert finding in CANONICAL_FINDINGS
