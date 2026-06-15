"""A6 — GRC evidence-export adapter (Vanta / Drata / Secureframe style).

The buyer's auditors and GRC managers already live inside Vanta, Drata,
Secureframe, Hyperproof, etc. Those platforms ingest control-evidence
records as JSON or CSV — one row per "control × evidence-collection-event"
pair, plus a few descriptive fields.

What A6 ships: every evidence row produced from an Aegis audit row
carries a back-reference to the verifiable AEVF bundle that contains
the same row. The GRC platform records "control SOC2-CC6.1 was tested
on 2026-06-13 — see https://<host>/compliance/export/eu-ai-act?…",
and the auditor pivots from the GRC platform to the AEVF bundle and
verifies it offline.

That positioning matters: Aegis is the **evidence engine behind** the
buyer's existing GRC workflow, not a replacement. The buyer keeps
Vanta. Their auditor keeps the auditor checklist. Aegis is invisible
machinery in the middle that makes both sides land on
cryptographically verifiable evidence.

Output format — one of:
  - "json"  → list of evidence records, one JSON object each
  - "csv"   → RFC 4180 CSV with a fixed header row

Schema (per row):

    {
      "evidence_id":        "<sha256 hex prefix of (control_id || event_hash)>",
      "evidence_type":      "automated_control_test",
      "control_framework":  "SOC2" | "EU_AI_ACT" | "NIST_AI_RMF" | "DPDP",
      "control_id":         "CC6.1" | "Article 12" | "MEASURE 2.1" | "Section 8(5)",
      "collected_at":       "2026-06-13T14:33:21.000000+00:00",
      "tenant_id":          "<uuid>",
      "agent_id":           "<uuid>",
      "action":             "execute_tool" | "human_override" | …,
      "tool":               "tool.sql_query" | …,
      "decision":           "allow" | "deny" | "escalate" | …,
      "summary":            "Tool call denied by policy rule X",
      "aevf_bundle_url":    "https://<host>/compliance/export/eu-ai-act?period_start=…",
      "aevf_event_hash":    "<sha256 hex>",
      "aevf_spec_version":  "aevf/0.1.0"
    }
"""
from __future__ import annotations

import csv
import hashlib
import io
import os
from datetime import timedelta
from typing import Any, Iterable
from urllib.parse import urlencode

from services.audit.models import AuditLog

AEVF_BASE_URL = os.environ.get("AEVF_PUBLIC_BASE_URL", "https://ha.aegisagent.in")
AEVF_SPEC_VERSION = "aevf/0.1.0"


# ─── Framework slug → AEVF-bundle endpoint path ────────────────────────────
#
# A GRC row references the bundle URL for its framework. SOC2 uses the
# SOC2 bundle; EU AI Act uses the eu-ai-act bundle; etc. The buyer's
# auditor pulls the matching bundle and verifies the same row offline.
_FRAMEWORK_BUNDLE_PATH = {
    "SOC2":        "/compliance/export/soc2",
    "EU_AI_ACT":   "/compliance/export/eu-ai-act",
    "NIST_AI_RMF": "/compliance/export/nist-ai-rmf",
    "DPDP":        "/compliance/export/dpdp",
}


def _aevf_bundle_url(framework_slug: str, day_start) -> str:
    path = _FRAMEWORK_BUNDLE_PATH.get(framework_slug, "/compliance/export/eu-ai-act")
    day_end = day_start + timedelta(days=1)
    qs = urlencode({
        "period_start": day_start.isoformat().replace("+00:00", "Z"),
        "period_end":   day_end.isoformat().replace("+00:00", "Z"),
    })
    return f"{AEVF_BASE_URL}{path}?{qs}"


def _evidence_id(control_id: str, event_hash: str | None) -> str:
    """Stable evidence-row id: sha256(control_id || event_hash)[:32]."""
    h = hashlib.sha256(
        f"{control_id}|{event_hash or ''}".encode("utf-8")
    ).hexdigest()
    return h[:32]


def _summary_for(row: AuditLog, control_framework: str, control_id: str) -> str:
    decision = (row.decision or "").lower()
    action = (row.action or "").lower()
    reason = row.reason or ""
    tool = row.tool or "(unknown tool)"

    if decision in ("deny", "block", "kill"):
        return (
            f"Tool call {tool!r} was {decision.upper()}D by policy "
            f"{('('+reason+')') if reason else ''}. Recorded under "
            f"{control_framework} {control_id} as evidence of a "
            f"runtime control firing on a real action."
        )
    if decision == "escalate":
        return (
            f"Tool call {tool!r} was ESCALATED for human approval "
            f"{('('+reason+')') if reason else ''}. Recorded under "
            f"{control_framework} {control_id} as evidence of the "
            f"human-oversight pathway in operation."
        )
    if action in ("human_override", "approval_granted", "approval_denied",
                  "manual_intervention"):
        return (
            f"Human override event ({action}) recorded under "
            f"{control_framework} {control_id} as evidence of the "
            f"grievance / oversight mechanism being exercised."
        )
    # Default — allowed tool call
    return (
        f"Tool call {tool!r} was ALLOWED by policy and signed into the "
        f"audit chain. Recorded under {control_framework} {control_id} "
        f"as evidence of the technical safeguard (logging + integrity) "
        f"operating during the period."
    )


def _row_to_evidence(
    row: AuditLog, mapping: dict[str, list[str]],
) -> Iterable[dict[str, Any]]:
    """Emit one evidence row per (framework, control_id) the audit row maps to.

    `mapping` is the output of `services.audit.verifiable_bundle._map_row_to_controls`
    — a dict of `{eu_ai_act: [...], soc2: [...], nist_ai_rmf: [...], dpdp: [...]}`.
    """
    if not row.timestamp:
        return
    day_start = row.timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
    ts_iso = row.timestamp.isoformat()

    framework_pairs = [
        ("EU_AI_ACT",   mapping.get("eu_ai_act")   or []),
        ("SOC2",        mapping.get("soc2")        or []),
        ("NIST_AI_RMF", mapping.get("nist_ai_rmf") or []),
        ("DPDP",        mapping.get("dpdp")        or []),
    ]
    bundle_url_for: dict[str, str] = {
        slug: _aevf_bundle_url(slug, day_start) for slug, _ in framework_pairs
    }

    for framework_slug, controls in framework_pairs:
        for control_id in controls:
            yield {
                "evidence_id":        _evidence_id(control_id, row.event_hash),
                "evidence_type":      "automated_control_test",
                "control_framework":  framework_slug,
                "control_id":         control_id,
                "collected_at":       ts_iso,
                "tenant_id":          str(row.tenant_id),
                "agent_id":           str(row.agent_id),
                "action":             row.action or "",
                "tool":               row.tool or "",
                "decision":           row.decision or "",
                "summary":            _summary_for(row, framework_slug, control_id),
                "aevf_bundle_url":    bundle_url_for[framework_slug],
                "aevf_event_hash":    row.event_hash or "",
                "aevf_spec_version":  AEVF_SPEC_VERSION,
            }


def build_grc_export(
    rows: list[AuditLog],
    mappings_by_row_id: dict[Any, dict[str, list[str]]],
    *,
    output: str = "json",
) -> str | list[dict[str, Any]]:
    """Build a GRC evidence export.

    Args:
        rows: audit rows in scope for the export (the producer should
              already have applied tenant + period filters).
        mappings_by_row_id: `{row.id: _map_row_to_controls(row)}` —
              precomputed because the caller has the SQL session.
        output: "json" → list of dicts, "csv" → CSV string.

    Returns either a list of evidence records (JSON shape) or a CSV string.
    """
    records: list[dict[str, Any]] = []
    for row in rows:
        mapping = mappings_by_row_id.get(row.id) or {}
        for ev in _row_to_evidence(row, mapping):
            records.append(ev)

    if output == "csv":
        if not records:
            # Still emit the header row so consumers don't have to guess
            # the schema on an empty period.
            header = [
                "evidence_id", "evidence_type", "control_framework",
                "control_id", "collected_at", "tenant_id", "agent_id",
                "action", "tool", "decision", "summary",
                "aevf_bundle_url", "aevf_event_hash", "aevf_spec_version",
            ]
            buf = io.StringIO()
            w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
            w.writerow(header)
            return buf.getvalue()
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=list(records[0].keys()),
                            quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        w.writerows(records)
        return buf.getvalue()

    return records
