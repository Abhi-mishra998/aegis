"""Gateway proxy routes for billing + usage.

All 13 ``/billing/*`` and ``/usage/*`` routes lifted out of
services/gateway/main.py in the sprint-5 audit cleanup. Every route
proxies to the usage service which is the system of record for
financial events, invoices, budget requests, and per-agent cost
attribution.

The POST ``/billing/events`` handler emits an SSE ``billing_updated``
event after a successful upstream write so dashboard cost tiles refresh
without polling — same shape as the inline handler that used to live in
main.py.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from redis.asyncio import Redis

from sdk.common.config import settings
from sdk.common.redis import get_redis_client
from services.gateway._helpers import (
    internal_headers,
    passthrough,
    publish_event,
)

router = APIRouter()

_redis: Redis = get_redis_client(settings.REDIS_URL, decode_responses=False)


def _usage_base() -> str:
    return settings.USAGE_SERVICE_URL.rstrip("/")


# ── Billing analytics ─────────────────────────────────────────────────────

@router.get("/billing/cost-attribution", tags=["billing"])
async def billing_cost_attribution(request: Request) -> Any:
    """Proxy → Usage service per-agent weekly cost attribution."""
    resp = await request.app.state.client.get(
        f"{_usage_base()}/billing/cost-attribution",
        params=dict(request.query_params),
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/billing/invoices", tags=["billing"])
async def billing_invoices(request: Request) -> Any:
    """Proxy → Usage service billing invoices."""
    resp = await request.app.state.client.get(
        f"{_usage_base()}/billing/invoices",
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/billing/summary", tags=["billing"])
async def billing_summary(request: Request) -> Any:
    """Proxy → Usage service Redis-based billing ROI summary."""
    resp = await request.app.state.client.get(
        f"{_usage_base()}/billing/summary",
        headers=internal_headers(request),
    )
    return passthrough(resp)


# ── Billing events (with SSE fan-out) ─────────────────────────────────────

@router.post("/billing/events", tags=["billing"])
async def billing_record_event(request: Request) -> Any:
    """Proxy → Usage service billing events (records money saved).

    On 200/201, publish a ``billing_updated`` SSE event to the per-tenant
    + per-agent Redis Pub/Sub channels so dashboard cost tiles refresh
    without polling.
    """
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{_usage_base()}/billing/events",
        json=body,
        headers=internal_headers(request),
    )
    if resp.status_code in (200, 201):
        tenant_id_str = request.headers.get("X-Tenant-ID", "") or (
            str(getattr(request.state, "tenant_id", "") or "")
        )
        if tenant_id_str:
            body_dict = body if isinstance(body, dict) else {}
            agent_id_val = str(body_dict.get("agent_id", "") or "")
            await publish_event(
                _redis, tenant_id_str, "billing_updated",
                {
                    "agent_id": agent_id_val or None,
                    "tool":     body_dict.get("tool"),
                    "action":   body_dict.get("action"),
                    "cost":     body_dict.get("cost") or body_dict.get("amount"),
                    "units":    body_dict.get("units") or body_dict.get("quantity"),
                    "audit_id": body_dict.get("audit_id"),
                },
                agent_id=agent_id_val or None,
            )
    return passthrough(resp)


# ── Budget requests ───────────────────────────────────────────────────────

@router.post("/billing/budget-requests", tags=["billing"])
async def billing_budget_requests_create(request: Request) -> Any:
    """Proxy → Usage service: create a budget increase request."""
    body = await request.body()
    resp = await request.app.state.client.post(
        f"{_usage_base()}/billing/budget-requests",
        content=body,
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/billing/budget-requests", tags=["billing"])
async def billing_budget_requests_list(request: Request) -> Any:
    """Proxy → Usage service: list budget requests for tenant."""
    resp = await request.app.state.client.get(
        f"{_usage_base()}/billing/budget-requests",
        params=dict(request.query_params),
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/billing/budget-requests/{req_id}", tags=["billing"])
async def billing_budget_request_get(req_id: str, request: Request) -> Any:
    """Proxy → Usage service: get a single budget request."""
    resp = await request.app.state.client.get(
        f"{_usage_base()}/billing/budget-requests/{req_id}",
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.post("/billing/budget-requests/{req_id}/approve", tags=["billing"])
async def billing_budget_request_approve(req_id: str, request: Request) -> Any:
    """Proxy → Usage service: approve a budget request."""
    body = await request.body()
    resp = await request.app.state.client.post(
        f"{_usage_base()}/billing/budget-requests/{req_id}/approve",
        content=body,
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.post("/billing/budget-requests/{req_id}/reject", tags=["billing"])
async def billing_budget_request_reject(req_id: str, request: Request) -> Any:
    """Proxy → Usage service: reject a budget request."""
    body = await request.body()
    resp = await request.app.state.client.post(
        f"{_usage_base()}/billing/budget-requests/{req_id}/reject",
        content=body,
        headers=internal_headers(request),
    )
    return passthrough(resp)


# ── Usage ────────────────────────────────────────────────────────────────

@router.post("/usage/record", tags=["usage"])
async def usage_record(request: Request) -> Any:
    """Proxy → Usage service tool execution recording."""
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{_usage_base()}/usage/record",
        json=body,
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/usage/summary", tags=["usage"])
async def usage_summary(request: Request) -> Any:
    """Proxy → Usage service tenant usage summary."""
    resp = await request.app.state.client.get(
        f"{_usage_base()}/usage/summary",
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/usage/dashboard", tags=["usage"])
async def usage_dashboard(request: Request) -> Any:
    """Proxy → Usage service revenue dashboard (injecting X-Internal-Secret)."""
    resp = await request.app.state.client.get(
        f"{_usage_base()}/usage/dashboard",
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/usage/anomalies", tags=["usage"])
async def usage_anomalies(request: Request) -> Any:
    """Proxy → Usage service billing anomalies (injecting X-Internal-Secret)."""
    resp = await request.app.state.client.get(
        f"{_usage_base()}/usage/anomalies",
        headers=internal_headers(request),
    )
    return passthrough(resp)
