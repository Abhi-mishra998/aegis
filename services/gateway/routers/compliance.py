"""Gateway proxy routes for compliance, SIEM integration, and scheduled
reports.

All 18 routes lifted out of services/gateway/main.py in the sprint-5
audit cleanup. Three related concerns share this module because they
all proxy to the audit service's compliance sub-router:

  /compliance/*                 — framework bundles + exports
  /siem/*                       — Splunk / Datadog credentials + push
  /reports/scheduled/*          — CRUD + run + history

Streaming endpoints (``POST /compliance/export``, ``POST /compliance/
board-report``) build their own ``StreamingResponse`` because the
upstream returns binary PDFs.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from sdk.common.config import settings
from services.gateway._helpers import (
    internal_headers,
    passthrough,
    trust_proxy,
)

router = APIRouter()


def _base() -> str:
    return settings.AUDIT_SERVICE_URL.rstrip("/")


# ── Compliance framework bundles ─────────────────────────────────────────

@router.get("/compliance/frameworks", tags=["compliance"])
async def compliance_frameworks(request: Request) -> Any:
    """Sprint U13 2026-06-26 — proxy → audit service framework discovery.

    The aegis-guide.md §32-F-19 walkthrough needed a way to discover the
    valid framework names without already knowing them (the previous
    UX was 'try one, read the 400 error'). Public — returns metadata
    only, no tenant data. Exempted in middleware.py exempt-paths."""
    return await trust_proxy(settings.AUDIT_SERVICE_URL, "/compliance/frameworks", request)


@router.get("/compliance/eu-ai-act", tags=["compliance"])
async def compliance_eu_ai_act(request: Request) -> Any:
    """Proxy → Audit service EU AI Act compliance bundle."""
    return await trust_proxy(settings.AUDIT_SERVICE_URL, "/compliance/eu-ai-act", request)


@router.get("/compliance/nist-ai-rmf", tags=["compliance"])
async def compliance_nist_ai_rmf(request: Request) -> Any:
    """Proxy → Audit service NIST AI RMF compliance bundle."""
    return await trust_proxy(settings.AUDIT_SERVICE_URL, "/compliance/nist-ai-rmf", request)


@router.get("/compliance/soc2", tags=["compliance"])
async def compliance_soc2(request: Request) -> Any:
    """Proxy → Audit service SOC 2 Type II compliance bundle."""
    return await trust_proxy(settings.AUDIT_SERVICE_URL, "/compliance/soc2", request)


@router.get("/compliance/tool-ledger", tags=["compliance"])
async def compliance_tool_ledger(request: Request) -> Any:
    """Proxy → Audit service per-agent tamper-evident tool-call ledger."""
    return await trust_proxy(settings.AUDIT_SERVICE_URL, "/compliance/tool-ledger", request)


@router.get("/compliance/dpdp", tags=["compliance"])
async def compliance_dpdp(request: Request) -> Any:
    """Proxy → Audit service India DPDP Act + Rules (Nov 2025) evidence bundle (A5)."""
    return await trust_proxy(settings.AUDIT_SERVICE_URL, "/compliance/dpdp", request)


@router.get("/compliance/export/grc", tags=["compliance"])
async def compliance_export_grc(request: Request) -> Response:
    """A6 — Stream GRC (Vanta / Drata) control-evidence export through.

    Query params: period_start, period_end (ISO-8601), format (json|csv).
    Streamed because the CSV variant can be tens of MB for a long period.
    """
    upstream_req = request.app.state.client.build_request(
        "GET",
        f"{_base()}/compliance/export/grc",
        params=dict(request.query_params),
        headers=internal_headers(request),
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
        media_type=upstream.headers.get("content-type", "application/json"),
        headers=forward_headers,
    )


# ── PDF exports (streamed) ───────────────────────────────────────────────

@router.get("/compliance/verifiable-bundle/{framework}", tags=["compliance"])
async def compliance_verifiable_bundle(framework: str, request: Request) -> Response:
    """R2 — Proxy → audit service offline-verifiable evidence bundle.

    framework ∈ {eu-ai-act, nist-ai-rmf, soc2}. Query params:
    ``period_start``, ``period_end`` (ISO-8601). The downloaded JSON
    is consumed by `python -m aegis_verify --bundle <file>` for
    offline cryptographic verification.
    """
    upstream_req = request.app.state.client.build_request(
        "GET",
        f"{_base()}/compliance/verifiable-bundle/{framework}",
        params=dict(request.query_params),
        headers=internal_headers(request),
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
        media_type=upstream.headers.get("content-type", "application/json"),
        headers=forward_headers,
    )


@router.get("/compliance/export/{bundle_type}", tags=["compliance"])
async def compliance_export_get(bundle_type: str, request: Request) -> Response:
    """Proxy → ``GET /compliance/export/{bundle_type}`` (JSON bundle).

    Streams the bundle JSON download. bundle_type ∈
    {tool-ledger, eu-ai-act, nist-ai-rmf, soc2}. Query params:
    ``period_start``, ``period_end`` (ISO-8601).
    """
    upstream_req = request.app.state.client.build_request(
        "GET",
        f"{_base()}/compliance/export/{bundle_type}",
        params=dict(request.query_params),
        headers=internal_headers(request),
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
        media_type=upstream.headers.get("content-type", "application/json"),
        headers=forward_headers,
    )


# Sprint S6 (2026-06-19) — One-click SOC 2 evidence ZIP. Wraps the
# existing /compliance/export/soc2 JSON output + the daily Merkle
# roots into the auditor-friendly ZIP shape documented in
# services/audit/compliance_export.py. Returns the ZIP bytes directly
# as application/zip with Content-Disposition: attachment.
@router.get("/compliance/zip/{framework}", tags=["compliance"])
async def compliance_zip(framework: str, request: Request) -> Response:
    from datetime import UTC, datetime, timedelta

    from services.audit.compliance_export import (
        build_soc2_zip,
        soc2_bundle_filename,
    )

    if framework.lower() != "soc2":
        return Response(
            content=f"framework '{framework}' is not yet supported by the ZIP exporter. "
                    f"Try /compliance/export/{framework} for the JSON view.",
            status_code=400,
            media_type="text/plain",
        )

    qp = dict(request.query_params)
    period_end = datetime.fromisoformat(qp.get("period_end", datetime.now(UTC).isoformat()))
    period_start = datetime.fromisoformat(
        qp.get("period_start", (period_end - timedelta(days=90)).isoformat()),
    )

    # 1. Fetch the JSON audit rows for the period.
    upstream = await request.app.state.client.get(
        f"{_base()}/compliance/export/soc2",
        params={
            "period_start": period_start.isoformat(),
            "period_end":   period_end.isoformat(),
        },
        headers=internal_headers(request),
    )
    if upstream.status_code != 200:
        return Response(
            content=f"audit service returned {upstream.status_code}: {upstream.text[:300]}",
            status_code=502,
            media_type="text/plain",
        )
    bundle = upstream.json()
    audit_rows = bundle.get("rows") or bundle.get("data") or []

    # 2. Fetch the per-day chain roots.
    chain_upstream = await request.app.state.client.get(
        f"{_base()}/transparency/roots",
        params={"after": period_start.date().isoformat(), "before": period_end.date().isoformat()},
        headers=internal_headers(request),
    )
    chain_proofs: dict[str, dict] = {}
    if chain_upstream.status_code == 200:
        for root in (chain_upstream.json().get("roots") or []):
            day = root.get("root_date") or root.get("day")
            if day:
                chain_proofs[day] = root

    # 3. Assemble the ZIP.
    zip_bytes = build_soc2_zip(
        audit_rows=audit_rows,
        chain_proofs=chain_proofs,
        period_start=period_start,
        period_end=period_end,
    )
    filename = soc2_bundle_filename(period_start, period_end)
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(zip_bytes)),
        },
    )


@router.post("/compliance/export", tags=["compliance"])
async def compliance_export(request: Request) -> Response:
    """Proxy → Audit service compliance PDF/JSON export.

    Streams the upstream response bytes directly so PDF downloads work
    correctly. Query params: ``framework`` (EU_AI_ACT|NIST_AI_RMF|SOC2),
    ``start_date``, ``end_date``, ``format`` (pdf|json).
    """
    upstream_req = request.app.state.client.build_request(
        "POST",
        f"{_base()}/compliance/export",
        params=dict(request.query_params),
        headers=internal_headers(request),
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


@router.post("/compliance/board-report", tags=["compliance"])
async def board_report_proxy(request: Request) -> Response:
    """Proxy → Audit service board-level executive PDF report (streamed)."""
    body = await request.body()
    upstream_req = request.app.state.client.build_request(
        "POST",
        f"{_base()}/board-report",
        content=body,
        headers=internal_headers(request),
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

    return StreamingResponse(
        _relay(),
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/pdf"),
        headers=forward_headers,
    )


# ── SIEM integration (proxies to audit /compliance/siem/*) ───────────────

@router.get("/siem/config", tags=["siem"])
async def get_siem_config_proxy(request: Request) -> Any:
    """Proxy → Audit service SIEM config (masked)."""
    return await trust_proxy(settings.AUDIT_SERVICE_URL, "/compliance/siem/config", request)


@router.post("/siem/config", tags=["siem"])
async def save_siem_config_proxy(request: Request) -> Any:
    """Proxy → Audit service — save Splunk/Datadog credentials."""
    return await trust_proxy(settings.AUDIT_SERVICE_URL, "/compliance/siem/config", request)


@router.post("/siem/test/splunk", tags=["siem"])
async def test_splunk_proxy(request: Request) -> Any:
    """Proxy → Audit service — test Splunk HEC connectivity."""
    return await trust_proxy(settings.AUDIT_SERVICE_URL, "/compliance/siem/test/splunk", request)


@router.post("/siem/test/datadog", tags=["siem"])
async def test_datadog_proxy(request: Request) -> Any:
    """Proxy → Audit service — test Datadog Logs connectivity."""
    return await trust_proxy(settings.AUDIT_SERVICE_URL, "/compliance/siem/test/datadog", request)


@router.post("/siem/push", tags=["siem"])
async def siem_push_proxy(request: Request) -> Any:
    """Proxy → Audit service — manually push last N audit events to SIEM."""
    return await trust_proxy(settings.AUDIT_SERVICE_URL, "/compliance/siem/push", request)


# ── Scheduled reports ────────────────────────────────────────────────────

@router.get("/reports/scheduled", tags=["reports"])
async def list_scheduled_reports_proxy(request: Request) -> Any:
    """Proxy → Audit service — list scheduled reports for tenant."""
    return await trust_proxy(settings.AUDIT_SERVICE_URL, "/compliance/scheduled-reports", request)


@router.post("/reports/scheduled", tags=["reports"])
async def create_scheduled_report_proxy(request: Request) -> Any:
    """Proxy → Audit service — create a new scheduled report config."""
    return await trust_proxy(settings.AUDIT_SERVICE_URL, "/compliance/scheduled-reports", request)


# /reports/scheduled/{report_id}/run + /history must precede the catch-all
# /reports/scheduled/{report_id} (same shape as the /playbooks ordering fix).

@router.post("/reports/scheduled/{report_id}/run", tags=["reports"])
async def run_scheduled_report_proxy(report_id: str, request: Request) -> Any:
    """Proxy → Audit service — trigger immediate report run (queues to Redis)."""
    return await trust_proxy(
        settings.AUDIT_SERVICE_URL, f"/compliance/scheduled-reports/{report_id}/run", request
    )


@router.get("/reports/scheduled/{report_id}/history", tags=["reports"])
async def report_delivery_history_proxy(report_id: str, request: Request) -> Any:
    """Proxy → Audit service — delivery history for one scheduled report."""
    resp = await request.app.state.client.get(
        f"{_base()}/compliance/scheduled-reports/{report_id}/history",
        params=dict(request.query_params),
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/reports/scheduled/{report_id}", tags=["reports"])
async def get_scheduled_report_proxy(report_id: str, request: Request) -> Any:
    """Proxy → Audit service — fetch a single scheduled report."""
    return await trust_proxy(
        settings.AUDIT_SERVICE_URL, f"/compliance/scheduled-reports/{report_id}", request
    )


@router.patch("/reports/scheduled/{report_id}", tags=["reports"])
async def update_scheduled_report_proxy(report_id: str, request: Request) -> Any:
    """Proxy → Audit service — update a scheduled report."""
    return await trust_proxy(
        settings.AUDIT_SERVICE_URL, f"/compliance/scheduled-reports/{report_id}", request
    )


@router.delete("/reports/scheduled/{report_id}", tags=["reports"])
async def delete_scheduled_report_proxy(report_id: str, request: Request) -> Any:
    """Proxy → Audit service — delete a scheduled report."""
    return await trust_proxy(
        settings.AUDIT_SERVICE_URL, f"/compliance/scheduled-reports/{report_id}", request
    )
