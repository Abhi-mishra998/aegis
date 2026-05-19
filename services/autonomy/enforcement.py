"""
Pure-function autonomy contract evaluator.

Used by the gateway middleware in-process AND by the autonomy service's
/check endpoint. Keeping the rule engine pure means the same code path
makes the call regardless of whether the gateway is enforcing or an
operator is dry-running a contract in the UI.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass
class ContractView:
    """Lightweight view of an AutonomyContract for evaluation."""
    id: object
    version: int
    enabled: bool
    allowed_actions: list[str]
    denied_actions: list[str]
    approval_required: list[str]
    max_runtime_seconds: int | None
    max_tool_calls: int | None
    max_cost_usd: float | None
    max_autonomy_level: int
    escalation_triggers: list[str]


def evaluate(
    contract: ContractView | None,
    action: str,
    *,
    cost_estimate_usd: float | None = None,
    runtime_estimate_seconds: int | None = None,
    tool_calls_so_far: int | None = None,
) -> dict:
    """
    Return:
      {
        "allowed":            bool,
        "requires_approval":  bool,
        "violated_rules":     [str],
        "reason":             str | None,
      }
    A disabled or missing contract evaluates to allowed=True (no contract == no enforcement).
    Operators that want fail-closed-by-default should declare a tenant-wide deny-all contract.
    """
    if contract is None or not contract.enabled:
        return {"allowed": True, "requires_approval": False, "violated_rules": [], "reason": "no_contract"}

    violated: list[str] = []
    requires_approval = False

    action_l = (action or "").lower()
    # 1. Explicit deny dominates everything.
    if _matches_any(action_l, contract.denied_actions):
        violated.append("denied_action")
    # 2. If allowed list is present, action must be in it (unless wildcard).
    if contract.allowed_actions and not _matches_any(action_l, contract.allowed_actions):
        violated.append("not_in_allowed_actions")
    # 3. Approval-required rules don't fail — they ask.
    if _matches_any(action_l, contract.approval_required):
        requires_approval = True

    # 4. Ceilings
    if (
        contract.max_cost_usd is not None and cost_estimate_usd is not None
        and cost_estimate_usd > contract.max_cost_usd
    ):
        violated.append("max_cost_usd")
    if (
        contract.max_runtime_seconds is not None and runtime_estimate_seconds is not None
        and runtime_estimate_seconds > contract.max_runtime_seconds
    ):
        violated.append("max_runtime_seconds")
    if (
        contract.max_tool_calls is not None and tool_calls_so_far is not None
        and tool_calls_so_far >= contract.max_tool_calls
    ):
        violated.append("max_tool_calls")

    allowed = len(violated) == 0
    reason: str | None = None
    if not allowed:
        reason = "violated: " + ", ".join(violated)
    elif requires_approval:
        reason = "approval_required"

    return {
        "allowed": allowed,
        "requires_approval": requires_approval,
        "violated_rules": violated,
        "reason": reason,
    }


def _matches_any(action: str, patterns: Iterable[str]) -> bool:
    for p in patterns or []:
        if not p:
            continue
        p_norm = p.strip().lower()
        if p_norm == "*" or p_norm == action:
            return True
        # Suffix wildcard: prefix.* matches prefix.anything
        if p_norm.endswith(".*") and action.startswith(p_norm[:-2] + "."):
            return True
    return False
