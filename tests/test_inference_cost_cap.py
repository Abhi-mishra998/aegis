"""Unit tests for the Sprint 3.5 inference cost cap.

Coverage:

* `InferenceCostLimiter.estimate_cost_usd` — pure dollar math; the
  unit-test pins the conversion so any future price-table refactor
  surfaces explicitly.
* `InferenceCostLimiter.check`:
    - No caps (both 0) → allowed, no warning, no Redis writes that
      would mark a tenant as throttled.
    - Tenant cap exceeded → allowed=False, scope="tenant".
    - Agent cap exceeded (tenant cap NULL) → allowed=False, scope="agent".
    - Both caps exceeded → tenant takes precedence (scope="tenant").
    - 80% crossing fires a warning event exactly once per scope/key/day.
    - The blocked path STILL fires the warning (a customer must see
      the warning even when the same call also triggers the block).
* `usage_snapshot` — read-only; never INCRs.
* Queue-age helpers correctly classify empty / live / malformed
  streams and lists.
"""
from __future__ import annotations

import json
import time
from datetime import UTC, datetime

import pytest

# --------------------------------------------------------------------------- #
# Fake Redis                                                                  #
# --------------------------------------------------------------------------- #


class _FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.streams: dict[str, list[dict]] = {}

    async def incrby(self, key: str, delta: int) -> int:
        v = int(self.kv.get(key, "0")) + int(delta)
        self.kv[key] = str(v)
        return v

    async def get(self, key: str) -> str | None:
        return self.kv.get(key)

    async def expire(self, key: str, ttl: int) -> bool:
        return key in self.kv

    async def setnx(self, key: str, val: str) -> bool:
        if key in self.kv:
            return False
        self.kv[key] = val
        return True

    async def xadd(self, stream: str, fields: dict, **_) -> bytes:
        self.streams.setdefault(stream, []).append(fields)
        return b"1-1"


@pytest.fixture
def fake_redis() -> _FakeRedis:
    return _FakeRedis()


# --------------------------------------------------------------------------- #
# InferenceCostLimiter                                                        #
# --------------------------------------------------------------------------- #


from sdk.common.inference_cost import InferenceCostLimiter


class TestEstimateCostUsd:
    def test_zero_tokens_zero_cost(self):
        assert InferenceCostLimiter.estimate_cost_usd() == 0.0

    def test_default_table_input_only(self):
        # 1000 input tokens at $0.50/1k → $0.50
        c = InferenceCostLimiter.estimate_cost_usd(input_tokens=1000)
        assert c == pytest.approx(0.50, rel=1e-6)

    def test_default_table_output_only(self):
        c = InferenceCostLimiter.estimate_cost_usd(output_tokens=2000)
        assert c == pytest.approx(1.00, rel=1e-6)

    def test_custom_price_table(self):
        c = InferenceCostLimiter.estimate_cost_usd(
            input_tokens=2000, output_tokens=1000,
            price_table={"groq_input_per_1k": 0.10, "groq_output_per_1k": 0.30},
        )
        assert c == pytest.approx(0.10 * 2 + 0.30 * 1, rel=1e-6)


@pytest.mark.asyncio
class TestCheck:
    async def test_no_caps_allows_and_does_not_block(self, fake_redis):
        lim = InferenceCostLimiter(fake_redis)
        d = await lim.check(
            tenant_id="t1", agent_id="a1",
            estimated_usd=0.50, tenant_cap_usd=0.0, agent_cap_usd=0.0,
        )
        assert d.allowed is True
        assert d.scope is None

    async def test_tenant_cap_exceeded(self, fake_redis):
        lim = InferenceCostLimiter(fake_redis)
        # First call uses $0.60 — under $1.
        d1 = await lim.check(
            tenant_id="t1", agent_id="a1",
            estimated_usd=0.60, tenant_cap_usd=1.00, agent_cap_usd=0.0,
        )
        # Second call adds $0.50, total $1.10 — over the $1 cap.
        d2 = await lim.check(
            tenant_id="t1", agent_id="a1",
            estimated_usd=0.50, tenant_cap_usd=1.00, agent_cap_usd=0.0,
        )
        assert d1.allowed is True
        assert d2.allowed is False
        assert d2.scope == "tenant"
        assert d2.tenant_usd_used == pytest.approx(1.10, rel=1e-3)

    async def test_agent_cap_exceeded_no_tenant_cap(self, fake_redis):
        lim = InferenceCostLimiter(fake_redis)
        d = await lim.check(
            tenant_id="t1", agent_id="a1",
            estimated_usd=2.00, tenant_cap_usd=0.0, agent_cap_usd=1.00,
        )
        assert d.allowed is False
        assert d.scope == "agent"

    async def test_tenant_cap_wins_when_both_exceeded(self, fake_redis):
        lim = InferenceCostLimiter(fake_redis)
        d = await lim.check(
            tenant_id="t1", agent_id="a1",
            estimated_usd=5.00, tenant_cap_usd=1.00, agent_cap_usd=1.00,
        )
        assert d.allowed is False
        assert d.scope == "tenant"

    async def test_80pct_warning_fires_once(self, fake_redis):
        lim = InferenceCostLimiter(fake_redis)
        # Cap $1.00 → 80% trigger at $0.80. First call uses $0.81 to land
        # just over the threshold.
        d1 = await lim.check(
            tenant_id="t1", agent_id="a1",
            estimated_usd=0.81, tenant_cap_usd=1.00, agent_cap_usd=0.0,
        )
        # Second call (under cap, still above 80%) must NOT fire again.
        d2 = await lim.check(
            tenant_id="t1", agent_id="a1",
            estimated_usd=0.05, tenant_cap_usd=1.00, agent_cap_usd=0.0,
        )
        assert d1.allowed is True
        assert d1.warned is True
        assert d2.allowed is True
        events = fake_redis.streams.get(InferenceCostLimiter.BILLING_ALERTS_STREAM, [])
        assert len(events) == 1
        payload = json.loads(events[0]["data"])
        assert payload["scope"] == "tenant"
        assert payload["key"] == "t1"
        assert payload["percent"] >= 80.0

    async def test_warning_fires_even_when_blocked(self, fake_redis):
        """A single huge call can cross BOTH 80% and 100% in one step.
        We still fire the 80% warning so the customer's logs reflect
        the threshold crossing."""
        lim = InferenceCostLimiter(fake_redis)
        d = await lim.check(
            tenant_id="t1", agent_id="a1",
            estimated_usd=10.00, tenant_cap_usd=1.00, agent_cap_usd=0.0,
        )
        assert d.allowed is False
        assert d.warned is True
        assert len(fake_redis.streams[InferenceCostLimiter.BILLING_ALERTS_STREAM]) == 1

    async def test_no_warning_below_80pct(self, fake_redis):
        lim = InferenceCostLimiter(fake_redis)
        d = await lim.check(
            tenant_id="t1", agent_id="a1",
            estimated_usd=0.10, tenant_cap_usd=1.00, agent_cap_usd=0.0,
        )
        assert d.allowed is True
        assert d.warned is False
        assert InferenceCostLimiter.BILLING_ALERTS_STREAM not in fake_redis.streams


@pytest.mark.asyncio
async def test_usage_snapshot_read_only(fake_redis):
    lim = InferenceCostLimiter(fake_redis)
    # Seed counters as if some prior calls had run.
    now = datetime.now(tz=UTC)
    fake_redis.kv[lim._counter_key("tenant", "t1", now)] = "250"  # $2.50
    fake_redis.kv[lim._counter_key("agent", "a1", now)] = "75"    # $0.75
    snap = await lim.usage_snapshot(tenant_id="t1", agent_id="a1")
    assert snap["tenant_usd_used"] == pytest.approx(2.50)
    assert snap["agent_usd_used"] == pytest.approx(0.75)
    # Counters must NOT change after a read-only snapshot.
    assert fake_redis.kv[lim._counter_key("tenant", "t1", now)] == "250"


# --------------------------------------------------------------------------- #
# Sprint 2.2 — period selection + monthly-aligned warning (audit C21)         #
# --------------------------------------------------------------------------- #


def test_default_period_is_monthly(monkeypatch):
    """README documents a once-per-period warning; cloud LLM providers bill
    monthly, so the production default is the monthly bucket. Operators can
    drop to daily on cost-constrained dev rigs by setting INFERENCE_COST_PERIOD."""
    monkeypatch.delenv("INFERENCE_COST_PERIOD", raising=False)
    lim = InferenceCostLimiter(fake_redis_stub := _FakeRedis())
    assert lim.period == "monthly"


def test_env_var_overrides_to_daily(monkeypatch):
    monkeypatch.setenv("INFERENCE_COST_PERIOD", "daily")
    lim = InferenceCostLimiter(_FakeRedis())
    assert lim.period == "daily"


def test_constructor_argument_overrides_env(monkeypatch):
    monkeypatch.setenv("INFERENCE_COST_PERIOD", "monthly")
    lim = InferenceCostLimiter(_FakeRedis(), period="daily")
    assert lim.period == "daily"


def test_monthly_keys_share_the_same_bucket_within_a_month():
    """The headline C21 fix: the warn key dedupe must rotate at most once per
    cap window — not 30× a month."""
    lim = InferenceCostLimiter(_FakeRedis(), period="monthly")
    early = datetime(2026, 6, 1, 0, 0, 1, tzinfo=UTC)
    mid = datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC)
    late = datetime(2026, 6, 30, 23, 59, 59, tzinfo=UTC)
    for stamp in (early, mid, late):
        assert lim._counter_key("tenant", "t1", stamp) == "acp:inference_cost:tenant:t1:2026-06"
        assert lim._warn_key("tenant", "t1", stamp) == "acp:inference_cost_warn:tenant:t1:2026-06"


def test_monthly_keys_rotate_at_month_boundary():
    lim = InferenceCostLimiter(_FakeRedis(), period="monthly")
    last_of_june = datetime(2026, 6, 30, 23, 59, 59, tzinfo=UTC)
    first_of_july = datetime(2026, 7, 1, 0, 0, 1, tzinfo=UTC)
    assert lim._counter_key("tenant", "t1", last_of_june) != lim._counter_key("tenant", "t1", first_of_july)
    assert lim._warn_key("tenant", "t1", last_of_june) != lim._warn_key("tenant", "t1", first_of_july)


def test_daily_keys_rotate_at_midnight():
    lim = InferenceCostLimiter(_FakeRedis(), period="daily")
    d1 = datetime(2026, 6, 13, 23, 59, 59, tzinfo=UTC)
    d2 = datetime(2026, 6, 14, 0, 0, 1, tzinfo=UTC)
    assert lim._counter_key("tenant", "t1", d1) == "acp:inference_cost:tenant:t1:2026-06-13"
    assert lim._counter_key("tenant", "t1", d2) == "acp:inference_cost:tenant:t1:2026-06-14"


def test_counter_and_warn_keys_always_share_period():
    """If a future refactor split the period between counter and warn, the
    bug the audit flagged returns — the warning would fire multiple times
    per cap window. This test pins them together."""
    for period in ("monthly", "daily"):
        lim = InferenceCostLimiter(_FakeRedis(), period=period)
        now = datetime(2026, 6, 13, 12, tzinfo=UTC)
        counter = lim._counter_key("tenant", "t1", now)
        warn = lim._warn_key("tenant", "t1", now)
        # Same trailing bucket — different prefix.
        assert counter.rsplit(":", 1)[1] == warn.rsplit(":", 1)[1]


@pytest.mark.asyncio
async def test_monthly_warning_fires_once_per_period(fake_redis):
    """End-to-end: two calls in the same UTC month → ONE warning event.
    Pre-Sprint-2.2 the daily key would have emitted 30+ alerts."""
    lim = InferenceCostLimiter(fake_redis, period="monthly")

    # First call lands at 80% of the cap → warning fires.
    dec1 = await lim.check(
        tenant_id="t1", agent_id=None,
        estimated_usd=8.0,         # 800 cents
        tenant_cap_usd=10.0,       # cap = 1000 cents → 80%
        agent_cap_usd=0.0,
    )
    assert dec1.allowed is True
    assert dec1.warned is True

    # Second call same month → cap is 9.5/10, still under 100%, but the
    # warning must NOT re-fire (one-shot per period).
    dec2 = await lim.check(
        tenant_id="t1", agent_id=None,
        estimated_usd=1.5,         # cumulative 950 cents = 95%
        tenant_cap_usd=10.0,
        agent_cap_usd=0.0,
    )
    assert dec2.allowed is True
    assert dec2.warned is False

    events = fake_redis.streams.get(InferenceCostLimiter.BILLING_ALERTS_STREAM, [])
    assert len(events) == 1, f"expected 1 alert per period, got {len(events)}"


@pytest.mark.asyncio
async def test_reset_at_uses_next_month_for_monthly_period(fake_redis):
    lim = InferenceCostLimiter(fake_redis, period="monthly")
    dec = await lim.check(
        tenant_id="t1", agent_id=None,
        estimated_usd=1.0, tenant_cap_usd=100.0, agent_cap_usd=0.0,
    )
    # reset_at must be a UTC month-start, not a midnight on the same month.
    assert dec.reset_at is not None
    parsed = datetime.fromisoformat(dec.reset_at)
    assert parsed.day == 1
    assert parsed.hour == 0 and parsed.minute == 0


# --------------------------------------------------------------------------- #
# Queue-age helpers                                                           #
# --------------------------------------------------------------------------- #


from sdk.common.queue_age import (
    list_oldest_age_and_depth,
    stream_oldest_age_and_depth,
)


class _StreamAwareRedis:
    """Sufficient surface for the queue_age helpers."""

    def __init__(self) -> None:
        self.streams: dict[str, dict] = {}
        self.lists: dict[str, list[str]] = {}

    async def xinfo_stream(self, key: str) -> dict:
        if key not in self.streams:
            raise RuntimeError("no such stream")
        return self.streams[key]

    async def llen(self, key: str) -> int:
        return len(self.lists.get(key, []))

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        items = self.lists.get(key, [])
        if end == -1:
            return items[start:]
        return items[start:end + 1]


@pytest.mark.asyncio
class TestStreamAge:
    async def test_missing_stream_returns_zero(self):
        r = _StreamAwareRedis()
        depth, age = await stream_oldest_age_and_depth(r, "nope")
        assert (depth, age) == (0, 0)

    async def test_live_stream_oldest_age(self):
        r = _StreamAwareRedis()
        # Insert "now-30s" millis as the first-entry ID.
        now = int(time.time())
        first_id = f"{(now - 30) * 1000}-0"
        r.streams["acp:audit_stream:dlq"] = {
            "length": 5,
            "first-entry": [first_id, ["data", "{}"]],
        }
        depth, age = await stream_oldest_age_and_depth(
            r, "acp:audit_stream:dlq", now_epoch=now,
        )
        assert depth == 5
        assert 25 <= age <= 35   # ~30s, tolerant to test scheduling


@pytest.mark.asyncio
class TestListAge:
    async def test_empty_list_returns_zero(self):
        r = _StreamAwareRedis()
        depth, age = await list_oldest_age_and_depth(r, "empty")
        assert (depth, age) == (0, 0)

    async def test_list_with_ts_field(self):
        r = _StreamAwareRedis()
        now = int(time.time())
        r.lists["acp:billing_dlq"] = [json.dumps({"ts": now - 45, "x": 1})]
        depth, age = await list_oldest_age_and_depth(
            r, "acp:billing_dlq", now_epoch=now,
        )
        assert depth == 1
        assert 40 <= age <= 50

    async def test_list_with_iso_timestamp_string(self):
        r = _StreamAwareRedis()
        now = int(time.time())
        old_iso = (datetime.fromtimestamp(now - 60, tz=UTC)).isoformat()
        r.lists["acp:billing_dlq"] = [json.dumps({"created_at": old_iso})]
        depth, age = await list_oldest_age_and_depth(
            r, "acp:billing_dlq", now_epoch=now,
        )
        assert depth == 1
        assert 55 <= age <= 65

    async def test_list_with_garbled_entry_yields_depth_only(self):
        r = _StreamAwareRedis()
        r.lists["acp:billing_dlq"] = ["this is not json{"]
        depth, age = await list_oldest_age_and_depth(r, "acp:billing_dlq")
        assert depth == 1
        assert age == 0


# --------------------------------------------------------------------------- #
# Static checks — dashboards + alertmanager rules                             #
# --------------------------------------------------------------------------- #


def test_alertmanager_rules_include_sprint3_5_alerts():
    src = open("infra/prometheus-rules.yml").read()
    for alert in (
        "OutboxOldestPendingAgeHigh",
        "AuditDLQGrowing",
        "BillingDLQGrowing",
        "InsightQueueAgeHigh",
        "FlightTimelineLeak",
        "InferenceCostCapBlocking",
        "ChainViolationImmediate",
    ):
        assert alert in src, f"missing alert: {alert}"


def test_chain_violation_alert_has_zero_window():
    src = open("infra/prometheus-rules.yml").read()
    chunk = src.split("ChainViolationImmediate")[1].split("- alert:")[0]
    # The "page immediately" promise — no 5-minute window.
    assert "for: 0m" in chunk, "ChainViolationImmediate must page with for:0m"


def test_four_dashboards_present_and_valid_json():
    import json as _json
    expected = (
        "acp-platform-slo",
        "acp-trust-layers",
        "acp-tenant-activity",
        "acp-queues",
    )
    for name in expected:
        body = _json.load(open(f"infra/grafana-dashboards/{name}.json"))
        assert body.get("uid") == name
        assert len(body.get("panels", [])) >= 4, f"{name} too few panels"


def test_queues_dashboard_renders_oldest_age_alongside_depth():
    """The whole point of Sprint 3.5: every queue panel must plot oldest-age
    alongside depth so an operator sees both signals on one screen."""
    import json as _json
    dash = _json.load(open("infra/grafana-dashboards/acp-queues.json"))
    # Search panel exprs for at least one oldest_age metric AND one depth metric.
    exprs: list[str] = []
    for panel in dash["panels"]:
        for t in panel.get("targets", []):
            exprs.append(t.get("expr", ""))
    has_age = any("oldest_age_seconds" in e for e in exprs)
    has_depth = any(
        ("depth" in e or "length" in e or "in_progress_count" in e)
        for e in exprs
    )
    assert has_age and has_depth


def test_new_metrics_pre_warmed_in_sdk_utils():
    """A missing metric on a /metrics scrape is silently a Prometheus
    discovery failure — pre-warming guards against it."""
    src = open("sdk/utils.py").read()
    for metric in (
        "OUTBOX_OLDEST_PENDING_AGE_SECONDS",
        "AUDIT_DLQ_OLDEST_AGE_SECONDS",
        "BILLING_DLQ_OLDEST_AGE_SECONDS",
        "INSIGHT_QUEUE_DEPTH",
        "INSIGHT_QUEUE_OLDEST_AGE_SECONDS",
        "GROQ_QUEUE_DEPTH",
        "GROQ_QUEUE_OLDEST_AGE_SECONDS",
        "FLIGHT_TIMELINE_IN_PROGRESS_COUNT",
        "INFERENCE_COST_USD_TOTAL",
        "INFERENCE_COST_BLOCKED_TOTAL",
        "INFERENCE_COST_WARNING_TOTAL",
    ):
        assert metric in src
