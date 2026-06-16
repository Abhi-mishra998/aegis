"""
Sprint 9 — Unit tests for the Stripe billing endpoints + helpers.

Exercises the pure logic (tier map + 503 fallback) without hitting
Stripe. The /billing/checkout-session integration with Stripe is
covered by the smoke probes on prod (401 no-auth + 503 when
STRIPE_SECRET_KEY missing).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from sdk.common.config import settings
from services.gateway.routers import billing


def _request_with_tenant():
    return SimpleNamespace(
        state=SimpleNamespace(
            tenant_id="11111111-1111-1111-1111-111111111111",
            actor="owner@example.com",
        ),
        headers={
            "X-Tenant-ID": "11111111-1111-1111-1111-111111111111",
            "X-ACP-Actor": "owner@example.com",
        },
    )


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _reset_stripe_settings(monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "", raising=False)
    monkeypatch.setattr(settings, "STRIPE_PRO_PRICE_ID", "", raising=False)
    monkeypatch.setattr(settings, "STRIPE_ENTERPRISE_PRICE_ID", "", raising=False)
    yield


# ───────────────────────────────────────────────────────────────────────
# _tier_to_price
# ───────────────────────────────────────────────────────────────────────


def test_tier_to_price_only_exposes_known_tiers():
    assert set(billing._tier_to_price().keys()) == {"pro", "enterprise"}


def test_tier_to_price_carries_configured_values(monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_PRO_PRICE_ID", "price_pro_123", raising=False)
    monkeypatch.setattr(
        settings, "STRIPE_ENTERPRISE_PRICE_ID", "price_ent_456", raising=False,
    )
    mapping = billing._tier_to_price()
    assert mapping["pro"] == "price_pro_123"
    assert mapping["enterprise"] == "price_ent_456"


# ───────────────────────────────────────────────────────────────────────
# _stripe_post — 503 when key missing
# ───────────────────────────────────────────────────────────────────────


def test_stripe_post_503s_when_key_missing():
    with pytest.raises(HTTPException) as exc:
        _run(billing._stripe_post("/checkout/sessions", {"a": "b"}))
    assert exc.value.status_code == 503
    assert "STRIPE_SECRET_KEY" in exc.value.detail


# ───────────────────────────────────────────────────────────────────────
# /billing/checkout-session — validation
# ───────────────────────────────────────────────────────────────────────


def test_checkout_rejects_unknown_tier():
    req = _request_with_tenant()
    async def _body():
        return {"tier": "rogue"}
    req.json = _body
    with pytest.raises(HTTPException) as exc:
        _run(billing.billing_checkout_session(req))
    assert exc.value.status_code == 400


def test_checkout_503s_when_price_id_missing(monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_xxx", raising=False)
    monkeypatch.setattr(settings, "STRIPE_PRO_PRICE_ID", "", raising=False)
    req = _request_with_tenant()
    async def _body():
        return {"tier": "pro"}
    req.json = _body
    with pytest.raises(HTTPException) as exc:
        _run(billing.billing_checkout_session(req))
    assert exc.value.status_code == 503
    assert "Price ID" in exc.value.detail


# ───────────────────────────────────────────────────────────────────────
# /billing/portal-session — validation
# ───────────────────────────────────────────────────────────────────────


def test_portal_requires_customer_id():
    req = _request_with_tenant()
    async def _body():
        return {}
    req.json = _body
    with pytest.raises(HTTPException) as exc:
        _run(billing.billing_portal_session(req))
    assert exc.value.status_code == 409
    assert "Checkout" in exc.value.detail


# ───────────────────────────────────────────────────────────────────────
# /billing/plan — reads tier from tenant metadata
# ───────────────────────────────────────────────────────────────────────


def test_billing_plan_returns_tier_and_stripe_status(monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_xxx", raising=False)
    monkeypatch.setattr(settings, "STRIPE_PRO_PRICE_ID", "price_pro", raising=False)
    req = _request_with_tenant()
    with patch.object(
        billing.service_client,
        "get_tenant_metadata",
        AsyncMock(return_value={"tier": "PRO"}),
    ):
        out = _run(billing.billing_plan(req))
    assert out["tier"] == "pro"
    assert out["stripe_configured"] is True
    assert any(u["tier"] == "pro" for u in out["available_upgrades"])
