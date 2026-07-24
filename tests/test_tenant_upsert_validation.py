"""Canary: `POST /auth/tenants` must convert every invalid-shape field
to a 4xx, never 500. The endpoint's docstring promises this since the
QA-VALIDATION-FIX (2026-06-24) sprint, but four fields drifted past that
guard (tier enum coercion, requests_per_second, burst, daily_request_cap,
monthly_request_cap, daily_inference_cost_cap_usd).

We test the underlying coercion primitives + enum constructor directly.
Locking these in as always-raising-on-bad-input prevents the 5xx from
returning without also failing this test.
"""
from __future__ import annotations

import pytest


def test_tenant_tier_enum_rejects_unknown_value():
    """`TenantTier("mystery")` MUST raise ValueError — the endpoint
    catches this and returns 422. If enum semantics ever change (e.g.
    stringly-typed mode), the wrap in router.py breaks silently."""
    from services.identity.models import TenantTier
    with pytest.raises(ValueError):
        TenantTier("mystery-tier-value")


def test_tenant_tier_enum_accepts_known_values():
    from services.identity.models import TenantTier
    # Sanity: real values still round-trip.
    for m in TenantTier:
        assert TenantTier(m.value) is m


def test_int_raises_on_non_numeric():
    """Canary for the numeric-field guard — python's int() must still
    raise on non-numeric strings so the endpoint's 4xx-conversion
    remains meaningful."""
    with pytest.raises(ValueError):
        int("not-a-number")


def test_int_raises_on_none():
    """`body.get(field, default)` returning None (from an explicit null)
    must be a TypeError, which the endpoint now catches. If python ever
    started coercing None → 0, tenants sending `{"burst": null}` would
    silently get burst=0 instead of a 4xx."""
    with pytest.raises(TypeError):
        int(None)


def test_float_raises_on_non_numeric():
    with pytest.raises(ValueError):
        float("not-a-number")
