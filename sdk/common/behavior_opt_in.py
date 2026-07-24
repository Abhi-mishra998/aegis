"""ATF v3.2 §Phase 3 item 4 — Behavioral fingerprinting as an OPT-IN add-on.

ADR-002 spirit: numeric behavioral scores are advisory forever.
Existing `services/behavior/` is the code; this module is the tenant
feature flag + guardrail that keeps it OFF by default and prevents
learned outputs from being consumed as an authoritative Gate input.

Flag surface:

    ACP_BEHAVIOR_FINGERPRINTING_TENANTS   comma-separated tenant IDs
                                          for whom the feature is enabled.
    ACP_BEHAVIOR_FINGERPRINTING_MODE      "advisory" (default) | "off"
                                          — never "authoritative".

A tenant that isn't in the enable list gets `off` regardless of mode.
"""
from __future__ import annotations

import os
from typing import Literal

BehaviorMode = Literal["off", "advisory"]


def _read_enabled_set() -> frozenset[str]:
    raw = os.getenv("ACP_BEHAVIOR_FINGERPRINTING_TENANTS", "")
    return frozenset(t.strip() for t in raw.split(",") if t.strip())


def _read_mode() -> BehaviorMode:
    raw = os.getenv("ACP_BEHAVIOR_FINGERPRINTING_MODE", "advisory").lower()
    return "advisory" if raw == "advisory" else "off"


def get_mode_for(tenant_id: str) -> BehaviorMode:
    """Effective mode for a given tenant. Off unless explicitly enabled."""
    if tenant_id not in _read_enabled_set():
        return "off"
    return _read_mode()


def is_advisory_only(mode: BehaviorMode) -> bool:
    """§9.2 hard invariant: behavior signal is NEVER authoritative.

    Callers use this to gate consumption: if `is_advisory_only(mode)`,
    the signal is a display/log candidate, never a decision-driver.
    """
    return mode == "advisory"


def gate_score_consumption(
    tenant_id: str,
    proposed_use: Literal["display", "gate_input"],
) -> bool:
    """Returns True iff the caller may consume the behavior score for
    the proposed purpose. `gate_input` is ALWAYS refused — a hard
    guard that survives even a config flip to a value we don't accept.
    """
    if proposed_use == "gate_input":
        return False  # ADR-002 — never authoritative
    mode = get_mode_for(tenant_id)
    return mode == "advisory" and proposed_use == "display"


if __name__ == "__main__":
    # Clean env for tests
    for k in (
        "ACP_BEHAVIOR_FINGERPRINTING_TENANTS",
        "ACP_BEHAVIOR_FINGERPRINTING_MODE",
    ):
        os.environ.pop(k, None)

    # Default: off for every tenant
    assert get_mode_for("acme") == "off"

    # Enabled + default mode = advisory
    os.environ["ACP_BEHAVIOR_FINGERPRINTING_TENANTS"] = "acme,beta"
    assert get_mode_for("acme") == "advisory"
    assert get_mode_for("beta") == "advisory"
    assert get_mode_for("charlie") == "off"

    # Explicit off overrides
    os.environ["ACP_BEHAVIOR_FINGERPRINTING_MODE"] = "off"
    assert get_mode_for("acme") == "off"

    # Explicit advisory
    os.environ["ACP_BEHAVIOR_FINGERPRINTING_MODE"] = "advisory"
    assert get_mode_for("acme") == "advisory"

    # `gate_input` ALWAYS refused, even for enabled tenants
    assert not gate_score_consumption("acme", "gate_input")
    assert gate_score_consumption("acme", "display")
    assert not gate_score_consumption("charlie", "display")

    # Even if someone injects a bogus mode, `gate_input` stays refused
    os.environ["ACP_BEHAVIOR_FINGERPRINTING_MODE"] = "authoritative"  # invalid
    assert get_mode_for("acme") == "off"  # invalid falls back to off
    assert not gate_score_consumption("acme", "gate_input")

    # Clean up
    for k in (
        "ACP_BEHAVIOR_FINGERPRINTING_TENANTS",
        "ACP_BEHAVIOR_FINGERPRINTING_MODE",
    ):
        os.environ.pop(k, None)

    print("behavior_opt_in OK")
