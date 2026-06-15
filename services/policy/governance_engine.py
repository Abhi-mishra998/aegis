"""
ARCH-8 2026-06-15 — Governance Engine surface (split from Security).

The governance engine owns business-policy approvals & compliance:

    * Cost limits          (inference_cost_cap_exceeded)
    * Approval workflows   (wire_external_high_value_approval_required,
                            iac_destruction_command, k8s_prod_namespace_destruction)
    * Compliance findings  (bulk_pii_egress_above_threshold,
                            slow_exfil_cumulative_threshold_breached)
    * Human review queue   (anything that lands in the Approval Inbox)

Findings here are scored 20-69. Outcomes are ALLOW / MONITOR / ESCALATE.
A pure-governance escalation should NEVER fall into DENY/QUARANTINE; that
is the security engine's domain.
"""
from __future__ import annotations

from services.policy.local_action_semantics import evaluate_full

_GOVERNANCE_FINDING_PREFIXES = (
    "money_transfer_external",
    "bulk_pii_egress_above_threshold",
    "slow_exfil_cumulative_threshold_breached",
    "iac_destruction_command",
    "k8s_prod_namespace_destruction",
    "behavior_baseline:",
    "behavior_anomaly:",
    "schema_recon",
    "compression_observed",
    "external_get",
    "inference_cost_cap_exceeded",
)


def is_governance_finding(finding: str) -> bool:
    return any(finding.startswith(p) or finding == p for p in _GOVERNANCE_FINDING_PREFIXES)


def evaluate_governance(arguments: dict | None, risk_level: str = "low") -> dict:
    """Run through canonical → policy and return ONLY the governance slice."""
    full = evaluate_full(arguments, risk_level)
    gov_findings = [f for f in (full.get("findings") or []) if is_governance_finding(f)]
    # Governance never DENY/QUARANTINE; clamp.
    tier = full["tier"]
    if tier in ("deny", "quarantine") and not any(
        f.startswith(("money_transfer_external", "iac_destruction_command", "k8s_prod_namespace"))
        for f in gov_findings
    ):
        tier = "allow"
    return {
        "tier":       tier if gov_findings else "allow",
        "policy_id":  full["policy_id"] if gov_findings else "",
        "reason":     full["reason"] if gov_findings else "",
        "findings":   gov_findings,
        "risk_score": full["risk_score"] if gov_findings else 0,
        "explanation": full["explanation"] if gov_findings else "",
        "engine":     "governance",
    }
