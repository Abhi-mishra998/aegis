"""Unit tests for the Sprint 2 performance changes (2026-05-15).

Covers the behavioural invariants of each code change; not a load test.
Load-test acceptance lives in reports/sprint2_perf_after.json — the
operator fills it in after running locust against the rebuilt stack.

* In-process JWT LRU: hit / miss / TTL expiry / targeted invalidation.
* ResilientClient: connect_timeout defaults from settings, override per call.
* Circuit-breaker-open exception in the behavior consult → service_status
  classified as `skipped` (Sprint 1.1 invariant). Other ConnectError shapes
  still classify as `error`.
* Audit xadd timeout in middleware is 0.25s, not 1.0s.
* pgbouncer pool size bumped.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import httpx
import pytest


# --------------------------------------------------------------------------- #
# In-process JWT LRU                                                          #
# --------------------------------------------------------------------------- #


def _fresh_lru(*, max_entries: int = 4, ttl_seconds: float = 60.0):
    from services.gateway.auth import _LocalTokenLRU
    return _LocalTokenLRU(max_entries=max_entries, ttl_seconds=ttl_seconds)


class TestLocalTokenLRU:
    def test_hit_returns_copy(self):
        lru = _fresh_lru()
        payload = {"sub": "a", "tenant_id": "t"}
        lru.set("k1", payload)
        out = lru.get("k1")
        assert out == payload
        # Mutating the returned dict must NOT poison the cache.
        out["sub"] = "evil"
        assert lru.get("k1")["sub"] == "a"

    def test_miss_returns_none(self):
        lru = _fresh_lru()
        assert lru.get("ghost") is None
        assert lru.misses == 1

    def test_ttl_expiry(self):
        lru = _fresh_lru(ttl_seconds=0.05)
        lru.set("k", {"sub": "a"})
        assert lru.get("k") is not None
        time.sleep(0.06)
        assert lru.get("k") is None

    def test_lru_eviction_under_pressure(self):
        lru = _fresh_lru(max_entries=2)
        lru.set("a", {"x": 1})
        lru.set("b", {"x": 2})
        lru.set("c", {"x": 3})   # evicts "a"
        assert lru.get("a") is None
        assert lru.get("b") is not None
        assert lru.get("c") is not None

    def test_targeted_invalidation(self):
        lru = _fresh_lru()
        lru.set("k", {"sub": "a"})
        assert lru.invalidate("k") is True
        assert lru.get("k") is None
        # Idempotent.
        assert lru.invalidate("k") is False

    def test_clear_resets_counters(self):
        lru = _fresh_lru()
        lru.set("k", {"x": 1})
        lru.get("k")
        lru.get("missing")
        snap_before = lru.metric_snapshot()
        assert snap_before["hits"] == 1 and snap_before["misses"] == 1
        lru.clear()
        snap_after = lru.metric_snapshot()
        assert snap_after == {"size": 0, "hits": 0, "misses": 0}

    def test_metric_snapshot_shape(self):
        lru = _fresh_lru()
        lru.set("k", {"x": 1})
        lru.get("k")
        snap = lru.metric_snapshot()
        assert set(snap.keys()) == {"size", "hits", "misses"}


# --------------------------------------------------------------------------- #
# Module-level helpers                                                        #
# --------------------------------------------------------------------------- #


def test_invalidate_local_token_routes_to_hash():
    from services.gateway.auth import (
        LocalTokenValidator,
        _LOCAL_TOKEN_LRU,
        invalidate_local_token,
    )
    token = "fake.jwt.token"
    _LOCAL_TOKEN_LRU.clear()
    _LOCAL_TOKEN_LRU.set(LocalTokenValidator._token_hash(token), {"sub": "a"})
    assert _LOCAL_TOKEN_LRU.get(LocalTokenValidator._token_hash(token)) is not None
    assert invalidate_local_token(token) is True
    assert _LOCAL_TOKEN_LRU.get(LocalTokenValidator._token_hash(token)) is None


# --------------------------------------------------------------------------- #
# ResilientClient timeout config                                              #
# --------------------------------------------------------------------------- #


class TestResilientClientTimeouts:
    def test_default_connect_timeout_is_100ms(self):
        from sdk.common.resilient_client import ResilientClient
        rc = ResilientClient(timeout=2.0, retries=1)
        assert rc._timeout.connect == pytest.approx(0.1, rel=0.01)

    def test_explicit_connect_override_wins(self):
        from sdk.common.resilient_client import ResilientClient
        rc = ResilientClient(timeout=2.0, retries=1, connect_timeout=0.05)
        assert rc._timeout.connect == pytest.approx(0.05, rel=0.01)

    def test_read_timeout_tracks_overall(self):
        """The caller's `timeout` parameter must remain the overall read
        budget — long-running calls (receipts verify, transparency proofs)
        must not be artificially truncated by the tightened connect window."""
        from sdk.common.resilient_client import ResilientClient
        rc_short = ResilientClient(timeout=1.0, retries=1)
        rc_long = ResilientClient(timeout=10.0, retries=1)
        assert rc_short._timeout.read == pytest.approx(1.0, rel=0.01)
        assert rc_long._timeout.read == pytest.approx(10.0, rel=0.01)


# --------------------------------------------------------------------------- #
# Behavior consult — breaker-open → skipped (Sprint 1.1 invariant)            #
# --------------------------------------------------------------------------- #


class TestBreakerOpenClassification:
    def test_breaker_open_exception_classifies_as_skipped(self):
        from services.decision.behavior_consult import classify_behavior_result
        exc = httpx.ConnectError("Circuit breaker is OPEN for http://behavior:8000")
        status, data, score = classify_behavior_result(exc)
        assert status == "skipped"
        assert score is None
        # Fail-safe behavior_data still has the unavailable flag.
        assert "behavior_service_unavailable" in data["flags"]

    def test_breaker_open_lowercase_also_caught(self):
        from services.decision.behavior_consult import classify_behavior_result
        exc = httpx.ConnectError("circuit breaker is open: behavior")
        status, _, _ = classify_behavior_result(exc)
        assert status == "skipped"

    def test_generic_connect_error_stays_error(self):
        """A real network connect error must NOT be classified as skipped —
        skipped means 'we chose not to call', error means 'we tried and
        couldn't reach the upstream'. They have different policy
        consequences in apply_degraded_mode_policy."""
        from services.decision.behavior_consult import classify_behavior_result
        exc = httpx.ConnectError("connection refused")
        status, _, _ = classify_behavior_result(exc)
        assert status == "error"

    def test_timeout_still_classifies_as_timeout(self):
        """Ensure the new skipped branch hasn't accidentally captured
        the timeout path."""
        from services.decision.behavior_consult import classify_behavior_result
        status, _, _ = classify_behavior_result(httpx.ConnectTimeout("slow"))
        assert status == "timeout"


# --------------------------------------------------------------------------- #
# Audit xadd timeout + pgbouncer pool — static checks                         #
# --------------------------------------------------------------------------- #


def test_audit_xadd_timeout_lowered_to_250ms():
    """Sprint 2 dropped the audit-write wait from 1.0s to 0.25s. Belt &
    braces: a regression that raises it back to 1.0s trips this test.
    The implementation lives in _mw_audit.py (split from middleware.py)."""
    src = open("services/gateway/_mw_audit.py").read()
    # The old "timeout=1.0" line on the audit path must be gone.
    assert "timeout=0.25," in src
    # And the explanatory comment must be intact so the next contributor
    # understands why.
    assert "Sprint 2 perf" in src


def test_pgbouncer_pool_size_is_50():
    src = open("infra/pgbouncer.ini").read()
    assert "default_pool_size = 50" in src
    assert "reserve_pool_size = 20" in src


# --------------------------------------------------------------------------- #
# After-report skeleton present                                               #
# --------------------------------------------------------------------------- #


def test_after_report_skeleton_has_required_shape():
    import json
    body = json.loads(open("reports/sprint2_perf_after.json").read())
    # Targets locked in
    assert body["targets"]["execute_valid"]["p99_ms"] == 250
    # Before numbers locked in (operator-supplied baseline)
    assert body["before"]["execute_valid"]["p99_ms"] == 2300
    # After block exists for operator to fill
    assert body["after"]["execute_valid"]["p99_ms"] is None
    # Invariant checks listed
    for k in ("audit_chain_integrous", "reconciliation_status",
              "behavior_consult_skipped_rate", "no_unexpected_status_202"):
        assert k in body["invariant_checks"]


def test_baseline_profile_doc_exists_and_anchors_code():
    """Spot-check that the baseline doc names the files we touched and the
    target numbers — proves the doc is more than a placeholder."""
    src = open("reports/sprint2_baseline_profile.txt").read()
    assert "services/gateway/auth.py" in src
    assert "sdk/common/resilient_client.py" in src
    assert "p99       2300 ms" in src
    assert "≤ 250 ms" in src
