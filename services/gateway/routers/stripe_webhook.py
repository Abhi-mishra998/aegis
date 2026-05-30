"""Stripe billing webhook — sprint-5.3.

Receives Stripe events, verifies the signature, maps `subscription.updated`
events to tenant tier changes, and writes the new quota into
`acp_identity.tenants` (rps, burst, daily_request_cap, monthly_request_cap).

Public path: POST /billing/stripe/webhook. Skipped by the auth middleware
(_SKIP_PATHS in main.py) because Stripe authenticates via signature, not JWT.

Tier → quota mapping is declarative; adjust `_TIER_QUOTAS` to match the
pricing page. New tiers ship by adding rows here and on Stripe's side.

This file is a SCAFFOLD: it implements signature verification, idempotency,
the tier mapping, and a Postgres update through Identity service's API.
Production deploy requires:
  1. STRIPE_WEBHOOK_SECRET in .env (from Stripe dashboard).
  2. Mapping Stripe `price_id` values onto `_PRICE_ID_TO_TIER` below.
  3. Registering the webhook URL at https://dashboard.stripe.com/webhooks
     for the following events:
       - customer.subscription.created
       - customer.subscription.updated
       - customer.subscription.deleted
       - invoice.payment_failed   (downgrade to "starter" tier)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, HTTPException, Request, Response

from sdk.common.config import settings
from sdk.common.redis import get_redis_client

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["billing"])

_STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
_SIGNATURE_TOLERANCE_SECONDS = 300  # Stripe's default replay-protection window.

# Idempotency: every Stripe event has a unique id; we SETNX it for 24h so
# replays / retries are no-ops at the application layer.
_REDIS_IDEMPOTENCY_PREFIX = "acp:stripe:event_seen:"
_IDEMPOTENCY_TTL_SECONDS = 24 * 60 * 60

_redis = get_redis_client(settings.REDIS_URL, decode_responses=False)

# Tier name → (rps, burst, daily_cap, monthly_cap).
# Values mirror the columns added to acp_identity.tenants in the 2026-05-15
# quota sprint. Adjust to match the pricing page; the keys must match
# whatever `nickname` or `metadata.tier` Stripe ships on the price object.
_TIER_QUOTAS: dict[str, dict[str, int]] = {
    "starter":    {"rps": 5,    "burst": 20,    "daily": 5_000,     "monthly": 100_000},
    "pro":        {"rps": 50,   "burst": 200,   "daily": 50_000,    "monthly": 1_000_000},
    "enterprise": {"rps": 500,  "burst": 2_000, "daily": 500_000,   "monthly": 10_000_000},
}

# Map Stripe price IDs onto our tier names. Operators populate this from
# the Stripe dashboard "Price IDs" column when a new price is created.
# Keys are Stripe price IDs (price_XXXXXX...); values are tier names above.
_PRICE_ID_TO_TIER: dict[str, str] = {
    # Example — replace with real price IDs from Stripe before going live.
    "price_REPLACE_STARTER_ID":    "starter",
    "price_REPLACE_PRO_ID":        "pro",
    "price_REPLACE_ENTERPRISE_ID": "enterprise",
}


def _verify_signature(payload: bytes, signature_header: str) -> None:
    """Verify Stripe's Stripe-Signature header.

    Format: `t=<unix_ts>,v1=<hex_hmac_sha256>` (plus optional v0=...).
    Raises HTTPException(400) if the signature is invalid or stale.
    """
    if not _STRIPE_WEBHOOK_SECRET:
        # If no secret is configured, refuse to process — better to drop the
        # event than to trust an unsigned source.
        raise HTTPException(status_code=503, detail="Stripe webhook secret not configured")
    if not signature_header:
        raise HTTPException(status_code=400, detail="missing Stripe-Signature header")

    parts: dict[str, str] = {}
    for piece in signature_header.split(","):
        if "=" not in piece:
            continue
        k, v = piece.strip().split("=", 1)
        parts.setdefault(k, v)

    timestamp = parts.get("t")
    v1 = parts.get("v1")
    if not timestamp or not v1:
        raise HTTPException(status_code=400, detail="malformed Stripe-Signature header")

    try:
        ts = int(timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="non-integer timestamp") from exc
    if abs(time.time() - ts) > _SIGNATURE_TOLERANCE_SECONDS:
        raise HTTPException(status_code=400, detail="signature timestamp outside tolerance window")

    signed_payload = f"{timestamp}.".encode() + payload
    expected = hmac.new(
        key=_STRIPE_WEBHOOK_SECRET.encode(),
        msg=signed_payload,
        digestmod=hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, v1):
        raise HTTPException(status_code=400, detail="signature mismatch")


async def _claim_idempotency(event_id: str) -> bool:
    """Return True if this event_id has not been seen in the last 24h."""
    key = f"{_REDIS_IDEMPOTENCY_PREFIX}{event_id}".encode()
    return bool(await _redis.set(key, b"1", nx=True, ex=_IDEMPOTENCY_TTL_SECONDS))


async def _apply_tier(tenant_id: str, tier: str) -> None:
    """Patch the tenant's quota columns through the Identity service.

    Uses the gateway's internal-secret header so the Identity service
    accepts the call. The Identity admin endpoint exists at
    /admin/tenants/{tenant_id} (PATCH) — see services/identity/router.py.
    """
    quotas = _TIER_QUOTAS.get(tier)
    if not quotas:
        logger.error("stripe_unknown_tier", tier=tier, tenant_id=tenant_id)
        return

    payload = {
        "tier": tier,
        "requests_per_second":  quotas["rps"],
        "burst":                quotas["burst"],
        "daily_request_cap":    quotas["daily"],
        "monthly_request_cap":  quotas["monthly"],
    }
    headers = {
        "X-Internal-Secret": settings.INTERNAL_SECRET,
        "Content-Type": "application/json",
    }
    url = f"{settings.IDENTITY_SERVICE_URL.rstrip('/')}/admin/tenants/{tenant_id}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            resp = await c.patch(url, json=payload, headers=headers)
        if resp.status_code not in (200, 204):
            logger.error(
                "stripe_tier_apply_failed",
                tenant_id=tenant_id,
                tier=tier,
                status=resp.status_code,
                body=resp.text[:200],
            )
        else:
            logger.info("stripe_tier_applied", tenant_id=tenant_id, tier=tier)
    except Exception as exc:
        logger.error("stripe_tier_apply_exception", tenant_id=tenant_id, tier=tier, error=str(exc))


def _extract_tenant_id(event: dict[str, Any]) -> str | None:
    """Best-effort tenant_id extraction from the Stripe event.

    Customers map tenant_id onto Stripe's customer object via metadata —
    operators must set `metadata.tenant_id` on the Stripe customer
    record at signup. If the metadata is missing the event is dropped
    with a logged warning rather than silently changing the wrong tier.
    """
    obj = event.get("data", {}).get("object", {})
    metadata = obj.get("metadata", {}) or {}
    tenant_id = metadata.get("tenant_id")
    if tenant_id:
        return str(tenant_id)
    # Subscriptions reference a customer; the customer's metadata is the
    # canonical place for tenant_id. Subscription-level metadata is a
    # fallback in case the operator put it there instead.
    customer = obj.get("customer")
    if customer and isinstance(customer, dict):
        cust_meta = customer.get("metadata", {}) or {}
        return cust_meta.get("tenant_id")
    return None


def _extract_tier_from_subscription(event: dict[str, Any]) -> str | None:
    """Map a subscription event's price_id to a tier name."""
    obj = event.get("data", {}).get("object", {})
    items = obj.get("items", {}).get("data") or []
    if not items:
        return None
    price = items[0].get("price") or {}
    # Prefer explicit nickname/metadata.tier on the price; fall back to
    # the _PRICE_ID_TO_TIER mapping for operators who didn't set them.
    if (md := price.get("metadata", {}) or {}).get("tier"):
        return str(md["tier"])
    if (nickname := price.get("nickname")):
        return str(nickname).lower()
    return _PRICE_ID_TO_TIER.get(price.get("id", ""))


@router.post("/billing/stripe/webhook", include_in_schema=False)
async def stripe_webhook(request: Request) -> Response:
    """Stripe event handler — see module docstring."""
    payload = await request.body()
    signature = request.headers.get("Stripe-Signature", "")
    _verify_signature(payload, signature)

    try:
        event = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="malformed JSON body") from exc

    event_id = event.get("id")
    if not event_id:
        raise HTTPException(status_code=400, detail="missing event id")
    event_type = event.get("type", "")

    if not await _claim_idempotency(event_id):
        logger.info("stripe_event_duplicate", event_id=event_id, event_type=event_type)
        return Response(status_code=200, content=b'{"received":true,"duplicate":true}', media_type="application/json")

    tenant_id = _extract_tenant_id(event)
    if not tenant_id:
        logger.warning("stripe_event_missing_tenant", event_id=event_id, event_type=event_type)
        # Acknowledge with 200 so Stripe doesn't retry — the operator must
        # backfill metadata.tenant_id on the customer record before the
        # next subscription event arrives.
        return Response(status_code=200, content=b'{"received":true,"reason":"missing tenant_id"}', media_type="application/json")

    if event_type in ("customer.subscription.created", "customer.subscription.updated"):
        tier = _extract_tier_from_subscription(event)
        if not tier or tier not in _TIER_QUOTAS:
            logger.warning("stripe_unmapped_price", event_id=event_id, event_type=event_type, tier=tier)
            return Response(status_code=200, content=b'{"received":true,"reason":"unmapped price"}', media_type="application/json")
        await _apply_tier(tenant_id, tier)

    elif event_type == "customer.subscription.deleted":
        # Subscription canceled → drop to starter tier (free).
        await _apply_tier(tenant_id, "starter")

    elif event_type == "invoice.payment_failed":
        # Payment failed → drop to starter tier until next successful invoice.
        logger.warning("stripe_invoice_failed_downgrade", tenant_id=tenant_id, event_id=event_id)
        await _apply_tier(tenant_id, "starter")

    else:
        logger.info("stripe_event_ignored", event_id=event_id, event_type=event_type)

    return Response(status_code=200, content=b'{"received":true}', media_type="application/json")
