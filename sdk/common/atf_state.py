"""ATF v3.2 §9.1 — deterministic agent state derivation.

Four states derived from readable rules — no numeric score is the Gate's
authoritative input (numeric fusion may FEED the derivation, but the Gate
consumes the state):

    VERIFIED     identity valid ∧ human_responsible resolvable
                 ∧ no CONTRADICTED verdict in 7d
                 ∧ unobserved_ratio_7d ≤ threshold
    RESTRICTED   identity valid, but unobserved_ratio_7d > threshold
                 ∨ escalation_ratio_30d > threshold
    QUARANTINED  any CONTRADICTED verdict in 24h
                 ∨ orphaned human_responsible
    UNKNOWN      new agent, < N ledgered actions, or identity not resolvable

Pure function — no I/O — so the existing registry can migrate on its own
schedule without breaking the derivation contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AgentState = Literal["VERIFIED", "RESTRICTED", "QUARANTINED", "UNKNOWN"]

# Defaults (tenant-tunable via policy in a later phase).
_UNOBSERVED_RATIO_THRESHOLD = 0.10  # 10% of C1-C3 with UNOBSERVED verdict
_ESCALATION_RATIO_THRESHOLD = 0.20  # 20% of actions hitting ESCALATE_HUMAN
_MIN_LEDGERED_ACTIONS = 5           # below this → UNKNOWN


@dataclass(frozen=True)
class StateInputs:
    identity_valid: bool
    human_responsible_resolvable: bool
    contradictions_24h: int
    contradictions_7d: int
    unobserved_ratio_7d: float
    escalation_ratio_30d: float
    ledgered_action_count: int


def derive(i: StateInputs) -> AgentState:
    # QUARANTINED wins over everything, per §9.1: any CONTRADICTED in 24h,
    # or an orphaned human_responsible on a previously-valid identity.
    if i.contradictions_24h > 0:
        return "QUARANTINED"
    if i.identity_valid and not i.human_responsible_resolvable:
        return "QUARANTINED"

    if not i.identity_valid or i.ledgered_action_count < _MIN_LEDGERED_ACTIONS:
        return "UNKNOWN"

    if (
        i.unobserved_ratio_7d > _UNOBSERVED_RATIO_THRESHOLD
        or i.escalation_ratio_30d > _ESCALATION_RATIO_THRESHOLD
    ):
        return "RESTRICTED"

    if i.contradictions_7d > 0:
        # Still healed inside the 7d contradiction window but the alert stays.
        return "RESTRICTED"

    return "VERIFIED"


if __name__ == "__main__":
    happy = StateInputs(True, True, 0, 0, 0.0, 0.0, 100)
    assert derive(happy) == "VERIFIED"

    contradiction = StateInputs(True, True, 1, 1, 0.0, 0.0, 100)
    assert derive(contradiction) == "QUARANTINED"

    orphaned = StateInputs(True, False, 0, 0, 0.0, 0.0, 100)
    assert derive(orphaned) == "QUARANTINED"

    new_agent = StateInputs(True, True, 0, 0, 0.0, 0.0, 2)
    assert derive(new_agent) == "UNKNOWN"

    bad_identity = StateInputs(False, True, 0, 0, 0.0, 0.0, 100)
    assert derive(bad_identity) == "UNKNOWN"

    noisy_unobserved = StateInputs(True, True, 0, 0, 0.20, 0.0, 100)
    assert derive(noisy_unobserved) == "RESTRICTED"

    week_old_scar = StateInputs(True, True, 0, 1, 0.0, 0.0, 100)
    assert derive(week_old_scar) == "RESTRICTED"

    print("atf_state OK")
