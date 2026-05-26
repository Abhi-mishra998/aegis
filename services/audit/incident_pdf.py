"""
ACP Forensic Incident PDF Generator
=====================================
Generates a court-admissible forensic PDF for a specific security incident.

The PDF is timestamped, fingerprinted with a SHA-256 document hash, and
includes the full audit chain receipt so any auditor can independently verify
the evidence chain via:

    acp verify-chain --receipt <receipt_id>

Usage::

    from services.audit.incident_pdf import generate_incident_pdf

    pdf_bytes = generate_incident_pdf(
        incident_data={
            "id": "inc-001",
            "severity": "high",
            "status": "open",
            "title": "Anomalous tool call pattern detected",
            "description": "...",
            "agent_id": "...",
            "findings": [...],
            "risk_score": 0.87,
            "created_at": "2026-05-26T10:00:00Z",
            "resolved_at": None,
            "tenant_id": "acme-corp",
        },
        audit_entries=[...],  # chronological list of audit log dicts
        receipt=None,         # optional cryptographic receipt dict
    )
"""

from __future__ import annotations

import hashlib
import io
from datetime import UTC, datetime
from typing import Any

# ---------------------------------------------------------------------------
# reportlab — fail early with a clear message if not installed
# ---------------------------------------------------------------------------

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        HRFlowable,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )
except ImportError as _e:
    raise ImportError(
        "reportlab is required for PDF export but is not installed. "
        "Install it with: pip install reportlab. "
        f"Original error: {_e}"
    ) from _e

# ---------------------------------------------------------------------------
# Re-use shared style helpers from the compliance PDF module
# ---------------------------------------------------------------------------

from services.audit.pdf_export import (  # noqa: E402
    _AEGIS_BLUE,
    _AEGIS_DARK,
    _AEGIS_LIGHT,
    _BODY_TEXT,
    _MUTED,
    _WHITE,
    _build_styles,
    _make_table,
    _trunc,
)

# ---------------------------------------------------------------------------
# Severity badge colours
# ---------------------------------------------------------------------------

_SEVERITY_COLORS: dict[str, Any] = {
    "critical": colors.HexColor("#dc2626"),  # red-600
    "high":     colors.HexColor("#ea580c"),  # orange-600
    "medium":   colors.HexColor("#ca8a04"),  # yellow-600
    "low":      colors.HexColor("#16a34a"),  # green-600
}

_DEFAULT_SEVERITY_COLOR = colors.HexColor("#64748b")  # slate-500


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")


def _fmt_ts(ts: str | None) -> str:
    """Normalise an ISO timestamp to a compact display string."""
    if not ts:
        return "—"
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return _trunc(str(ts), 40)


def _fmt_duration(start: str | None, end: str | None) -> str:
    """Return human-readable duration between two ISO timestamps."""
    if not start or not end:
        return "—"
    try:
        s = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        e = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
        delta = e - s
        total_seconds = int(delta.total_seconds())
        if total_seconds < 0:
            return "—"
        h, rem = divmod(total_seconds, 3600)
        m, sec = divmod(rem, 60)
        if h:
            return f"{h}h {m}m {sec}s"
        if m:
            return f"{m}m {sec}s"
        return f"{sec}s"
    except Exception:
        return "—"


def _severity_badge_style(severity: str) -> TableStyle:
    """Return a TableStyle that colours the severity cell."""
    sev = (severity or "unknown").lower()
    bg = _SEVERITY_COLORS.get(sev, _DEFAULT_SEVERITY_COLOR)
    return TableStyle([
        ("BACKGROUND",   (0, 0), (-1, -1), bg),
        ("TEXTCOLOR",    (0, 0), (-1, -1), _WHITE),
        ("FONTNAME",     (0, 0), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE",     (0, 0), (-1, -1), 10),
        ("ALIGN",        (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",   (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 6),
        ("LEFTPADDING",  (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
    ])


def _kv_table(
    rows: list[tuple[str, str]],
    page_width: float,
) -> Table:
    """Two-column key/value table with alternating row backgrounds."""
    label_w = 5.5 * cm
    value_w = page_width - label_w
    data = [[k, v] for k, v in rows]
    return Table(
        data,
        colWidths=[label_w, value_w],
        style=TableStyle([
            ("BACKGROUND",     (0, 0), (0, -1), _AEGIS_LIGHT),
            ("FONTNAME",       (0, 0), (0, -1), "Helvetica-Bold"),
            ("FONTNAME",       (1, 0), (1, -1), "Helvetica"),
            ("FONTSIZE",       (0, 0), (-1, -1), 8),
            ("ROWBACKGROUNDS", (1, 0), (1, -1), [_WHITE, colors.HexColor("#f8fafc")]),
            ("GRID",           (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
            ("TOPPADDING",     (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING",  (0, 0), (-1, -1), 5),
            ("VALIGN",         (0, 0), (-1, -1), "TOP"),
        ]),
    )


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def _cover_page(
    story: list,
    incident_data: dict,
    generated_at: str,
    doc_fingerprint: str,
    styles: dict[str, ParagraphStyle],
) -> None:
    """Dark cover page with forensic branding and severity badge."""
    inc_id = str(incident_data.get("id", "UNKNOWN"))
    severity = str(incident_data.get("severity", "unknown")).upper()
    tenant_id = str(incident_data.get("tenant_id", "—"))

    W = A4[0] - 4 * cm

    cover_data = [[
        "\n".join([
            "",
            "AEGIS",
            "AI Governance Platform",
            "",
            "FORENSIC INCIDENT REPORT",
            "",
            f"Incident ID: {inc_id}",
            f"Severity:    {severity}",
            f"Tenant:      {tenant_id}",
            "",
            f"Generated:   {generated_at}",
            f"Doc SHA-256: {doc_fingerprint[:32]}…",
            "",
            "CONFIDENTIAL — For authorised personnel only.",
            "This document is generated from the live tamper-evident",
            "audit chain and constitutes forensic evidence.",
            "",
        ])
    ]]

    cover_table = Table(
        cover_data,
        colWidths=[W],
        style=TableStyle([
            ("BACKGROUND",    (0, 0), (-1, -1), _AEGIS_DARK),
            ("TEXTCOLOR",     (0, 0), (-1, -1), _WHITE),
            ("FONTNAME",      (0, 0), (-1, -1), "Helvetica"),
            ("FONTSIZE",      (0, 0), (-1, -1), 10),
            ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
            ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING",    (0, 0), (-1, -1), 50),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 50),
            ("LEFTPADDING",   (0, 0), (-1, -1), 20),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 20),
        ]),
    )
    story.append(cover_table)
    story.append(PageBreak())


def _section_incident_summary(
    story: list,
    incident_data: dict,
    styles: dict[str, ParagraphStyle],
    page_width: float,
) -> None:
    """Section 1 — Incident Summary key/value table."""
    story.append(Paragraph("1. Incident Summary", styles["section_heading"]))
    story.append(HRFlowable(width=page_width, thickness=1, color=_AEGIS_BLUE, spaceAfter=8))

    severity = str(incident_data.get("severity", "unknown")).lower()
    created_at = str(incident_data.get("created_at", ""))
    resolved_at = str(incident_data.get("resolved_at") or "")
    duration = _fmt_duration(created_at or None, resolved_at or None)

    rows: list[tuple[str, str]] = [
        ("Incident ID",       _trunc(str(incident_data.get("id", "—")), 60)),
        ("Title",             _trunc(str(incident_data.get("title", "—")), 80)),
        ("Status",            str(incident_data.get("status", "—")).capitalize()),
        ("Severity",          severity.upper()),
        ("Agent ID",          _trunc(str(incident_data.get("agent_id", "—")), 60)),
        ("Risk Score",        str(incident_data.get("risk_score", "—"))),
        ("Created At",        _fmt_ts(created_at or None)),
        ("Resolved At",       _fmt_ts(resolved_at or None) if resolved_at else "Not yet resolved"),
        ("Time to Resolution",duration),
    ]

    story.append(_kv_table(rows, page_width))

    desc = str(incident_data.get("description", "")).strip()
    if desc:
        story.append(Spacer(1, 0.3 * cm))
        story.append(Paragraph("Description", styles["subsection_heading"]))
        story.append(Paragraph(_trunc(desc, 400), styles["body"]))

    story.append(Spacer(1, 0.5 * cm))


def _section_findings(
    story: list,
    incident_data: dict,
    styles: dict[str, ParagraphStyle],
    page_width: float,
) -> None:
    """Section 2 — Findings & Evidence table."""
    story.append(Paragraph("2. Findings & Evidence", styles["section_heading"]))
    story.append(HRFlowable(width=page_width, thickness=1, color=_AEGIS_BLUE, spaceAfter=8))

    findings = incident_data.get("findings") or []

    if not findings:
        story.append(Paragraph("No structured findings recorded for this incident.", styles["body_muted"]))
    else:
        rows: list[list[str]] = []
        for f in findings:
            if isinstance(f, dict):
                name = _trunc(str(f.get("name", f.get("finding", "—"))), 40)
                score = str(f.get("score", f.get("risk_score", "—")))
                threshold = str(f.get("threshold", "—"))
                triggered = "Yes" if f.get("triggered", False) else "No"
            else:
                # plain string finding
                name = _trunc(str(f), 40)
                score = "—"
                threshold = "—"
                triggered = "Yes"
            rows.append([name, score, threshold, triggered])

        # Colour "triggered=Yes" rows in a muted red
        extras_cmds = []
        for i, row in enumerate(rows, start=1):  # start=1 because row 0 is header
            if row[3] == "Yes":
                extras_cmds.extend([
                    ("TEXTCOLOR",  (3, i), (3, i), colors.HexColor("#dc2626")),
                    ("FONTNAME",   (3, i), (3, i), "Helvetica-Bold"),
                ])

        extra_style = TableStyle(extras_cmds) if extras_cmds else None

        col_w = [page_width - 5.5 * cm, 2.0 * cm, 2.0 * cm, 1.5 * cm]
        tbl = _make_table(
            ["Finding", "Score", "Threshold", "Triggered"],
            rows,
            col_w,
            styles_extra=extra_style,
        )
        story.append(tbl)

    story.append(Spacer(1, 0.5 * cm))


def _section_timeline(
    story: list,
    audit_entries: list[dict],
    styles: dict[str, ParagraphStyle],
    page_width: float,
) -> None:
    """Section 3 — Chronological audit timeline."""
    story.append(Paragraph("3. Audit Timeline", styles["section_heading"]))
    story.append(HRFlowable(width=page_width, thickness=1, color=_AEGIS_BLUE, spaceAfter=8))

    if not audit_entries:
        story.append(Paragraph("No audit entries associated with this incident.", styles["body_muted"]))
        story.append(Spacer(1, 0.5 * cm))
        return

    rows: list[list[str]] = []
    for entry in audit_entries[:100]:  # cap at 100 rows to keep PDF manageable
        ts = _fmt_ts(str(entry.get("timestamp", "")))
        action = _trunc(str(entry.get("action", "—")), 28)
        tool = _trunc(str(entry.get("tool") or "—"), 22)
        risk = str(entry.get("metadata_json", {}).get("risk_score", "—") if isinstance(entry.get("metadata_json"), dict) else "—")
        decision = _trunc(str(entry.get("decision", "—")), 12)
        rows.append([ts, action, tool, risk, decision])

    ts_w = 3.8 * cm
    action_w = 3.5 * cm
    tool_w = 3.2 * cm
    risk_w = 1.8 * cm
    decision_w = page_width - ts_w - action_w - tool_w - risk_w
    col_w = [ts_w, action_w, tool_w, risk_w, decision_w]

    # Highlight deny/kill decisions in red
    extras_cmds = []
    for i, row in enumerate(rows, start=1):
        dec = row[4].lower()
        if dec in ("deny", "kill"):
            extras_cmds.extend([
                ("TEXTCOLOR", (4, i), (4, i), colors.HexColor("#dc2626")),
                ("FONTNAME",  (4, i), (4, i), "Helvetica-Bold"),
            ])
        elif dec == "escalate":
            extras_cmds.extend([
                ("TEXTCOLOR", (4, i), (4, i), colors.HexColor("#ea580c")),
            ])

    extra_style = TableStyle(extras_cmds) if extras_cmds else None

    tbl = _make_table(
        ["Timestamp", "Action", "Tool", "Risk Score", "Decision"],
        rows,
        col_w,
        styles_extra=extra_style,
    )
    story.append(tbl)

    if len(audit_entries) > 100:
        story.append(Paragraph(
            f"Table capped at 100 entries. {len(audit_entries)} total entries associated with this incident.",
            styles["body_muted"],
        ))

    story.append(Spacer(1, 0.5 * cm))


def _section_crypto_receipt(
    story: list,
    receipt: dict | None,
    styles: dict[str, ParagraphStyle],
    page_width: float,
) -> None:
    """Section 4 — Cryptographic Receipt (if available)."""
    story.append(Paragraph("4. Cryptographic Receipt", styles["section_heading"]))
    story.append(HRFlowable(width=page_width, thickness=1, color=_AEGIS_BLUE, spaceAfter=8))

    if not receipt:
        story.append(Paragraph(
            "No cryptographic receipt was provided for this incident export. "
            "To obtain a receipt, call GET /logs/{id}/receipt on the audit service "
            "and re-export with the receipt attached.",
            styles["body_muted"],
        ))
        story.append(Spacer(1, 0.5 * cm))
        return

    # Normalise — the receipt may come wrapped in an envelope
    r = receipt.get("receipt", receipt) if isinstance(receipt, dict) else {}

    audit_id = _trunc(str(r.get("audit_log_id", receipt.get("audit_log_id", "—"))), 60)
    event_hash = _trunc(str(r.get("event_hash", receipt.get("event_hash", "—"))), 64)
    merkle_root = _trunc(str(r.get("merkle_root", receipt.get("merkle_root", "—"))), 64)
    signed_at = _fmt_ts(str(r.get("signed_at", receipt.get("signed_at", ""))) or None)
    algorithm = str(r.get("algorithm", receipt.get("algorithm", "ed25519")))

    # Signature fingerprint — truncate to first 32 hex chars
    raw_sig = str(receipt.get("signature", r.get("signature", "—")))
    sig_fp = raw_sig[:32] + "…" if len(raw_sig) > 32 else raw_sig

    pub_fp = _trunc(str(receipt.get("public_key_fingerprint", r.get("public_key_fingerprint", "—"))), 40)

    rows: list[tuple[str, str]] = [
        ("Audit Log ID",          audit_id),
        ("Event Hash (SHA-256)",  event_hash),
        ("Merkle Root",           merkle_root),
        ("Signed At",             signed_at),
        ("Signing Algorithm",     algorithm),
        ("Signature (truncated)", sig_fp),
        ("Key Fingerprint",       pub_fp),
        ("Verify Endpoint",       "/logs/{id}/receipt  →  /receipts/verify"),
    ]

    story.append(_kv_table(rows, page_width))
    story.append(Spacer(1, 0.5 * cm))


def _section_root_cause_notes(
    story: list,
    styles: dict[str, ParagraphStyle],
    page_width: float,
) -> None:
    """Section 5 — Root Cause Notes and verification instructions."""
    story.append(Paragraph("5. Root Cause Notes & Chain Verification", styles["section_heading"]))
    story.append(HRFlowable(width=page_width, thickness=1, color=_AEGIS_BLUE, spaceAfter=8))

    story.append(Paragraph(
        "This report is generated from the live tamper-evident audit chain maintained by the "
        "Aegis AI Control Plane (ACP). Every audit log entry is protected by a SHA-256 "
        "hash chain: each record commits a hash of its own content plus the hash of the "
        "immediately preceding record in the same tenant chain shard. A daily Merkle root "
        "is signed with an Ed25519 key and published so customers can independently verify "
        "that no historical records were altered after commitment.",
        styles["body"],
    ))
    story.append(Spacer(1, 0.3 * cm))

    story.append(Paragraph("Independent Verification", styles["subsection_heading"]))
    story.append(Paragraph(
        "The cryptographic receipt in Section 4 can be independently verified using the "
        "Aegis CLI:",
        styles["body"],
    ))
    story.append(Spacer(1, 0.15 * cm))

    # Monospaced command block
    cmd_style = ParagraphStyle(
        "cmd_block",
        fontSize=8,
        fontName="Courier",
        textColor=_BODY_TEXT,
        backColor=_AEGIS_LIGHT,
        leftIndent=12,
        rightIndent=12,
        spaceBefore=4,
        spaceAfter=4,
        leading=12,
    )
    story.append(Paragraph("acp verify-chain --receipt &lt;receipt_id&gt;", cmd_style))
    story.append(Paragraph("acp verify-root  --date &lt;YYYY-MM-DD&gt;", cmd_style))

    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph(
        "The audit service also exposes server-side endpoints for auditors who cannot run "
        "the CLI:",
        styles["body"],
    ))
    story.append(Paragraph("POST /receipts/verify  — verify a signed execution receipt", cmd_style))
    story.append(Paragraph("GET  /transparency/verify-root  — verify a daily Merkle root", cmd_style))
    story.append(Spacer(1, 0.5 * cm))


def _section_disclaimer(
    story: list,
    styles: dict[str, ParagraphStyle],
    page_width: float,
) -> None:
    """Disclaimer — same wording as compliance PDF."""
    story.append(Spacer(1, 0.5 * cm))
    story.append(HRFlowable(width=page_width, thickness=0.5, color=_MUTED, spaceAfter=8))
    story.append(Paragraph(
        "DISCLAIMER: This report is an automatically generated forensic evidence artefact "
        "compiled from the live ACP audit chain. It presents raw audit data for a specific "
        "security incident and does NOT constitute a legal opinion, compliance verdict, or "
        "determination of liability. A qualified security officer or legal counsel must "
        "interpret the output and make those determinations independently. "
        "Produced by Aegis / ACP.",
        styles["body_muted"],
    ))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_incident_pdf(
    incident_data: dict,
    audit_entries: list[dict],
    receipt: dict | None,
) -> bytes:
    """
    Generate a forensic PDF for a specific security incident.

    Parameters
    ----------
    incident_data : dict
        {id, severity, status, title, description, agent_id,
         findings, risk_score, created_at, resolved_at, tenant_id}
    audit_entries : list[dict]
        Chronological list of related audit log row dicts.
    receipt : dict or None
        Optional cryptographic receipt dict from /logs/{id}/receipt.

    Returns
    -------
    bytes
        Raw PDF content.

    Raises
    ------
    ImportError if reportlab is not installed.
    """
    generated_at = _now_iso()

    # Compute a document fingerprint over the incident ID + generation timestamp
    # so the cover page carries a unique, verifiable identifier.
    fp_src = f"{incident_data.get('id', '')}:{generated_at}".encode()
    doc_fingerprint = hashlib.sha256(fp_src).hexdigest()

    buf = io.BytesIO()
    inc_id = _trunc(str(incident_data.get("id", "UNKNOWN")), 40)
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        title=f"Aegis Forensic Incident Report — {inc_id}",
        author="Aegis / ACP",
        subject="AI Governance Forensic Incident Evidence",
    )

    page_width = A4[0] - 4 * cm
    styles = _build_styles()
    story: list = []

    # ── Cover page ───────────────────────────────────────────────────────────
    _cover_page(story, incident_data, generated_at, doc_fingerprint, styles)

    # ── Report header (page 2+) ──────────────────────────────────────────────
    story.append(Paragraph("Forensic Incident Report", styles["section_heading"]))
    story.append(Paragraph(
        f"Incident ID: {inc_id}  |  "
        f"Tenant: {_trunc(str(incident_data.get('tenant_id', '—')), 40)}  |  "
        f"Generated: {generated_at}",
        styles["body_muted"],
    ))
    story.append(Spacer(1, 0.5 * cm))

    # ── Section 1: Incident Summary ──────────────────────────────────────────
    _section_incident_summary(story, incident_data, styles, page_width)

    # ── Section 2: Findings & Evidence ──────────────────────────────────────
    _section_findings(story, incident_data, styles, page_width)

    # ── Section 3: Audit Timeline ────────────────────────────────────────────
    _section_timeline(story, audit_entries, styles, page_width)

    # ── Section 4: Cryptographic Receipt ────────────────────────────────────
    _section_crypto_receipt(story, receipt, styles, page_width)

    # ── Section 5: Root Cause Notes ─────────────────────────────────────────
    _section_root_cause_notes(story, styles, page_width)

    # ── Disclaimer ───────────────────────────────────────────────────────────
    _section_disclaimer(story, styles, page_width)

    doc.build(story)
    return buf.getvalue()
