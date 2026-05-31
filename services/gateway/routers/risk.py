"""Gateway proxy routes for risk analytics, threat-intel, and insight feeds.

9 routes lifted out of services/gateway/main.py in the sprint-5 audit
cleanup. The three prefixes share one module because they're all
read-only dashboard feeds:

  /risk/*              — risk dashboard aggregates (proxied to audit
                         + decision services; ``/risk/summary``
                         intentionally piggybacks on the audit logs
                         summary endpoint)
  /threat-intel/*      — IP/domain enrichment + summary counters
                         (proxied to audit's compliance sub-router)
  /insights/recent     — recent AI analysis results from the insight
                         service
  /playbooks/autotrigger-stats — auto-trigger counter rollup from the
                                 autonomy service (kept here because
                                 it's a single dashboard endpoint and
                                 belongs in the read-only cluster)
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from sdk.common.config import settings
from services.gateway._helpers import (
    clamp_int,
    internal_headers,
    passthrough,
    trust_proxy,
)

router = APIRouter()


def _audit_base() -> str:
    return settings.AUDIT_SERVICE_URL.rstrip("/")


# ── Risk dashboard aggregates ────────────────────────────────────────────

@router.get("/risk/summary", tags=["risk"])
async def risk_summary(request: Request) -> Any:
    """Proxy → Audit service summary for risk dashboard.

    Piggybacks on /logs/summary intentionally — the risk dashboard tile
    needs the same totals + threat counts the audit summary already
    computes; an extra endpoint would duplicate the same SQL.
    """
    resp = await request.app.state.client.get(
        f"{_audit_base()}/logs/summary",
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/risk/signal-weights", tags=["risk"])
async def risk_signal_weights(request: Request) -> Any:
    """Proxy → Decision service signal weights."""
    resp = await request.app.state.client.get(
        f"{settings.DECISION_SERVICE_URL.rstrip('/')}/decision/signal-weights",
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/risk/timeline", tags=["risk"])
async def risk_timeline(request: Request) -> Any:
    """Proxy → Audit service risk timeline. Forwards ?days= query param."""
    resp = await request.app.state.client.get(
        f"{_audit_base()}/logs/risk/timeline",
        params={"days": clamp_int(request.query_params.get("days"), 7, 1, 90)},
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/risk/top-threats", tags=["risk"])
async def risk_top_threats(request: Request) -> Any:
    """Proxy → Audit service top threats. Forwards ?limit= query param."""
    resp = await request.app.state.client.get(
        f"{_audit_base()}/logs/risk/top-threats",
        params={"limit": clamp_int(request.query_params.get("limit"), 10, 1, 100)},
        headers=internal_headers(request),
    )
    return passthrough(resp)


# ── Threat intelligence (proxied to audit /compliance/threat-intel/*) ────

@router.post("/threat-intel/ip", tags=["threat-intel"])
async def threat_intel_ip_proxy(request: Request) -> Any:
    """Proxy → Audit service — enrich an IP address via threat intelligence."""
    return await trust_proxy(settings.AUDIT_SERVICE_URL, "/compliance/threat-intel/ip", request)


@router.post("/threat-intel/domain", tags=["threat-intel"])
async def threat_intel_domain_proxy(request: Request) -> Any:
    """Proxy → Audit service — enrich a domain via threat intelligence."""
    return await trust_proxy(settings.AUDIT_SERVICE_URL, "/compliance/threat-intel/domain", request)


@router.get("/threat-intel/summary", tags=["threat-intel"])
async def threat_intel_summary_proxy(request: Request) -> Any:
    """Proxy → Audit service — return threat intel summary counters."""
    return await trust_proxy(settings.AUDIT_SERVICE_URL, "/compliance/threat-intel/summary", request)


# ── Recent AI insights ───────────────────────────────────────────────────

@router.get("/insights/recent", tags=["risk"])
async def get_recent_insights(request: Request) -> Any:
    """Proxy → Insight service for recent AI analysis results."""
    resp = await request.app.state.client.get(
        f"{settings.INSIGHT_SERVICE_URL.rstrip('/')}/insights",
        params=request.query_params,
        headers=internal_headers(request),
    )
    return passthrough(resp)


# ── Playbook auto-trigger counts (autonomy service) ──────────────────────

@router.get("/playbooks/autotrigger-stats", tags=["autonomy"])
async def playbook_autotrigger_stats(request: Request) -> Any:
    """Proxy → Autonomy service per-playbook auto-trigger counts."""
    resp = await request.app.state.client.get(
        f"{settings.AUTONOMY_SERVICE_URL.rstrip('/')}/autonomy/playbooks/autotrigger-stats",
        headers=internal_headers(request),
    )
    return passthrough(resp)
