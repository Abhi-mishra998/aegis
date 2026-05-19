"""
Hypothesis property tests for DecisionEngine.

Each test verifies an invariant stated in DecisionEngine's contract docstring.
Run with:
    .venv/bin/python -m pytest tests/test_decision_engine_properties.py -v
"""
from __future__ import annotations

import uuid

from hypothesis import assume, given
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st

from services.decision.engine import DecisionEngine, _action_from_score
from services.decision.findings import CANONICAL_FINDINGS, SIGNAL_THRESHOLDS
from services.decision.schemas import DecisionContext, ExecutionAction

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ENGINE = DecisionEngine()

_RISK = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
_MAYBE_RISK = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
_FP_RATE = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
_UUID = st.uuids()


def _ctx(
    *,
    inference_risk: float = 0.0,
    behavior_risk: float = 0.0,
    anomaly_score: float = 0.0,
    cost_risk: float = 0.0,
    cross_agent_risk: float = 0.0,
    policy_allowed: bool = True,
    policy_risk_adjustment: float = 0.0,
    false_positive_rate: float = 0.0,
) -> DecisionContext:
    return DecisionContext(
        tenant_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        agent_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        tool="test_tool",
        inference_risk=inference_risk,
        behavior_risk=behavior_risk,
        anomaly_score=anomaly_score,
        cost_risk=cost_risk,
        cross_agent_risk=cross_agent_risk,
        policy_allowed=policy_allowed,
        policy_risk_adjustment=policy_risk_adjustment,
        false_positive_rate=false_positive_rate,
    )


# ---------------------------------------------------------------------------
# 1. Bounded output: risk ∈ [0.0, 1.0] for any finite input
# ---------------------------------------------------------------------------

@given(
    inference_risk=_RISK,
    behavior_risk=_RISK,
    anomaly_score=_RISK,
    cost_risk=_RISK,
    cross_agent_risk=_RISK,
    policy_allowed=st.booleans(),
    policy_risk_adjustment=st.floats(min_value=-1.0, max_value=1.0,
                                     allow_nan=False, allow_infinity=False),
    false_positive_rate=_FP_RATE,
)
@hyp_settings(max_examples=500)
def test_output_always_bounded(
    inference_risk, behavior_risk, anomaly_score, cost_risk, cross_agent_risk,
    policy_allowed, policy_risk_adjustment, false_positive_rate,
):
    ctx = _ctx(
        inference_risk=inference_risk, behavior_risk=behavior_risk,
        anomaly_score=anomaly_score, cost_risk=cost_risk,
        cross_agent_risk=cross_agent_risk, policy_allowed=policy_allowed,
        policy_risk_adjustment=policy_risk_adjustment,
        false_positive_rate=false_positive_rate,
    )
    d = _ENGINE.evaluate(ctx)
    assert 0.0 <= d.risk <= 1.0, f"risk={d.risk} out of [0,1]"


# ---------------------------------------------------------------------------
# 2. Policy supremacy: policy_allowed=False → risk ≥ 0.70, action ∈ {ESCALATE,KILL}
#    (learning discount does NOT apply when policy denies)
# ---------------------------------------------------------------------------

@given(
    inference_risk=_RISK,
    behavior_risk=_RISK,
    anomaly_score=_RISK,
    cost_risk=_RISK,
    cross_agent_risk=_RISK,
    false_positive_rate=_FP_RATE,
)
@hyp_settings(max_examples=300)
def test_policy_deny_floors_risk(
    inference_risk, behavior_risk, anomaly_score, cost_risk, cross_agent_risk,
    false_positive_rate,
):
    ctx = _ctx(
        inference_risk=inference_risk, behavior_risk=behavior_risk,
        anomaly_score=anomaly_score, cost_risk=cost_risk,
        cross_agent_risk=cross_agent_risk,
        policy_allowed=False,
        policy_risk_adjustment=0.0,
        false_positive_rate=false_positive_rate,
    )
    d = _ENGINE.evaluate(ctx)
    assert d.risk >= 0.70, f"policy deny must floor risk at 0.70, got {d.risk}"
    assert d.action in (ExecutionAction.ESCALATE, ExecutionAction.KILL), (
        f"policy deny risk≥0.70 must map to ESCALATE or KILL, got {d.action}"
    )


# ---------------------------------------------------------------------------
# 3. Boost dominance: max_signal ≥ 0.95 AND fp_rate=0 AND policy_allowed=True
#    → risk ≥ 0.95 AND action = KILL
# ---------------------------------------------------------------------------

# Generate the dominating signal directly so assume() filtering isn't needed.
_CRITICAL = st.floats(min_value=0.95, max_value=1.0, allow_nan=False, allow_infinity=False)

@given(
    dominating_signal=_CRITICAL,
    other_risk=_RISK,
    # Use `other_risk` for all non-dominating signals to keep it simple.
)
@hyp_settings(max_examples=300)
def test_critical_signal_boosts_to_kill(dominating_signal, other_risk):
    # Dominate on inference (arbitrary choice — boost checks max of all signals)
    ctx = _ctx(
        inference_risk=dominating_signal,
        behavior_risk=other_risk,
        anomaly_score=other_risk,
        cost_risk=other_risk,
        cross_agent_risk=other_risk,
        policy_allowed=True,
        policy_risk_adjustment=0.0,
        false_positive_rate=0.0,  # no discount — see docstring note on learning discount
    )
    d = _ENGINE.evaluate(ctx)
    assert d.risk >= 0.95, (
        f"max_signal≥0.95, fp_rate=0 must give risk≥0.95, got {d.risk}"
    )
    assert d.action == ExecutionAction.KILL, (
        f"risk≥0.95 must map to KILL, got {d.action}"
    )


# ---------------------------------------------------------------------------
# 4. Zero signals → ALLOW
# ---------------------------------------------------------------------------

def test_zero_signals_gives_allow():
    ctx = _ctx(policy_allowed=True, policy_risk_adjustment=0.0, false_positive_rate=0.0)
    d = _ENGINE.evaluate(ctx)
    assert d.action == ExecutionAction.ALLOW, f"zero signals must give ALLOW, got {d.action}"
    assert d.risk == 0.0, f"zero signals must give risk=0.0, got {d.risk}"


# ---------------------------------------------------------------------------
# 5. findings ⊆ CANONICAL_FINDINGS
# ---------------------------------------------------------------------------

@given(
    inference_risk=_RISK,
    behavior_risk=_RISK,
    anomaly_score=_RISK,
    cost_risk=_RISK,
    cross_agent_risk=_RISK,
    policy_allowed=st.booleans(),
)
@hyp_settings(max_examples=300)
def test_findings_are_canonical(
    inference_risk, behavior_risk, anomaly_score, cost_risk, cross_agent_risk,
    policy_allowed,
):
    ctx = _ctx(
        inference_risk=inference_risk, behavior_risk=behavior_risk,
        anomaly_score=anomaly_score, cost_risk=cost_risk,
        cross_agent_risk=cross_agent_risk, policy_allowed=policy_allowed,
    )
    d = _ENGINE.evaluate(ctx)
    for f in d.findings:
        assert f in CANONICAL_FINDINGS, f"non-canonical finding: {f!r}"


# ---------------------------------------------------------------------------
# 6. signals_evaluated has exactly the 5 canonical signal keys
# ---------------------------------------------------------------------------

@given(
    inference_risk=_RISK,
    behavior_risk=_RISK,
    policy_allowed=st.booleans(),
)
@hyp_settings(max_examples=100)
def test_signals_evaluated_keys(inference_risk, behavior_risk, policy_allowed):
    ctx = _ctx(inference_risk=inference_risk, behavior_risk=behavior_risk,
               policy_allowed=policy_allowed)
    d = _ENGINE.evaluate(ctx)
    assert set(d.signals_evaluated.keys()) == set(SIGNAL_THRESHOLDS.keys()), (
        f"signals_evaluated must contain exactly {set(SIGNAL_THRESHOLDS.keys())}, "
        f"got {set(d.signals_evaluated.keys())}"
    )


# ---------------------------------------------------------------------------
# 7. Action threshold table is monotone: higher score → stricter or equal action
# ---------------------------------------------------------------------------

@given(
    lo=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    hi=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
)
@hyp_settings(max_examples=500)
def test_action_from_score_monotone(lo, hi):
    assume(lo <= hi)
    _ACTION_ORDER = [
        ExecutionAction.ALLOW,
        ExecutionAction.MONITOR,
        ExecutionAction.THROTTLE,
        ExecutionAction.ESCALATE,
        ExecutionAction.KILL,
    ]
    lo_action = _action_from_score(lo)
    hi_action = _action_from_score(hi)
    assert _ACTION_ORDER.index(lo_action) <= _ACTION_ORDER.index(hi_action), (
        f"score {lo:.4f}→{lo_action} is stricter than {hi:.4f}→{hi_action}"
    )


# ---------------------------------------------------------------------------
# 8. Policy deny always injects policy_deny finding
# ---------------------------------------------------------------------------

@given(
    inference_risk=_RISK,
    behavior_risk=_RISK,
)
@hyp_settings(max_examples=100)
def test_policy_deny_includes_policy_deny_finding(inference_risk, behavior_risk):
    ctx = _ctx(inference_risk=inference_risk, behavior_risk=behavior_risk,
               policy_allowed=False)
    d = _ENGINE.evaluate(ctx)
    assert "policy_deny" in d.findings, (
        f"policy_allowed=False must add 'policy_deny' to findings; got {d.findings}"
    )
