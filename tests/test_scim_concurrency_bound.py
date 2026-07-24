"""Test the SCIM reconciler concurrency bound. The semaphore must
prevent an unbounded burst that would trip the customer's SCIM rate
limit + exhaust our httpx connection pool."""
from __future__ import annotations

import asyncio
from importlib import reload

import pytest

from services.policy.scim_agent import AgentRecord


def _agents(n: int) -> list[AgentRecord]:
    """N agents each with a distinct human_responsible ref."""
    return [
        AgentRecord(f"ag_{i}", f"scim://acme/Users/u_{i}", "VERIFIED")
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_concurrency_never_exceeds_ceiling(monkeypatch):
    """Instrument the ScimClient stub to track the peak number of
    simultaneously-in-flight requests. Peak must be ≤ _SCIM_CONCURRENCY."""
    monkeypatch.setenv("SCIM_BASE_URL", "https://scim.example/scim/v2")
    monkeypatch.setenv("SCIM_BEARER_TOKEN", "test")
    monkeypatch.setenv("SCIM_RECONCILE_CONCURRENCY", "8")

    from sdk.common import config as _cfg
    reload(_cfg)
    from services.identity import scim_reconciler as _sr
    reload(_sr)

    in_flight = 0
    peak = 0
    lock = asyncio.Lock()

    class _CountingStubClient:
        def __init__(self, *_a, **_kw): pass
        async def lookup_user(self, ref):
            nonlocal in_flight, peak
            async with lock:
                in_flight += 1
                peak = max(peak, in_flight)
            # Simulate SCIM latency so overlap is measurable.
            await asyncio.sleep(0.005)
            async with lock:
                in_flight -= 1
            return "ACTIVE"

    monkeypatch.setattr(_sr, "ScimClient", _CountingStubClient)

    # 100 agents with distinct refs — without the semaphore, peak
    # would be 100 (or close to it). With cap 8, peak must be ≤ 8.
    await _sr.run_once_async(_agents(100))
    assert peak <= 8, f"peak concurrency {peak} exceeded cap 8"
    # Sanity: we DID actually parallelize (peak > 1) — the semaphore
    # isn't accidentally serializing everything.
    assert peak > 1, "expected some concurrency; got serial execution"


@pytest.mark.asyncio
async def test_all_refs_still_reconciled(monkeypatch):
    """Regression: the semaphore must not drop any lookups. 100 agents
    in, 100 reconciled out."""
    monkeypatch.setenv("SCIM_BASE_URL", "https://scim.example/scim/v2")
    monkeypatch.setenv("SCIM_BEARER_TOKEN", "test")
    monkeypatch.setenv("SCIM_RECONCILE_CONCURRENCY", "8")

    from sdk.common import config as _cfg
    reload(_cfg)
    from services.identity import scim_reconciler as _sr
    reload(_sr)

    class _AllActive:
        def __init__(self, *_a, **_kw): pass
        async def lookup_user(self, ref):
            return "ACTIVE"

    monkeypatch.setattr(_sr, "ScimClient", _AllActive)

    results = await _sr.run_once_async(_agents(100))
    assert len(results) == 100
    assert all(r.action == "OK" for r in results)
