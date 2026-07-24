"""ATF v3.2 §5.4 + §14.5 — Gate operating mode.

    dry_run   → every decision classified + ledgered, ENFORCEMENT SUPPRESSED
                (§5.4 dry-run: 'everything ALLOWs, everything is classified
                 and ledgered, producing a baseline before enforcement').
    shadow    → active policy decides; candidate policy evaluates in
                parallel + divergences ledgered.
    enforce   → default. Decisions bite.

The mode gates the FINAL step of the decision path: whether a deny/
escalate result becomes an outgoing 403 or gets logged and rewritten
to allow. Classification, ledger entry, witness reconciliation all
happen regardless.

Consumed from ACP_GATE_MODE env var (default: enforce). Read once at
module import — flipping mode requires a container restart, which is
itself a lifecycle event (§14.5 ROTATE/UPGRADE).
"""
from __future__ import annotations

import os
from typing import Literal

GateMode = Literal["enforce", "dry_run", "shadow"]

_DEFAULT: GateMode = "enforce"
_VALID: set[GateMode] = {"enforce", "dry_run", "shadow"}


def _read_from_env() -> GateMode:
    raw = os.getenv("ACP_GATE_MODE", _DEFAULT).lower()
    if raw in _VALID:
        return raw  # type: ignore[return-value]
    return _DEFAULT


CURRENT_MODE: GateMode = _read_from_env()


def is_enforcing() -> bool:
    """Should a deny/escalate actually block? False in dry_run."""
    return CURRENT_MODE == "enforce"


def is_shadow() -> bool:
    """Shadow mode: active policy decides, candidate evaluated in parallel."""
    return CURRENT_MODE == "shadow"


def apply_mode_to_decision(decision: str) -> str:
    """dry_run rewrites deny/escalate → allow (still ledgered as
    dry_run_would_deny / dry_run_would_escalate in metadata by the
    caller). enforce + shadow pass through.
    """
    if CURRENT_MODE == "dry_run" and decision in ("deny", "escalate"):
        return "allow"
    return decision


if __name__ == "__main__":
    # Explicit re-read so tests can flip the env var.
    os.environ["ACP_GATE_MODE"] = "dry_run"
    assert _read_from_env() == "dry_run"

    os.environ["ACP_GATE_MODE"] = "enforce"
    assert _read_from_env() == "enforce"

    os.environ["ACP_GATE_MODE"] = "garbage"
    assert _read_from_env() == "enforce"  # invalid falls back

    del os.environ["ACP_GATE_MODE"]
    assert _read_from_env() == "enforce"  # default

    # Behavior: dry_run rewrites deny → allow, but only there
    original_mode = CURRENT_MODE
    globals()["CURRENT_MODE"] = "dry_run"
    assert apply_mode_to_decision("deny") == "allow"
    assert apply_mode_to_decision("escalate") == "allow"
    assert apply_mode_to_decision("allow") == "allow"
    assert not is_enforcing()

    globals()["CURRENT_MODE"] = "enforce"
    assert apply_mode_to_decision("deny") == "deny"
    assert is_enforcing()

    globals()["CURRENT_MODE"] = original_mode
    print("gate_mode OK")
