"""Canonical finding vocabulary for the ACP decision response.

Before this module, every classifier flag from upstream (inference, behavior,
anomaly, cost, cross_agent) was appended to `Decision.reasons` whether or not
the underlying score crossed the trigger threshold. Customers saw entries
like ``"data_exfiltration_risk"`` on clean reads of public files and
reasonably concluded the detection signal was unreliable.

This module defines the authoritative list of strings that are allowed in a
production decision's `findings` array. Any string outside this set is a bug
and must be caught by `validate_findings()` — enforced in unit tests, not
just at runtime, so a regression fails CI before it ships.

Diagnostic information (every classifier that ran, with its score and
threshold) lives in `Decision.signals_evaluated`. That field is the answer
to "did we evaluate behaviour?". `findings` is the answer to "what did we
actually conclude?".

See docs/risk_reasons.md for the operator-facing reference.
"""

from __future__ import annotations

from collections.abc import Iterable

# --------------------------------------------------------------------------- #
# Canonical vocabulary                                                        #
# --------------------------------------------------------------------------- #

# Engine-emitted (signal-threshold triggered)
FINDING_PROMPT_INJECTION_DETECTED   = "prompt_injection_detected"
FINDING_DATA_EXFILTRATION_DETECTED  = "data_exfiltration_detected"
FINDING_ANOMALOUS_BEHAVIOR_DETECTED = "anomalous_behavior_detected"
FINDING_AUTONOMY_MAX_COST_EXCEEDED  = "autonomy_max_cost_exceeded"

# Policy-side (OPA / autonomy)
FINDING_POLICY_DENY                    = "policy_deny"
FINDING_AUTONOMY_DENIED_ACTION         = "autonomy_denied_action"
FINDING_AUTONOMY_MAX_RUNTIME_EXCEEDED  = "autonomy_max_runtime_exceeded"

# Middleware / inference proxy / autonomy
FINDING_PATH_TRAVERSAL_DETECTED        = "path_traversal_detected"
FINDING_INFERENCE_PROXY_BLOCKED        = "inference_proxy_blocked"
FINDING_APPROVAL_REQUIRED              = "approval_required"

# Sprint 1.1 degraded-mode (behavior firewall unreachable)
FINDING_BEHAVIOR_DEGRADED_BLOCKED          = "behavior_degraded_blocked"
FINDING_BEHAVIOR_DEGRADED_FAIL_OPEN        = "behavior_degraded_fail_open"
FINDING_BEHAVIOR_DEGRADED_LOW_RISK_ALLOWED = "behavior_degraded_low_risk_allowed"


CANONICAL_FINDINGS: frozenset[str] = frozenset({
    FINDING_POLICY_DENY,
    FINDING_AUTONOMY_DENIED_ACTION,
    FINDING_AUTONOMY_MAX_COST_EXCEEDED,
    FINDING_AUTONOMY_MAX_RUNTIME_EXCEEDED,
    FINDING_PATH_TRAVERSAL_DETECTED,
    FINDING_PROMPT_INJECTION_DETECTED,
    FINDING_DATA_EXFILTRATION_DETECTED,
    FINDING_ANOMALOUS_BEHAVIOR_DETECTED,
    FINDING_APPROVAL_REQUIRED,
    FINDING_BEHAVIOR_DEGRADED_BLOCKED,
    FINDING_BEHAVIOR_DEGRADED_FAIL_OPEN,
    FINDING_BEHAVIOR_DEGRADED_LOW_RISK_ALLOWED,
    FINDING_INFERENCE_PROXY_BLOCKED,
})


# --------------------------------------------------------------------------- #
# Signal → threshold + canonical finding mapping                              #
# --------------------------------------------------------------------------- #
#
# Single source of truth for which signal triggers which finding at which
# threshold. The DecisionEngine reads from here when building both
# `signals_evaluated` (every signal, score, threshold, triggered bit) and
# `findings` (only the canonical names for triggered signals).

SIGNAL_THRESHOLDS: dict[str, float] = {
    "inference":   0.60,
    "behavior":    0.60,
    "anomaly":     0.70,
    "cost":        0.50,
    "cross_agent": 0.40,
}

# Multiple signals can map to the same finding — at the customer's
# operational level, "anomalous behavior" is one concept regardless of
# which classifier raised it. The engine dedupes when emitting.
SIGNAL_TO_FINDING: dict[str, str] = {
    "inference":   FINDING_PROMPT_INJECTION_DETECTED,
    "behavior":    FINDING_ANOMALOUS_BEHAVIOR_DETECTED,
    "anomaly":     FINDING_ANOMALOUS_BEHAVIOR_DETECTED,
    "cost":        FINDING_AUTONOMY_MAX_COST_EXCEEDED,
    "cross_agent": FINDING_ANOMALOUS_BEHAVIOR_DETECTED,
}


# --------------------------------------------------------------------------- #
# Validation                                                                  #
# --------------------------------------------------------------------------- #


def validate_findings(findings: Iterable[str]) -> list[str]:
    """Return the input as a list IFF every element is canonical.

    Raises ValueError on the first non-canonical entry. Empty lists pass
    through. Unit tests in `tests/test_findings_vocabulary.py` exercise
    this on the DecisionEngine's output across a representative
    cross-product of contexts, so any future regression that pushes a
    non-canonical string into findings fails CI immediately.
    """
    out: list[str] = []
    for f in findings or []:
        if not isinstance(f, str):
            raise ValueError(f"finding must be a string, got {type(f).__name__}")
        if f not in CANONICAL_FINDINGS:
            raise ValueError(
                f"non-canonical finding {f!r}; allowed values are "
                f"{sorted(CANONICAL_FINDINGS)}"
            )
        out.append(f)
    return out
