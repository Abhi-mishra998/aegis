"""
MITRE ATT&CK TA0004 — Privilege Escalation.

Two surfaces:
  * SQL identity-table writes (INSERT/UPDATE/DELETE on users/roles/etc.)
    — `privilege_escalation_attempt` if the write elevates a role to
    admin/superuser/root, otherwise the softer `identity_table_write`.
  * Privileged HTTP endpoints (password reset, IAM mutations, role
    grants) → `privilege_url_access`.

Mutual exclusion: `privilege_escalation_attempt` and `identity_table_write`
are tiered — the strong signal preempts the weak one so the SOC sees the
specific finding (matches the registry's `deny` vs `escalate` defaults).
"""
from __future__ import annotations


def detect(c: dict) -> list[str]:
    findings: list[str] = []
    if c.get("is_privilege_escalation"):
        findings.append("privilege_escalation_attempt")
    elif c.get("is_identity_table_write"):
        findings.append("identity_table_write")
    if c.get("is_privilege_url"):
        findings.append("privilege_url_access")
    return findings
