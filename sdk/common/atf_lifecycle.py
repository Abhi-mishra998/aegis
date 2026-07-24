"""ATF v3.2 §14.5 — deployment lifecycle state machine.

    INSTALL → BOOTSTRAP → ENFORCE → (ROTATE|UPGRADE|ROLLBACK)* → DECOMMISSION → DESTROY

Every transition is itself a C3 ledger event. DESTROY produces a signed
destruction certificate referencing the final anchor — the customer can
forever prove what existed and when it was destroyed.

Pure module: legal transitions live here, the actual eventing happens in
the gateway when a lifecycle endpoint is hit. Keeps the state machine
independently unit-testable.
"""
from __future__ import annotations

from typing import Literal

LifecycleState = Literal[
    "INSTALL",
    "BOOTSTRAP",
    "ENFORCE",
    "ROTATE",
    "UPGRADE",
    "ROLLBACK",
    "EXPORT",
    "DECOMMISSION",
    "DESTROY",
]

# EXPORT is a continuous capability, not an exit-only state (§14.5).
# ROTATE / UPGRADE / ROLLBACK return to ENFORCE on completion.
_TRANSITIONS: dict[LifecycleState, set[LifecycleState]] = {
    "INSTALL":      {"BOOTSTRAP"},
    "BOOTSTRAP":    {"ENFORCE"},
    "ENFORCE":      {"ROTATE", "UPGRADE", "ROLLBACK", "DECOMMISSION"},
    "ROTATE":       {"ENFORCE"},
    "UPGRADE":      {"ENFORCE", "ROLLBACK"},
    "ROLLBACK":     {"ENFORCE"},
    "EXPORT":       set(),  # not a persistent state; a subcommand
    "DECOMMISSION": {"DESTROY"},
    "DESTROY":      set(),  # terminal
}


def is_legal(current: LifecycleState, target: LifecycleState) -> bool:
    return target in _TRANSITIONS.get(current, set())


def next_states(current: LifecycleState) -> set[LifecycleState]:
    return _TRANSITIONS.get(current, set())


class IllegalTransition(ValueError):
    """Raised when a caller requests an invalid state transition."""


def transition(current: LifecycleState, target: LifecycleState) -> LifecycleState:
    if not is_legal(current, target):
        raise IllegalTransition(f"{current} → {target} not permitted")
    return target


if __name__ == "__main__":
    assert transition("INSTALL", "BOOTSTRAP") == "BOOTSTRAP"
    assert transition("BOOTSTRAP", "ENFORCE") == "ENFORCE"
    assert transition("ENFORCE", "ROTATE") == "ROTATE"
    assert transition("ROTATE", "ENFORCE") == "ENFORCE"
    assert transition("ENFORCE", "DECOMMISSION") == "DECOMMISSION"
    assert transition("DECOMMISSION", "DESTROY") == "DESTROY"

    for illegal in [
        ("INSTALL", "ENFORCE"),
        ("ENFORCE", "INSTALL"),
        ("DESTROY", "ENFORCE"),
        ("BOOTSTRAP", "DESTROY"),
    ]:
        try:
            transition(*illegal)
            raise AssertionError(f"expected IllegalTransition for {illegal}")
        except IllegalTransition:
            pass

    print("atf_lifecycle OK")
