"""
ARCH-8 2026-06-15 — Security Engine surface (split from Governance).

The security engine owns adversarial / destructive intent detection:

    * Exfiltration         (external_pii_exfil, known_exfil_destination)
    * Privilege escalation (cred_path, sensitive_path)
    * Destructive actions  (destructive_shell_command, destructive_sql_ddl,
                            k8s_destruction_prod, iac_destruction_prod)
    * Attack chains        (attack_chain_match, runaway loop)
    * Money out the door   (money_transfer_above_hard_cap)

Findings here are scored 50-100. A single SEC finding can carry a DENY
or QUARANTINE tier outcome.

This is a thin facade over the existing canonical + evaluate_full
pipeline — it doesn't duplicate logic. The point of splitting is to give
operators a *single dashboard scope* for adversarial events distinct
from governance/cost/approval traffic. Two engines, two scoring
rationales, two on-call rotations.
"""
from __future__ import annotations

from services.policy.local_action_semantics import evaluate_full

_SECURITY_FINDING_PREFIXES = (
    "external_pii_exfil",
    "known_exfil_destination",
    "system_sensitive_path",
    "cloud_credential_path",
    "ssh_credential_path",
    "destructive_shell_command",
    "destructive_sql_ddl",
    "destructive_sql_dml_no_predicate",
    "sql_injection_detected",
    "k8s_destruction_prod",
    "iac_destruction_prod",
    "money_transfer_above_hard_cap",
    "bulk_pii_egress_dump",
    "attack_chain:",
    "agent_quarantined",
)


def is_security_finding(finding: str) -> bool:
    return any(finding.startswith(p) or finding == p for p in _SECURITY_FINDING_PREFIXES)


def evaluate_security(arguments: dict | None, risk_level: str = "low") -> dict:
    """Run the action through the canonical → policy pipeline and return ONLY
    the security-relevant slice.

    Returns the same shape as ``evaluate_full`` but with findings filtered
    to the SEC vocabulary and policy_id prefix preserved.
    """
    full = evaluate_full(arguments, risk_level)
    sec_findings = [f for f in (full.get("findings") or []) if is_security_finding(f)]
    return {
        "tier":       full["tier"] if sec_findings else "allow",
        "policy_id":  full["policy_id"] if sec_findings else "",
        "reason":     full["reason"] if sec_findings else "",
        "findings":   sec_findings,
        "risk_score": full["risk_score"] if sec_findings else 0,
        "explanation": full["explanation"] if sec_findings else "",
        "engine":     "security",
    }
