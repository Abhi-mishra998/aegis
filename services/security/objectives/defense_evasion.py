"""
MITRE ATT&CK TA0005 — Defense Evasion.

Sprint 2's anti-tamper home. Aegis depends on its own control plane
(audit_logs, policies, kill_switches, decisions, transparency_roots, …)
for enforcement; ANY agent-issued write against those tables = adversarial
intent regardless of the WHERE-clause shape.

  * DDL (DROP/TRUNCATE/ALTER) against a control-plane table → quarantine
    via `aegis_control_plane_destructive_ddl` (T1485 Data Destruction).
  * DML (INSERT/UPDATE/DELETE) against a control-plane table → deny via
    `aegis_control_plane_write` (T1070.002 Clear Logs).

Both signals win over the weaker generic destructive_sql_ddl /
destructive_sql_dml_no_predicate / identity_table_write rules; the LAS
evaluator places them at the top of the deny ladder so the SOC sees the
specific tamper finding rather than a coarser misclassification.
"""
from __future__ import annotations


def detect(c: dict) -> list[str]:
    findings: list[str] = []
    if c.get("is_aegis_control_plane_destructive_ddl"):
        findings.append("aegis_control_plane_destructive_ddl")
    if c.get("is_aegis_control_plane_write"):
        findings.append("aegis_control_plane_write")
    return findings
