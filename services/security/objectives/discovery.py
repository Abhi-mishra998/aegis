"""
MITRE ATT&CK TA0007 — Discovery.

Recon-shaped actions the agent surface emits. These are MONITOR-tier on
their own (`schema_recon`, `external_get`); they only become attack
signal once the risk_pipeline sees them accumulate alongside higher-tier
findings.

The `behavior_baseline_drift` signal also lives under Discovery in the
registry but is emitted by services/behavior/_baseline.py — outside the
canonical per-call detector — so it's not handled here. Sprint 5 (IAG)
may move the per-agent surface into this module.
"""
from __future__ import annotations


def detect(c: dict) -> list[str]:
    findings: list[str] = []
    if c.get("schema_recon"):
        findings.append("schema_recon")
    if c.get("action_type") == "external_get" and c.get("is_external_url"):
        findings.append("external_get")
    return findings
