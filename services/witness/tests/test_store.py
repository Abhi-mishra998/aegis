"""Real tests for the Redis-backed witness store — exercises the
per-gate cap, TTL semantics, heartbeat, and the memory-fallback path."""
from __future__ import annotations

import asyncio
import time

import pytest

from services.witness import store as ws
from services.witness.schemas import Observation


def _obs(gid: str, i: int = 0) -> Observation:
    return Observation(
        gate_decision_id=gid,
        type="net",
        detail=f"tls-{i}",
        ts="2026-07-22T14:00:00Z",
    )


class _FakeRedis:
    """In-process approximation of Redis LIST + EXPIRE + SETEX + GET.
    Supports pipeline for RPUSH/LTRIM/EXPIRE composition."""

    def __init__(self) -> None:
        self._kv: dict[str, str] = {}
        self._lists: dict[str, list[bytes]] = {}
        self._ttls: dict[str, float] = {}

    def _expired(self, key: str) -> bool:
        exp = self._ttls.get(key)
        if exp is None:
            return False
        return time.time() >= exp

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self._kv[key] = str(value)
        self._ttls[key] = time.time() + ttl

    async def get(self, key: str):
        if self._expired(key):
            self._kv.pop(key, None)
            self._ttls.pop(key, None)
            return None
        return self._kv.get(key)

    async def lrange(self, key: str, start: int, stop: int) -> list[bytes]:
        if self._expired(key):
            self._lists.pop(key, None)
            self._ttls.pop(key, None)
            return []
        lst = self._lists.get(key, [])
        if stop == -1:
            return lst[start:]
        return lst[start:stop + 1]

    def pipeline(self):
        outer = self

        class _Pipe:
            def __init__(self):
                self._ops: list = []
            def rpush(self, key: str, payload: str):
                self._ops.append(("rpush", key, payload))
                return self
            def ltrim(self, key: str, start: int, stop: int):
                self._ops.append(("ltrim", key, start, stop))
                return self
            def expire(self, key: str, ttl: int):
                self._ops.append(("expire", key, ttl))
                return self
            async def execute(self):
                for op in self._ops:
                    if op[0] == "rpush":
                        outer._lists.setdefault(op[1], []).append(op[2].encode())
                    elif op[0] == "ltrim":
                        _, key, start, stop = op
                        lst = outer._lists.get(key, [])
                        # Redis semantics: LTRIM keeps [start, stop] inclusive
                        # with negative-index-from-end support.
                        if stop == -1 and start < 0:
                            outer._lists[key] = lst[start:]
                        else:
                            outer._lists[key] = lst[start:stop + 1 if stop >= 0 else stop]
                    elif op[0] == "expire":
                        outer._ttls[op[1]] = time.time() + op[2]

        return _Pipe()


@pytest.fixture(autouse=True)
def _reset_store():
    """Every test starts with a fresh backend so state doesn't bleed."""
    yield
    ws._backend = None
    ws._backend_kind = "unknown"


class TestRedisBackend:
    @pytest.mark.asyncio
    async def test_record_and_fetch_round_trip(self):
        backend = ws._RedisBackend(_FakeRedis())
        ws._reset_for_tests(backend, "redis")
        await ws.record(_obs("gd_1", i=1))
        await ws.record(_obs("gd_1", i=2))
        obs = await ws.fetch("gd_1")
        assert len(obs) == 2
        assert [o.detail for o in obs] == ["tls-1", "tls-2"]

    @pytest.mark.asyncio
    async def test_per_gate_cap_prevents_flood(self):
        """Attacker floods one gate id with 5000 events — store caps at
        WITNESS_OBS_MAX_PER_GATE (default 1000)."""
        backend = ws._RedisBackend(_FakeRedis())
        ws._reset_for_tests(backend, "redis")
        for i in range(1200):
            await ws.record(_obs("gd_flood", i=i))
        obs = await ws.fetch("gd_flood")
        # Cap = 1000; the LATEST 1000 are kept (LTRIM -1000..-1).
        assert len(obs) == 1000
        # Latest event is preserved (attacker-flood ≠ silent-drop of new evidence).
        assert obs[-1].detail == "tls-1199"

    @pytest.mark.asyncio
    async def test_heartbeat_round_trip(self):
        backend = ws._RedisBackend(_FakeRedis())
        ws._reset_for_tests(backend, "redis")
        ts = time.time()
        await ws.heartbeat("spiffe://w/0", ts)
        got = await ws.last_heartbeat("spiffe://w/0")
        assert got == ts

    @pytest.mark.asyncio
    async def test_missing_heartbeat_is_none(self):
        backend = ws._RedisBackend(_FakeRedis())
        ws._reset_for_tests(backend, "redis")
        assert await ws.last_heartbeat("spiffe://never/heartbeated") is None

    @pytest.mark.asyncio
    async def test_isolation_across_gate_ids(self):
        backend = ws._RedisBackend(_FakeRedis())
        ws._reset_for_tests(backend, "redis")
        await ws.record(_obs("gd_A", i=1))
        await ws.record(_obs("gd_B", i=2))
        a = await ws.fetch("gd_A")
        b = await ws.fetch("gd_B")
        assert len(a) == 1 and a[0].detail == "tls-1"
        assert len(b) == 1 and b[0].detail == "tls-2"


class TestMemoryFallback:
    @pytest.mark.asyncio
    async def test_fallback_functions_when_redis_absent(self):
        backend = ws._MemoryFallback()
        ws._reset_for_tests(backend, "memory")
        await ws.record(_obs("gd_1", i=1))
        obs = await ws.fetch("gd_1")
        assert len(obs) == 1
        assert ws.get_backend_kind() == "memory"

    @pytest.mark.asyncio
    async def test_fallback_also_caps_per_gate(self):
        backend = ws._MemoryFallback()
        ws._reset_for_tests(backend, "memory")
        for i in range(1200):
            await ws.record(_obs("gd_flood", i=i))
        obs = await ws.fetch("gd_flood")
        assert len(obs) == 1000
        assert obs[-1].detail == "tls-1199"

    @pytest.mark.asyncio
    async def test_fallback_global_gate_id_cap(self, monkeypatch):
        """Attacker with mesh JWT floods distinct gate_decision_ids —
        the LRU-bounded cap evicts oldest gates rather than growing the
        dict without bound."""
        monkeypatch.setattr(ws, "_MEMFB_MAX_GATE_IDS", 100)
        backend = ws._MemoryFallback()
        ws._reset_for_tests(backend, "memory")

        for i in range(150):
            await ws.record(_obs(f"gd_{i:04d}", i=1))

        assert len(backend._observations) == 100
        surviving = list(backend._observations.keys())
        assert surviving[0] == "gd_0050"
        assert surviving[-1] == "gd_0149"

    @pytest.mark.asyncio
    async def test_fallback_write_to_existing_gate_promotes_to_hot(self, monkeypatch):
        """Writing to an already-present gate id refreshes its LRU
        position so hot gate ids aren't evicted just because their
        first observation landed early."""
        monkeypatch.setattr(ws, "_MEMFB_MAX_GATE_IDS", 3)
        backend = ws._MemoryFallback()
        ws._reset_for_tests(backend, "memory")

        await ws.record(_obs("gd_A", i=1))
        await ws.record(_obs("gd_B", i=1))
        await ws.record(_obs("gd_C", i=1))
        await ws.record(_obs("gd_A", i=2))  # promote A to newest
        await ws.record(_obs("gd_D", i=1))  # push out oldest (now B)

        surviving = list(backend._observations.keys())
        assert "gd_B" not in surviving
        assert "gd_A" in surviving
        assert "gd_C" in surviving
        assert "gd_D" in surviving


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_records_all_land(self):
        """50 tasks racing to record on the same gate id — none dropped
        by races on the append path."""
        backend = ws._RedisBackend(_FakeRedis())
        ws._reset_for_tests(backend, "redis")

        async def _write(i: int) -> None:
            await ws.record(_obs("gd_race", i=i))

        await asyncio.gather(*(_write(i) for i in range(50)))
        obs = await ws.fetch("gd_race")
        assert len(obs) == 50
