"""Sprint 6 — RemediationPolicy load / round-trip tests."""
from __future__ import annotations

import pytest

from services.security.remediation.policy import (
    DEFAULT_POLICY,
    RemediationPolicy,
    policy_for_tenant,
    upsert_policy,
)


# Phase-2 cleanup 2026-06-15 — fake moved to tests/security/_fakes.py.
from tests.security._fakes import FakeRedis as _FakeRedis


@pytest.mark.asyncio
async def test_policy_unset_returns_defaults():
    r = _FakeRedis()
    p = await policy_for_tenant(r, "t-empty")
    assert p == DEFAULT_POLICY
    # Defaults: revoke + kill + audit on; page_oncall off; no webhook.
    assert p.revoke_api_keys is True
    assert p.kill_active_tokens is True
    assert p.audit_log is True
    assert p.page_oncall is False
    assert p.webhook_url == ""


@pytest.mark.asyncio
async def test_policy_upsert_then_read_round_trip():
    r = _FakeRedis()
    p_in = RemediationPolicy(
        revoke_api_keys=True,
        kill_active_tokens=False,
        page_oncall=True,
        audit_log=True,
        webhook_url="https://hooks.slack.com/services/T0/X/Y",
    )
    await upsert_policy(r, "t1", p_in)
    p_out = await policy_for_tenant(r, "t1")
    assert p_out == p_in


@pytest.mark.asyncio
async def test_policy_partial_redis_falls_back_to_defaults_per_field():
    r = _FakeRedis()
    # Tenant only set webhook_url; everything else should default.
    await r.hset("acp:remediation:policy:t1", mapping={"webhook_url": "https://x"})
    p = await policy_for_tenant(r, "t1")
    assert p.webhook_url == "https://x"
    assert p.revoke_api_keys is True       # default
    assert p.kill_active_tokens is True    # default
    assert p.page_oncall is False          # default
    assert p.audit_log is True             # default


@pytest.mark.asyncio
async def test_policy_bool_coerces_str_yes_true_1():
    r = _FakeRedis()
    await r.hset("acp:remediation:policy:t1", mapping={
        "revoke_api_keys":    "yes",
        "kill_active_tokens": "true",
        "page_oncall":        "1",
        "audit_log":          "off",
        "webhook_url":        "",
    })
    p = await policy_for_tenant(r, "t1")
    assert p.revoke_api_keys is True
    assert p.kill_active_tokens is True
    assert p.page_oncall is True
    assert p.audit_log is False
