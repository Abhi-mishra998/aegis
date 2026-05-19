"""Unit tests for the /status vs /system/health reconciliation sprint (2026-05-15).

Covers:

* LatencyWindow: rolling-window correctness — percentile math, lazy
  eviction past window_seconds, count() reflects live samples only.
* summary() canonical shape — every field the docs + dashboards depend on.
* The two singletons report distinct `scope` labels and do NOT share
  storage (they're intentionally separate windows).
* `_read_global_kill_switch` returns the canonical shape on every
  branch: disengaged / engaged-without-meta / engaged-with-meta /
  Redis-error.
* OpenAPI declarations: `_LATENCY_BLOCK_SCHEMA` lists every required
  field and `_KILL_SWITCH_BLOCK_SCHEMA` mirrors the runtime body.
"""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest


# --------------------------------------------------------------------------- #
# LatencyWindow                                                               #
# --------------------------------------------------------------------------- #


from services.gateway.latency_window import (
    LatencyWindow,
    end_to_end_window,
    gateway_internal_window,
)


@pytest.fixture(autouse=True)
def _reset_singletons():
    gateway_internal_window.reset_for_tests()
    end_to_end_window.reset_for_tests()
    yield
    gateway_internal_window.reset_for_tests()
    end_to_end_window.reset_for_tests()


class TestLatencyWindow:
    def test_invalid_scope_rejected(self):
        with pytest.raises(ValueError):
            LatencyWindow(scope="not_a_scope")

    def test_percentile_on_empty_is_zero(self):
        w = LatencyWindow(scope="gateway_internal")
        assert w.percentile(0.5) == 0.0
        s = w.summary()
        assert s["p50_ms"] == 0 and s["p95_ms"] == 0 and s["p99_ms"] == 0
        assert s["request_count"] == 0

    def test_percentile_on_single_sample(self):
        w = LatencyWindow(scope="gateway_internal")
        w.record(42)
        assert w.percentile(0.5) == 42
        assert w.percentile(0.95) == 42
        assert w.percentile(0.99) == 42

    def test_percentile_nearest_rank(self):
        w = LatencyWindow(scope="gateway_internal")
        for v in (10, 20, 30, 40, 50, 60, 70, 80, 90, 100):
            w.record(v)
        # 10 samples, nearest-rank on (n-1)*p:
        #   p50 → round(0.5*9) = 4  → vals[4] = 50
        #   p95 → round(0.95*9) ≈ 9 → vals[9] = 100
        #   p99 → round(0.99*9) ≈ 9 → vals[9] = 100
        s = w.summary()
        assert s["p50_ms"] == 50
        assert s["p95_ms"] == 100
        assert s["p99_ms"] == 100
        assert s["request_count"] == 10

    def test_invalid_percentile_raises(self):
        w = LatencyWindow(scope="end_to_end")
        with pytest.raises(ValueError):
            w.percentile(-0.1)
        with pytest.raises(ValueError):
            w.percentile(1.1)

    def test_negative_sample_dropped(self):
        w = LatencyWindow(scope="gateway_internal")
        w.record(-5)
        w.record(10)
        assert w.count() == 1
        assert w.percentile(0.5) == 10

    def test_sample_eviction_past_window(self, monkeypatch):
        """A sample older than window_seconds disappears on the next read."""
        w = LatencyWindow(scope="gateway_internal", window_seconds=1)
        w.record(99)
        # Move monotonic clock forward.
        real_mono = time.monotonic
        fake_now = real_mono() + 5
        monkeypatch.setattr("services.gateway.latency_window.time.monotonic",
                            lambda: fake_now)
        # Old sample evicted; window now empty.
        assert w.count() == 0
        assert w.percentile(0.5) == 0.0

    def test_summary_shape_is_canonical(self):
        w = LatencyWindow(scope="end_to_end", window_seconds=30)
        w.record(15)
        s = w.summary()
        assert set(s.keys()) == {
            "scope", "window_seconds", "p50_ms", "p95_ms",
            "p99_ms", "request_count", "computed_at",
        }
        assert s["scope"] == "end_to_end"
        assert s["window_seconds"] == 30
        # computed_at is an ISO-8601 UTC string.
        assert "T" in s["computed_at"] and s["computed_at"].endswith("+00:00")


class TestSingletonsAreDistinct:
    def test_scopes_differ(self):
        assert gateway_internal_window.scope == "gateway_internal"
        assert end_to_end_window.scope == "end_to_end"

    def test_storage_does_not_bleed(self):
        gateway_internal_window.record(11)
        end_to_end_window.record(34)
        gw = gateway_internal_window.summary()
        e2e = end_to_end_window.summary()
        # Same shape, different scope, distinct numbers.
        assert gw["scope"] == "gateway_internal"
        assert e2e["scope"] == "end_to_end"
        assert gw["p95_ms"] == 11
        assert e2e["p95_ms"] == 34
        assert gw["request_count"] == 1
        assert e2e["request_count"] == 1


# --------------------------------------------------------------------------- #
# /status: kill switch                                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_kill_switch_disengaged_default(monkeypatch):
    from services.gateway import main as gw

    fake_redis = AsyncMock()
    fake_redis.exists = AsyncMock(return_value=0)
    fake_redis.get = AsyncMock(return_value=None)
    monkeypatch.setattr(gw, "redis", fake_redis)

    out = await gw._read_global_kill_switch()
    assert out == {
        "engaged": False, "last_toggled_at": None,
        "actor": None, "reason": None,
    }


@pytest.mark.asyncio
async def test_kill_switch_engaged_with_meta(monkeypatch):
    from services.gateway import main as gw

    meta = {
        "last_toggled_at": "2026-05-15T18:30:00+00:00",
        "actor": "admin@example.com",
        "reason": "emergency_lockdown",
    }
    fake_redis = AsyncMock()
    fake_redis.exists = AsyncMock(return_value=1)
    fake_redis.get = AsyncMock(return_value=json.dumps(meta).encode())
    monkeypatch.setattr(gw, "redis", fake_redis)

    out = await gw._read_global_kill_switch()
    assert out["engaged"] is True
    assert out["last_toggled_at"] == meta["last_toggled_at"]
    assert out["actor"] == "admin@example.com"
    assert out["reason"] == "emergency_lockdown"


@pytest.mark.asyncio
async def test_kill_switch_engaged_without_meta(monkeypatch):
    from services.gateway import main as gw

    fake_redis = AsyncMock()
    fake_redis.exists = AsyncMock(return_value=1)
    fake_redis.get = AsyncMock(return_value=None)
    monkeypatch.setattr(gw, "redis", fake_redis)

    out = await gw._read_global_kill_switch()
    assert out["engaged"] is True
    assert out["last_toggled_at"] is None
    assert out["actor"] is None


@pytest.mark.asyncio
async def test_kill_switch_redis_error_fails_safe(monkeypatch):
    """A Redis blip on the read path must NOT make the indicator look
    engaged — it surfaces the read failure in `reason` instead."""
    from services.gateway import main as gw

    fake_redis = AsyncMock()
    fake_redis.exists = AsyncMock(side_effect=RuntimeError("conn refused"))
    monkeypatch.setattr(gw, "redis", fake_redis)

    out = await gw._read_global_kill_switch()
    assert out["engaged"] is False
    assert out["reason"] and "kill_switch_read_failed" in out["reason"]


# --------------------------------------------------------------------------- #
# OpenAPI schemas — static checks                                             #
# --------------------------------------------------------------------------- #


def test_openapi_latency_schema_lists_every_field():
    from services.gateway.main import _LATENCY_BLOCK_SCHEMA
    expected = {
        "scope", "window_seconds", "p50_ms", "p95_ms",
        "p99_ms", "request_count", "computed_at",
    }
    assert set(_LATENCY_BLOCK_SCHEMA["properties"].keys()) == expected
    assert set(_LATENCY_BLOCK_SCHEMA["required"]) == expected
    # scope is an enum with exactly the two intended values.
    scope_enum = set(_LATENCY_BLOCK_SCHEMA["properties"]["scope"]["enum"])
    assert scope_enum == {"gateway_internal", "end_to_end"}


def test_openapi_status_response_documents_kill_switch():
    from services.gateway.main import _STATUS_RESPONSE_SCHEMA
    body = _STATUS_RESPONSE_SCHEMA["200"]["content"]["application/json"]["schema"]
    assert "kill_switch" in body["properties"]
    assert "latency" in body["properties"]


def test_openapi_system_health_response_documents_latency():
    from services.gateway.main import _SYSTEM_HEALTH_RESPONSE_SCHEMA
    body = _SYSTEM_HEALTH_RESPONSE_SCHEMA["200"]["content"]["application/json"]["schema"]
    assert "latency" in body["properties"]
