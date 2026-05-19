"""Pure helpers for the behavior-firewall consult performed by the decision
service. Split out from `services/decision/main.py` so the policy/classification
branches are unit-testable without spinning up FastAPI, httpx, or Redis.

The public surface is intentionally narrow:

    classify_behavior_result(raw_result, exc=None) -> (service_status, behavior_data)
        Map an `asyncio.gather(..., return_exceptions=True)` slot to a precise
        outcome label and a populated behavior_data dict. Never raises.

    is_high_risk(tool, inference_risk, inference_flags) -> bool
        Pure predicate the degraded-mode policy uses to decide whether a
        low-information request should be blocked.

    apply_degraded_mode_policy(policy, ...) -> DegradedDecision
        Given a non-ok behavior consult, materialize a tenant-aware verdict
        (short-circuit Decision and/or extra audit row) per the per-tenant
        `degraded_mode_policy` setting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from services.decision.schemas import (  # noqa: I001 (kept after stdlib/third-party split for readability)
    Decision,
    ExecutionAction,
)

# --------------------------------------------------------------------------- #
# Constants                                                                   #
# --------------------------------------------------------------------------- #

DEFAULT_DEGRADED_MODE_POLICY = "block_high_risk"

# Tool names that are inherently capable of mutating state, exfiltrating data,
# or executing untrusted code. When the behavior firewall is unreachable, we
# refuse to authorize these without an explicit "allow_with_audit" opt-in.
HIGH_RISK_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "exec",
        "execute",
        "shell",
        "shell_exec",
        "run_command",
        "system",
        "query",                # raw SQL
        "write_file",
        "delete_file",
        "delete",
        "rm",
        "admin",
        "admin_action",
        "kill",
        "credentials_read",
        "secrets_read",
        "transfer_funds",
        "wire_transfer",
    }
)

# Inference risk above this floor classifies the request as high-risk
# regardless of tool name. Anything the inference layer was already nervous
# about should not get a free pass during a behavior brownout.
HIGH_RISK_INFERENCE_RISK_FLOOR = 0.50

# Inference flags that indicate elevated risk (independent of tool/score).
HIGH_RISK_INFERENCE_FLAGS: frozenset[str] = frozenset(
    {
        "SENSITIVE_PATH_DETECTED",
        "SQL_INJECTION_PATTERN",
        "PROMPT_INJECTION",
        "DATA_EXFIL_PATTERN",
        "CREDENTIAL_ACCESS",
    }
)

_FAIL_SAFE_BEHAVIOR_DATA: dict[str, Any] = {
    "behavior_risk":    0.5,
    "anomaly_score":    0.5,
    "cross_agent_risk": 0.0,
    "confidence":       0.5,
    "flags":            ["behavior_service_unavailable"],
}

_OK_BEHAVIOR_DEFAULTS: dict[str, Any] = {
    "behavior_risk":    0.0,
    "anomaly_score":    0.0,
    "cross_agent_risk": 0.0,
    "confidence":       1.0,
    "flags":            [],
}


# --------------------------------------------------------------------------- #
# Classification                                                              #
# --------------------------------------------------------------------------- #


def classify_behavior_result(
    raw_result: Any,
    *,
    fanout_timed_out: bool = False,
) -> tuple[str, dict[str, Any], float | None]:
    """Translate one slot of `asyncio.gather(..., return_exceptions=True)` into
    (service_status, behavior_data, returned_score).

    service_status is one of: ``ok`` | ``timeout`` | ``error``.

    On any non-ok path the returned behavior_data is the fail-closed default
    (risk floor 0.5, flag ``behavior_service_unavailable``). On ok, the data
    is taken from the upstream response with strict shape coercion.
    """
    if fanout_timed_out or isinstance(
        raw_result, (TimeoutError, httpx.TimeoutException)
    ):
        return "timeout", dict(_FAIL_SAFE_BEHAVIOR_DATA), None

    # Sprint 2: circuit-breaker fail-fast distinguishes from a generic
    # connection error so the consult can be marked `skipped` (not
    # `error`). The ResilientClient raises httpx.ConnectError with the
    # specific text "Circuit breaker is OPEN" — pattern-match on it so
    # the breaker-open path lands in service_status="skipped" instead
    # of conflating with a real network error.
    if isinstance(raw_result, httpx.ConnectError) and "circuit breaker is open" in str(raw_result).lower():
        return "skipped", dict(_FAIL_SAFE_BEHAVIOR_DATA), None

    if isinstance(raw_result, Exception):
        return "error", dict(_FAIL_SAFE_BEHAVIOR_DATA), None

    if isinstance(raw_result, httpx.Response):
        if raw_result.status_code == 200:
            data = dict(_OK_BEHAVIOR_DEFAULTS)
            try:
                upstream = raw_result.json().get("data", {}) or {}
            except ValueError:
                # 200 with un-parseable body — treat as upstream error.
                return "error", dict(_FAIL_SAFE_BEHAVIOR_DATA), None
            data.update(upstream)
            try:
                returned_score = float(data.get("behavior_risk", 0.0))
            except (TypeError, ValueError):
                returned_score = 0.0
            data["behavior_risk"] = returned_score
            return "ok", data, returned_score
        return "error", dict(_FAIL_SAFE_BEHAVIOR_DATA), None

    # Unknown shape (None, dict-without-json, etc.) — be safe.
    return "error", dict(_FAIL_SAFE_BEHAVIOR_DATA), None


# --------------------------------------------------------------------------- #
# High-risk classifier                                                        #
# --------------------------------------------------------------------------- #


def is_high_risk(
    tool: str,
    inference_risk: float,
    inference_flags: list[str] | tuple[str, ...] | None,
) -> bool:
    """Pure predicate. High-risk if ANY of:
      * tool name in HIGH_RISK_TOOL_NAMES (case-insensitive)
      * inference_risk >= HIGH_RISK_INFERENCE_RISK_FLOOR
      * any flag in HIGH_RISK_INFERENCE_FLAGS
    """
    if (tool or "").strip().lower() in HIGH_RISK_TOOL_NAMES:
        return True
    try:
        if float(inference_risk or 0.0) >= HIGH_RISK_INFERENCE_RISK_FLOOR:
            return True
    except (TypeError, ValueError):
        pass
    if inference_flags:
        for f in inference_flags:
            if str(f) in HIGH_RISK_INFERENCE_FLAGS:
                return True
    return False


# --------------------------------------------------------------------------- #
# Degraded-mode policy                                                        #
# --------------------------------------------------------------------------- #


@dataclass
class DegradedDecision:
    """Outcome of applying the tenant's degraded_mode_policy to a non-ok
    behavior consult.

    * `short_circuit` — if set, the decision service should skip the engine
      and return this Decision directly.
    * `behavior_data` — updated behavior_data dict (may carry extra flags
      such as ``behavior_degraded_low_risk_allowed`` / ``behavior_degraded_fail_open``).
    * `policy_applied` — human-readable label persisted in the audit row.
    * `extra_reasons` — appended to the engine's reasons on the fall-through
      paths.
    * `emit_fail_open_audit` — when True the caller must emit an extra
      ``action="degraded_mode_fail_open"`` audit row (allow_with_audit only).
    """

    short_circuit: Decision | None = None
    behavior_data: dict[str, Any] = field(default_factory=dict)
    policy_applied: str = ""
    extra_reasons: list[str] = field(default_factory=list)
    emit_fail_open_audit: bool = False


def apply_degraded_mode_policy(
    policy: str | None,
    *,
    tool: str,
    inference_risk: float,
    inference_flags: list[str] | tuple[str, ...] | None,
    behavior_data: dict[str, Any],
    service_status: str,
) -> DegradedDecision:
    """Materialize the tenant's degraded-mode posture.

    Pre: service_status != "ok" (caller has already verified that the behavior
    firewall did not respond cleanly). Calling this on a healthy consult is a
    programming error.
    """
    if service_status == "ok":
        # Defensive: behavior was actually healthy, no policy to apply.
        return DegradedDecision(
            short_circuit=None,
            behavior_data=dict(behavior_data),
            policy_applied="behavior_consulted",
        )

    chosen = (policy or DEFAULT_DEGRADED_MODE_POLICY).strip().lower()
    if chosen not in {"block_high_risk", "block_all", "allow_with_audit"}:
        chosen = DEFAULT_DEGRADED_MODE_POLICY

    if chosen == "block_all":
        return DegradedDecision(
            short_circuit=Decision(
                action=ExecutionAction.DENY,
                risk=1.0,
                confidence=0.5,
                findings=["behavior_degraded_blocked"],
                reasons=["behavior_degraded_blocked"],
                signals={"behavior_service_status": _status_to_signal(service_status)},
                metadata={
                    "degraded_mode_policy":  chosen,
                    "behavior_service_status": service_status,
                },
            ),
            behavior_data=dict(behavior_data),
            policy_applied=chosen,
        )

    if chosen == "block_high_risk":
        if is_high_risk(tool, inference_risk, inference_flags):
            return DegradedDecision(
                short_circuit=Decision(
                    action=ExecutionAction.DENY,
                    risk=1.0,
                    confidence=0.5,
                    reasons=["behavior_degraded_blocked"],
                    signals={"behavior_service_status": _status_to_signal(service_status)},
                    metadata={
                        "degraded_mode_policy":  chosen,
                        "behavior_service_status": service_status,
                    },
                ),
                behavior_data=dict(behavior_data),
                policy_applied=chosen,
            )
        # Low-risk allowed but marked: callers append the reason and engine still
        # weighs the 0.5 behavior floor in the final risk.
        new_data = dict(behavior_data)
        flags = list(new_data.get("flags", []))
        if "behavior_degraded_low_risk_allowed" not in flags:
            flags.append("behavior_degraded_low_risk_allowed")
        new_data["flags"] = flags
        return DegradedDecision(
            short_circuit=None,
            behavior_data=new_data,
            policy_applied=chosen,
            extra_reasons=["behavior_degraded_low_risk_allowed"],
        )

    # allow_with_audit — preserve original fail-open posture, but make it loud.
    new_data = dict(behavior_data)
    flags = list(new_data.get("flags", []))
    if "behavior_degraded_fail_open" not in flags:
        flags.append("behavior_degraded_fail_open")
    new_data["flags"] = flags
    return DegradedDecision(
        short_circuit=None,
        behavior_data=new_data,
        policy_applied=chosen,
        extra_reasons=["behavior_degraded_fail_open"],
        emit_fail_open_audit=True,
    )


def _status_to_signal(service_status: str) -> float:
    # Floor signal used by the engine for any downstream weighting that cares.
    return {"ok": 0.0, "timeout": 0.5, "error": 0.5, "skipped": 0.0}.get(
        service_status, 0.5
    )
