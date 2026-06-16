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

import uuid
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, HTTPException, Request

from redis.asyncio import Redis

from sdk.common.config import settings
from sdk.common.redis import get_redis_client
from services.gateway._helpers import (
    internal_headers,
    passthrough,
    publish_event,
)
from services.gateway.client import service_client

router = APIRouter()
logger = structlog.get_logger(__name__)


# Sprint 9 — Stripe price-tier map. Keys are canonical Aegis tier names
# (matches the tenants.tier enum); values are Stripe Price IDs from
# settings. Used by the checkout-session endpoint to pick the right price.
def _tier_to_price() -> dict[str, str]:
    return {
        "pro":        settings.STRIPE_PRO_PRICE_ID,
        "enterprise": settings.STRIPE_ENTERPRISE_PRICE_ID,
    }


_STRIPE_API_BASE = "https://api.stripe.com/v1"
_STRIPE_TIMEOUT_SECONDS = 8.0


async def _stripe_post(path: str, form_data: dict[str, Any]) -> dict[str, Any]:
    """POST to the Stripe API with the configured secret. Form-urlencoded body."""
    if not settings.STRIPE_SECRET_KEY:
        raise HTTPException(
            status_code=503,
            detail="STRIPE_SECRET_KEY not configured on the gateway",
        )
    url = f"{_STRIPE_API_BASE}{path}"
    headers = {
        "Authorization": f"Bearer {settings.STRIPE_SECRET_KEY}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    try:
        async with httpx.AsyncClient(timeout=_STRIPE_TIMEOUT_SECONDS) as client:
            resp = await client.post(url, headers=headers, data=form_data)
    except httpx.HTTPError as exc:
        logger.error("stripe_api_transport_error", path=path, error=str(exc))
        raise HTTPException(
            status_code=502, detail="Stripe API unreachable",
        ) from exc
    if resp.status_code >= 400:
        logger.error(
            "stripe_api_error", path=path, status=resp.status_code,
            body=resp.text[:200],
        )
        raise HTTPException(
            status_code=502,
            detail=f"Stripe API returned {resp.status_code}: {resp.text[:160]}",
        )
    try:
        return resp.json()
    except ValueError:
        return {}

_redis: Redis = get_redis_client(settings.REDIS_URL, decode_responses=False)


def _usage_base() -> str:
    return settings.USAGE_SERVICE_URL.rstrip("/")


# ── Sprint 9 — Stripe Checkout + Portal + Plan ────────────────────────────


def _resolve_tenant_id(request: Request) -> str:
    tid = getattr(request.state, "tenant_id", "") or request.headers.get("X-Tenant-ID", "")
    if not tid:
        raise HTTPException(status_code=401, detail="tenant_id missing on request")
    return str(tid)


@router.get("/billing/plan", tags=["billing"])
async def billing_plan(request: Request) -> Any:
    """Sprint 9 — Return the workspace's current tier + the upgrade options.

    Reads tier from the gateway's TenantMetadataCache (TenantMetadataCache
    polls the identity service every 10 min; PATCH /admin/tenants/{id}
    busts it).
    """
    tenant_id_str = _resolve_tenant_id(request)
    try:
        tenant_meta = await service_client.get_tenant_metadata(
            uuid.UUID(tenant_id_str),
        )
    except Exception as exc:
        logger.warning("billing_plan_tenant_meta_failed", error=str(exc))
        tenant_meta = {}
    tier = (tenant_meta.get("tier") or "basic").lower()
    available_upgrades = []
    if settings.STRIPE_PRO_PRICE_ID:
        available_upgrades.append({"tier": "pro", "price_id": settings.STRIPE_PRO_PRICE_ID})
    if settings.STRIPE_ENTERPRISE_PRICE_ID:
        available_upgrades.append(
            {"tier": "enterprise", "price_id": settings.STRIPE_ENTERPRISE_PRICE_ID},
        )
    return {
        "tier":               tier,
        "stripe_configured":  bool(settings.STRIPE_SECRET_KEY),
        "available_upgrades": available_upgrades,
        "checkout_success_url": settings.STRIPE_CHECKOUT_SUCCESS_URL,
        "checkout_cancel_url":  settings.STRIPE_CHECKOUT_CANCEL_URL,
    }


@router.post("/billing/checkout-session", tags=["billing"])
async def billing_checkout_session(request: Request) -> Any:
    """Sprint 9 — Create a Stripe Checkout Session for the target tier.

    Body: ``{"tier": "pro"|"enterprise"}``.

    Returns ``{url, session_id, tier}``. The frontend redirects the
    browser to ``url``; on completion Stripe fires the webhook handled
    by services/gateway/routers/stripe_webhook.py which patches the
    tenant's tier via /admin/tenants/{id}.
    """
    tenant_id_str = _resolve_tenant_id(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    tier = str((body or {}).get("tier", "")).lower()
    if tier not in _tier_to_price():
        raise HTTPException(
            status_code=400,
            detail=f"tier must be one of {sorted(_tier_to_price().keys())}",
        )
    price_id = _tier_to_price()[tier]
    if not price_id:
        raise HTTPException(
            status_code=503,
            detail=f"No Stripe Price ID configured for tier {tier!r}",
        )

    # Customer email — pull from the JWT-validated user state. Stripe
    # uses this to pre-fill the checkout form.
    customer_email = getattr(request.state, "actor", "") or ""

    form_data: dict[str, Any] = {
        "mode":                                  "subscription",
        "line_items[0][price]":                  price_id,
        "line_items[0][quantity]":               "1",
        "success_url":                           settings.STRIPE_CHECKOUT_SUCCESS_URL,
        "cancel_url":                            settings.STRIPE_CHECKOUT_CANCEL_URL,
        # tenant_id rides on both the session AND the subscription so
        # the webhook handler at stripe_webhook.py can map back. The
        # subscription metadata is what the existing handler reads.
        "metadata[tenant_id]":                   tenant_id_str,
        "metadata[tier]":                        tier,
        "subscription_data[metadata][tenant_id]": tenant_id_str,
        "subscription_data[metadata][tier]":     tier,
    }
    if customer_email:
        form_data["customer_email"] = customer_email

    body = await _stripe_post("/checkout/sessions", form_data)
    url = body.get("url") or ""
    session_id = body.get("id") or ""
    if not url:
        raise HTTPException(
            status_code=502, detail="Stripe Checkout returned no URL",
        )
    return {"url": url, "session_id": session_id, "tier": tier}


@router.post("/billing/portal-session", tags=["billing"])
async def billing_portal_session(request: Request) -> Any:
    """Sprint 9 — Create a Stripe Customer Portal session.

    Body: ``{"customer_id": "cus_..."}``  (Stripe Customer ID).
    Returns ``{url}``. The frontend redirects to that URL for the customer
    to manage / cancel / update payment method.

    If the workspace doesn't have a customer_id yet (i.e. they never
    completed a checkout), returns 409 — the frontend should send them
    through the Checkout flow instead.
    """
    _ = _resolve_tenant_id(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    customer_id = str((body or {}).get("customer_id", ""))
    if not customer_id:
        raise HTTPException(
            status_code=409,
            detail=(
                "No Stripe customer_id supplied. Complete a Checkout "
                "session first before opening the Customer Portal."
            ),
        )
    form_data = {
        "customer":   customer_id,
        "return_url": settings.STRIPE_PORTAL_RETURN_URL,
    }
    body = await _stripe_post("/billing_portal/sessions", form_data)
    url = body.get("url") or ""
    if not url:
        raise HTTPException(
            status_code=502, detail="Stripe Portal returned no URL",
        )
    return {"url": url}


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


# ── Sprint 4.4 — Agent FinOps burn-down ──────────────────────────────────


@router.get("/usage/fleet/burn-down", tags=["usage"])
async def usage_burn_down(request: Request) -> Any:
    """Proxy → ``GET /usage/fleet/burn-down`` (per-tenant / per-agent cap status)."""
    resp = await request.app.state.client.get(
        f"{_usage_base()}/usage/fleet/burn-down",
        params=dict(request.query_params),
        headers=internal_headers(request),
    )
    return passthrough(resp)
