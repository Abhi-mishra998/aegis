"""
MITRE ATT&CK TA0001 — Initial Access.

Aegis-specific scope: an LLM agent's "initial access" surface is the
attacker's injected tool call. Right now the only signal we own here is
SQL-injection-shaped query input. (Web-layer injection vectors —
template injection, command injection — are out of scope for an
agent-tool-call platform and would belong in a WAF.)
"""
from __future__ import annotations


def detect(c: dict) -> list[str]:
    findings: list[str] = []
    if c.get("sql_injection_detected"):
        findings.append("sql_injection_detected")
    return findings
