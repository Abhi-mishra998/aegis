"""Gateway proxy routes for the audit service.

All 33 ``/audit/*`` routes lifted out of services/gateway/main.py in the
sprint-5 audit cleanup. Each route is a thin reverse-proxy to the audit
service (``settings.AUDIT_SERVICE_URL``); none of them touch app.state
beyond ``request.app.state.client`` for the shared httpx pool.

The two streaming endpoints (`GET /audit/export` NDJSON-streamed,
`POST /audit/export` CSV/JSON-streamed) build their own ``StreamingResponse``
because the generic ``passthrough()`` helper materialises the upstream body.

Path-ordering note: ``/audit/logs/{audit_id}/explain`` and
``/audit/logs/{audit_id}/notes`` (both GET + POST) must sit BEFORE any
hypothetical ``/audit/logs/{audit_id}`` catch-all that might be added in
the future. There is none today, so registration order is informational.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from sdk.common.config import settings
from services.gateway._helpers import (
    clamp_int,
    internal_headers,
    passthrough,
    trust_proxy,
)

router = APIRouter()


def _base() -> str:
    return settings.AUDIT_SERVICE_URL.rstrip("/")


# ── Audit logs core (summary / list / search) ─────────────────────────────

@router.get("/audit/logs/summary", tags=["audit"])
async def audit_summary(request: Request) -> Any:
    """Proxy → Audit logs summary."""
    resp = await request.app.state.client.get(
        f"{_base()}/logs/summary",
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/audit/logs", tags=["audit"])
async def list_audit_logs(request: Request) -> Any:
    """Proxy → Audit logs list."""
    params: dict[str, Any] = {
        "limit":  clamp_int(request.query_params.get("limit"),  50, 1, 500),
        "offset": clamp_int(request.query_params.get("offset"),  0, 0, 100_000),
    }
    for key in ("agent_id", "action", "decision"):
        if val := request.query_params.get(key):
            params[key] = val
    resp = await request.app.state.client.get(
        f"{_base()}/logs",
        params=params,
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.post("/audit/logs/search", tags=["audit"])
async def search_audit_logs(request: Request) -> Any:
    """Proxy → Audit logs search."""
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{_base()}/logs/search",
        json=body,
        headers=internal_headers(request),
    )
    return passthrough(resp)


# ── Audit export (NDJSON stream + CSV/JSON stream) ────────────────────────

@router.get("/audit/export", tags=["audit"])
async def audit_export(request: Request) -> StreamingResponse:
    """Stream the tamper-evident audit chain as NDJSON for SIEM ingest.

    See docs/integrations/siem.md for Splunk HEC / Datadog Logs / S3 examples.
    Forwarded as-is to the audit service; query params (since, until, agent_id,
    chain_shard, limit) are preserved.
    """
    upstream = await request.app.state.client.send(
        request.app.state.client.build_request(
            "GET",
            f"{_base()}/logs/export",
            params=dict(request.query_params),
            headers=internal_headers(request),
        ),
        stream=True,
    )

    async def _relay() -> Any:
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(
        _relay(),
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/x-ndjson"),
        headers={
            k: v for k, v in upstream.headers.items()
            if k.lower() in {"x-acp-chain-format", "cache-control"}
        },
    )


@router.post("/audit/export", tags=["audit"])
async def audit_export_post(request: Request) -> Response:
    """Proxy → Audit service CSV/JSON audit log export.

    Body: ``{format, start_date?, end_date?, agent_id?, action?, limit?}``.
    Streams the upstream response so large CSV downloads work correctly.
    """
    body = await request.body()
    upstream_req = request.app.state.client.build_request(
        "POST",
        f"{_base()}/audit/export",
        content=body,
        headers={**internal_headers(request), "Content-Type": "application/json"},
    )
    upstream = await request.app.state.client.send(upstream_req, stream=True)

    async def _relay() -> Any:
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()

    forward_headers: dict[str, str] = {}
    if "content-disposition" in upstream.headers:
        forward_headers["Content-Disposition"] = upstream.headers["content-disposition"]
    if "content-length" in upstream.headers:
        forward_headers["Content-Length"] = upstream.headers["content-length"]

    return StreamingResponse(
        _relay(),
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/octet-stream"),
        headers=forward_headers,
    )


# ── SOC timeline + heatmap ────────────────────────────────────────────────

@router.get("/audit/logs/soc-timeline", tags=["audit"])
async def soc_timeline(request: Request) -> Any:
    """Proxy → Audit service SOC event feed (deny + kill + high-risk)."""
    resp = await request.app.state.client.get(
        f"{_base()}/logs/soc-timeline",
        params={"limit": clamp_int(request.query_params.get("limit"), 60, 1, 200)},
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/audit/logs/heatmap", tags=["audit"])
async def audit_heatmap(request: Request) -> Any:
    """Proxy → Audit service request-volume heatmap (day × hour, last 7 days)."""
    return await trust_proxy(settings.AUDIT_SERVICE_URL, "/logs/heatmap", request)


# ── Integrity + per-row endpoints ─────────────────────────────────────────

@router.get("/audit/logs/verify", tags=["audit"])
async def verify_audit_integrity(request: Request) -> Any:
    """Proxy → Audit logs integrity verification."""
    resp = await request.app.state.client.get(
        f"{_base()}/logs/verify",
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/audit/logs/{audit_id}/explain", tags=["audit"])
async def explain_decision_proxy(audit_id: str, request: Request) -> Any:
    """Proxy → Audit service root-cause explanation for one decision."""
    resp = await request.app.state.client.get(
        f"{_base()}/logs/{audit_id}/explain",
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.post("/audit/logs/{audit_id}/notes", tags=["audit"])
async def add_audit_note_proxy(audit_id: str, request: Request) -> Any:
    """Proxy → Audit service — add analyst note to an audit entry.

    Must forward Content-Type so FastAPI on the audit side knows to parse
    the body as JSON into the Pydantic _NoteCreate model. Without the
    header, the request body is treated as raw bytes and validation fails
    with "Input should be a valid dictionary or object".
    """
    headers = internal_headers(request)
    ctype = request.headers.get("content-type")
    if ctype:
        headers["Content-Type"] = ctype
    resp = await request.app.state.client.post(
        f"{_base()}/logs/{audit_id}/notes",
        content=await request.body(),
        headers=headers,
    )
    return passthrough(resp)


@router.get("/audit/logs/{audit_id}/notes", tags=["audit"])
async def list_audit_notes_proxy(audit_id: str, request: Request) -> Any:
    """Proxy → Audit service — list analyst notes for an audit entry."""
    resp = await request.app.state.client.get(
        f"{_base()}/logs/{audit_id}/notes",
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/audit/drift/{agent_id}", tags=["audit"])
async def agent_drift_proxy(agent_id: str, request: Request) -> Any:
    """Proxy → Audit service behavioral drift report for one agent."""
    resp = await request.app.state.client.get(
        f"{_base()}/logs/drift/{agent_id}",
        params=dict(request.query_params),
        headers=internal_headers(request),
    )
    return passthrough(resp)


# ── Analytics aggregates (20 endpoints) ───────────────────────────────────
# All have identical shape: GET → audit /logs/<name>?<query_params>.
# Defined explicitly rather than via a generic dispatcher so they appear in
# the OpenAPI schema and the gateway's route map.

@router.get("/audit/trends", tags=["audit"])
async def audit_trends_proxy(request: Request) -> Any:
    """Proxy → Audit service — tenant-level daily anomaly trend."""
    resp = await request.app.state.client.get(
        f"{_base()}/logs/trends",
        params=dict(request.query_params),
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/audit/top-findings", tags=["audit"])
async def top_findings_proxy(request: Request) -> Any:
    """Proxy → Audit service — canonical findings frequency ranking."""
    resp = await request.app.state.client.get(
        f"{_base()}/logs/top-findings",
        params=dict(request.query_params),
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/audit/peer-benchmark/{agent_id}", tags=["audit"])
async def agent_peer_benchmark_proxy(agent_id: str, request: Request) -> Any:
    """Proxy → Audit service — percentile rank of one agent vs. tenant peers."""
    resp = await request.app.state.client.get(
        f"{_base()}/logs/peer-benchmark/{agent_id}",
        params=dict(request.query_params),
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/audit/tool-breakdown", tags=["audit"])
async def tool_risk_breakdown_proxy(request: Request) -> Any:
    """Proxy → Audit service — per-tool deny rate and risk score breakdown."""
    resp = await request.app.state.client.get(
        f"{_base()}/logs/tool-breakdown",
        params=dict(request.query_params),
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/audit/risk-trend/{agent_id}", tags=["audit"])
async def agent_risk_trend_proxy(agent_id: str, request: Request) -> Any:
    """Proxy → Audit service — 30-day daily risk score trend for one agent."""
    resp = await request.app.state.client.get(
        f"{_base()}/logs/risk-trend/{agent_id}",
        params=dict(request.query_params),
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/audit/hourly-activity", tags=["audit"])
async def audit_hourly_activity_proxy(request: Request) -> Any:
    """Proxy → Audit service — decision velocity by hour-of-day."""
    resp = await request.app.state.client.get(
        f"{_base()}/logs/hourly-activity",
        params=dict(request.query_params),
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/audit/risk-histogram", tags=["audit"])
async def audit_risk_histogram_proxy(request: Request) -> Any:
    """Proxy → Audit service — risk score frequency distribution histogram."""
    resp = await request.app.state.client.get(
        f"{_base()}/logs/risk-histogram",
        params=dict(request.query_params),
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/audit/weekly-heatmap", tags=["audit"])
async def audit_weekly_heatmap_proxy(request: Request) -> Any:
    """Proxy → Audit service — 7×24 weekly activity heatmap."""
    resp = await request.app.state.client.get(
        f"{_base()}/logs/weekly-heatmap",
        params=dict(request.query_params),
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/audit/decision-trend", tags=["audit"])
async def audit_decision_trend_proxy(request: Request) -> Any:
    """Proxy → Audit service — daily decision outcome breakdown."""
    resp = await request.app.state.client.get(
        f"{_base()}/logs/decision-trend",
        params=dict(request.query_params),
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/audit/agent-activity", tags=["audit"])
async def audit_agent_activity_proxy(request: Request) -> Any:
    """Proxy → Audit service — per-agent activity summary table."""
    resp = await request.app.state.client.get(
        f"{_base()}/logs/agent-activity",
        params=dict(request.query_params),
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/audit/high-risk-events", tags=["audit"])
async def audit_high_risk_events_proxy(request: Request) -> Any:
    """Proxy → Audit service — recent events at or above risk threshold."""
    resp = await request.app.state.client.get(
        f"{_base()}/logs/high-risk-events",
        params=dict(request.query_params),
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/audit/deny-reasons", tags=["audit"])
async def audit_deny_reasons_proxy(request: Request) -> Any:
    """Proxy → Audit service — top deny reason strings by frequency."""
    resp = await request.app.state.client.get(
        f"{_base()}/logs/deny-reasons",
        params=dict(request.query_params),
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/audit/tool-usage/{agent_id}", tags=["audit"])
async def audit_tool_usage_proxy(agent_id: str, request: Request) -> Any:
    """Proxy → Audit service — per-tool call stats for a single agent."""
    resp = await request.app.state.client.get(
        f"{_base()}/logs/tool-usage/{agent_id}",
        params=dict(request.query_params),
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/audit/tool-risk", tags=["audit"])
async def audit_tool_risk_proxy(request: Request) -> Any:
    """Proxy → Audit service — cross-agent tool risk leaderboard."""
    resp = await request.app.state.client.get(
        f"{_base()}/logs/tool-risk",
        params=dict(request.query_params),
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/audit/risk-percentile-trend", tags=["audit"])
async def audit_risk_percentile_trend_proxy(request: Request) -> Any:
    """Proxy → Audit service — daily p50/p75/p95 risk score percentiles."""
    resp = await request.app.state.client.get(
        f"{_base()}/logs/risk-percentile-trend",
        params=dict(request.query_params),
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/audit/daily-active-agents", tags=["audit"])
async def audit_daily_active_agents_proxy(request: Request) -> Any:
    """Proxy → Audit service — distinct active agents per day."""
    resp = await request.app.state.client.get(
        f"{_base()}/logs/daily-active-agents",
        params=dict(request.query_params),
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/audit/finding-breakdown", tags=["audit"])
async def audit_finding_breakdown_proxy(request: Request) -> Any:
    """Proxy → Audit service — ranked frequency of canonical finding types."""
    resp = await request.app.state.client.get(
        f"{_base()}/logs/finding-breakdown",
        params=dict(request.query_params),
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/audit/agent-daily-decisions/{agent_id}", tags=["audit"])
async def audit_agent_daily_decisions_proxy(agent_id: str, request: Request) -> Any:
    """Proxy → Audit service — daily allow/deny counts for a single agent."""
    resp = await request.app.state.client.get(
        f"{_base()}/logs/agent-daily-decisions/{agent_id}",
        params=dict(request.query_params),
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/audit/agent-findings/{agent_id}", tags=["audit"])
async def audit_agent_findings_proxy(agent_id: str, request: Request) -> Any:
    """Proxy → Audit service — ranked finding type frequency for a single agent."""
    resp = await request.app.state.client.get(
        f"{_base()}/logs/agent-findings/{agent_id}",
        params=dict(request.query_params),
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/audit/posture-score-trend", tags=["audit"])
async def audit_posture_score_trend_proxy(request: Request) -> Any:
    """Proxy → Audit service — daily tenant posture score trend."""
    resp = await request.app.state.client.get(
        f"{_base()}/logs/posture-score-trend",
        params=dict(request.query_params),
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/audit/escalation-rate-trend", tags=["audit"])
async def audit_escalation_rate_trend_proxy(request: Request) -> Any:
    """Proxy → Audit service — daily escalation rate trend."""
    resp = await request.app.state.client.get(
        f"{_base()}/logs/escalation-rate-trend",
        params=dict(request.query_params),
        headers=internal_headers(request),
    )
    return passthrough(resp)
