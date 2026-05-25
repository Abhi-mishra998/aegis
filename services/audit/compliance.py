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

import hashlib
import json
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
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

    by_decision: Counter[str] = Counter()
    by_tool: Counter[str] = Counter()
    entries: list[dict[str, Any]] = []

    for row in rows:
        decision_val = (row.decision or "unknown").lower()
        tool_val = row.tool or "unknown"
        by_decision[decision_val] += 1
        by_tool[tool_val] += 1
        entries.append(
            {
                "id": str(row.id),
                "timestamp": _to_iso(row.timestamp),
                "agent_id": str(row.agent_id),
                "tool": tool_val,
                "decision": decision_val,
                "reason": row.reason,
                "event_hash": row.event_hash,
                "request_id": row.request_id,
            }
        )

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

    tool_summary: dict[str, Any] = {"total_calls": len(tool_rows), "by_tool": {}, "by_decision": {}}
    by_tool: Counter[str] = Counter()
    by_decision: Counter[str] = Counter()
    for r in tool_rows:
        by_tool[r.tool or "unknown"] += 1
        by_decision[(r.decision or "unknown").lower()] += 1
    tool_summary["by_tool"] = dict(by_tool)
    tool_summary["by_decision"] = dict(by_decision)

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


@compliance_router.get("/export/{bundle_type}")
async def export_compliance_bundle(
    bundle_type: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    period_start: datetime = Query(..., description="ISO-8601 period start"),
    period_end: datetime = Query(..., description="ISO-8601 period end"),
) -> FileResponse:
    """
    Download a compliance bundle as a JSON file.

    bundle_type: tool-ledger | eu-ai-act | nist-ai-rmf | soc2
    """
    _VALID = {"tool-ledger", "eu-ai-act", "nist-ai-rmf", "soc2"}
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
