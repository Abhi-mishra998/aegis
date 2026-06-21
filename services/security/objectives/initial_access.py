"""
MITRE ATT&CK TA0001 — Initial Access.

Aegis-specific scope: an LLM agent's "initial access" surface is the
attacker's injected tool call. Signals:

  * `sql_injection_detected` — UNION / stacked DROP / tautology in a SQL
    query parameter.
  * `ssrf_local_file` — http tool with url=file:///… (or gopher://, ftp://).
    Reads filesystem via the HTTP fetcher; classic SSRF→LFI pivot.
  * `ssrf_cloud_metadata` — http tool with url=169.254.169.254 /
    metadata.google.internal / metadata.azure.com. AWS IMDS exfil is the
    #1 cited "agent gone rogue" scenario in F500 risk reviews.
  * `ssrf_internal_network` — http tool with url targeting RFC1918,
    loopback, link-local, *.internal, *.local, *.corp. Pivoting from the
    agent's network into the customer's private subnet.

All three SSRF findings carry deny-tier scores (95 / 95 / 80) so they
hard-deny on their own through the canonical inherent-risk path.

P0-1 fix 2026-06-21.
"""
from __future__ import annotations


def detect(c: dict) -> list[str]:
    findings: list[str] = []
    if c.get("sql_injection_detected"):
        findings.append("sql_injection_detected")
    if c.get("is_ssrf_local_file"):
        findings.append("ssrf_local_file")
    if c.get("is_ssrf_cloud_metadata"):
        findings.append("ssrf_cloud_metadata")
    if c.get("is_ssrf_internal_network"):
        findings.append("ssrf_internal_network")
    return findings
