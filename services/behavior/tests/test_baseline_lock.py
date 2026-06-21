"""Unit tests for the N14 cold-start baseline lock in
``services.behavior._baseline``.

These tests cover the training-poisoning attack: a compromised agent
flooding a newly-unlocked tool ought to keep triggering
``behavior_anomaly:unusual_tool`` rather than being able to *train out*
the finding by repetition.

Scenarios (per the N14 spec):
    1. 100 calls of tool ``foo`` → baseline learns ``foo``; no finding
       (single tool means count == total, never <= 3 once total >= 30).
    2. 1 call of tool ``bar`` (first time post-lock) → finding emitted
       AND baseline is NOT extended (``tools`` hash entry for ``bar``
       stays absent / 0).
    3. 100 more calls of tool ``bar`` → every call emits
       ``behavior_anomaly:unusual_tool:bar`` because the baseline never
       learns the new tool.

Extra coverage:
    * ``is_baseline_locked`` predicate.
    * Lock threshold is honoured (call_count == threshold still
      writes; call_count > threshold freezes).
    * Prometheus gauge transitions 0 → 1 across the lock boundary.
"""
from __future__ import annotations

from typing import Any

import pytest


# --------------------------------------------------------------------------- #
# Fake Redis (in-memory; matches the redis.asyncio surface used by            #
# ``_baseline.record_and_score``).                                            #
# --------------------------------------------------------------------------- #


class _FakePipeline:
    """Synchronous queue, async execute — mirrors redis.asyncio.client.Pipeline."""

    def __init__(self, redis: "_FakeRedis") -> None:
        self._redis = redis
        self._ops: list[tuple[str, tuple]] = []

    def hincrby(self, key: str, field: str, amount: int) -> "_FakePipeline":
        self._ops.append(("hincrby", (key, field, amount)))
        return self

    def expire(self, key: str, ttl: int) -> "_FakePipeline":
        self._ops.append(("expire", (key, ttl)))
        return self

    async def execute(self) -> list[Any]:
        out: list[Any] = []
        for op, args in self._ops:
            if op == "hincrby":
                key, field, amount = args
                h = self._redis.hashes.setdefault(key, {})
                cur = int(h.get(field, "0"))
                cur += int(amount)
                h[field] = str(cur)
                out.append(cur)
            elif op == "expire":
                key, _ttl = args
                out.append(1 if key in self._redis.hashes or key in self._redis.kv else 0)
            else:  # pragma: no cover — unsupported op in test
                raise NotImplementedError(op)
        self._ops.clear()
        return out


class _FakeRedis:
    """Minimal redis.asyncio surface needed by ``record_and_score``.

    Only implements: ``incr``, ``expire``, ``hgetall``, ``pipeline`` (with
    ``hincrby`` + ``expire`` + ``execute``). Everything else raises so a
    surprise dependency surfaces loudly.
    """

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}

    async def incr(self, key: str) -> int:
        v = int(self.kv.get(key, "0")) + 1
        self.kv[key] = str(v)
        return v

    async def expire(self, key: str, _ttl: int) -> bool:
        return key in self.kv or key in self.hashes

    async def hgetall(self, key: str) -> dict[str, str]:
        # Return a *copy* — production redis returns a fresh dict per call.
        return dict(self.hashes.get(key, {}))

    def pipeline(self) -> _FakePipeline:
        return _FakePipeline(self)


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


@pytest.fixture
def fake_redis() -> _FakeRedis:
    return _FakeRedis()


@pytest.fixture
def fresh_baseline_module(monkeypatch):
    """Import _baseline with the lock threshold pinned to 100.

    We don't ``importlib.reload`` because re-running the module-level
    ``Gauge(...)`` registration would raise (prometheus_client treats
    duplicate registrations as a programming error). Instead we
    monkey-patch the module's ``_BASELINE_LOCK_AFTER_CALLS`` constant in
    place. The unit tests pin the boundary via that constant.
    """
    from services.behavior import _baseline as mod

    monkeypatch.setattr(mod, "_BASELINE_LOCK_AFTER_CALLS", 100)
    yield mod


# --------------------------------------------------------------------------- #
# Tests                                                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_is_baseline_locked_predicate(fresh_baseline_module):
    """The pure predicate is the contract for downstream callers.

    Boundary semantics: the first ``threshold`` calls (inclusive) are
    the *learning* window. ``call_count`` 1..100 → still learning;
    101 onwards → locked.
    """
    m = fresh_baseline_module
    assert m._BASELINE_LOCK_AFTER_CALLS == 100
    assert m.is_baseline_locked(0) is False
    assert m.is_baseline_locked(99) is False
    assert m.is_baseline_locked(100) is False  # the N-th call still learns
    assert m.is_baseline_locked(101) is True   # (N+1)-th onwards is locked
    assert m.is_baseline_locked(200) is True


@pytest.mark.asyncio
async def test_n14_step_1_baseline_learns_foo(fresh_baseline_module, fake_redis):
    """Step 1 — 100 calls of ``foo``: baseline learns it; no finding
    because a single-tool workload never satisfies the
    ``tool_count <= 3`` clause once ``total_calls >= 30``."""
    m = fresh_baseline_module
    for _ in range(100):
        findings = await m.record_and_score(
            fake_redis,
            tenant_id="t1",
            agent_id="agent-a",
            tool="foo",
            table_norm=None,
        )
        # The single-tool workload never trips unusual_tool because
        # tool_count == total_calls.
        assert not any(f.startswith("behavior_anomaly:unusual_tool") for f in findings), (
            f"unexpected unusual_tool during learning window: {findings}"
        )

    # The baseline learnt 100 of foo while the lock window was open.
    assert fake_redis.hashes["acp:baseline:agent-a:tools"] == {"foo": "100"}
    assert fake_redis.kv["acp:baseline:agent-a:call_count"] == "100"
    # call_count == 100 is still the learning boundary; call 101 is
    # the first *locked* call and will not extend the baseline.
    assert m.is_baseline_locked(100) is False
    assert m.is_baseline_locked(101) is True


@pytest.mark.asyncio
async def test_n14_step_2_post_lock_first_bar_flags_and_does_not_learn(
    fresh_baseline_module, fake_redis
):
    """Step 2 — after 100 calls of foo, a SINGLE call of bar:
    * emits ``behavior_anomaly:unusual_tool:bar``, AND
    * baseline does NOT learn bar (the ``tools`` hash has no ``bar``).
    """
    m = fresh_baseline_module
    # Warm up to the lock threshold.
    for _ in range(100):
        await m.record_and_score(
            fake_redis,
            tenant_id="t1",
            agent_id="agent-a",
            tool="foo",
            table_norm=None,
        )
    pre_tools = dict(fake_redis.hashes.get("acp:baseline:agent-a:tools", {}))
    assert pre_tools == {"foo": "100"}

    # First post-lock call with a brand-new tool.
    findings = await m.record_and_score(
        fake_redis,
        tenant_id="t1",
        agent_id="agent-a",
        tool="bar",
        table_norm=None,
    )
    assert "behavior_anomaly:unusual_tool:bar" in findings, findings

    # The baseline must be frozen: bar must NOT appear in the tools
    # hash, and foo's count must NOT be incremented.
    post_tools = dict(fake_redis.hashes.get("acp:baseline:agent-a:tools", {}))
    assert post_tools == {"foo": "100"}, (
        f"baseline was extended after lock — saw {post_tools}"
    )
    # But the lifetime call_count keeps climbing.
    assert fake_redis.kv["acp:baseline:agent-a:call_count"] == "101"


@pytest.mark.asyncio
async def test_n14_step_3_post_lock_flood_keeps_firing(
    fresh_baseline_module, fake_redis
):
    """Step 3 — 100 calls of bar AFTER the lock: every call emits a
    finding and the baseline stays frozen. This is the actual
    poisoning-attack regression: pre-fix, after ~5 calls of bar the
    finding stopped firing."""
    m = fresh_baseline_module
    # Warm to the lock threshold with foo.
    for _ in range(100):
        await m.record_and_score(
            fake_redis,
            tenant_id="t1",
            agent_id="agent-a",
            tool="foo",
            table_norm=None,
        )

    # Now flood with bar 100 times.
    fired = 0
    for _ in range(100):
        findings = await m.record_and_score(
            fake_redis,
            tenant_id="t1",
            agent_id="agent-a",
            tool="bar",
            table_norm=None,
        )
        if "behavior_anomaly:unusual_tool:bar" in findings:
            fired += 1

    # Every single call must have emitted the finding.
    assert fired == 100, f"expected 100 firings, got {fired}"

    # Baseline stayed locked — bar still absent from the tools hash.
    tools_after = dict(fake_redis.hashes.get("acp:baseline:agent-a:tools", {}))
    assert tools_after == {"foo": "100"}, (
        f"baseline drifted under post-lock flood — saw {tools_after}"
    )
    # Lifetime call_count tracked all 200 calls.
    assert fake_redis.kv["acp:baseline:agent-a:call_count"] == "200"


@pytest.mark.asyncio
async def test_prometheus_gauge_transitions_across_lock_boundary(
    fresh_baseline_module, fake_redis
):
    """Lock-state gauge must read 0 while learning and 1 after lock.

    Skipped if prometheus_client is unavailable in the test env.
    """
    m = fresh_baseline_module
    if m.BASELINE_LOCKED_GAUGE is None:  # pragma: no cover
        pytest.skip("prometheus_client unavailable")

    # First call: gauge should be 0 (still learning).
    await m.record_and_score(
        fake_redis, tenant_id="t1", agent_id="agent-g",
        tool="foo", table_norm=None,
    )
    gauge_val = m.BASELINE_LOCKED_GAUGE.labels(agent_id="agent-g")._value.get()
    assert gauge_val == 0, f"expected gauge=0 during learning, got {gauge_val}"

    # Drive past the threshold.
    for _ in range(120):
        await m.record_and_score(
            fake_redis, tenant_id="t1", agent_id="agent-g",
            tool="foo", table_norm=None,
        )
    gauge_val = m.BASELINE_LOCKED_GAUGE.labels(agent_id="agent-g")._value.get()
    assert gauge_val == 1, f"expected gauge=1 after lock, got {gauge_val}"


@pytest.mark.asyncio
async def test_call_count_resilient_to_incr_failure(
    fresh_baseline_module, fake_redis, monkeypatch
):
    """If the call_count INCR itself fails, the function must degrade
    gracefully and continue to score (best-effort contract)."""
    m = fresh_baseline_module

    async def _boom(_key):  # noqa: ANN001
        raise RuntimeError("redis down")

    monkeypatch.setattr(fake_redis, "incr", _boom)
    findings = await m.record_and_score(
        fake_redis, tenant_id="t1", agent_id="agent-z",
        tool="foo", table_norm=None,
    )
    # No crash; findings may be empty (no baseline established yet).
    assert isinstance(findings, list)
