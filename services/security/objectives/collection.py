"""
MITRE ATT&CK TA0009 — Collection.

Bulk-PII reads and the compression step that typically follows them in
the kill chain.

  * `bulk_pii_egress_dump` — ≥ 10K rows of PII-shaped columns
    (CRITICAL, deny tier).
  * `bulk_pii_egress_above_threshold` — 200 ≤ rows < 10K
    (HIGH, escalate tier).
  * `compression_for_exfil` — tar/zip/gzip on a PII-shaped path.
  * `compression_observed` — bare compression on something else.
    Note: `compression_observed` is added by the LAS monitor branch when
    no destructive context surrounds the call, so it does NOT fire here.
    The collection objective only owns the per-call inherent signals.

Row-threshold knobs (10K, 200) match the registry's tier semantics.
Don't drift these without updating signal_registry.py's `default_score`
fields in lockstep — Sprint 1 unit tests catch the divergence.
"""
from __future__ import annotations


_PII_DUMP_THRESHOLD     = 10_000
_PII_ESCALATE_THRESHOLD = 200


def detect(c: dict) -> list[str]:
    findings: list[str] = []
    if c.get("contains_pii_columns"):
        rows = int(c.get("rows_requested") or 0)
        if rows >= _PII_DUMP_THRESHOLD:
            findings.append("bulk_pii_egress_dump")
        elif rows >= _PII_ESCALATE_THRESHOLD:
            findings.append("bulk_pii_egress_above_threshold")
    if c.get("is_compression"):
        findings.append("compression_for_exfil")
    return findings
