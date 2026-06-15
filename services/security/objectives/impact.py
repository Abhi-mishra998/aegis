"""
MITRE ATT&CK TA0040 — Impact.

The destructive payload at the end of the kill chain (or just on its own
from a confused agent). Five domains:

  * SQL destruction — DDL (DROP/TRUNCATE/ALTER), DML without predicate.
  * Shell destruction — rm -rf, dd, mkfs, fork bomb, kubectl drain,
    sudo-rooted shell.
  * K8s — `kubectl delete / drain` on prod-namespace markers (CRITICAL)
    vs non-prod (HIGH escalate).
  * IaC — terraform/pulumi/cdk destroy on prod-path (CRITICAL) vs non-
    prod (HIGH escalate).
  * Money movement — wires ≥ $10M (CRITICAL deny) vs ≥ $200K to
    external/offshore/unknown (HIGH escalate).

All the actual extraction (e.g. matching `kubectl delete` against the
prod-namespace markers) lives in canonical.py's extractors. This module
just maps the canonical boolean / numeric fields to the registered
signal IDs.
"""
from __future__ import annotations


_WIRE_HARD_DENY_USD       = 10_000_000
_WIRE_ESCALATE_EXTERNAL_USD = 200_000
_EXTERNAL_DEST_KINDS = ("external", "offshore", "unknown")


def detect(c: dict) -> list[str]:
    findings: list[str] = []

    # ---- SQL destruction ----
    if c.get("is_destructive_ddl"):
        findings.append("destructive_sql_ddl")
    if c.get("is_destructive_dml_no_predicate"):
        findings.append("destructive_sql_dml_no_predicate")

    # ---- Shell destruction ----
    if c.get("is_destructive_shell"):
        findings.append("destructive_shell_command")

    # ---- K8s ----
    k8s_verb = c.get("k8s_verb")
    if k8s_verb in ("delete", "drain"):
        if c.get("k8s_targets_prod"):
            findings.append("k8s_destruction_prod")
        else:
            findings.append("k8s_destruction")

    # ---- IaC ----
    if c.get("iac_tool") and c.get("iac_action"):
        if c.get("iac_targets_prod"):
            findings.append("iac_destruction_prod")
        else:
            findings.append("iac_destruction")

    # ---- Money movement ----
    amt = int(c.get("amount_usd") or 0)
    dk  = c.get("destination_kind") or "unknown"
    if amt >= _WIRE_HARD_DENY_USD:
        findings.append("money_transfer_above_hard_cap")
    elif amt >= _WIRE_ESCALATE_EXTERNAL_USD and dk in _EXTERNAL_DEST_KINDS:
        findings.append("money_transfer_external")

    return findings
