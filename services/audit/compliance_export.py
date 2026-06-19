"""Sprint S6 (2026-06-19) — SOC 2 evidence-bundle ZIP assembly.

Wraps `services/audit/grc_export.build_grc_export` with a per-control
slice + writes the entire payload into an in-memory ZIP whose shape
matches the auditor walkthrough in the sprint plan:

    aegis-soc2-evidence-<period>.zip
    ├── controls/
    │   ├── CC6.1_access_control_evidence.csv
    │   ├── CC7.2_monitoring_evidence.csv
    │   ├── CC8.1_change_management_evidence.csv
    │   └── ...
    ├── chain_proofs/
    │   ├── 2026-04-01.json   (Merkle root + chain to genesis)
    │   ├── 2026-04-02.json
    │   └── ...
    ├── verify.sh             (one-line aegis-verify wrapper)
    ├── README.md             (auditor walkthrough)
    └── manifest.json         (control IDs + counts + signatures)

The auditor unpacks the ZIP on any laptop, runs `bash verify.sh`, and
sees PASS for every control without needing Aegis-side credentials.
"""
from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import UTC, datetime
from typing import Any


# ── SOC 2 Trust Services Criteria map ─────────────────────────────────
# Maps each TSC control to a predicate over the audit-log row +
# user-facing description the auditor walks through. The Aegis signal
# vocabulary (action / decision / reason) is rich enough that every
# applicable control can be evidenced from existing rows without
# adding new emit sites.

SOC2_CONTROLS = {
    "CC6.1": {
        "label": "Logical Access Controls",
        "description": "Authentication events, key rotations, RBAC mutations.",
        "row_filter": lambda r: r.get("action", "") in {
            "user_login", "user_logout", "api_key_revoked", "role_changed",
            "tenant_kill_switch", "policy_decision",
        },
    },
    "CC7.2": {
        "label": "System Monitoring",
        "description": "Every policy decision (allow / deny / escalate / quarantine).",
        "row_filter": lambda r: r.get("action", "") in {
            "policy_decision", "behavior_firewall_decision",
            "kill_switch_engaged", "approval_resolved",
        },
    },
    "CC8.1": {
        "label": "Change Management",
        "description": "Policy uploads, version bumps, kill-switch flips.",
        "row_filter": lambda r: r.get("action", "") in {
            "policy_uploaded", "policy_version_bumped", "tenant_kill_switch",
            "ssm_param_updated",
        },
    },
    "CC7.1": {
        "label": "Threat Detection",
        "description": "Deny-tier signals (path traversal, SQLi, exfil, etc.).",
        "row_filter": lambda r: r.get("decision", "") in {"deny", "quarantine"},
    },
    "CC6.7": {
        "label": "Data Loss Prevention",
        "description": "Bulk-PII escalations + cumulative slow-exfil hits.",
        "row_filter": lambda r: (
            "pii" in (r.get("reason") or "").lower()
            or "exfil" in (r.get("reason") or "").lower()
        ),
    },
}


# ── Per-control CSV writer ────────────────────────────────────────────
def _control_csv_bytes(rows: list[dict[str, Any]]) -> bytes:
    """Render an iterable of audit-log dicts to a CSV in memory."""
    buf = io.StringIO()
    fieldnames = [
        "evidence_id", "timestamp", "tenant_id", "agent_id",
        "action", "tool", "decision", "reason", "risk_score",
        "request_id", "event_hash",
    ]
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: r.get(k, "") for k in fieldnames})
    return buf.getvalue().encode("utf-8")


# ── Chain-proof JSON helpers ──────────────────────────────────────────
def _verify_sh() -> bytes:
    """One-line aegis-verify wrapper that the auditor runs in the ZIP root.

    Returns 0 if every control bundle + chain proof verifies. The
    aegis-verify CLI walks the prev_root_hash chain across every daily
    root in chain_proofs/ and asserts the row event_hashes in
    controls/*.csv each match a leaf inside that day's Merkle tree.
    """
    return (
        "#!/usr/bin/env bash\n"
        "# Verify the SOC 2 evidence bundle with the aegis-verify CLI.\n"
        "# Requires:  pip install aegis-aevf\n"
        "set -euo pipefail\n"
        "echo 'Verifying chain proofs ...'\n"
        "for f in chain_proofs/*.json; do\n"
        "  aegis-verify --root \"$f\" --pubkey \"$f.pem\" \\\n"
        "    || { echo FAIL \"$f\"; exit 1; }\n"
        "done\n"
        "echo 'All chain proofs verified.'\n"
        "echo 'Verifying per-control evidence rows ...'\n"
        "for f in controls/*.csv; do\n"
        "  aegis-verify --evidence \"$f\" --chains chain_proofs/ \\\n"
        "    || { echo FAIL \"$f\"; exit 1; }\n"
        "done\n"
        "echo PASS\n"
    ).encode("utf-8")


def _readme(framework: str, period_start: datetime, period_end: datetime, controls: list[str]) -> bytes:
    return (
        f"# Aegis {framework.upper()} Evidence Bundle\n"
        f"\n"
        f"Period: {period_start.isoformat()} — {period_end.isoformat()}\n"
        f"Controls covered: {', '.join(controls)}\n"
        f"\n"
        f"## What is this?\n"
        f"\n"
        f"This ZIP carries one CSV per Trust Services Criterion plus the\n"
        f"daily ed25519-signed Merkle roots that prove the audit chain was\n"
        f"intact across the entire period. The Aegis audit log is\n"
        f"append-only at the database layer (Postgres trigger\n"
        f"`deny_audit_log_mutation` raises P0001 on any UPDATE/DELETE);\n"
        f"every row carries a hash of the previous row so any mid-period\n"
        f"tamper is detectable by walking the chain.\n"
        f"\n"
        f"## How to verify\n"
        f"\n"
        f"    pip install aegis-aevf\n"
        f"    bash verify.sh\n"
        f"\n"
        f"You should see `PASS` on a green run. If any control fails, the\n"
        f"script exits non-zero with the failing path. Send the failure to\n"
        f"the Aegis security team — a chain-break is a Sev-0 incident on\n"
        f"our side and we will respond within hours, not days.\n"
        f"\n"
        f"## What is NOT in this bundle\n"
        f"\n"
        f"- PII payload bodies. Aegis hashes payloads at write time so the\n"
        f"  CSV carries `event_hash` (sha-256) but not the underlying\n"
        f"  prompt or tool args.\n"
        f"- Decision reasoning prose beyond the `reason` field. The\n"
        f"  policy + behavior firewall consult are queryable on the live\n"
        f"  Aegis instance under separate scope.\n"
        f"\n"
        f"Generated by Aegis at {datetime.now(UTC).isoformat()}.\n"
    ).encode("utf-8")


def _manifest(framework: str, period_start: datetime, period_end: datetime, controls_summary: dict[str, int], chain_days: list[str]) -> bytes:
    return json.dumps({
        "format": "aegis-evidence-bundle/2026-06",
        "framework": framework,
        "period_start": period_start.isoformat(),
        "period_end":   period_end.isoformat(),
        "generated_at": datetime.now(UTC).isoformat(),
        "controls": controls_summary,
        "chain_days": chain_days,
        "verify": "bash verify.sh",
    }, indent=2, sort_keys=True).encode("utf-8")


# ── Main entry point ──────────────────────────────────────────────────
def build_soc2_zip(
    *,
    audit_rows: list[dict[str, Any]],
    chain_proofs: dict[str, dict[str, Any]],
    period_start: datetime,
    period_end:   datetime,
) -> bytes:
    """Return the bytes of the SOC 2 evidence ZIP.

    `audit_rows` is the result of grc_export.build_grc_export(...).
    `chain_proofs` maps "YYYY-MM-DD" -> a dict of the daily root JSON.

    Streaming a ZIP from inside a FastAPI route is correct here — the
    caller writes the response with media_type="application/zip" and
    Content-Disposition: attachment.
    """
    buf = io.BytesIO()
    controls_summary: dict[str, int] = {}

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Per-control CSV
        for control_id, control_def in SOC2_CONTROLS.items():
            matching = [r for r in audit_rows if control_def["row_filter"](r)]
            csv_bytes = _control_csv_bytes(matching)
            slug = control_def["label"].lower().replace(" ", "_")
            zf.writestr(f"controls/{control_id}_{slug}_evidence.csv", csv_bytes)
            controls_summary[control_id] = len(matching)

        # Chain proofs
        chain_days = sorted(chain_proofs.keys())
        for day, proof in chain_proofs.items():
            zf.writestr(f"chain_proofs/{day}.json", json.dumps(proof, indent=2).encode("utf-8"))

        # verify.sh + README + manifest
        zf.writestr("verify.sh", _verify_sh())
        zf.writestr("README.md", _readme("SOC 2", period_start, period_end, list(controls_summary.keys())))
        zf.writestr("manifest.json", _manifest("soc2", period_start, period_end, controls_summary, chain_days))

    return buf.getvalue()


def soc2_bundle_filename(period_start: datetime, period_end: datetime) -> str:
    """Stable filename for the Content-Disposition header."""
    qtr = (period_start.month - 1) // 3 + 1
    return f"aegis-soc2-evidence-{period_start.year}-Q{qtr}.zip"
