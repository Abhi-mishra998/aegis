"""ATF v3.2 §6.3 + §9.1 — auto-lockout on CONTRADICTED.

Missing piece from Phase 1b: a CONTRADICTED verdict must translate
into an agent state transition + subsequent Gate denials.

Wire shape (pure — no I/O):

    verdict stream ──► apply_verdict() ──► StateChange
                                                │
                                                ▼
                                       registry state update
                                       (caller-supplied applier)

The verdict subscriber (in production: a Redis Streams reader or a
webhook from services/witness/router.py) invokes `apply_verdict()` for
each verdict; the returned `StateChange` describes the state transition
the registry must persist. No transition → the returned change is
`None`.

Emits a `C2` ledger event on every state change (per §9.1
"state transitions are themselves ledger events").
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sdk.common.atf_state import AgentState, StateInputs, derive
from services.witness.schemas import WitnessVerdict


@dataclass(frozen=True)
class VerdictSummary:
    """The current Witness-side view of one agent — the input the
    subscriber has aggregated (contradiction counters, unobserved
    ratios) since the last state derivation."""

    identity_valid: bool
    human_responsible_resolvable: bool
    contradictions_24h: int
    contradictions_7d: int
    unobserved_ratio_7d: float
    escalation_ratio_30d: float
    ledgered_action_count: int


@dataclass(frozen=True)
class StateChange:
    agent_id: str
    from_state: AgentState
    to_state: AgentState
    reason: str
    trigger_verdict: WitnessVerdict
    trigger_gate_decision_id: str


LedgerAction = Literal["state_change", "no_change"]


def apply_verdict(
    *,
    agent_id: str,
    current_state: AgentState,
    verdict: WitnessVerdict,
    gate_decision_id: str,
    summary: VerdictSummary,
) -> StateChange | None:
    """Given a Witness verdict + the current per-agent stats, decide if
    the registry must move the agent to a new state.

    Returns a `StateChange` iff the derived state differs from
    `current_state`. The caller is responsible for the actual persist
    (registry update + C2 ledger event).
    """
    new_state = derive(StateInputs(
        identity_valid=summary.identity_valid,
        human_responsible_resolvable=summary.human_responsible_resolvable,
        contradictions_24h=summary.contradictions_24h,
        contradictions_7d=summary.contradictions_7d,
        unobserved_ratio_7d=summary.unobserved_ratio_7d,
        escalation_ratio_30d=summary.escalation_ratio_30d,
        ledgered_action_count=summary.ledgered_action_count,
    ))
    if new_state == current_state:
        return None

    reason = _explain_transition(verdict, current_state, new_state, summary)
    return StateChange(
        agent_id=agent_id,
        from_state=current_state,
        to_state=new_state,
        reason=reason,
        trigger_verdict=verdict,
        trigger_gate_decision_id=gate_decision_id,
    )


def _explain_transition(
    verdict: WitnessVerdict,
    from_state: AgentState,
    to_state: AgentState,
    s: VerdictSummary,
) -> str:
    """One-line reason a human auditor can read on the C2 ledger row.
    Prefer the strongest observable signal for the audit trail.
    """
    if verdict == "CONTRADICTED" and to_state == "QUARANTINED":
        return f"contradiction_verdict_within_24h_count={s.contradictions_24h}"
    if to_state == "QUARANTINED" and not s.human_responsible_resolvable:
        return "human_responsible_orphaned"
    if to_state == "RESTRICTED" and s.unobserved_ratio_7d > 0.10:
        return f"unobserved_ratio_7d={s.unobserved_ratio_7d:.2f}>0.10"
    if to_state == "RESTRICTED" and s.escalation_ratio_30d > 0.20:
        return f"escalation_ratio_30d={s.escalation_ratio_30d:.2f}>0.20"
    if to_state == "VERIFIED":
        return "healed:no_contradictions_in_7d"
    return f"derived_transition:{from_state}->{to_state}"


if __name__ == "__main__":
    # A CONTRADICTED verdict on a VERIFIED agent → QUARANTINED.
    change = apply_verdict(
        agent_id="ag_1",
        current_state="VERIFIED",
        verdict="CONTRADICTED",
        gate_decision_id="gd_1",
        summary=VerdictSummary(
            identity_valid=True,
            human_responsible_resolvable=True,
            contradictions_24h=1,
            contradictions_7d=1,
            unobserved_ratio_7d=0.0,
            escalation_ratio_30d=0.0,
            ledgered_action_count=100,
        ),
    )
    assert change is not None
    assert change.from_state == "VERIFIED"
    assert change.to_state == "QUARANTINED"
    assert "contradiction" in change.reason

    # A CORROBORATED verdict on a VERIFIED agent → no change.
    no_change = apply_verdict(
        agent_id="ag_2",
        current_state="VERIFIED",
        verdict="CORROBORATED",
        gate_decision_id="gd_2",
        summary=VerdictSummary(
            identity_valid=True,
            human_responsible_resolvable=True,
            contradictions_24h=0,
            contradictions_7d=0,
            unobserved_ratio_7d=0.0,
            escalation_ratio_30d=0.0,
            ledgered_action_count=100,
        ),
    )
    assert no_change is None

    # QUARANTINED agent healing back to VERIFIED after 7d clean window.
    heal = apply_verdict(
        agent_id="ag_3",
        current_state="QUARANTINED",
        verdict="CORROBORATED",
        gate_decision_id="gd_3",
        summary=VerdictSummary(
            identity_valid=True,
            human_responsible_resolvable=True,
            contradictions_24h=0,
            contradictions_7d=0,
            unobserved_ratio_7d=0.0,
            escalation_ratio_30d=0.0,
            ledgered_action_count=100,
        ),
    )
    assert heal is not None
    assert heal.from_state == "QUARANTINED"
    assert heal.to_state == "VERIFIED"

    # UNOBSERVED with high ratio pushes VERIFIED → RESTRICTED.
    degrade = apply_verdict(
        agent_id="ag_4",
        current_state="VERIFIED",
        verdict="UNOBSERVED",
        gate_decision_id="gd_4",
        summary=VerdictSummary(
            identity_valid=True,
            human_responsible_resolvable=True,
            contradictions_24h=0,
            contradictions_7d=0,
            unobserved_ratio_7d=0.15,
            escalation_ratio_30d=0.0,
            ledgered_action_count=100,
        ),
    )
    assert degrade is not None
    assert degrade.to_state == "RESTRICTED"

    print("auto_lockout OK")
