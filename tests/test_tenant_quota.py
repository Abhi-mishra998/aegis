"""Unit tests for the per-tenant rate-limit sprint (Sprint 3.2, 2026-05-15).

Coverage:

* TenantQuotaLimiter.check:
    - RPS bucket allows up to `burst` and denies the (burst+1)th — when
      both happen inside the bucket's refill window.
    - When the bucket says no, `limit_type="rps"` with a usable
      `reset_at` + `retry_after_s >= 1`.
    - Daily cap: a tenant past the daily_request_cap gets
      `limit_type="daily"` with reset_at at the next UTC midnight.
    - Monthly cap NULL skips monthly check entirely (no INCR).
    - Monthly cap exceeded → `limit_type="monthly"`.
    - 80% monthly warning fires exactly once per tenant per month
      (SETNX idempotency) and pushes a payload to `acp:billing_alerts`.
* TenantQuotaLimiter.usage_snapshot:
    - Read-only — never INCRs.
    - Shape matches `/tenant/quota` contract.
* SecurityMiddleware._is_readonly_for_monthly_cap:
    - GET is read-only for any path.
    - POST is read-only ONLY for the explicit allowlist.
    - POST /execute/* is NOT read-only (the regression we're guarding).
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from sdk.common.ratelimit import QuotaDecision, TenantQuotaLimiter

# --------------------------------------------------------------------------- #
# Fake Redis                                                                  #
# --------------------------------------------------------------------------- #


class _FakeRedis:
    """In-memory mock — just enough Redis surface for TenantQuotaLimiter.

    The Lua script is replaced by a Python token-bucket so we don't need
    a real Redis server. Counters / SETNX / xadd are recorded so tests
    can assert on side effects.
    """

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.streams: dict[str, list[dict]] = {}
        self.bucket_state: dict[str, tuple[float, float]] = {}  # key → (tokens, last_refill)

    # -- counters / kv --
    async def incr(self, key: str) -> int:
        v = int(self.kv.get(key, "0")) + 1
        self.kv[key] = str(v)
        return v

    async def get(self, key: str) -> str | None:
        return self.kv.get(key)

    async def expire(self, key: str, ttl: int) -> bool:
        return key in self.kv  # always succeed, no real TTL needed in tests

    async def setnx(self, key: str, val: str) -> bool:
        if key in self.kv:
            return False
        self.kv[key] = val
        return True

    async def exists(self, key: str) -> int:
        return 1 if key in self.kv else 0

    async def xadd(self, stream: str, fields: dict, **_) -> bytes:
        self.streams.setdefault(stream, []).append(fields)
        return f"1-{len(self.streams[stream])}".encode()

    # -- script registration: returns a callable matching the Lua-script API --
    def register_script(self, _src: str):
        async def _bucket(*, keys, args):
            (capacity, refill_rate, cost, now) = args
            key = keys[0]
            tokens, last = self.bucket_state.get(key, (capacity, now))
            time_passed = max(0.0, now - last)
            tokens = min(capacity, tokens + time_passed * refill_rate)
            allowed = 0
            if tokens >= cost:
                tokens -= cost
                allowed = 1
            self.bucket_state[key] = (tokens, now)
            return allowed
        return _bucket


@pytest.fixture
def fake_redis() -> _FakeRedis:
    return _FakeRedis()


@pytest.fixture
def limiter(fake_redis) -> TenantQuotaLimiter:
    return TenantQuotaLimiter(fake_redis)


# --------------------------------------------------------------------------- #
# RPS / burst                                                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_rps_burst_allows_up_to_capacity(limiter):
    """burst=5 → first 5 calls allowed back-to-back, 6th denied."""
    results = []
    for _ in range(6):
        d = await limiter.check(
            tenant_id="t1",
            requests_per_second=1,    # slow refill so we don't get a token mid-test
            burst=5,
            daily_cap=1_000_000,
            monthly_cap=None,
        )
        results.append(d)
    assert [r.allowed for r in results] == [True, True, True, True, True, False]
    assert results[-1].limit_type == "rps"
    assert results[-1].retry_after_s >= 1
    assert results[-1].reset_at is not None


@pytest.mark.asyncio
async def test_rps_denied_carries_limit_type(limiter):
    """Acceptance (a): with rps=10 hammered well above 10, the deny
    response carries limit_type='rps'. Here we mimic the hammering by
    exhausting the bucket and asserting the next call is denied with
    the right shape."""
    for _ in range(100):
        d = await limiter.check(
            tenant_id="hammer",
            requests_per_second=10, burst=10,
            daily_cap=1_000_000, monthly_cap=None,
        )
    # The final iteration should be a denial (bucket emptied long ago).
    assert d.allowed is False
    assert d.limit_type == "rps"


# --------------------------------------------------------------------------- #
# Daily cap                                                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_daily_cap_denies_after_threshold(limiter, fake_redis):
    """When `daily_used > daily_cap`, decision flips to limit_type='daily'."""
    # Pre-load counter past the cap.
    now = datetime.now(tz=UTC)
    daily_key = TenantQuotaLimiter._daily_key("t1", now)
    fake_redis.kv[daily_key] = "11"

    d = await limiter.check(
        tenant_id="t1",
        requests_per_second=100, burst=100,
        daily_cap=10, monthly_cap=None,
    )
    assert d.allowed is False
    assert d.limit_type == "daily"
    assert "next" not in (d.reset_at or "")
    # reset_at is next UTC midnight.
    parsed = datetime.fromisoformat(d.reset_at)
    assert parsed.hour == 0 and parsed.minute == 0 and parsed.second == 0


@pytest.mark.asyncio
async def test_daily_cap_increments_counter(limiter, fake_redis):
    """Each check increments the daily counter (visible to /tenant/quota)."""
    for _ in range(3):
        await limiter.check(
            tenant_id="t2", requests_per_second=100, burst=100,
            daily_cap=1_000_000, monthly_cap=None,
        )
    key = TenantQuotaLimiter._daily_key("t2", datetime.now(tz=UTC))
    assert int(fake_redis.kv[key]) == 3


# --------------------------------------------------------------------------- #
# Monthly cap + 80% warning                                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_monthly_cap_none_skips_monthly_check(limiter, fake_redis):
    """When monthly_cap=None, the monthly counter must NOT be touched."""
    for _ in range(5):
        await limiter.check(
            tenant_id="t3", requests_per_second=100, burst=100,
            daily_cap=1_000_000, monthly_cap=None,
        )
    monthly_key = TenantQuotaLimiter._monthly_key("t3", datetime.now(tz=UTC))
    assert monthly_key not in fake_redis.kv


@pytest.mark.asyncio
async def test_monthly_cap_exceeded_returns_monthly(limiter, fake_redis):
    """Pre-load the monthly counter past the cap → next check denies."""
    now = datetime.now(tz=UTC)
    monthly_key = TenantQuotaLimiter._monthly_key("t4", now)
    fake_redis.kv[monthly_key] = "1000000"

    d = await limiter.check(
        tenant_id="t4",
        requests_per_second=100, burst=100,
        daily_cap=10_000_000,           # daily not relevant here
        monthly_cap=1000,
    )
    assert d.allowed is False
    assert d.limit_type == "monthly"


@pytest.mark.asyncio
async def test_monthly_80pct_warning_fires_once(limiter, fake_redis):
    """First crossing of 80% emits a warning event; subsequent crossings
    in the same month do not."""
    now = datetime.now(tz=UTC)
    monthly_key = TenantQuotaLimiter._monthly_key("t5", now)
    # Pre-load to 79 (so the next INCR makes it 80, which is exactly 80% of 100).
    fake_redis.kv[monthly_key] = "79"

    d1 = await limiter.check(
        tenant_id="t5",
        requests_per_second=100, burst=100,
        daily_cap=1_000_000, monthly_cap=100,
    )
    assert d1.allowed is True

    # Now beyond 80% — but warning should ONLY have fired once.
    d2 = await limiter.check(
        tenant_id="t5",
        requests_per_second=100, burst=100,
        daily_cap=1_000_000, monthly_cap=100,
    )
    assert d2.allowed is True

    events = fake_redis.streams.get(TenantQuotaLimiter.BILLING_ALERTS_STREAM, [])
    assert len(events) == 1
    payload = json.loads(events[0]["data"])
    assert payload["kind"] == "monthly_quota_warning"
    assert payload["tenant_id"] == "t5"
    assert payload["monthly_cap"] == 100
    assert payload["percent"] >= 80.0

    # The SETNX flag is present so a re-cross in the same month is a no-op.
    warn_key = TenantQuotaLimiter._monthly_warn_key("t5", now)
    assert fake_redis.kv[warn_key] == "1"


# --------------------------------------------------------------------------- #
# usage_snapshot (read-only)                                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_usage_snapshot_does_not_increment(limiter, fake_redis):
    now = datetime.now(tz=UTC)
    daily_key = TenantQuotaLimiter._daily_key("t6", now)
    fake_redis.kv[daily_key] = "42"
    snap = await limiter.usage_snapshot(
        tenant_id="t6", daily_cap=1_000_000, monthly_cap=None,
    )
    assert snap["daily_used"] == 42
    # No second call happened — the counter must still be 42.
    assert fake_redis.kv[daily_key] == "42"


@pytest.mark.asyncio
async def test_usage_snapshot_shape_is_canonical(limiter):
    snap = await limiter.usage_snapshot(
        tenant_id="t7", daily_cap=1000, monthly_cap=50000,
    )
    required = {
        "daily_used", "daily_cap", "daily_resets_at",
        "monthly_used", "monthly_cap", "monthly_resets_at",
        "monthly_warn_emitted",
    }
    assert required.issubset(set(snap.keys()))
    assert snap["daily_cap"] == 1000
    assert snap["monthly_cap"] == 50000


# --------------------------------------------------------------------------- #
# Middleware monthly read-only allowlist                                      #
# --------------------------------------------------------------------------- #


def _stub_request(*, method: str, path: str) -> MagicMock:
    req = MagicMock()
    req.method = method
    req.url.path = path
    return req


def test_monthly_readonly_allows_get_anywhere():
    from services.gateway.middleware import SecurityMiddleware
    assert SecurityMiddleware._is_readonly_for_monthly_cap(
        _stub_request(method="GET", path="/audit/logs")
    ) is True
    assert SecurityMiddleware._is_readonly_for_monthly_cap(
        _stub_request(method="GET", path="/execute/read_file")
    ) is True
    assert SecurityMiddleware._is_readonly_for_monthly_cap(
        _stub_request(method="GET", path="/tenant/quota")
    ) is True


def test_monthly_readonly_post_allowlist():
    """POST allowed only for the explicit verification endpoints."""
    from services.gateway.middleware import SecurityMiddleware
    assert SecurityMiddleware._is_readonly_for_monthly_cap(
        _stub_request(method="POST", path="/receipts/verify")
    ) is True
    assert SecurityMiddleware._is_readonly_for_monthly_cap(
        _stub_request(method="POST", path="/transparency/verify-root")
    ) is True


def test_monthly_readonly_blocks_post_execute():
    """The regression we're guarding: POST /execute/* is NEVER read-only,
    so a monthly-cap-exceeded tenant cannot run new tools."""
    from services.gateway.middleware import SecurityMiddleware
    assert SecurityMiddleware._is_readonly_for_monthly_cap(
        _stub_request(method="POST", path="/execute/read_file")
    ) is False
    assert SecurityMiddleware._is_readonly_for_monthly_cap(
        _stub_request(method="POST", path="/execute/write_file")
    ) is False


# --------------------------------------------------------------------------- #
# QuotaDecision dataclass shape                                               #
# --------------------------------------------------------------------------- #


def test_quota_decision_default_shape():
    d = QuotaDecision(allowed=True)
    assert d.allowed is True
    assert d.limit_type is None
    assert d.reset_at is None
    assert d.retry_after_s == 0
    assert d.usage == {}
