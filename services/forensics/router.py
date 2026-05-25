"""
ACP Forensics Service — Router
================================
Production-quality investigation endpoints that aggregate data from audit,
flight recorder, identity graph, and decision history services.

All endpoints require service-to-service auth (X-Internal-Secret or X-Mesh-Token)
and a X-Tenant-ID header for multi-tenant isolation.
"""
from __future__ import annotations

import json
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from sdk.common.auth import verify_internal_secret
from sdk.common.config import settings

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/forensics", tags=["Forensics"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_HTTP_TIMEOUT = 5.0  # seconds per downstream call
_EXPORT_TTL = 3600   # 1 hour Redis TTL for forensics exports

# ---------------------------------------------------------------------------
# Pydantic Response Models
# ---------------------------------------------------------------------------


class DenialEvent(BaseModel):
    id: str
    agent_id: str
    tool: str | None
    risk_score: float
    action: str
    decision: str
    timestamp: str
    findings: list[str]
    request_id: str | None


class InvestigationListResponse(BaseModel):
    tenant_id: str
    total: int
    events: list[DenialEvent]
    source_errors: list[str] = Field(default_factory=list)


class ReplayEvent(BaseModel):
    event_id: str
    source: str  # "audit" | "flight"
    timestamp: str
    tool: str | None
    decision: str | None
    risk_score: float
    findings: list[str]
    step_name: str | None = None
    step_status: str | None = None
    request_id: str | None = None


class ReplayResponse(BaseModel):
    agent_id: str
    tenant_id: str
    event_count: int
    events: list[ReplayEvent]
    source_errors: list[str] = Field(default_factory=list)


class RiskBucket(BaseModel):
    low: int     # 0.0–0.3
    medium: int  # 0.3–0.6
    high: int    # 0.6–0.8
    critical: int  # 0.8–1.0


class InvestigationProfile(BaseModel):
    agent_id: str
    tenant_id: str
    window_hours: int
    total_events: int
    avg_risk_score: float
    risk_distribution: RiskBucket
    decision_breakdown: dict[str, int]
    top_findings: list[dict[str, Any]]
    recent_high_risk_events: list[dict[str, Any]]
    source_errors: list[str] = Field(default_factory=list)


class BlastRadiusNode(BaseModel):
    node_id: str
    node_type: str
    label: str
    criticality: float
    reachable_via: str | None = None


class BlastRadiusResponse(BaseModel):
    agent_id: str
    tenant_id: str
    node_count: int
    nodes: list[BlastRadiusNode]
    worst_case_path: list[str]
    max_criticality: float
    source_errors: list[str] = Field(default_factory=list)


class TimelineEvent(BaseModel):
    event_id: str
    source: str  # "audit" | "flight"
    timestamp: str
    tool: str | None
    decision: str | None
    risk_score: float
    findings: list[str]
    step_name: str | None = None
    step_status: str | None = None
    flight_timeline_id: str | None = None
    request_id: str | None = None


class TimelineResponse(BaseModel):
    agent_id: str
    tenant_id: str
    event_count: int
    events: list[TimelineEvent]
    source_errors: list[str] = Field(default_factory=list)


class ExportResponse(BaseModel):
    agent_id: str
    tenant_id: str
    generated_at: str
    exported_by: str
    audit_event_count: int
    flight_timeline_count: int
    decision_count: int
    export_key: str
    source_errors: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal Helpers
# ---------------------------------------------------------------------------


def _internal_headers(tenant_id: str) -> dict[str, str]:
    """Build headers for service-to-service calls."""
    return {
        "X-Internal-Secret": settings.INTERNAL_SECRET,
        "X-Tenant-ID": tenant_id,
    }


def _safe_float(val: Any, default: float = 0.0) -> float:
    """Coerce a value to float without raising."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _safe_list(val: Any) -> list[str]:
    """Coerce a value to a list of strings without raising."""
    if isinstance(val, list):
        return [str(v) for v in val]
    if isinstance(val, str) and val:
        return [val]
    return []


def _to_iso(val: Any) -> str:
    """Normalise a timestamp to ISO-8601 string."""
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, str):
        return val
    return str(val)


def _risk_bucket(score: float) -> str:
    if score < 0.3:
        return "low"
    if score < 0.6:
        return "medium"
    if score < 0.8:
        return "high"
    return "critical"


async def _fetch_audit_logs(
    client: httpx.AsyncClient,
    tenant_id: str,
    agent_id: str | None = None,
    action: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], str | None]:
    """
    Fetch audit logs from the audit service.
    Returns (logs, error_string_or_None).
    """
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if agent_id:
        params["agent_id"] = agent_id
    if action:
        params["action"] = action

    url = f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs"
    try:
        resp = await client.get(
            url,
            params=params,
            headers=_internal_headers(tenant_id),
            timeout=_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
        # Audit service wraps in data.items or data (list) depending on version
        if isinstance(body, dict):
            data = body.get("data", body)
            if isinstance(data, dict):
                items = data.get("items", [])
            elif isinstance(data, list):
                items = data
            else:
                items = []
        elif isinstance(body, list):
            items = body
        else:
            items = []
        return items, None
    except httpx.HTTPStatusError as exc:
        return [], f"audit_service: HTTP {exc.response.status_code}"
    except Exception as exc:
        return [], f"audit_service: {type(exc).__name__}: {exc}"


async def _fetch_flight_timelines(
    client: httpx.AsyncClient,
    tenant_id: str,
    agent_id: str,
    limit: int = 20,
) -> tuple[list[dict[str, Any]], str | None]:
    """Fetch flight recorder timelines for an agent."""
    url = f"{settings.FLIGHT_RECORDER_SERVICE_URL.rstrip('/')}/flight/timelines"
    try:
        resp = await client.get(
            url,
            params={"agent_id": agent_id, "limit": limit},
            headers=_internal_headers(tenant_id),
            timeout=_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
        if isinstance(body, dict):
            data = body.get("data", body)
            items = data.get("items", data) if isinstance(data, dict) else data
        elif isinstance(body, list):
            items = body
        else:
            items = []
        return items, None
    except httpx.HTTPStatusError as exc:
        return [], f"flight_recorder: HTTP {exc.response.status_code}"
    except Exception as exc:
        return [], f"flight_recorder: {type(exc).__name__}: {exc}"


async def _fetch_flight_timeline_detail(
    client: httpx.AsyncClient,
    tenant_id: str,
    timeline_id: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Fetch full detail for one flight timeline including steps."""
    url = f"{settings.FLIGHT_RECORDER_SERVICE_URL.rstrip('/')}/flight/timelines/{timeline_id}"
    try:
        resp = await client.get(
            url,
            headers=_internal_headers(tenant_id),
            timeout=_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
        detail = body.get("data", body) if isinstance(body, dict) else body
        return detail, None
    except httpx.HTTPStatusError as exc:
        return None, f"flight_recorder_detail: HTTP {exc.response.status_code}"
    except Exception as exc:
        return None, f"flight_recorder_detail: {type(exc).__name__}: {exc}"


async def _fetch_blast_radius(
    client: httpx.AsyncClient,
    tenant_id: str,
    agent_id: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Fetch blast-radius from identity graph service."""
    url = f"{settings.IDENTITY_GRAPH_SERVICE_URL.rstrip('/')}/graph/blast-radius/{agent_id}"
    try:
        resp = await client.get(
            url,
            headers=_internal_headers(tenant_id),
            timeout=_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
        data = body.get("data", body) if isinstance(body, dict) else body
        return data, None
    except httpx.HTTPStatusError as exc:
        return None, f"identity_graph: HTTP {exc.response.status_code}"
    except Exception as exc:
        return None, f"identity_graph: {type(exc).__name__}: {exc}"


async def _fetch_decision_history(
    client: httpx.AsyncClient,
    tenant_id: str,
    agent_id: str,
    limit: int = 50,
) -> tuple[list[dict[str, Any]], str | None]:
    """Fetch decision history from the decision service."""
    url = f"{settings.DECISION_SERVICE_URL.rstrip('/')}/decision/history"
    try:
        resp = await client.get(
            url,
            params={"agent_id": agent_id, "tenant_id": tenant_id, "limit": limit},
            headers=_internal_headers(tenant_id),
            timeout=_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        body = resp.json()
        if isinstance(body, dict):
            data = body.get("data", body)
            items = data.get("items", data) if isinstance(data, dict) else data
        elif isinstance(body, list):
            items = body
        else:
            items = []
        return items, None
    except httpx.HTTPStatusError as exc:
        return [], f"decision_service: HTTP {exc.response.status_code}"
    except Exception as exc:
        return [], f"decision_service: {type(exc).__name__}: {exc}"


def _extract_risk_score(entry: dict[str, Any]) -> float:
    """Pull risk_score from an audit log entry — checks multiple locations."""
    meta = entry.get("metadata_json") or entry.get("metadata") or {}
    score = meta.get("risk_score") or meta.get("risk") or entry.get("risk_score") or entry.get("risk", 0.0)
    return _safe_float(score)


def _extract_findings(entry: dict[str, Any]) -> list[str]:
    """Pull findings/reasons list from an audit log entry."""
    meta = entry.get("metadata_json") or entry.get("metadata") or {}
    findings = meta.get("findings") or meta.get("reasons") or []
    return _safe_list(findings)


# ---------------------------------------------------------------------------
# 1. GET /forensics/investigation — list high-risk denials for a tenant
# ---------------------------------------------------------------------------


@router.get("/investigation", response_model=InvestigationListResponse)
async def list_investigations(
    tenant_id: str = Header(alias="X-Tenant-ID"),
    _auth: str = Depends(verify_internal_secret),
    limit: int = 20,
    start_time: str | None = None,
    end_time: str | None = None,
    min_risk: float = 0.5,
) -> InvestigationListResponse:
    """
    List recent high-risk denials across all agents for a tenant.

    Queries the audit service for denied/blocked execute_tool events
    with risk_score >= min_risk, optionally bounded by time window.
    """
    source_errors: list[str] = []
    events: list[DenialEvent] = []

    async with httpx.AsyncClient() as client:
        logs, err = await _fetch_audit_logs(
            client,
            tenant_id=tenant_id,
            action="execute_tool",
            limit=min(limit * 4, 200),  # over-fetch to allow client-side filtering
        )
        if err:
            source_errors.append(err)

    # Normalise start/end filters to datetime objects
    dt_start: datetime | None = None
    dt_end: datetime | None = None
    try:
        if start_time:
            dt_start = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        if end_time:
            dt_end = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid time format: {exc}") from exc

    for entry in logs:
        decision = (entry.get("decision") or "").lower()
        action_field = (entry.get("action") or "").lower()

        # Only surface denied or blocked events
        is_denial = decision in {"deny", "denied", "block", "blocked", "kill", "escalate"}
        is_execute = "execute" in action_field or action_field == ""  # empty = all

        if not is_denial and not is_execute:
            continue
        if not is_denial:
            continue

        risk = _extract_risk_score(entry)
        if risk < min_risk:
            continue

        ts_raw = entry.get("timestamp", "")
        ts_str = _to_iso(ts_raw)

        # Time-window filter (skip if outside bounds)
        if dt_start or dt_end:
            try:
                ts_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if dt_start and ts_dt < dt_start:
                    continue
                if dt_end and ts_dt > dt_end:
                    continue
            except (ValueError, TypeError):
                pass  # malformed timestamp — include the event rather than silently drop

        events.append(
            DenialEvent(
                id=str(entry.get("id", "")),
                agent_id=str(entry.get("agent_id", "")),
                tool=entry.get("tool"),
                risk_score=risk,
                action=entry.get("action", ""),
                decision=decision,
                timestamp=ts_str,
                findings=_extract_findings(entry),
                request_id=entry.get("request_id"),
            )
        )

        if len(events) >= limit:
            break

    return InvestigationListResponse(
        tenant_id=tenant_id,
        total=len(events),
        events=events,
        source_errors=source_errors,
    )


# ---------------------------------------------------------------------------
# 2. GET /forensics/replay/{agent_id} — step-by-step execution timeline
# ---------------------------------------------------------------------------


@router.get("/replay/{agent_id}", response_model=ReplayResponse)
async def replay_agent_behavior(
    agent_id: uuid.UUID,
    tenant_id: str = Header(alias="X-Tenant-ID"),
    _auth: str = Depends(verify_internal_secret),
    limit: int = 50,
    start_time: str | None = None,
    end_time: str | None = None,
) -> ReplayResponse:
    """
    Full tool-call replay for an agent.

    Combines audit log events (risk scores recorded at execution time) with
    flight recorder timeline steps so investigators see exactly what happened
    step by step. Risk scores are never re-evaluated — this is a stable
    forensic replay independent of current model versions.
    """
    source_errors: list[str] = []
    replay_events: list[ReplayEvent] = []

    agent_str = str(agent_id)

    async with httpx.AsyncClient() as client:
        # Fetch both sources concurrently
        import asyncio

        audit_task = asyncio.create_task(
            _fetch_audit_logs(client, tenant_id, agent_id=agent_str, limit=limit)
        )
        flight_task = asyncio.create_task(
            _fetch_flight_timelines(client, tenant_id, agent_id=agent_str, limit=20)
        )
        audit_logs, audit_err = await audit_task
        flight_timelines, flight_err = await flight_task

        if audit_err:
            source_errors.append(audit_err)
        if flight_err:
            source_errors.append(flight_err)

        # Expand flight timeline steps (fetch detail for each timeline)
        flight_steps: list[ReplayEvent] = []
        for timeline in flight_timelines[:10]:  # cap to avoid thundering-herd
            tl_id = str(timeline.get("id", ""))
            if not tl_id:
                continue
            detail, detail_err = await _fetch_flight_timeline_detail(client, tenant_id, tl_id)
            if detail_err:
                source_errors.append(detail_err)
                continue
            if not detail:
                continue
            steps = detail.get("steps") or []
            for step in steps:
                ts = _to_iso(step.get("timestamp") or step.get("started_at") or timeline.get("started_at", ""))
                flight_steps.append(
                    ReplayEvent(
                        event_id=str(step.get("id", tl_id + "_step")),
                        source="flight",
                        timestamp=ts,
                        tool=step.get("tool") or timeline.get("tool"),
                        decision=None,
                        risk_score=0.0,
                        findings=[],
                        step_name=step.get("name") or step.get("step_name"),
                        step_status=step.get("status"),
                        request_id=timeline.get("request_id"),
                    )
                )

    # Build audit events
    dt_start: datetime | None = None
    dt_end: datetime | None = None
    try:
        if start_time:
            dt_start = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        if end_time:
            dt_end = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid time format: {exc}") from exc

    for entry in audit_logs:
        ts_str = _to_iso(entry.get("timestamp", ""))
        if dt_start or dt_end:
            try:
                ts_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if dt_start and ts_dt < dt_start:
                    continue
                if dt_end and ts_dt > dt_end:
                    continue
            except (ValueError, TypeError):
                pass

        replay_events.append(
            ReplayEvent(
                event_id=str(entry.get("id", "")),
                source="audit",
                timestamp=ts_str,
                tool=entry.get("tool"),
                decision=(entry.get("decision") or "UNKNOWN").upper(),
                risk_score=_extract_risk_score(entry),
                findings=_extract_findings(entry),
                step_name=None,
                step_status=None,
                request_id=entry.get("request_id"),
            )
        )

    # Merge and sort all events chronologically
    all_events = replay_events + flight_steps
    try:
        all_events.sort(key=lambda e: e.timestamp)
    except Exception:
        pass  # non-comparable timestamps — return as-is

    if not all_events and not source_errors:
        raise HTTPException(status_code=404, detail="No events found for this agent.")

    return ReplayResponse(
        agent_id=agent_str,
        tenant_id=tenant_id,
        event_count=len(all_events),
        events=all_events[:limit],
        source_errors=source_errors,
    )


# ---------------------------------------------------------------------------
# 3. GET /forensics/investigation/{agent_id} — full investigation profile
# ---------------------------------------------------------------------------


@router.get("/investigation/{agent_id}", response_model=InvestigationProfile)
async def get_investigation_profile(
    agent_id: uuid.UUID,
    tenant_id: str = Header(alias="X-Tenant-ID"),
    _auth: str = Depends(verify_internal_secret),
    window_hours: int = 24,
) -> InvestigationProfile:
    """
    Full investigation profile for an agent.

    Aggregates event stats, risk distribution, decision breakdown, top
    triggered findings, and recent high-risk events over the specified window.
    """
    source_errors: list[str] = []
    agent_str = str(agent_id)

    async with httpx.AsyncClient() as client:
        logs, err = await _fetch_audit_logs(
            client,
            tenant_id=tenant_id,
            agent_id=agent_str,
            limit=200,
        )
        if err:
            source_errors.append(err)

    if not logs and not source_errors:
        raise HTTPException(status_code=404, detail="No data found for this agent.")

    # Apply window filter
    now = datetime.now(tz=timezone.utc)
    window_start = now.replace(tzinfo=timezone.utc)
    from datetime import timedelta
    cutoff = now - timedelta(hours=window_hours)

    windowed: list[dict[str, Any]] = []
    for entry in logs:
        ts_str = _to_iso(entry.get("timestamp", ""))
        try:
            ts_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts_dt.tzinfo is None:
                ts_dt = ts_dt.replace(tzinfo=timezone.utc)
            if ts_dt >= cutoff:
                windowed.append(entry)
        except (ValueError, TypeError):
            windowed.append(entry)  # include if timestamp is unparseable

    if not windowed:
        # Fall back to all logs if window filter produced empty set (e.g. no recent events)
        windowed = logs

    # Stats
    total = len(windowed)
    risk_scores = [_extract_risk_score(e) for e in windowed]
    avg_risk = round(sum(risk_scores) / total, 4) if total else 0.0

    # Risk distribution
    buckets: dict[str, int] = {"low": 0, "medium": 0, "high": 0, "critical": 0}
    for score in risk_scores:
        buckets[_risk_bucket(score)] += 1

    # Decision breakdown
    decision_counts: dict[str, int] = defaultdict(int)
    for entry in windowed:
        decision = (entry.get("decision") or "unknown").lower()
        decision_counts[decision] += 1

    # Top findings by frequency
    findings_freq: dict[str, int] = defaultdict(int)
    for entry in windowed:
        for f in _extract_findings(entry):
            findings_freq[f] += 1

    top_findings = [
        {"finding": k, "count": v}
        for k, v in sorted(findings_freq.items(), key=lambda x: x[1], reverse=True)[:10]
    ]

    # Recent high-risk events (risk >= 0.6)
    high_risk = [
        {
            "id": str(e.get("id", "")),
            "timestamp": _to_iso(e.get("timestamp", "")),
            "tool": e.get("tool"),
            "decision": (e.get("decision") or "UNKNOWN").upper(),
            "risk_score": _extract_risk_score(e),
            "findings": _extract_findings(e),
            "request_id": e.get("request_id"),
        }
        for e in windowed
        if _extract_risk_score(e) >= 0.6
    ]
    # Sort descending by risk, take top 20
    high_risk.sort(key=lambda x: x["risk_score"], reverse=True)

    return InvestigationProfile(
        agent_id=agent_str,
        tenant_id=tenant_id,
        window_hours=window_hours,
        total_events=total,
        avg_risk_score=avg_risk,
        risk_distribution=RiskBucket(**buckets),
        decision_breakdown=dict(decision_counts),
        top_findings=top_findings,
        recent_high_risk_events=high_risk[:20],
        source_errors=source_errors,
    )


# ---------------------------------------------------------------------------
# 4. GET /forensics/blast-radius/{agent_id} — identity graph blast radius
# ---------------------------------------------------------------------------


@router.get("/blast-radius/{agent_id}", response_model=BlastRadiusResponse)
async def get_blast_radius(
    agent_id: uuid.UUID,
    tenant_id: str = Header(alias="X-Tenant-ID"),
    _auth: str = Depends(verify_internal_secret),
) -> BlastRadiusResponse:
    """
    Compute the blast radius if this agent is compromised.

    Calls the identity graph service for the full reachability set and
    enriches results with criticality scores and the worst-case traversal path.
    Returns partial data with source_errors if the graph service is unavailable.
    """
    agent_str = str(agent_id)
    source_errors: list[str] = []
    nodes: list[BlastRadiusNode] = []
    worst_case_path: list[str] = []
    max_criticality = 0.0

    async with httpx.AsyncClient() as client:
        data, err = await _fetch_blast_radius(client, tenant_id, agent_str)

    if err:
        source_errors.append(err)

    if data:
        raw_nodes = data.get("nodes") or data.get("reachable_nodes") or []
        raw_path = data.get("worst_case_path") or data.get("critical_path") or []

        for n in raw_nodes:
            criticality = _safe_float(n.get("criticality") or n.get("risk_score") or n.get("weight", 0.0))
            max_criticality = max(max_criticality, criticality)
            nodes.append(
                BlastRadiusNode(
                    node_id=str(n.get("id") or n.get("node_id", "")),
                    node_type=str(n.get("type") or n.get("node_type", "unknown")),
                    label=str(n.get("label") or n.get("name") or n.get("id", "")),
                    criticality=round(criticality, 4),
                    reachable_via=n.get("reachable_via") or n.get("edge_label"),
                )
            )

        # Worst-case path may be a list of node ids or dicts
        for step in raw_path:
            if isinstance(step, dict):
                worst_case_path.append(str(step.get("id") or step.get("label") or step))
            else:
                worst_case_path.append(str(step))

    # Sort nodes by criticality descending for operator convenience
    nodes.sort(key=lambda x: x.criticality, reverse=True)

    return BlastRadiusResponse(
        agent_id=agent_str,
        tenant_id=tenant_id,
        node_count=len(nodes),
        nodes=nodes,
        worst_case_path=worst_case_path,
        max_criticality=round(max_criticality, 4),
        source_errors=source_errors,
    )


# ---------------------------------------------------------------------------
# 5. GET /forensics/timeline/{agent_id} — cross-source merged timeline
# ---------------------------------------------------------------------------


@router.get("/timeline/{agent_id}", response_model=TimelineResponse)
async def get_timeline(
    agent_id: uuid.UUID,
    tenant_id: str = Header(alias="X-Tenant-ID"),
    _auth: str = Depends(verify_internal_secret),
    limit: int = 100,
) -> TimelineResponse:
    """
    Cross-source chronological timeline for an agent.

    Merges audit log events and flight recorder steps into a single unified
    event stream, tagged with source. Flight steps carry execution step
    names/statuses; audit events carry decision outcomes and risk scores.
    """
    import asyncio

    agent_str = str(agent_id)
    source_errors: list[str] = []
    timeline_events: list[TimelineEvent] = []

    async with httpx.AsyncClient() as client:
        audit_task = asyncio.create_task(
            _fetch_audit_logs(client, tenant_id, agent_id=agent_str, limit=limit)
        )
        flight_task = asyncio.create_task(
            _fetch_flight_timelines(client, tenant_id, agent_id=agent_str, limit=20)
        )
        audit_logs, audit_err = await audit_task
        flight_timelines, flight_err = await flight_task

        if audit_err:
            source_errors.append(audit_err)
        if flight_err:
            source_errors.append(flight_err)

        # Expand flight steps with detail fetches (capped to avoid latency blow-up)
        fetch_tasks = [
            asyncio.create_task(
                _fetch_flight_timeline_detail(client, tenant_id, str(tl.get("id", "")))
            )
            for tl in flight_timelines[:15]
            if tl.get("id")
        ]
        detail_results = await asyncio.gather(*fetch_tasks, return_exceptions=True)

    # Audit events → timeline
    for entry in audit_logs:
        timeline_events.append(
            TimelineEvent(
                event_id=str(entry.get("id", "")),
                source="audit",
                timestamp=_to_iso(entry.get("timestamp", "")),
                tool=entry.get("tool"),
                decision=(entry.get("decision") or "UNKNOWN").upper(),
                risk_score=_extract_risk_score(entry),
                findings=_extract_findings(entry),
                step_name=None,
                step_status=None,
                flight_timeline_id=None,
                request_id=entry.get("request_id"),
            )
        )

    # Flight steps → timeline
    for idx, result in enumerate(detail_results):
        if isinstance(result, Exception):
            source_errors.append(f"flight_recorder_detail: {result}")
            continue
        detail, detail_err = result
        if detail_err:
            source_errors.append(detail_err)
        if not detail:
            continue

        tl_id = str(detail.get("id", ""))
        tl_tool = detail.get("tool")
        tl_request_id = detail.get("request_id")

        steps = detail.get("steps") or []
        if steps:
            for step in steps:
                ts = _to_iso(
                    step.get("timestamp")
                    or step.get("started_at")
                    or detail.get("started_at", "")
                )
                timeline_events.append(
                    TimelineEvent(
                        event_id=str(step.get("id", f"{tl_id}_step_{idx}")),
                        source="flight",
                        timestamp=ts,
                        tool=step.get("tool") or tl_tool,
                        decision=None,
                        risk_score=0.0,
                        findings=[],
                        step_name=step.get("name") or step.get("step_name"),
                        step_status=step.get("status"),
                        flight_timeline_id=tl_id or None,
                        request_id=tl_request_id,
                    )
                )
        else:
            # No steps detail — emit the timeline itself as a single event
            ts = _to_iso(detail.get("started_at") or detail.get("created_at", ""))
            timeline_events.append(
                TimelineEvent(
                    event_id=tl_id,
                    source="flight",
                    timestamp=ts,
                    tool=tl_tool,
                    decision=detail.get("status"),
                    risk_score=0.0,
                    findings=[],
                    step_name=None,
                    step_status=detail.get("status"),
                    flight_timeline_id=tl_id or None,
                    request_id=tl_request_id,
                )
            )

    # Merge chronologically
    try:
        timeline_events.sort(key=lambda e: e.timestamp)
    except Exception as exc:
        logger.warning("timeline_sort_failed", error=str(exc))

    if not timeline_events and not source_errors:
        raise HTTPException(status_code=404, detail="No events found for this agent.")

    return TimelineResponse(
        agent_id=agent_str,
        tenant_id=tenant_id,
        event_count=len(timeline_events),
        events=timeline_events[:limit],
        source_errors=source_errors,
    )


# ---------------------------------------------------------------------------
# 6. POST /forensics/export/{agent_id} — full investigation package export
# ---------------------------------------------------------------------------


@router.post("/export/{agent_id}", response_model=ExportResponse)
async def export_investigation(
    agent_id: uuid.UUID,
    tenant_id: str = Header(alias="X-Tenant-ID"),
    x_internal_secret: str | None = Header(default=None, alias="X-Internal-Secret"),
    _auth: str = Depends(verify_internal_secret),
) -> ExportResponse:
    """
    Export full investigation package as JSON.

    Aggregates audit history, flight timelines, blast-radius graph, and
    decision history into a single exportable document stored in Redis
    (key: acp:forensics_export:{agent_id}, TTL: 1 hour) and returned
    as structured JSON. The export_key can be used to retrieve the cached
    export without re-fetching all sources.
    """
    import asyncio

    agent_str = str(agent_id)
    source_errors: list[str] = []
    generated_at = datetime.now(tz=timezone.utc).isoformat()

    # exported_by: use caller identity from auth header (service name or secret prefix)
    exported_by = "service"
    if x_internal_secret and len(x_internal_secret) >= 4:
        exported_by = f"secret:{x_internal_secret[:4]}***"

    async with httpx.AsyncClient() as client:
        audit_task = asyncio.create_task(
            _fetch_audit_logs(client, tenant_id, agent_id=agent_str, limit=200)
        )
        flight_task = asyncio.create_task(
            _fetch_flight_timelines(client, tenant_id, agent_id=agent_str, limit=30)
        )
        blast_task = asyncio.create_task(
            _fetch_blast_radius(client, tenant_id, agent_str)
        )
        decision_task = asyncio.create_task(
            _fetch_decision_history(client, tenant_id, agent_id=agent_str, limit=100)
        )

        audit_logs, audit_err = await audit_task
        flight_timelines, flight_err = await flight_task
        blast_data, blast_err = await blast_task
        decision_history, decision_err = await decision_task

    if audit_err:
        source_errors.append(audit_err)
    if flight_err:
        source_errors.append(flight_err)
    if blast_err:
        source_errors.append(blast_err)
    if decision_err:
        source_errors.append(decision_err)

    # Build the exportable document
    export_doc: dict[str, Any] = {
        "generated_at": generated_at,
        "exported_by": exported_by,
        "agent_id": agent_str,
        "tenant_id": tenant_id,
        "source_errors": source_errors,
        "audit_history": {
            "total": len(audit_logs),
            "events": [
                {
                    "id": str(e.get("id", "")),
                    "timestamp": _to_iso(e.get("timestamp", "")),
                    "tool": e.get("tool"),
                    "action": e.get("action"),
                    "decision": e.get("decision"),
                    "risk_score": _extract_risk_score(e),
                    "findings": _extract_findings(e),
                    "request_id": e.get("request_id"),
                    "event_hash": e.get("event_hash"),
                }
                for e in audit_logs
            ],
        },
        "flight_timelines": {
            "total": len(flight_timelines),
            "timelines": [
                {
                    "id": str(t.get("id", "")),
                    "tool": t.get("tool"),
                    "status": t.get("status"),
                    "started_at": _to_iso(t.get("started_at", "")),
                    "ended_at": _to_iso(t.get("ended_at", "")) if t.get("ended_at") else None,
                    "request_id": t.get("request_id"),
                    "steps": t.get("steps") or [],
                }
                for t in flight_timelines
            ],
        },
        "blast_radius": blast_data or {},
        "decision_history": {
            "total": len(decision_history),
            "decisions": [
                {
                    "id": str(d.get("id", "")),
                    "timestamp": _to_iso(d.get("timestamp") or d.get("created_at", "")),
                    "action": d.get("action"),
                    "risk_score": _safe_float(d.get("risk_score") or d.get("risk", 0.0)),
                    "findings": _safe_list(d.get("findings") or d.get("reasons") or []),
                    "tool": d.get("tool"),
                    "request_id": d.get("request_id"),
                }
                for d in decision_history
            ],
        },
    }

    # Cache in Redis with 1-hour TTL
    export_key = f"acp:forensics_export:{agent_str}"
    try:
        redis: Redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            await redis.setex(export_key, _EXPORT_TTL, json.dumps(export_doc, default=str))
        finally:
            await redis.aclose()
    except Exception as exc:
        # Redis caching is best-effort; do not fail the export
        logger.warning(
            "forensics_export_redis_cache_failed",
            agent_id=agent_str,
            error=str(exc),
        )
        source_errors.append(f"redis_cache: {type(exc).__name__}: {exc}")

    return ExportResponse(
        agent_id=agent_str,
        tenant_id=tenant_id,
        generated_at=generated_at,
        exported_by=exported_by,
        audit_event_count=len(audit_logs),
        flight_timeline_count=len(flight_timelines),
        decision_count=len(decision_history),
        export_key=export_key,
        source_errors=source_errors,
    )
