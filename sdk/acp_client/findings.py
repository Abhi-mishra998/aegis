"""SDK-side mirror of the canonical decision finding vocabulary.

Lets agent code branch on findings without string literals:

    from acp_client import FINDINGS

    result = acp.execute(agent_id=..., tool="read_file", payload={...})
    if FINDINGS.PROMPT_INJECTION_DETECTED in result.get("findings", []):
        ...

The set of valid strings is the same one enforced server-side in
`services/decision/findings.py`. If the server adds a new finding, this
file must update in lockstep — there's a unit test that flags drift.

See docs/risk_reasons.md for the operator-facing reference.
"""

from __future__ import annotations

from types import SimpleNamespace

# Single source of truth, mirrored from services/decision/findings.py.
# Keeping this as a frozenset (not an enum) so customer code can compare
# against the raw string returned by the gateway without a coercion step.
CANONICAL_FINDINGS: frozenset[str] = frozenset({
    "policy_deny",
    "autonomy_denied_action",
    "autonomy_max_cost_exceeded",
    "autonomy_max_runtime_exceeded",
    "path_traversal_detected",
    "prompt_injection_detected",
    "sql_injection_detected",
    "data_exfiltration_detected",
    "anomalous_behavior_detected",
    "approval_required",
    "behavior_degraded_blocked",
    "behavior_degraded_fail_open",
    "behavior_degraded_low_risk_allowed",
    "inference_proxy_blocked",
})


# Attribute-style accessor so customer code reads cleanly:
#     if FINDINGS.PATH_TRAVERSAL_DETECTED in result["findings"]:
FINDINGS = SimpleNamespace(
    POLICY_DENY                          = "policy_deny",
    AUTONOMY_DENIED_ACTION               = "autonomy_denied_action",
    AUTONOMY_MAX_COST_EXCEEDED           = "autonomy_max_cost_exceeded",
    AUTONOMY_MAX_RUNTIME_EXCEEDED        = "autonomy_max_runtime_exceeded",
    PATH_TRAVERSAL_DETECTED              = "path_traversal_detected",
    PROMPT_INJECTION_DETECTED            = "prompt_injection_detected",
    SQL_INJECTION_DETECTED               = "sql_injection_detected",
    DATA_EXFILTRATION_DETECTED           = "data_exfiltration_detected",
    ANOMALOUS_BEHAVIOR_DETECTED          = "anomalous_behavior_detected",
    APPROVAL_REQUIRED                    = "approval_required",
    BEHAVIOR_DEGRADED_BLOCKED            = "behavior_degraded_blocked",
    BEHAVIOR_DEGRADED_FAIL_OPEN          = "behavior_degraded_fail_open",
    BEHAVIOR_DEGRADED_LOW_RISK_ALLOWED   = "behavior_degraded_low_risk_allowed",
    INFERENCE_PROXY_BLOCKED              = "inference_proxy_blocked",
)
