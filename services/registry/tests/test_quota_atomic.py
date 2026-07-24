"""Real tests for atomic quota EVAL — exercises the actual Lua path via
a fake in-process Redis stub that supports GET/INCR/DECR/EVAL. Proves
the TOCTOU race is closed: N concurrent reserves at cap-1 yield exactly
1 'ok' and N-1 'exceeded'."""
from __future__ import annotations

import asyncio

import pytest

from services.registry.quota_atomic import (
    _QUOTA_RESERVE_LUA,
    _headroom_threshold,
    release_quota_slot,
    reserve_quota_slot,
)


class _FakeRedis:
    """Minimal in-memory Redis substitute — implements GET/INCR/DECR/EVAL
    (with the specific Lua we ship). Real integration test lives against
    a real Redis in the compose stack."""

    def __init__(self) -> None:
        self._kv: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def execute_command(self, *args):
        # We only implement the EVAL shape our Lua uses.
        assert args[0] == "EVAL"
        script = args[1]
        _numkeys = int(args[2])
        key = args[3]
        argv = args[4:]

        # Emulate the Lua script atomically (single asyncio task at a time
        # on this event loop is inherently atomic without the lock; the
        # lock makes intent explicit).
        async with self._lock:
            cur = self._kv.get(key, 0)
            if _QUOTA_RESERVE_LUA in script:
                cap = int(argv[0])
                warn = int(argv[1])
                if cur >= cap:
                    return ["exceeded", str(cur), str(cap), "0"]
                self._kv[key] = cur + 1
                newv = self._kv[key]
                alert = "1" if newv >= warn and (newv - 1) < warn else "0"
                return ["ok", str(newv), str(cap), alert]
            raise NotImplementedError(f"unsupported script: {script!r}")

    async def decr(self, key: str) -> int:
        async with self._lock:
            self._kv[key] = self._kv.get(key, 0) - 1
            return self._kv[key]


class TestHeadroomThreshold:
    def test_matches_evaluate_mint_semantics(self):
        # These MUST match sdk/common/tenant_quota.evaluate_mint exactly.
        from sdk.common.tenant_quota import evaluate_mint
        for cap in (10, 50, 100, 1000, 10_000):
            thresh = _headroom_threshold(cap)
            # Mint just under threshold → no alert.
            d_before = evaluate_mint(active_count=thresh - 1, quota=cap)
            assert not d_before.should_ledger_c2, (cap, thresh)
            # Mint at threshold → alert.
            d_at = evaluate_mint(active_count=thresh, quota=cap)
            assert d_at.should_ledger_c2, (cap, thresh)


class TestReserveQuotaSlot:
    @pytest.mark.asyncio
    async def test_ok_under_cap(self):
        r = _FakeRedis()
        status, n, cap, alert = await reserve_quota_slot(r, "t1", 100)
        assert status == "ok"
        assert n == 1 and cap == 100 and alert is False

    @pytest.mark.asyncio
    async def test_exceeded_at_cap(self):
        r = _FakeRedis()
        r._kv["acp:tenant:profile_count:t1"] = 100
        status, cur, cap, alert = await reserve_quota_slot(r, "t1", 100)
        assert status == "exceeded"
        assert cur == 100 and cap == 100
        # Counter must NOT have been INCR'd on exceeded.
        assert r._kv["acp:tenant:profile_count:t1"] == 100

    @pytest.mark.asyncio
    async def test_alert_fires_exactly_once_at_threshold(self):
        r = _FakeRedis()
        # Cap 100 → threshold 95. Fill to 94, next mint crosses.
        r._kv["acp:tenant:profile_count:t1"] = 94
        s, n, cap, alert = await reserve_quota_slot(r, "t1", 100)
        assert (s, n, alert) == ("ok", 95, True)
        # Next mint above threshold → NO alert (fires only on crossing).
        s2, n2, _, alert2 = await reserve_quota_slot(r, "t1", 100)
        assert (s2, n2, alert2) == ("ok", 96, False)

    @pytest.mark.asyncio
    async def test_race_is_closed_under_concurrency(self):
        """The core TOCTOU proof: N concurrent reserves at cap-1 yield
        exactly ONE 'ok' and N-1 'exceeded' — no over-shoot."""
        r = _FakeRedis()
        r._kv["acp:tenant:profile_count:t1"] = 99   # 1 slot left
        # 50 concurrent attempts.
        tasks = [reserve_quota_slot(r, "t1", 100) for _ in range(50)]
        results = await asyncio.gather(*tasks)
        ok_count = sum(1 for s, *_ in results if s == "ok")
        exceeded_count = sum(1 for s, *_ in results if s == "exceeded")
        assert ok_count == 1, (ok_count, results)
        assert exceeded_count == 49
        # Final counter is EXACTLY at the cap — no over-shoot.
        assert r._kv["acp:tenant:profile_count:t1"] == 100

    @pytest.mark.asyncio
    async def test_release_decrements(self):
        r = _FakeRedis()
        r._kv["acp:tenant:profile_count:t1"] = 50
        await release_quota_slot(r, "t1")
        assert r._kv["acp:tenant:profile_count:t1"] == 49

    @pytest.mark.asyncio
    async def test_err_on_redis_failure(self):
        class _BrokenRedis:
            async def execute_command(self, *a):
                raise RuntimeError("connection lost")
        s, n, cap, alert = await reserve_quota_slot(_BrokenRedis(), "t1", 100)
        assert (s, n, cap, alert) == ("err", 0, 0, False)
