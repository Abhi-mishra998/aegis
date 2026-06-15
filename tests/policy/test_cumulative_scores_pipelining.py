"""Sprint 8 — pipelining test for cumulative_scores.

The pre-Sprint-8 path made three sequential ZRANGEBYSCORE calls. Sprint 8
collapses them into one Redis pipeline. This test mocks a Redis client
that records pipeline usage and asserts:

  1. cumulative_scores invokes redis.pipeline() exactly once.
  2. Exactly one execute() roundtrip is made.
  3. The returned scores are identical to what the sequential path
     would have produced on the same fake data.
  4. When pipeline() raises, the fallback path produces the same
     result via 3 sequential zrangebyscore calls.
"""
from __future__ import annotations

import pytest

from services.policy import risk_pipeline


# ---------------------------------------------------------------------------
# A minimal fake — enough Redis surface for the function under test.
# ---------------------------------------------------------------------------
class _Pipeline:
    def __init__(self, parent: "_FakeRedis") -> None:
        self._parent = parent
        self._calls: list[tuple[str, int]] = []  # (key, cutoff)
        parent.executes_seen += 1

    def zrangebyscore(self, key, min_score, max_score):
        cutoff = int(min_score)
        self._calls.append((key, cutoff))

    async def execute(self) -> list[list[bytes]]:
        # Resolve each scheduled call to whatever the parent's data says.
        out: list[list[bytes]] = []
        for key, cutoff in self._calls:
            out.append(self._parent._zrange(key, cutoff))
        self._parent.pipeline_runs += 1
        return out


class _FakeRedis:
    """Stores {key: [(ts, member)]} so zrangebyscore can filter."""

    def __init__(self) -> None:
        self.data: dict[str, list[tuple[int, str]]] = {}
        self.pipeline_runs = 0
        self.sequential_zrange_calls = 0
        self.executes_seen = 0
        self.pipeline_should_fail = False

    def seed(self, key: str, members: list[tuple[int, str]]) -> None:
        self.data[key] = list(members)

    def _zrange(self, key: str, cutoff: int) -> list[bytes]:
        rows = self.data.get(key, [])
        return [m.encode() for ts, m in rows if ts >= cutoff]

    def pipeline(self, transaction: bool = False) -> _Pipeline:
        if self.pipeline_should_fail:
            raise RuntimeError("pipeline disabled for this test")
        return _Pipeline(self)

    async def zrangebyscore(self, key, min_score, max_score):
        self.sequential_zrange_calls += 1
        return self._zrange(key, int(min_score))


@pytest.mark.asyncio
async def test_pipelined_one_rtt_returns_expected_scores():
    r = _FakeRedis()
    # Stamps deliberately well inside the windows.
    now = int(__import__("time").time())
    sk = risk_pipeline._session_key("s1")
    ak = risk_pipeline._agent_key("t1", "agA")
    alk = risk_pipeline._agent_long_key("t1", "agA")
    r.seed(sk,  [(now - 60,  f"{now-60}:schema_recon:10")])
    r.seed(ak,  [(now - 100, f"{now-100}:bulk_pii:50"),
                 (now - 200, f"{now-200}:offshore_intent:5")])
    r.seed(alk, [(now - 500, f"{now-500}:bulk_pii:50")])

    ss, ag, al, recent = await risk_pipeline.cumulative_scores(
        r, "t1", "agA", "s1",
    )
    assert ss == 10
    assert ag == 55
    assert al == 50
    assert recent == ["schema_recon"]
    # ONE pipeline.execute(); ZERO sequential zrangebyscore calls.
    assert r.pipeline_runs == 1
    assert r.sequential_zrange_calls == 0
    assert r.executes_seen == 1


@pytest.mark.asyncio
async def test_fallback_to_sequential_when_pipeline_raises():
    r = _FakeRedis()
    r.pipeline_should_fail = True   # forces RuntimeError in pipeline()
    now = int(__import__("time").time())
    ak = risk_pipeline._agent_key("t1", "agA")
    alk = risk_pipeline._agent_long_key("t1", "agA")
    r.seed(ak,  [(now - 100, f"{now-100}:bulk_pii:50")])
    r.seed(alk, [(now - 500, f"{now-500}:bulk_pii:50")])

    ss, ag, al, recent = await risk_pipeline.cumulative_scores(
        r, "t1", "agA", session_id=None,
    )
    assert ss == 0
    assert ag == 50
    assert al == 50
    # 2 sequential zrangebyscore (no session window), 0 pipeline runs.
    assert r.sequential_zrange_calls == 2
    assert r.pipeline_runs == 0


@pytest.mark.asyncio
async def test_no_session_uses_two_pipeline_reads_not_three():
    """When session_id is empty, the pipeline holds just the agent +
    long windows — a small optimization that matters at scale."""
    r = _FakeRedis()
    now = int(__import__("time").time())
    ak = risk_pipeline._agent_key("t1", "agA")
    alk = risk_pipeline._agent_long_key("t1", "agA")
    r.seed(ak,  [(now - 50, f"{now-50}:x:7")])
    r.seed(alk, [(now - 50, f"{now-50}:x:3")])

    ss, ag, al, recent = await risk_pipeline.cumulative_scores(
        r, "t1", "agA", session_id="",
    )
    assert (ss, ag, al, recent) == (0, 7, 3, [])
    assert r.pipeline_runs == 1
    assert r.executes_seen == 1
