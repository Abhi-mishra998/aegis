"""
MITRE ATT&CK TA0001 — Initial Access.

Aegis-specific scope: an LLM agent's "initial access" surface is the
attacker's injected tool call. We catch:

  * SQL-injection-shaped query input (T1190).
  * SSRF (Server-Side Request Forgery) family — the URL fetcher pointed
    at a file://, cloud-metadata endpoint, or RFC1918 host. Each gets
    its own MITRE technique so the SOC can route them distinctly.

Web-layer injection vectors (template injection, command injection)
remain out of scope for an agent-tool-call platform; those belong in a
WAF.
"""
from __future__ import annotations


def detect(c: dict) -> list[str]:
    findings: list[str] = []
    if c.get("sql_injection_detected"):
        findings.append("sql_injection_detected")
    # P0-1 2026-06-21 — SSRF triad. Flags are mutually compatible (a URL
    # could in principle be both file:// and internal-network); emit each
    # independently so the response carries the most precise MITRE map.
    if c.get("is_ssrf_local_file"):
        findings.append("ssrf_local_file")
    if c.get("is_ssrf_cloud_metadata"):
        findings.append("ssrf_cloud_metadata")
    if c.get("is_ssrf_internal_network"):
        findings.append("ssrf_internal_network")
    return findings
