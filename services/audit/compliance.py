"""
ACP Compliance Evidence Export
================================
Queries the live audit log and formats the results into compliance-shaped
JSON artifacts.  Each export references the actual audit_log IDs so an
auditor can independently verify any record via the cryptographic chain.

IMPORTANT — scope of this module:
  This is an EVIDENCE COLLECTOR AND FORMATTER, not a compliance verifier.
  It aggregates raw audit data (event counts, decision tallies, policy-change
  logs) and structures it under the relevant article/control numbers.  It does
  NOT evaluate whether the collected data satisfies any compliance requirement,
  compute coverage metrics, or return a PASS/FAIL verdict.  A qualified
  compliance officer must interpret the output and determine whether it
  constitutes sufficient evidence.

Supported frameworks:
  - EU AI Act (Articles 13, 16, 61) — transparency + record-keeping
  - NIST AI RMF (GOVERN, MAP, MEASURE, MANAGE functions)
  - SOC 2 Type II (CC6, CC7, CC8 — logical access + monitoring)
  - Tool-call ledger (per-agent tamper-evident activity report)
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sdk.common.auth import verify_internal_secret
from sdk.common.db import get_db, get_tenant_id
from sdk.common.response import APIResponse
from services.audit.integrity import verify_audit_chain
from services.audit.models import AuditLog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EXPORT_DIR = Path("/tmp/acp_compliance_exports")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


def _tally(rows: list[AuditLog]) -> tuple[Counter[str], Counter[str]]:
    by_tool: Counter[str] = Counter()
    by_decision: Counter[str] = Counter()
    for r in rows:
        by_tool[r.tool or "unknown"] += 1
        by_decision[(r.decision or "unknown").lower()] += 1
    return by_tool, by_decision


# ---------------------------------------------------------------------------
# Core report generators
# ---------------------------------------------------------------------------


async def generate_tool_call_ledger(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> dict[str, Any]:
    """
    Per-agent tamper-evident activity report.

    Returns a full ledger of tool calls for a tenant (optionally filtered by
    agent and date range) with aggregated breakdowns and a chain-integrity flag.
    Every entry references the audit_log row ID so auditors can independently
    verify records via the cryptographic chain.
    """
    query = (
        select(AuditLog)
        .where(AuditLog.tenant_id == tenant_id)
        .where(AuditLog.action == "execute_tool")
    )
    if agent_id is not None:
        query = query.where(AuditLog.agent_id == agent_id)
    if start_date is not None:
        query = query.where(AuditLog.timestamp >= start_date)
    if end_date is not None:
        query = query.where(AuditLog.timestamp <= end_date)

    query = query.order_by(AuditLog.timestamp.asc()).limit(50_000)
    result = await db.execute(query)
    rows: list[AuditLog] = list(result.scalars().all())

    by_tool, by_decision = _tally(rows)
    entries: list[dict[str, Any]] = [
        {
            "id": str(row.id),
            "timestamp": _to_iso(row.timestamp),
            "agent_id": str(row.agent_id),
            "tool": row.tool or "unknown",
            "decision": (row.decision or "unknown").lower(),
            "reason": row.reason,
            "event_hash": row.event_hash,
            "request_id": row.request_id,
        }
        for row in rows
    ]

    # Light chain-integrity check — runs only over the filtered window so the
    # report is self-contained; a full tenant-wide check uses /logs/verify.
    chain_verified: bool = True
    if rows:
        try:
            integrity = await verify_audit_chain(db, tenant_id)
            chain_verified = bool(integrity.get("is_integrous", False))
        except Exception as exc:
            logger.warning("compliance_chain_verify_failed", error=str(exc))
            chain_verified = False

    period_start = _to_iso(start_date) or (entries[0]["timestamp"] if entries else None)
    period_end = _to_iso(end_date) or (entries[-1]["timestamp"] if entries else None)

    return {
        "report_type": "tool_call_ledger",
        "tenant_id": str(tenant_id),
        "agent_id": str(agent_id) if agent_id else "all",
        "period": {"start": period_start, "end": period_end},
        "generated_at": _now_iso(),
        "chain_verified": chain_verified,
        "total_calls": len(entries),
        "by_decision": dict(by_decision),
        "by_tool": dict(by_tool),
        "entries": entries,
    }


async def generate_eu_ai_act_bundle(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    period_start: datetime,
    period_end: datetime,
) -> dict[str, Any]:
    """
    EU AI Act compliance bundle (Articles 13, 16, 61).

    Article 13 — Transparency: tool calls, decisions, and reasoning logged.
    Article 16 — Record-keeping: immutable audit trail reference + integrity proof.
    Article 61 — Post-market monitoring: anomaly counts, escalations, denials.
    """
    base_q = (
        select(AuditLog)
        .where(AuditLog.tenant_id == tenant_id)
        .where(AuditLog.timestamp >= period_start)
        .where(AuditLog.timestamp <= period_end)
    )

    # ── Article 13: Transparency — tool usage summary ──────────────────────
    tool_q = base_q.where(AuditLog.action == "execute_tool")
    tool_result = await db.execute(tool_q.order_by(AuditLog.timestamp.asc()))
    tool_rows: list[AuditLog] = list(tool_result.scalars().all())

    by_tool, by_decision = _tally(tool_rows)
    tool_summary: dict[str, Any] = {
        "total_calls": len(tool_rows),
        "by_tool": dict(by_tool),
        "by_decision": dict(by_decision),
    }

    # ── Article 16: Record-keeping — chain integrity reference ─────────────
    integrity_result = await verify_audit_chain(db, tenant_id)
    first_id = str(tool_rows[0].id) if tool_rows else None
    last_id = str(tool_rows[-1].id) if tool_rows else None

    integrity_proof = {
        "chain_valid": integrity_result.get("is_integrous", False),
        "processed_count": integrity_result.get("processed_count", 0),
        "violations": integrity_result.get("violations", []),
        "first_audit_log_id": first_id,
        "last_audit_log_id": last_id,
        "verify_endpoint": "/logs/verify",
        "receipt_endpoint": "/logs/{id}/receipt",
    }

    # ── Article 16: Decision audit — record of every allow/deny ────────────
    decision_sample_q = (
        base_q
        .where(AuditLog.action == "execute_tool")
        .order_by(AuditLog.timestamp.desc())
        .limit(500)
    )
    decision_rows = list((await db.execute(decision_sample_q)).scalars().all())
    decision_audit = [
        {
            "id": str(r.id),
            "timestamp": _to_iso(r.timestamp),
            "agent_id": str(r.agent_id),
            "tool": r.tool,
            "decision": r.decision,
            "reason": r.reason,
            "request_id": r.request_id,
        }
        for r in decision_rows
    ]

    # ── Article 61: Post-market monitoring — anomalies + escalations ───────
    escalate_count_q = (
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.tenant_id == tenant_id)
        .where(AuditLog.timestamp >= period_start)
        .where(AuditLog.timestamp <= period_end)
        .where(AuditLog.decision.in_(["escalate", "deny", "kill"]))
    )
    escalate_count = (await db.execute(escalate_count_q)).scalar_one_or_none() or 0

    anomaly_q = (
        select(AuditLog)
        .where(AuditLog.tenant_id == tenant_id)
        .where(AuditLog.timestamp >= period_start)
        .where(AuditLog.timestamp <= period_end)
        .where(AuditLog.action == "anomaly_detected")
        .order_by(AuditLog.timestamp.desc())
        .limit(200)
    )
    anomaly_rows = list((await db.execute(anomaly_q)).scalars().all())
    anomaly_log = [
        {
            "id": str(r.id),
            "timestamp": _to_iso(r.timestamp),
            "agent_id": str(r.agent_id),
            "action": r.action,
            "decision": r.decision,
            "reason": r.reason,
        }
        for r in anomaly_rows
    ]

    # ── System description ─────────────────────────────────────────────────
    system_description = {
        "system_name": "ACP — Agent Control Plane",
        "purpose": "Runtime governance of AI agent tool calls",
        "risk_management": "OPA policy engine + behavioral analysis + cryptographic audit chain",
        "transparency_mechanism": "Every tool call logged with decision rationale + tamper-evident hash chain",
        "human_oversight": "Escalation path for high-risk decisions; kill-switch available",
        "audit_framework": "Cryptographically chained audit log — SHA-256 Merkle roots committed daily",
    }

    return {
        "report_type": "eu_ai_act_bundle",
        "framework": "EU AI Act",
        "articles_covered": ["Article 13 (Transparency)", "Article 16 (Record-keeping)", "Article 61 (Post-market monitoring)"],
        "tenant_id": str(tenant_id),
        "period": {"start": period_start.isoformat(), "end": period_end.isoformat()},
        "generated_at": _now_iso(),
        "system_description": system_description,
        "tool_call_summary": tool_summary,
        "decision_audit": decision_audit,
        "anomaly_log": {
            "escalation_and_denial_count": int(escalate_count),
            "anomaly_events": anomaly_log,
        },
        "integrity_proof_reference": integrity_proof,
    }


async def generate_nist_ai_rmf_bundle(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    period_start: datetime,
    period_end: datetime,
) -> dict[str, Any]:
    """
    NIST AI RMF compliance bundle.

    GOVERN: policy configuration evidence (OPA policies in place).
    MAP:    risk classification records per agent.
    MEASURE: risk score distributions, FP/FN estimates.
    MANAGE: escalation records, kill switch activations, anomaly responses.
    """
    base_q = (
        select(AuditLog)
        .where(AuditLog.tenant_id == tenant_id)
        .where(AuditLog.timestamp >= period_start)
        .where(AuditLog.timestamp <= period_end)
    )

    # ── GOVERN: evidence that governance policies are deployed ─────────────
    policy_change_q = (
        base_q
        .where(AuditLog.action.in_(["policy_updated", "policy_created", "policy_deleted"]))
        .order_by(AuditLog.timestamp.desc())
        .limit(100)
    )
    policy_rows = list((await db.execute(policy_change_q)).scalars().all())
    govern_section = {
        "description": "OPA policy engine deployed; every tool call evaluated against tenant policy bundle",
        "policy_enforcement_mechanism": "Open Policy Agent (OPA) — rego policies evaluated per-request",
        "policy_change_events": [
            {
                "id": str(r.id),
                "timestamp": _to_iso(r.timestamp),
                "action": r.action,
                "agent_id": str(r.agent_id),
                "reason": r.reason,
            }
            for r in policy_rows
        ],
        "kill_switch_available": True,
    }

    # ── MAP: per-agent risk classification records ─────────────────────────
    agent_risk_q = (
        select(
            AuditLog.agent_id,
            func.count(AuditLog.id).label("total_calls"),
            func.count(AuditLog.id).filter(AuditLog.decision == "deny").label("denied"),
            func.count(AuditLog.id).filter(AuditLog.decision == "escalate").label("escalated"),
        )
        .where(AuditLog.tenant_id == tenant_id)
        .where(AuditLog.timestamp >= period_start)
        .where(AuditLog.timestamp <= period_end)
        .where(AuditLog.action == "execute_tool")
        .group_by(AuditLog.agent_id)
        .order_by(func.count(AuditLog.id).desc())
        .limit(50)
    )
    agent_risk_rows = list((await db.execute(agent_risk_q)).all())
    map_section = {
        "description": "Risk classification records per AI agent",
        "agents": [
            {
                "agent_id": str(row.agent_id),
                "total_tool_calls": int(row.total_calls),
                "denied_calls": int(row.denied),
                "escalated_calls": int(row.escalated),
                "denial_rate": round(row.denied / row.total_calls, 4) if row.total_calls else 0.0,
            }
            for row in agent_risk_rows
        ],
    }

    # ── MEASURE: risk score distributions ─────────────────────────────────
    all_tool_q = (
        base_q
        .where(AuditLog.action == "execute_tool")
        .order_by(AuditLog.timestamp.asc())
        .limit(10_000)
    )
    all_tool_rows = list((await db.execute(all_tool_q)).scalars().all())

    risk_buckets: dict[str, int] = {"low": 0, "medium": 0, "high": 0, "critical": 0}
    risk_scores: list[float] = []
    for r in all_tool_rows:
        score = float((r.metadata_json or {}).get("risk_score", 0.0))
        risk_scores.append(score)
        if score >= 0.9:
            risk_buckets["critical"] += 1
        elif score >= 0.7:
            risk_buckets["high"] += 1
        elif score >= 0.4:
            risk_buckets["medium"] += 1
        else:
            risk_buckets["low"] += 1

    avg_risk = round(sum(risk_scores) / len(risk_scores), 4) if risk_scores else 0.0
    measure_section = {
        "description": "Risk score distributions from behavioral analysis",
        "total_evaluated": len(all_tool_rows),
        "avg_risk_score": avg_risk,
        "risk_distribution": risk_buckets,
        "note": "FP/FN estimation requires ground-truth labelling — use override audit rows as proxy",
    }

    # ── MANAGE: escalations, kills, anomaly responses ──────────────────────
    manage_q = (
        base_q
        .where(AuditLog.decision.in_(["escalate", "kill"]))
        .order_by(AuditLog.timestamp.desc())
        .limit(200)
    )
    manage_rows = list((await db.execute(manage_q)).scalars().all())
    manage_section = {
        "description": "Escalation records, kill-switch activations, and anomaly responses",
        "total_escalations": sum(1 for r in manage_rows if r.decision == "escalate"),
        "total_kills": sum(1 for r in manage_rows if r.decision == "kill"),
        "events": [
            {
                "id": str(r.id),
                "timestamp": _to_iso(r.timestamp),
                "agent_id": str(r.agent_id),
                "tool": r.tool,
                "decision": r.decision,
                "reason": r.reason,
                "request_id": r.request_id,
            }
            for r in manage_rows
        ],
    }

    return {
        "report_type": "nist_ai_rmf_bundle",
        "framework": "NIST AI RMF",
        "functions_covered": ["GOVERN", "MAP", "MEASURE", "MANAGE"],
        "tenant_id": str(tenant_id),
        "period": {"start": period_start.isoformat(), "end": period_end.isoformat()},
        "generated_at": _now_iso(),
        "GOVERN": govern_section,
        "MAP": map_section,
        "MEASURE": measure_section,
        "MANAGE": manage_section,
    }


async def generate_soc2_evidence(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    period_start: datetime,
    period_end: datetime,
) -> dict[str, Any]:
    """
    SOC 2 Type II evidence bundle.

    CC6.1: Logical access controls (agent permissions, token issuance records).
    CC6.6: System boundary protection (tool-level enforcement).
    CC7.2: System monitoring (behavioral analysis records, alerts fired).
    CC8.1: Change management (policy change log from audit).
    """
    base_q = (
        select(AuditLog)
        .where(AuditLog.tenant_id == tenant_id)
        .where(AuditLog.timestamp >= period_start)
        .where(AuditLog.timestamp <= period_end)
    )

    # ── CC6.1: Logical access controls ────────────────────────────────────
    login_q = (
        base_q
        .where(AuditLog.action.in_(["user_login", "token_issued", "token_revoked", "agent_registered"]))
        .order_by(AuditLog.timestamp.desc())
        .limit(500)
    )
    login_rows = list((await db.execute(login_q)).scalars().all())
    cc6_1 = {
        "control": "CC6.1 — Logical and Physical Access Controls",
        "description": "Access to AI agent capabilities restricted by JWT bearer tokens with tenant isolation",
        "access_events": [
            {
                "id": str(r.id),
                "timestamp": _to_iso(r.timestamp),
                "action": r.action,
                "agent_id": str(r.agent_id),
                "decision": r.decision,
            }
            for r in login_rows
        ],
        "total_access_events": len(login_rows),
    }

    # ── CC6.6: System boundary protection (tool denials) ──────────────────
    deny_q = (
        base_q
        .where(AuditLog.action == "execute_tool")
        .where(AuditLog.decision.in_(["deny", "kill"]))
        .order_by(AuditLog.timestamp.desc())
        .limit(500)
    )
    deny_rows = list((await db.execute(deny_q)).scalars().all())

    tool_deny_counter: Counter[str] = Counter()
    for r in deny_rows:
        tool_deny_counter[r.tool or "unknown"] += 1

    cc6_6 = {
        "control": "CC6.6 — Logical Access Security Measures (system boundaries)",
        "description": "Tool-level enforcement via OPA policy engine; denied calls never execute",
        "total_denied_tool_calls": len(deny_rows),
        "denied_by_tool": dict(tool_deny_counter),
        "sample_denials": [
            {
                "id": str(r.id),
                "timestamp": _to_iso(r.timestamp),
                "agent_id": str(r.agent_id),
                "tool": r.tool,
                "decision": r.decision,
                "reason": r.reason,
            }
            for r in deny_rows[:50]
        ],
    }

    # ── CC7.2: System monitoring ───────────────────────────────────────────
    monitoring_q = (
        base_q
        .where(AuditLog.action.in_(["anomaly_detected", "behavior_firewall_decision", "rate_limited", "inference_cost_cap_exceeded"]))
        .order_by(AuditLog.timestamp.desc())
        .limit(500)
    )
    monitoring_rows = list((await db.execute(monitoring_q)).scalars().all())
    alert_by_action: Counter[str] = Counter(r.action for r in monitoring_rows)

    cc7_2 = {
        "control": "CC7.2 — System Operations (monitoring and alerting)",
        "description": "Continuous behavioral analysis; anomalies, rate limits, and cost caps trigger audit events",
        "total_monitoring_events": len(monitoring_rows),
        "events_by_type": dict(alert_by_action),
        "monitoring_events": [
            {
                "id": str(r.id),
                "timestamp": _to_iso(r.timestamp),
                "action": r.action,
                "agent_id": str(r.agent_id),
                "decision": r.decision,
                "reason": r.reason,
            }
            for r in monitoring_rows[:100]
        ],
    }

    # ── CC8.1: Change management ───────────────────────────────────────────
    change_q = (
        base_q
        .where(AuditLog.action.in_(["policy_updated", "policy_created", "policy_deleted", "agent_registered", "agent_deactivated"]))
        .order_by(AuditLog.timestamp.desc())
        .limit(200)
    )
    change_rows = list((await db.execute(change_q)).scalars().all())
    cc8_1 = {
        "control": "CC8.1 — Change Management",
        "description": "Policy changes and agent lifecycle events recorded in tamper-evident audit log",
        "total_change_events": len(change_rows),
        "change_events": [
            {
                "id": str(r.id),
                "timestamp": _to_iso(r.timestamp),
                "action": r.action,
                "agent_id": str(r.agent_id),
                "reason": r.reason,
                "request_id": r.request_id,
            }
            for r in change_rows
        ],
    }

    return {
        "report_type": "soc2_evidence",
        "framework": "SOC 2 Type II",
        "controls_covered": ["CC6.1", "CC6.6", "CC7.2", "CC8.1"],
        "tenant_id": str(tenant_id),
        "period": {"start": period_start.isoformat(), "end": period_end.isoformat()},
        "generated_at": _now_iso(),
        "CC6_1": cc6_1,
        "CC6_6": cc6_6,
        "CC7_2": cc7_2,
        "CC8_1": cc8_1,
    }


def export_bundle_as_json(bundle: dict[str, Any], output_path: Path) -> Path:
    """
    Write a compliance bundle to disk as JSON with a companion SHA-256 checksum.

    Creates:
      <output_path>           — the JSON bundle
      <output_path>.sha256    — hex-encoded SHA-256 digest of the JSON file

    Returns the output_path (the .json file, not the checksum sidecar).
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    json_bytes = json.dumps(bundle, indent=2, default=str).encode("utf-8")
    output_path.write_bytes(json_bytes)

    digest = hashlib.sha256(json_bytes).hexdigest()
    checksum_path = output_path.with_suffix(output_path.suffix + ".sha256")
    checksum_path.write_text(f"{digest}  {output_path.name}\n", encoding="utf-8")

    logger.info(
        "compliance_bundle_exported",
        path=str(output_path),
        sha256=digest,
        size_bytes=len(json_bytes),
    )
    return output_path


# ---------------------------------------------------------------------------
# FastAPI Router
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Audit Log CSV / JSON Export — POST /audit/export
# ---------------------------------------------------------------------------

class AuditExportRequest(BaseModel):
    format: str = "csv"          # "csv" | "json"
    start_date: str | None = None
    end_date: str | None = None
    agent_id: str | None = None
    action: str | None = None
    limit: int = 5000


audit_export_router = APIRouter(
    prefix="/audit",
    tags=["audit-export"],
    dependencies=[Depends(verify_internal_secret)],
)


@audit_export_router.post("/export")
async def export_audit_logs(
    payload: AuditExportRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> StreamingResponse:
    """
    Export audit logs as CSV or JSON.

    Body: {format, start_date?, end_date?, agent_id?, action?, limit?}
    limit: default 5000, max 10000.
    Returns a StreamingResponse with Content-Disposition attachment.
    """
    export_format = (payload.format or "csv").lower()
    if export_format not in ("csv", "json"):
        raise HTTPException(status_code=400, detail="format must be 'csv' or 'json'")

    limit = max(1, min(payload.limit or 5000, 10_000))

    def _parse_dt(s: str | None, default: datetime) -> datetime:
        if not s:
            return default
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid date format '{s}'. Expected ISO-8601.",
            )

    now_utc = datetime.now(UTC)
    period_start = _parse_dt(payload.start_date, now_utc - timedelta(days=30))
    period_end = _parse_dt(payload.end_date, now_utc)

    q = (
        select(AuditLog)
        .where(AuditLog.tenant_id == tenant_id)
        .where(AuditLog.timestamp >= period_start)
        .where(AuditLog.timestamp <= period_end)
    )
    if payload.agent_id:
        try:
            agent_uuid = uuid.UUID(payload.agent_id)
            q = q.where(AuditLog.agent_id == agent_uuid)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid agent_id format")
    if payload.action:
        q = q.where(AuditLog.action == payload.action)

    q = q.order_by(AuditLog.timestamp.desc()).limit(limit)
    rows = list((await db.execute(q)).scalars().all())

    date_slug = now_utc.strftime("%Y%m%d")
    tid_short = str(tenant_id).replace("-", "")[:12]

    if export_format == "csv":
        filename = f"acp-audit-{tid_short}-{date_slug}.csv"

        _CSV_FIELDS = [
            "id", "timestamp", "tenant_id", "agent_id", "action",
            "tool", "decision", "reason", "request_id", "event_hash",
            "billing_status", "risk_score",
        ]

        def _csv_generator():
            buf = io.StringIO()
            writer = csv.DictWriter(buf, fieldnames=_CSV_FIELDS, extrasaction="ignore")
            writer.writeheader()
            yield buf.getvalue()
            for r in rows:
                buf = io.StringIO()
                writer = csv.DictWriter(buf, fieldnames=_CSV_FIELDS, extrasaction="ignore")
                writer.writerow({
                    "id":             str(r.id),
                    "timestamp":      r.timestamp.isoformat() if r.timestamp else "",
                    "tenant_id":      str(r.tenant_id),
                    "agent_id":       str(r.agent_id),
                    "action":         r.action or "",
                    "tool":           r.tool or "",
                    "decision":       r.decision or "",
                    "reason":         r.reason or "",
                    "request_id":     r.request_id or "",
                    "event_hash":     r.event_hash or "",
                    "billing_status": r.billing_status or "",
                    "risk_score":     str((r.metadata_json or {}).get("risk_score", "")),
                })
                yield buf.getvalue()

        return StreamingResponse(
            _csv_generator(),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )

    # JSON format
    filename = f"acp-audit-{tid_short}-{date_slug}.json"

    entries = [
        {
            "id":             str(r.id),
            "timestamp":      r.timestamp.isoformat() if r.timestamp else None,
            "tenant_id":      str(r.tenant_id),
            "agent_id":       str(r.agent_id),
            "action":         r.action,
            "tool":           r.tool,
            "decision":       r.decision,
            "reason":         r.reason,
            "request_id":     r.request_id,
            "event_hash":     r.event_hash,
            "billing_status": r.billing_status,
            "metadata_json":  r.metadata_json or {},
        }
        for r in rows
    ]

    json_bytes = json.dumps(
        {"tenant_id": str(tenant_id), "exported_at": now_utc.isoformat(), "count": len(entries), "rows": entries},
        indent=2,
        default=str,
    ).encode("utf-8")

    return StreamingResponse(
        iter([json_bytes]),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


# ---------------------------------------------------------------------------
# Compliance Router
# ---------------------------------------------------------------------------

compliance_router = APIRouter(
    prefix="/compliance",
    tags=["compliance"],
    dependencies=[Depends(verify_internal_secret)],
)


@compliance_router.get("/tool-ledger", response_model=APIResponse[dict])
async def get_tool_ledger(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    agent_id: uuid.UUID | None = Query(None, description="Filter to a single agent"),
    start_date: datetime | None = Query(None, description="ISO-8601 start bound (inclusive)"),
    end_date: datetime | None = Query(None, description="ISO-8601 end bound (inclusive)"),
) -> APIResponse[dict]:
    """Return a tamper-evident tool-call ledger for auditors."""
    bundle = await generate_tool_call_ledger(
        db,
        tenant_id=tenant_id,
        agent_id=agent_id,
        start_date=start_date,
        end_date=end_date,
    )
    return APIResponse(data=bundle)


@compliance_router.get("/eu-ai-act", response_model=APIResponse[dict])
async def get_eu_ai_act_bundle(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    period_start: datetime = Query(..., description="ISO-8601 period start"),
    period_end: datetime = Query(..., description="ISO-8601 period end"),
) -> APIResponse[dict]:
    """Return EU AI Act compliance bundle (Articles 13, 16, 61)."""
    if period_end <= period_start:
        raise HTTPException(status_code=400, detail="period_end must be after period_start")
    bundle = await generate_eu_ai_act_bundle(db, tenant_id, period_start, period_end)
    return APIResponse(data=bundle)


@compliance_router.get("/nist-ai-rmf", response_model=APIResponse[dict])
async def get_nist_ai_rmf_bundle(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    period_start: datetime = Query(..., description="ISO-8601 period start"),
    period_end: datetime = Query(..., description="ISO-8601 period end"),
) -> APIResponse[dict]:
    """Return NIST AI RMF compliance bundle (GOVERN, MAP, MEASURE, MANAGE)."""
    if period_end <= period_start:
        raise HTTPException(status_code=400, detail="period_end must be after period_start")
    bundle = await generate_nist_ai_rmf_bundle(db, tenant_id, period_start, period_end)
    return APIResponse(data=bundle)


@compliance_router.get("/soc2", response_model=APIResponse[dict])
async def get_soc2_evidence(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    period_start: datetime = Query(..., description="ISO-8601 period start"),
    period_end: datetime = Query(..., description="ISO-8601 period end"),
) -> APIResponse[dict]:
    """Return SOC 2 Type II evidence bundle (CC6.1, CC6.6, CC7.2, CC8.1)."""
    if period_end <= period_start:
        raise HTTPException(status_code=400, detail="period_end must be after period_start")
    bundle = await generate_soc2_evidence(db, tenant_id, period_start, period_end)
    return APIResponse(data=bundle)


# ---------------------------------------------------------------------------
# A5 — India DPDP Act, 2023 + DPDP Rules (Nov 2025) evidence bundle
# ---------------------------------------------------------------------------

async def generate_dpdp_bundle(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    period_start: datetime,
    period_end: datetime,
) -> dict[str, Any]:
    """India DPDP Act, 2023 + DPDP Rules (Nov 2025) evidence bundle.

    Sections covered:
      §8(5) — technical & organisational measures (every tool-call audit row)
      §8(6) — record of processing activities (denials + escalations)
      §8(7) — reasonable security safeguards (logging itself)
      §8(8) — breach detection & response (blocks + escalations)
      §8(9) — grievance & redressal mechanism (human-override events)
      §11   — Data Principal rights (PII-block evidence)
      Rules Schedule II — restriction on unauthorised transfer (external-domain blocks)

    Retention requirement: DPDP Rules Nov 2025 require activity logs to
    be retained for >=1 year. The bundle surfaces the configured retention
    so an auditor can verify the producer's claim meets the requirement.
    """
    base_q = (
        select(AuditLog)
        .where(AuditLog.tenant_id == tenant_id)
        .where(AuditLog.timestamp >= period_start)
        .where(AuditLog.timestamp <= period_end)
    )

    # ── §8(5) + §8(7) — every execute_tool row is technical/organisational
    # safeguard evidence. We tally rather than dump every row.
    tool_q = base_q.where(AuditLog.action == "execute_tool")
    tool_rows: list[AuditLog] = list((await db.execute(tool_q)).scalars().all())
    by_tool, by_decision = _tally(tool_rows)

    safeguards_summary: dict[str, Any] = {
        "total_signed_records":  len(tool_rows),
        "by_tool":               dict(by_tool),
        "by_decision":           dict(by_decision),
        "explanation": (
            "Every tool-call decision is recorded in the tamper-evident audit "
            "chain. The presence of a signed record for each call is DPDP "
            "§8(5) / §8(7) evidence: the Data Fiduciary has implemented "
            "the technical safeguard of logging + cryptographic integrity."
        ),
    }

    # ── §8(8) — breach detection + response. Counts + samples of denials,
    # escalations, and kills in the period.
    breach_q = (
        base_q
        .where(AuditLog.decision.in_(["deny", "block", "kill", "escalate"]))
        .order_by(AuditLog.timestamp.desc())
        .limit(500)
    )
    breach_rows = list((await db.execute(breach_q)).scalars().all())
    breach_count = len(breach_rows)
    breach_events = [
        {
            "id":         str(r.id),
            "timestamp":  _to_iso(r.timestamp),
            "agent_id":   str(r.agent_id),
            "tool":       r.tool,
            "decision":   r.decision,
            "reason":     r.reason,
            "request_id": r.request_id,
        }
        for r in breach_rows[:200]   # cap inline sample
    ]

    # ── §8(9) — grievance & redressal. Human-override events show the
    # natural-person review path was exercised.
    override_q = (
        base_q
        .where(AuditLog.action.in_([
            "human_override", "approval_granted", "approval_denied",
            "manual_intervention",
        ]))
        .order_by(AuditLog.timestamp.desc())
        .limit(200)
    )
    override_rows = list((await db.execute(override_q)).scalars().all())

    # ── §11 — Data Principal PII-related blocks. Match on reason field
    # to surface the bulk-PII + external-PII-exfil denials specifically.
    pii_q = (
        base_q
        .where(AuditLog.decision.in_(["deny", "block"]))
        .order_by(AuditLog.timestamp.desc())
        .limit(200)
    )
    pii_rows_all = list((await db.execute(pii_q)).scalars().all())
    pii_rows = [
        r for r in pii_rows_all
        if r.reason and any(
            marker in r.reason.lower()
            for marker in ("pii", "egress", "exfil")
        )
    ]
    pii_events = [
        {
            "id":         str(r.id),
            "timestamp":  _to_iso(r.timestamp),
            "agent_id":   str(r.agent_id),
            "tool":       r.tool,
            "reason":     r.reason,
            "request_id": r.request_id,
        }
        for r in pii_rows[:100]
    ]

    # ── Retention claim — surfaced from env / config so auditor can verify
    # against the DPDP Rules Nov 2025 ≥1-year requirement.
    from services.audit.verifiable_bundle import AUDIT_RETENTION_DAYS
    retention_meets_dpdp = AUDIT_RETENTION_DAYS >= 365

    return {
        "report_type": "dpdp_bundle",
        "framework":   "India DPDP Act, 2023 + DPDP Rules (Nov 2025)",
        "sections_covered": [
            "Section 8(5) — technical & organisational measures",
            "Section 8(6) — record of processing activities",
            "Section 8(7) — reasonable security safeguards",
            "Section 8(8) — breach detection & response",
            "Section 8(9) — grievance & redressal mechanism",
            "Section 11 — Data Principal rights",
            "Rules Schedule II — restriction on unauthorised transfer",
        ],
        "tenant_id":    str(tenant_id),
        "period":       {"start": period_start.isoformat(), "end": period_end.isoformat()},
        "generated_at": _now_iso(),
        # §8(5) / §8(7)
        "safeguards_summary":     safeguards_summary,
        # §8(6) / §8(8)
        "breach_detection_log": {
            "total_blocked_or_escalated":  breach_count,
            "events":                       breach_events,
            "explanation": (
                "Each event below is a signed audit row recording that the "
                "platform blocked or escalated a tool call that would have "
                "constituted unauthorised personal-data processing. These "
                "rows evidence DPDP §8(6) (record-of-processing) and §8(8) "
                "(breach detection)."
            ),
        },
        # §8(9)
        "grievance_redressal_log": {
            "human_override_count":   len(override_rows),
            "events": [
                {
                    "id":        str(r.id),
                    "timestamp": _to_iso(r.timestamp),
                    "action":    r.action,
                    "decision":  r.decision,
                    "reason":    r.reason,
                }
                for r in override_rows
            ],
            "explanation": (
                "Every escalated decision routes through a natural-person "
                "review queue. The events below show the §8(9) review "
                "mechanism was exercised during the period."
            ),
        },
        # §11
        "data_principal_safeguards": {
            "pii_block_count": len(pii_rows),
            "events":          pii_events,
            "explanation": (
                "PII-egress denials evidence that the Data Principal's rights "
                "(§11) are being enforced at the platform layer — bulk PII "
                "reads and external-domain transfers are blocked at runtime."
            ),
        },
        # Retention
        "retention_claim": {
            "configured_retention_days":  AUDIT_RETENTION_DAYS,
            "dpdp_rules_minimum_days":    365,
            "meets_dpdp_minimum":         retention_meets_dpdp,
            "explanation": (
                "DPDP Rules (Nov 2025) require activity logs to be retained "
                "for at least one year. The platform's configured retention "
                "(AUDIT_RETENTION_DAYS env var) is reported here for the "
                "auditor to verify against the requirement. A value below "
                "365 means the producer should adjust before claiming DPDP "
                "Rules conformance."
            ),
        },
    }


@compliance_router.get("/dpdp", response_model=APIResponse[dict])
async def get_dpdp_evidence(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    period_start: datetime = Query(..., description="ISO-8601 period start"),
    period_end: datetime = Query(..., description="ISO-8601 period end"),
) -> APIResponse[dict]:
    """Return India DPDP Act + Rules (Nov 2025) evidence bundle (Sections 8(5)–8(9), 11)."""
    if period_end <= period_start:
        raise HTTPException(status_code=400, detail="period_end must be after period_start")
    bundle = await generate_dpdp_bundle(db, tenant_id, period_start, period_end)
    return APIResponse(data=bundle)


@compliance_router.get("/verifiable-bundle/{framework}")
async def export_verifiable_bundle(
    framework: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    period_start: datetime = Query(..., description="ISO-8601 period start"),
    period_end: datetime = Query(..., description="ISO-8601 period end"),
) -> FileResponse:
    """
    R2 — Download a self-contained, offline-verifiable evidence bundle.

    Schema: aegis-evidence-bundle/2026-06. The bundle embeds every
    public key, every signed daily Merkle root, and every audit row
    with per-row mapping to EU AI Act articles / NIST AI RMF / SOC 2
    control IDs. The customer's auditor verifies it offline with
    `python -m aegis_verify --bundle <file>`.

    framework: eu-ai-act | nist-ai-rmf | soc2
    """
    from services.audit.verifiable_bundle import generate_verifiable_bundle
    _VALID = {"eu-ai-act", "nist-ai-rmf", "soc2"}
    if framework not in _VALID:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown framework '{framework}'. Valid: {sorted(_VALID)}",
        )
    if period_end <= period_start:
        raise HTTPException(status_code=400, detail="period_end must be after period_start")

    bundle = await generate_verifiable_bundle(
        db, tenant_id, framework, period_start, period_end,
    )
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    filename = f"aegis-verifiable-{framework}-{str(tenant_id)[:8]}-{ts}.json"
    out_path = _EXPORT_DIR / filename
    export_bundle_as_json(bundle, out_path)
    return FileResponse(
        path=str(out_path),
        filename=filename,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@compliance_router.get("/export/{bundle_type}")
async def export_compliance_bundle(
    bundle_type: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    period_start: datetime = Query(..., description="ISO-8601 period start"),
    period_end: datetime = Query(..., description="ISO-8601 period end"),
    format: str = Query("json", description="json | csv (applies to bundle_type=grc only)"),
) -> FileResponse:
    """
    Download a compliance bundle as a JSON file.

    bundle_type: tool-ledger | eu-ai-act | nist-ai-rmf | soc2 | dpdp | grc
    """
    _VALID = {"tool-ledger", "eu-ai-act", "nist-ai-rmf", "soc2", "dpdp", "grc"}
    if bundle_type not in _VALID:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown bundle_type '{bundle_type}'. Valid: {sorted(_VALID)}",
        )
    if period_end <= period_start:
        raise HTTPException(status_code=400, detail="period_end must be after period_start")

    if bundle_type == "tool-ledger":
        bundle = await generate_tool_call_ledger(db, tenant_id, start_date=period_start, end_date=period_end)
    elif bundle_type == "eu-ai-act":
        bundle = await generate_eu_ai_act_bundle(db, tenant_id, period_start, period_end)
    elif bundle_type == "nist-ai-rmf":
        bundle = await generate_nist_ai_rmf_bundle(db, tenant_id, period_start, period_end)
    elif bundle_type == "dpdp":
        bundle = await generate_dpdp_bundle(db, tenant_id, period_start, period_end)
    elif bundle_type == "grc":
        # A6 — Vanta/Drata-style control-evidence export. Each evidence row
        # carries the AEVF bundle URL + event_hash so the auditor can pivot
        # from the GRC platform to the verifiable bundle. CSV or JSON via
        # ?format=json|csv (default json).
        return await _build_grc_export_response(
            db, tenant_id, period_start, period_end, output=format,
        )
    else:  # soc2
        bundle = await generate_soc2_evidence(db, tenant_id, period_start, period_end)

    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    filename = f"acp-{bundle_type}-{str(tenant_id)[:8]}-{ts}.json"
    out_path = _EXPORT_DIR / filename

    export_bundle_as_json(bundle, out_path)

    return FileResponse(
        path=str(out_path),
        filename=filename,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# A6 — GRC evidence export (Vanta / Drata / Secureframe style)
# ---------------------------------------------------------------------------

async def _build_grc_export_response(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    period_start: datetime,
    period_end: datetime,
    output: str = "json",
) -> Response:
    """Build the GRC evidence response — JSON list or CSV string.

    The format is selected via the `format` query parameter on the wrapper
    endpoint below; this helper just executes the build.
    """
    from services.audit.grc_export import build_grc_export
    from services.audit.verifiable_bundle import _map_row_to_controls

    # Pull every row in the period — bounded by the same query the
    # eu-ai-act bundle would issue, so the GRC export and the verifiable
    # bundle stay synchronized in row coverage.
    rows_result = await db.execute(
        select(AuditLog)
        .where(AuditLog.tenant_id == tenant_id)
        .where(AuditLog.timestamp >= period_start)
        .where(AuditLog.timestamp <= period_end)
        .order_by(AuditLog.timestamp.asc())
        .limit(5000)
    )
    rows = list(rows_result.scalars().all())
    mappings_by_id = {r.id: _map_row_to_controls(r) for r in rows}

    body = build_grc_export(rows, mappings_by_id, output=output)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    if output == "csv":
        filename = f"acp-grc-{str(tenant_id)[:8]}-{ts}.csv"
        return Response(
            content=body if isinstance(body, str) else "",
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    # default → JSON
    import json as _json
    filename = f"acp-grc-{str(tenant_id)[:8]}-{ts}.json"
    return Response(
        content=_json.dumps({
            "format":            "aegis-grc-export/2026-06",
            "aevf_spec_version": "aevf/0.1.0",
            "tenant_id":         str(tenant_id),
            "period":            {
                "start": period_start.isoformat(),
                "end":   period_end.isoformat(),
            },
            "generated_at":      datetime.now(UTC).isoformat(),
            "evidence":          body,
        }, indent=2, default=str),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@compliance_router.get("/export/grc")
async def export_grc_evidence(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    period_start: datetime = Query(..., description="ISO-8601 period start"),
    period_end:   datetime = Query(..., description="ISO-8601 period end"),
    format:       str = Query("json", description="json | csv"),
) -> Response:
    """A6 — Vanta/Drata-style control-evidence export.

    Each evidence record names a (framework, control_id) the audit row
    evidences AND carries `aevf_bundle_url` + `aevf_event_hash` so the
    auditor can pivot from the GRC platform to the verifiable AEVF
    bundle and verify the same row offline.
    """
    if period_end <= period_start:
        raise HTTPException(status_code=400, detail="period_end must be after period_start")
    if format not in ("json", "csv"):
        raise HTTPException(status_code=400, detail="format must be 'json' or 'csv'")
    return await _build_grc_export_response(db, tenant_id, period_start, period_end, output=format)


# ---------------------------------------------------------------------------
# POST /compliance/export — PDF or JSON export (Day 9-10 feature)
# ---------------------------------------------------------------------------

_FRAMEWORK_TO_GENERATOR = {
    "EU_AI_ACT":    "eu_ai_act",
    "NIST_AI_RMF":  "nist_ai_rmf",
    "SOC2":         "soc2",
}


@compliance_router.post("/export")
async def compliance_export(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    framework: str = Query("EU_AI_ACT", description="EU_AI_ACT | NIST_AI_RMF | SOC2"),
    start_date: str | None = Query(None, description="ISO-8601 start date (default: 30 days ago)"),
    end_date: str | None = Query(None, description="ISO-8601 end date (default: today)"),
    format: str = Query("pdf", description="pdf | json"),
) -> Response:
    """
    Generate and download a compliance report as PDF or JSON.

    For PDF: returns application/pdf with Content-Disposition attachment.
    For JSON: returns the raw evidence dict.

    Returns HTTP 501 if `format=pdf` and reportlab is not installed.
    """
    # ── Validate framework ─────────────────────────────────────────────────
    if framework not in _FRAMEWORK_TO_GENERATOR:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown framework '{framework}'. Valid: {sorted(_FRAMEWORK_TO_GENERATOR)}",
        )

    # ── Parse / default date range ──────────────────────────────────────────
    now_utc = datetime.now(UTC)
    _default_end = now_utc
    _default_start = now_utc - timedelta(days=30)

    def _parse_dt(s: str | None, default: datetime) -> datetime:
        if not s:
            return default
        try:
            dt = datetime.fromisoformat(s)
            # Make timezone-aware if naive
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid date format '{s}'. Expected ISO-8601 (e.g. 2026-05-01 or 2026-05-01T00:00:00Z).",
            )

    period_start = _parse_dt(start_date, _default_start)
    period_end = _parse_dt(end_date, _default_end)

    if period_end <= period_start:
        raise HTTPException(status_code=400, detail="end_date must be after start_date")

    # ── Generate evidence bundle ────────────────────────────────────────────
    if framework == "EU_AI_ACT":
        evidence = await generate_eu_ai_act_bundle(db, tenant_id, period_start, period_end)
    elif framework == "NIST_AI_RMF":
        evidence = await generate_nist_ai_rmf_bundle(db, tenant_id, period_start, period_end)
    else:  # SOC2
        evidence = await generate_soc2_evidence(db, tenant_id, period_start, period_end)

    # ── JSON format ─────────────────────────────────────────────────────────
    if format.lower() == "json":
        ts = now_utc.strftime("%Y%m%dT%H%M%SZ")
        fw_slug = framework.lower().replace("_", "-")
        filename = f"aegis-compliance-{fw_slug}-{ts}.json"
        return Response(
            content=json.dumps(evidence, indent=2, default=str).encode("utf-8"),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # ── PDF format ──────────────────────────────────────────────────────────
    try:
        from services.audit.pdf_export import generate_compliance_pdf
    except ImportError as exc:
        raise HTTPException(
            status_code=501,
            detail=(
                "PDF export requires reportlab which is not installed. "
                "Use format=json or install reportlab (pip install reportlab). "
                f"Error: {exc}"
            ),
        ) from exc

    start_str = period_start.strftime("%Y-%m-%d")
    end_str = period_end.strftime("%Y-%m-%d")

    try:
        pdf_bytes = generate_compliance_pdf(
            tenant_id=str(tenant_id),
            framework=framework,
            start_date=start_str,
            end_date=end_str,
            evidence=evidence,
        )
    except Exception as exc:
        logger.error("compliance_pdf_generation_failed", error=str(exc), framework=framework)
        raise HTTPException(
            status_code=500,
            detail=f"PDF generation failed: {type(exc).__name__}: {exc}",
        ) from exc

    date_slug = now_utc.strftime("%Y%m%d")
    fw_slug = framework.lower().replace("_", "-")
    filename = f"aegis-compliance-{fw_slug}-{date_slug}.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# POST /incidents/{incident_id}/export — Forensic incident PDF
# ---------------------------------------------------------------------------

# NOTE: This route is intentionally on the compliance_router (prefix=/compliance)
# so it shares the same `verify_internal_secret` dependency and is mounted at
# /compliance/incidents/{incident_id}/export on the audit service.  The gateway
# proxy at /incidents/{incident_id}/export strips the /compliance prefix when
# forwarding to the upstream audit service.

@compliance_router.post("/incidents/{incident_id}/export")
async def export_incident_pdf(
    incident_id: str,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """
    Generate and download a forensic PDF for a specific security incident.

    Evidence strategy (no separate incidents table in the audit service):
    1. Query audit_logs where metadata->>'incident_id' = incident_id OR
       where request_id = incident_id (either field may carry the reference).
    2. Also pull the 20 most recent audit rows for the tenant as general context.
    3. Derive a minimal incident_data dict from the audit rows.
    4. Return application/pdf with Content-Disposition attachment.

    Returns HTTP 501 if reportlab is not installed.
    """
    try:
        from services.audit.incident_pdf import generate_incident_pdf  # noqa: PLC0415
    except ImportError as exc:
        raise HTTPException(
            status_code=501,
            detail=(
                "PDF export requires reportlab which is not installed. "
                "Install it with: pip install reportlab. "
                f"Error: {exc}"
            ),
        ) from exc

    # ── 1. Fetch audit rows linked to this incident ─────────────────────────
    from sqlalchemy import Text, cast, or_  # noqa: PLC0415

    incident_q = (
        select(AuditLog)
        .where(AuditLog.tenant_id == tenant_id)
        .where(
            or_(
                AuditLog.request_id == incident_id,
                # JSONB path: metadata_json->>'incident_id' = incident_id
                AuditLog.metadata_json[cast("incident_id", Text)].as_string() == incident_id,
            )
        )
        .order_by(AuditLog.timestamp.asc())
        .limit(100)
    )
    incident_rows = list((await db.execute(incident_q)).scalars().all())

    # ── 2. Recent tenant context rows ───────────────────────────────────────
    context_q = (
        select(AuditLog)
        .where(AuditLog.tenant_id == tenant_id)
        .order_by(AuditLog.timestamp.desc())
        .limit(20)
    )
    context_rows = list((await db.execute(context_q)).scalars().all())

    # Merge, deduplicate, keep chronological order
    all_rows_map: dict[str, AuditLog] = {}
    for r in (*context_rows, *incident_rows):
        all_rows_map[str(r.id)] = r
    all_rows = sorted(all_rows_map.values(), key=lambda r: r.timestamp)

    # ── 3. Build incident_data from the first matched incident row ──────────
    primary: AuditLog | None = incident_rows[0] if incident_rows else (all_rows[0] if all_rows else None)

    if primary is not None:
        meta: dict = primary.metadata_json or {}
        risk_score = meta.get("risk_score", meta.get("score", "—"))
        findings_raw = meta.get("findings", meta.get("signals", []))
        severity = meta.get("severity", "medium")
        # Infer severity from action/decision if not in metadata
        if primary.decision and primary.decision.lower() in ("deny", "kill"):
            severity = meta.get("severity", "high")
        elif primary.decision and primary.decision.lower() == "escalate":
            severity = meta.get("severity", "medium")

        incident_data: dict = {
            "id": incident_id,
            "severity": severity,
            "status": meta.get("status", "open"),
            "title": meta.get("title", f"Security incident {incident_id[:8]}"),
            "description": meta.get("description", primary.reason or ""),
            "agent_id": str(primary.agent_id),
            "findings": findings_raw if isinstance(findings_raw, list) else [],
            "risk_score": risk_score,
            "created_at": primary.timestamp.isoformat() if primary.timestamp else None,
            "resolved_at": meta.get("resolved_at"),
            "tenant_id": str(tenant_id),
        }
    else:
        # No audit rows found — produce a minimal placeholder report
        incident_data = {
            "id": incident_id,
            "severity": "unknown",
            "status": "unknown",
            "title": f"Incident {incident_id}",
            "description": "No audit entries found for this incident ID.",
            "agent_id": "—",
            "findings": [],
            "risk_score": "—",
            "created_at": None,
            "resolved_at": None,
            "tenant_id": str(tenant_id),
        }

    # Serialise audit rows to plain dicts for the PDF generator
    audit_entries: list[dict] = [
        {
            "id": str(r.id),
            "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            "action": r.action,
            "tool": r.tool,
            "decision": r.decision,
            "reason": r.reason,
            "request_id": r.request_id,
            "metadata_json": r.metadata_json or {},
        }
        for r in all_rows
    ]

    # ── 4. Generate PDF ─────────────────────────────────────────────────────
    try:
        pdf_bytes = generate_incident_pdf(
            incident_data=incident_data,
            audit_entries=audit_entries,
            receipt=None,  # receipt requires separate /logs/{id}/receipt call
        )
    except Exception as exc:
        logger.error("incident_pdf_generation_failed", error=str(exc), incident_id=incident_id)
        raise HTTPException(
            status_code=500,
            detail=f"PDF generation failed: {type(exc).__name__}: {exc}",
        ) from exc

    date_slug = datetime.now(UTC).strftime("%Y%m%d")
    filename = f"aegis-incident-{incident_id[:8]}-{date_slug}.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# SIEM Integration — /compliance/siem/*
# Stores Splunk HEC / Datadog config per-tenant in Redis and exposes
# manual-push + connection-test endpoints.
# ---------------------------------------------------------------------------

import os as _os  # noqa: E402

import redis.asyncio as aioredis  # noqa: E402


def _get_redis() -> aioredis.Redis:
    return aioredis.from_url(_os.environ.get("REDIS_URL", "redis://redis:6379"))


def _siem_key(tenant_id: uuid.UUID) -> str:
    return f"acp:siem:{tenant_id}"


def _mask(value: str | None) -> str:
    """Show only the last 4 characters of a secret value."""
    if not value:
        return ""
    return f"***{value[-4:]}" if len(value) > 4 else "****"


@compliance_router.get("/siem/config", response_model=APIResponse[dict])
async def get_siem_config(
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[dict]:
    """Return the current SIEM config (tokens masked) from Redis."""
    r = _get_redis()
    try:
        raw: dict[bytes, bytes] = await r.hgetall(_siem_key(tenant_id))
        cfg: dict[str, str] = {k.decode(): v.decode() for k, v in raw.items()}
    finally:
        await r.aclose()

    return APIResponse(
        data={
            "splunk_url":    cfg.get("splunk_url", ""),
            "splunk_token":  _mask(cfg.get("splunk_token", "")),
            "datadog_key":   _mask(cfg.get("datadog_key", "")),
            "datadog_site":  cfg.get("datadog_site", "datadoghq.com"),
        }
    )


@compliance_router.post("/siem/config", response_model=APIResponse[dict])
async def save_siem_config(
    payload: dict[str, Any],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[dict]:
    """Persist SIEM config {splunk_url, splunk_token, datadog_key, datadog_site} to Redis."""
    mapping: dict[str, str] = {}
    for field in ("splunk_url", "splunk_token", "datadog_key", "datadog_site"):
        if field in payload:
            mapping[field] = str(payload[field])

    if not mapping:
        raise HTTPException(status_code=400, detail="No recognised SIEM fields provided")

    r = _get_redis()
    try:
        await r.hset(_siem_key(tenant_id), mapping=mapping)
    finally:
        await r.aclose()

    return APIResponse(data={"saved": list(mapping.keys())})


@compliance_router.post("/siem/test/splunk", response_model=APIResponse[dict])
async def test_splunk_connection(
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[dict]:
    """Push one synthetic test event to Splunk and return the result."""
    from services.audit.siem_export import push_to_splunk  # noqa: PLC0415

    r = _get_redis()
    try:
        raw: dict[bytes, bytes] = await r.hgetall(_siem_key(tenant_id))
        cfg: dict[str, str] = {k.decode(): v.decode() for k, v in raw.items()}
    finally:
        await r.aclose()

    test_event = {
        "type": "acp_siem_test",
        "tenant_id": str(tenant_id),
        "message": "ACP SIEM connectivity test",
        "timestamp": _now_iso(),
    }
    result = await push_to_splunk(
        [test_event],
        hec_url=cfg.get("splunk_url", ""),
        token=cfg.get("splunk_token", ""),
    )
    return APIResponse(data=result)


@compliance_router.post("/siem/test/datadog", response_model=APIResponse[dict])
async def test_datadog_connection(
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[dict]:
    """Push one synthetic test event to Datadog and return the result."""
    from services.audit.siem_export import push_to_datadog  # noqa: PLC0415

    r = _get_redis()
    try:
        raw: dict[bytes, bytes] = await r.hgetall(_siem_key(tenant_id))
        cfg: dict[str, str] = {k.decode(): v.decode() for k, v in raw.items()}
    finally:
        await r.aclose()

    test_event = {
        "type": "acp_siem_test",
        "tenant_id": str(tenant_id),
        "message": "ACP SIEM connectivity test",
        "timestamp": _now_iso(),
    }
    result = await push_to_datadog(
        [test_event],
        api_key=cfg.get("datadog_key", ""),
        site=cfg.get("datadog_site", "datadoghq.com"),
    )
    return APIResponse(data=result)


@compliance_router.post("/siem/push", response_model=APIResponse[dict])
async def manual_siem_push(
    payload: dict[str, Any],
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[dict]:
    """
    Manually push the last N audit events to the configured SIEM target.

    Body: {limit: 100, target: "splunk"|"datadog"|"all"}
    Default: limit=100, target="all"
    """
    from services.audit.siem_export import (  # noqa: PLC0415
        push_to_datadog,
        push_to_splunk,
    )

    limit = int(payload.get("limit", 100))
    target = str(payload.get("target", "all")).lower()

    if limit < 1 or limit > 10_000:
        raise HTTPException(status_code=400, detail="limit must be 1–10000")

    # Fetch recent audit rows
    q = (
        select(AuditLog)
        .where(AuditLog.tenant_id == tenant_id)
        .order_by(AuditLog.timestamp.desc())
        .limit(limit)
    )
    rows = list((await db.execute(q)).scalars().all())
    events: list[dict[str, Any]] = [
        {
            "id": str(r.id),
            "timestamp": _to_iso(r.timestamp),
            "tenant_id": str(r.tenant_id),
            "agent_id": str(r.agent_id),
            "action": r.action,
            "tool": r.tool,
            "decision": r.decision,
            "reason": r.reason,
            "request_id": r.request_id,
            "event_hash": r.event_hash,
        }
        for r in rows
    ]

    # Load SIEM config from Redis
    r_client = _get_redis()
    try:
        raw: dict[bytes, bytes] = await r_client.hgetall(_siem_key(tenant_id))
        cfg: dict[str, str] = {k.decode(): v.decode() for k, v in raw.items()}
    finally:
        await r_client.aclose()

    results: dict[str, Any] = {"events_fetched": len(events)}

    if target in ("splunk", "all"):
        results["splunk"] = await push_to_splunk(
            events,
            hec_url=cfg.get("splunk_url", ""),
            token=cfg.get("splunk_token", ""),
        )

    if target in ("datadog", "all"):
        results["datadog"] = await push_to_datadog(
            events,
            api_key=cfg.get("datadog_key", ""),
            site=cfg.get("datadog_site", "datadoghq.com"),
        )

    if target not in ("splunk", "datadog", "all"):
        raise HTTPException(status_code=400, detail="target must be splunk, datadog, or all")

    return APIResponse(data=results)


# ---------------------------------------------------------------------------
# SCHEDULED REPORTS — /compliance/scheduled-reports/*
# ---------------------------------------------------------------------------

from services.audit.scheduled_reports import (  # noqa: E402
    create_report,
    delete_report,
    get_report,
    list_deliveries,
    list_reports,
    record_delivery,
    trigger_report_now,
    update_report,
)


@compliance_router.get("/scheduled-reports", response_model=APIResponse[list])
async def list_scheduled_reports_endpoint(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[list]:
    """List all scheduled report configs for the tenant."""
    reports = await list_reports(db, str(tenant_id))
    return APIResponse(
        data=[
            {
                "id": str(r.id),
                "tenant_id": r.tenant_id,
                "name": r.name,
                "report_type": r.report_type,
                "schedule": r.schedule,
                "recipients": r.recipients,
                "framework": r.framework,
                "is_active": r.is_active,
                "last_run_at": r.last_run_at.isoformat() if r.last_run_at else None,
                "next_run_at": r.next_run_at.isoformat() if r.next_run_at else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in reports
        ]
    )


@compliance_router.post("/scheduled-reports", response_model=APIResponse[dict])
async def create_scheduled_report_endpoint(
    payload: dict[str, Any],
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[dict]:
    """Create a new scheduled report configuration."""
    required = ("name", "report_type", "schedule")
    missing = [f for f in required if f not in payload]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing required fields: {missing}")

    report = await create_report(db, str(tenant_id), payload)
    return APIResponse(
        data={
            "id": str(report.id),
            "tenant_id": report.tenant_id,
            "name": report.name,
            "report_type": report.report_type,
            "schedule": report.schedule,
            "recipients": report.recipients,
            "framework": report.framework,
            "is_active": report.is_active,
            "last_run_at": report.last_run_at.isoformat() if report.last_run_at else None,
            "next_run_at": report.next_run_at.isoformat() if report.next_run_at else None,
            "created_at": report.created_at.isoformat() if report.created_at else None,
        }
    )


@compliance_router.get("/scheduled-reports/{report_id}", response_model=APIResponse[dict])
async def get_scheduled_report_endpoint(
    report_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[dict]:
    """Fetch a single scheduled report config."""
    report = await get_report(db, str(tenant_id), report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Scheduled report not found")
    return APIResponse(
        data={
            "id": str(report.id),
            "tenant_id": report.tenant_id,
            "name": report.name,
            "report_type": report.report_type,
            "schedule": report.schedule,
            "recipients": report.recipients,
            "framework": report.framework,
            "is_active": report.is_active,
            "last_run_at": report.last_run_at.isoformat() if report.last_run_at else None,
            "next_run_at": report.next_run_at.isoformat() if report.next_run_at else None,
            "created_at": report.created_at.isoformat() if report.created_at else None,
        }
    )


@compliance_router.patch("/scheduled-reports/{report_id}", response_model=APIResponse[dict])
async def update_scheduled_report_endpoint(
    report_id: str,
    payload: dict[str, Any],
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[dict]:
    """Update name/schedule/recipients/is_active on a scheduled report."""
    report = await update_report(db, str(tenant_id), report_id, payload)
    if report is None:
        raise HTTPException(status_code=404, detail="Scheduled report not found")
    return APIResponse(
        data={
            "id": str(report.id),
            "tenant_id": report.tenant_id,
            "name": report.name,
            "report_type": report.report_type,
            "schedule": report.schedule,
            "recipients": report.recipients,
            "framework": report.framework,
            "is_active": report.is_active,
            "last_run_at": report.last_run_at.isoformat() if report.last_run_at else None,
            "next_run_at": report.next_run_at.isoformat() if report.next_run_at else None,
            "created_at": report.created_at.isoformat() if report.created_at else None,
        }
    )


@compliance_router.delete("/scheduled-reports/{report_id}", response_model=APIResponse[dict])
async def delete_scheduled_report_endpoint(
    report_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[dict]:
    """Delete a scheduled report config."""
    deleted = await delete_report(db, str(tenant_id), report_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Scheduled report not found")
    return APIResponse(data={"deleted": True, "report_id": report_id})


@compliance_router.post("/scheduled-reports/{report_id}/run", response_model=APIResponse[dict])
async def run_scheduled_report_now_endpoint(
    report_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[dict]:
    """Trigger an immediate one-shot report run (writes Redis trigger key, TTL 3600)."""
    result = await trigger_report_now(db, str(tenant_id), report_id)
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="Scheduled report not found")

    # Record the manual trigger in delivery history
    report = await get_report(db, str(tenant_id), report_id)
    if report:
        await record_delivery(
            db, report_id, str(tenant_id), "queued",
            triggered_by="manual", recipients=list(report.recipients or []),
        )

    return APIResponse(data=result)


@compliance_router.get(
    "/scheduled-reports/{report_id}/history",
    response_model=APIResponse[list],
)
async def list_report_delivery_history(
    report_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    limit: int = Query(20, ge=1, le=100),
) -> APIResponse[list]:
    """Return delivery attempts for one scheduled report, newest first."""
    report = await get_report(db, str(tenant_id), report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Scheduled report not found")

    deliveries = await list_deliveries(db, str(tenant_id), report_id, limit=limit)
    return APIResponse(data=[
        {
            "id":           str(d.id),
            "report_id":    str(d.report_id),
            "status":       d.status,
            "triggered_by": d.triggered_by,
            "recipients":   d.recipients or [],
            "error_message": d.error_message,
            "duration_ms":  d.duration_ms,
            "created_at":   d.created_at.isoformat() if d.created_at else None,
        }
        for d in deliveries
    ])


# ---------------------------------------------------------------------------
# THREAT INTELLIGENCE — /compliance/threat-intel/*
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# IN-APP NOTIFICATIONS — /notifications
# ---------------------------------------------------------------------------
from services.audit.notifications import (  # noqa: E402
    create_notification,
    get_unread_count,
    list_notifications,
    mark_all_read,
    mark_read,
)
from services.audit.threat_intel import enrich_domain, enrich_ip  # noqa: E402

_notifications_router = APIRouter(
    prefix="/notifications",
    tags=["notifications"],
    dependencies=[Depends(verify_internal_secret)],
)


@_notifications_router.get("", response_model=APIResponse[list])
async def list_notifications_endpoint(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    unread_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
) -> APIResponse[list]:
    """List notifications for the tenant (newest first)."""
    rows = await list_notifications(db, str(tenant_id), unread_only=unread_only, limit=limit)
    return APIResponse(
        data=[
            {
                "id": str(r.id),
                "tenant_id": r.tenant_id,
                "title": r.title,
                "body": r.body,
                "level": r.level,
                "category": r.category,
                "is_read": r.is_read,
                "link": r.link,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    )


@_notifications_router.post("", response_model=APIResponse[dict], status_code=201)
async def create_notification_endpoint(
    payload: dict[str, Any],
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[dict]:
    """Create a new in-app notification (internal or system-generated)."""
    required = ("title", "body")
    missing = [f for f in required if f not in payload]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing required fields: {missing}")

    notif = await create_notification(
        db,
        tenant_id=str(tenant_id),
        title=str(payload["title"]),
        body=str(payload["body"]),
        level=str(payload.get("level", "info")),
        category=str(payload.get("category", "system")),
        link=payload.get("link"),
    )
    return APIResponse(
        data={
            "id": str(notif.id),
            "tenant_id": notif.tenant_id,
            "title": notif.title,
            "body": notif.body,
            "level": notif.level,
            "category": notif.category,
            "is_read": notif.is_read,
            "link": notif.link,
            "created_at": notif.created_at.isoformat() if notif.created_at else None,
        }
    )


@_notifications_router.post("/{notification_id}/read", response_model=APIResponse[dict])
async def mark_notification_read_endpoint(
    notification_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[dict]:
    """Mark a single notification as read."""
    found = await mark_read(db, str(tenant_id), notification_id)
    if not found:
        raise HTTPException(status_code=404, detail="Notification not found")
    return APIResponse(data={"marked_read": True, "id": notification_id})


@_notifications_router.post("/read-all", response_model=APIResponse[dict])
async def mark_all_read_endpoint(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[dict]:
    """Mark all unread notifications for the tenant as read."""
    count = await mark_all_read(db, str(tenant_id))
    return APIResponse(data={"marked_read": count})


@_notifications_router.get("/count", response_model=APIResponse[dict])
async def get_unread_count_endpoint(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[dict]:
    """Return count of unread notifications for the tenant."""
    count = await get_unread_count(db, str(tenant_id))
    return APIResponse(data={"unread": count})


def _threat_intel_redis() -> aioredis.Redis:
    return aioredis.from_url(_os.environ.get("REDIS_URL", "redis://redis:6379"))


@compliance_router.post("/threat-intel/ip", response_model=APIResponse[dict])
async def threat_intel_ip_endpoint(
    payload: dict[str, Any],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[dict]:
    """Enrich an IP address via AbuseIPDB (or demo data if no key is set)."""
    ip = payload.get("ip", "").strip()
    if not ip:
        raise HTTPException(status_code=400, detail="'ip' field is required")

    cache_key = f"acp:threat_intel:cache:ip:{ip}"
    count_key = f"acp:threat_intel:ip_count:{tenant_id}"

    r = _threat_intel_redis()
    try:
        # Check cache
        cached = await r.get(cache_key)
        if cached:
            import json as _json  # noqa: PLC0415
            result = _json.loads(cached)
        else:
            result = await enrich_ip(ip)
            import json as _json  # noqa: PLC0415 F811
            await r.set(cache_key, _json.dumps(result), ex=3600)

        await r.incr(count_key)
    finally:
        await r.aclose()

    return APIResponse(data=result)


@compliance_router.post("/threat-intel/domain", response_model=APIResponse[dict])
async def threat_intel_domain_endpoint(
    payload: dict[str, Any],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[dict]:
    """Enrich a domain via AlienVault OTX (or demo data if no key is set)."""
    domain = payload.get("domain", "").strip()
    if not domain:
        raise HTTPException(status_code=400, detail="'domain' field is required")

    cache_key = f"acp:threat_intel:cache:domain:{domain}"
    count_key = f"acp:threat_intel:domain_count:{tenant_id}"

    r = _threat_intel_redis()
    try:
        cached = await r.get(cache_key)
        if cached:
            import json as _json  # noqa: PLC0415
            result = _json.loads(cached)
        else:
            result = await enrich_domain(domain)
            import json as _json  # noqa: PLC0415 F811
            await r.set(cache_key, _json.dumps(result), ex=3600)

        await r.incr(count_key)
    finally:
        await r.aclose()

    return APIResponse(data=result)


@compliance_router.get("/threat-intel/summary", response_model=APIResponse[dict])
async def threat_intel_summary_endpoint(
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[dict]:
    """Return {ips_checked, domains_checked, high_risk_count} from Redis counters."""
    ip_count_key = f"acp:threat_intel:ip_count:{tenant_id}"
    domain_count_key = f"acp:threat_intel:domain_count:{tenant_id}"

    r = _threat_intel_redis()
    try:
        ip_count_raw = await r.get(ip_count_key)
        domain_count_raw = await r.get(domain_count_key)
        # high_risk_count is not tracked per-tenant in the current implementation;
        # return 0 as a safe default (enrichment results are cached individually).
        ips_checked = int(ip_count_raw) if ip_count_raw else 0
        domains_checked = int(domain_count_raw) if domain_count_raw else 0
    finally:
        await r.aclose()

    return APIResponse(
        data={
            "ips_checked": ips_checked,
            "domains_checked": domains_checked,
            "high_risk_count": 0,
        }
    )


# ---------------------------------------------------------------------------
# INCIDENT WORKFLOW API — /incidents
# GET    /incidents/{incident_id}         — fetch incident
# POST   /incidents                       — create incident
# PATCH  /incidents/{incident_id}         — update status, assignee, severity, notes
# POST   /incidents/{incident_id}/comments — add timeline comment
# GET    /incidents/{incident_id}/comments — list comments (ASC)
# ---------------------------------------------------------------------------

from services.audit.models import AuditIncident, IncidentComment  # noqa: E402

_VALID_STATUSES = frozenset({"open", "investigating", "contained", "resolved", "closed"})

incidents_router = APIRouter(
    prefix="/incidents",
    tags=["incidents"],
    dependencies=[Depends(verify_internal_secret)],
)


def _serialize_incident(inc: AuditIncident) -> dict:
    return {
        "id":              str(inc.id),
        "tenant_id":       inc.tenant_id,
        "title":           inc.title,
        "description":     inc.description,
        "severity":        inc.severity,
        "status":          inc.status,
        "assignee":        inc.assignee,
        "notes":           inc.notes,
        "source_audit_id": inc.source_audit_id,
        "created_at":      inc.created_at.isoformat() if inc.created_at else None,
        "updated_at":      inc.updated_at.isoformat() if inc.updated_at else None,
    }


def _serialize_comment(c: IncidentComment) -> dict:
    return {
        "id":          str(c.id),
        "incident_id": str(c.incident_id),
        "tenant_id":   c.tenant_id,
        "author":      c.author,
        "body":        c.body,
        "created_at":  c.created_at.isoformat() if c.created_at else None,
    }


@incidents_router.post("", response_model=APIResponse[dict], status_code=201)
async def create_incident(
    payload: dict[str, Any],
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[dict]:
    """Create a new audit-service incident record."""
    required = ("title",)
    missing = [f for f in required if f not in payload]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing required fields: {missing}")

    status_val = str(payload.get("status", "open")).lower()
    if status_val not in _VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status '{status_val}'. Valid: {sorted(_VALID_STATUSES)}")

    inc = AuditIncident(
        tenant_id=str(tenant_id),
        title=str(payload["title"]),
        description=payload.get("description"),
        severity=str(payload.get("severity", "medium")).lower(),
        status=status_val,
        assignee=payload.get("assignee"),
        notes=payload.get("notes"),
        source_audit_id=payload.get("source_audit_id"),
    )
    db.add(inc)
    await db.commit()
    await db.refresh(inc)
    return APIResponse(data=_serialize_incident(inc))


@incidents_router.get("", response_model=APIResponse[list])
async def list_incidents(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    status: str | None = Query(None),
    severity: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> APIResponse[list]:
    """List incidents for the tenant with optional status/severity filter."""
    q = select(AuditIncident).where(AuditIncident.tenant_id == str(tenant_id))
    if status:
        q = q.where(AuditIncident.status == status.lower())
    if severity:
        q = q.where(AuditIncident.severity == severity.lower())
    q = q.order_by(AuditIncident.created_at.desc()).offset(offset).limit(limit)
    rows = list((await db.execute(q)).scalars().all())
    return APIResponse(data=[_serialize_incident(r) for r in rows])


@incidents_router.get("/transitions", response_model=APIResponse[dict])
async def get_incident_transitions() -> APIResponse[dict]:
    """Return the valid state machine transitions for incidents."""
    return APIResponse(data={
        "transitions": {
            "OPEN":          ["INVESTIGATING"],
            "INVESTIGATING": ["MITIGATED", "ESCALATED", "RESOLVED"],
            "ESCALATED":     ["INVESTIGATING", "RESOLVED"],
            "MITIGATED":     ["RESOLVED", "OPEN"],
            "RESOLVED":      [],
        },
        "terminal_states": ["RESOLVED"],
        "initial_state": "OPEN",
    })


@incidents_router.get("/{incident_id}", response_model=APIResponse[dict])
async def get_incident(
    incident_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[dict]:
    """Fetch a single incident by UUID."""
    try:
        inc_uuid = uuid.UUID(incident_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid incident_id format")

    inc = (
        await db.execute(
            select(AuditIncident).where(
                AuditIncident.id == inc_uuid,
                AuditIncident.tenant_id == str(tenant_id),
            )
        )
    ).scalar_one_or_none()
    if inc is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return APIResponse(data=_serialize_incident(inc))


@incidents_router.patch("/{incident_id}", response_model=APIResponse[dict])
async def update_incident(
    incident_id: str,
    payload: dict[str, Any],
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[dict]:
    """
    Update status, assignee, severity, or notes on an incident.

    Writes an audit row recording which fields changed and the previous status.
    """
    try:
        inc_uuid = uuid.UUID(incident_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid incident_id format")

    inc = (
        await db.execute(
            select(AuditIncident).where(
                AuditIncident.id == inc_uuid,
                AuditIncident.tenant_id == str(tenant_id),
            )
        )
    ).scalar_one_or_none()
    if inc is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    previous_status = inc.status
    changed_fields: list[str] = []

    if "status" in payload:
        new_status = str(payload["status"]).lower()
        if new_status not in _VALID_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status '{new_status}'. Valid: {sorted(_VALID_STATUSES)}",
            )
        if new_status != inc.status:
            inc.status = new_status
            changed_fields.append("status")

    if "assignee" in payload:
        inc.assignee = payload["assignee"]
        changed_fields.append("assignee")

    if "severity" in payload:
        inc.severity = str(payload["severity"]).lower()
        changed_fields.append("severity")

    if "notes" in payload:
        inc.notes = payload["notes"]
        changed_fields.append("notes")

    if changed_fields:
        inc.updated_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(inc)

        # Write audit row for the update
        try:
            import os as _os  # noqa: PLC0415

            import redis.asyncio as _aioredis  # noqa: PLC0415

            from services.audit.writer import AuditWriter  # noqa: PLC0415
            _r = _aioredis.from_url(_os.environ.get("REDIS_URL", "redis://redis:6379"))
            try:
                from services.audit.schemas import AuditLogCreate  # noqa: PLC0415
                audit_payload = AuditLogCreate(
                    tenant_id=tenant_id,
                    agent_id=uuid.UUID(int=0),
                    action="incident_updated",
                    tool=None,
                    decision="allow",
                    reason=f"Incident {incident_id} updated: {', '.join(changed_fields)}",
                    metadata_json={
                        "incident_id": incident_id,
                        "changed_fields": changed_fields,
                        "previous_status": previous_status,
                        "new_status": inc.status,
                    },
                )
                await AuditWriter.log(db, _r, audit_payload)
            finally:
                await _r.aclose()
        except Exception as _audit_exc:
            logger.warning("incident_audit_write_failed", error=str(_audit_exc))

    return APIResponse(data=_serialize_incident(inc))


@incidents_router.post("/{incident_id}/comments", response_model=APIResponse[dict], status_code=201)
async def add_comment(
    incident_id: str,
    payload: dict[str, Any],
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[dict]:
    """Add a timeline comment to an incident."""
    try:
        inc_uuid = uuid.UUID(incident_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid incident_id format")

    # Verify incident exists and belongs to tenant
    inc = (
        await db.execute(
            select(AuditIncident).where(
                AuditIncident.id == inc_uuid,
                AuditIncident.tenant_id == str(tenant_id),
            )
        )
    ).scalar_one_or_none()
    if inc is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    required = ("author", "body")
    missing = [f for f in required if f not in payload]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing required fields: {missing}")

    comment = IncidentComment(
        incident_id=inc_uuid,
        tenant_id=str(tenant_id),
        author=str(payload["author"]),
        body=str(payload["body"]),
    )
    db.add(comment)
    await db.commit()
    await db.refresh(comment)
    return APIResponse(data=_serialize_comment(comment))


@incidents_router.get("/{incident_id}/comments", response_model=APIResponse[list])
async def list_comments(
    incident_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[list]:
    """Return all comments for an incident, ordered by created_at ASC."""
    try:
        inc_uuid = uuid.UUID(incident_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid incident_id format")

    # Verify incident belongs to tenant
    inc_exists = (
        await db.execute(
            select(AuditIncident.id).where(
                AuditIncident.id == inc_uuid,
                AuditIncident.tenant_id == str(tenant_id),
            )
        )
    ).scalar_one_or_none()
    if inc_exists is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    q = (
        select(IncidentComment)
        .where(IncidentComment.incident_id == inc_uuid)
        .order_by(IncidentComment.created_at.asc())
    )
    rows = list((await db.execute(q)).scalars().all())
    return APIResponse(data=[_serialize_comment(r) for r in rows])
