"""Phase-2 cleanup 2026-06-15 — shared in-memory async Redis fake.

Before this module, every Sprint-4-through-7 test file declared its own
`_FakeRedis` class. They diverged enough (set / hash / pipeline coverage
varies per Sprint) that a copy-paste each time was easier than a shared
fixture — but eight near-identical fakes is exactly the kind of thing
Phase 1 flagged.

This is the union of every method any of those fakes exercised:

  KV         : set / get / delete / setex / expire
  LIST       : rpush / lrange
  SET        : sadd / srem / sismember / smembers
  HASH       : hset / hsetnx / hget / hgetall
  ZSET       : zadd / zrangebyscore / zrevrangebyscore
  Stream     : xadd
  Pub/Sub    : publish     (records calls; no subscribers)

Behavior matches redis-py async semantics closely enough that the
sprint code under test runs unchanged. NX/EX semantics, byte/str
encoding at the boundary, hash batch via `mapping=` + 3-arg form
(`hset(k, field, value)`), all reproduced.

Use:

    from tests.security._fakes import FakeRedis

    @pytest.mark.asyncio
    async def test_x():
        r = FakeRedis()
        ...
"""
from __future__ import annotations



class FakeRedis:
    """In-memory async Redis fake covering the subset Sprints 4-7 use.

    Encodes values as `str` internally; reads return `bytes` (matching
    redis-py with `decode_responses=False`, which is what the gateway's
    `get_redis_client(..., decode_responses=False)` returns).
    """

    def __init__(self) -> None:
        self.kv:       dict[str, str]             = {}
        self.lists:    dict[str, list[str]]       = {}
        self.sets:     dict[str, set[str]]        = {}
        self.hashes:   dict[str, dict[str, str]]  = {}
        self.zsets:    dict[str, dict[str, float]] = {}
        self.streams:  dict[str, list[dict[str, str]]] = {}
        self.ttls:     dict[str, int]             = {}
        # Observability hooks tests can read after the call.
        self.publishes:        list[tuple[str, str]] = []
        self.pipeline_runs:    int = 0
        self.sequential_zrange_calls: int = 0

    # ────────────────────── KV ──────────────────────
    async def set(self, k, v, ex=None, nx=False):
        if nx and k in self.kv:
            return False
        self.kv[k] = str(v)
        if ex is not None:
            self.ttls[k] = int(ex)
        return True

    async def setex(self, k, ex, v):
        self.kv[k] = str(v)
        self.ttls[k] = int(ex)
        return True

    async def get(self, k):
        v = self.kv.get(k)
        return v.encode() if isinstance(v, str) else v

    async def delete(self, *keys):
        n = 0
        for k in keys:
            for store_dict in (self.kv, self.lists, self.sets, self.hashes, self.zsets, self.streams):
                if k in store_dict:
                    del store_dict[k]
                    n += 1
        return n

    async def expire(self, k, ex):
        self.ttls[k] = int(ex)
        return True

    # ────────────────────── LIST ──────────────────────
    async def rpush(self, k, *vals):
        lst = self.lists.setdefault(k, [])
        for v in vals:
            lst.append(v if isinstance(v, str) else str(v))
        return len(lst)

    async def lrange(self, k, start, end):
        lst = self.lists.get(k, [])
        if end == -1:
            return [x.encode() for x in lst[start:]]
        return [x.encode() for x in lst[start: end + 1]]

    # ────────────────────── SET ──────────────────────
    async def sadd(self, k, *vals):
        s = self.sets.setdefault(k, set())
        before = len(s)
        s.update(str(v) for v in vals)
        return len(s) - before

    async def srem(self, k, *vals):
        s = self.sets.get(k, set())
        before = len(s)
        for v in vals:
            s.discard(str(v))
        return before - len(s)

    async def sismember(self, k, v):
        return str(v) in self.sets.get(k, set())

    async def smembers(self, k):
        return {v.encode() for v in self.sets.get(k, set())}

    # ────────────────────── HASH ──────────────────────
    async def hset(self, k, field=None, value=None, mapping=None, **kw):
        """Supports redis-py's two call shapes:
          hset(name, field, value)
          hset(name, mapping={field: value})
        """
        h = self.hashes.setdefault(k, {})
        wrote = 0
        if field is not None:
            h[field] = str(value) if value is not None else ""
            wrote += 1
        if mapping:
            for kk, vv in mapping.items():
                h[kk] = str(vv) if vv is not None else ""
                wrote += 1
        for kk, vv in kw.items():
            h[kk] = str(vv) if vv is not None else ""
            wrote += 1
        return wrote

    async def hsetnx(self, k, field, value):
        h = self.hashes.setdefault(k, {})
        if field in h:
            return 0
        h[field] = str(value)
        return 1

    async def hget(self, k, field):
        h = self.hashes.get(k, {})
        v = h.get(field)
        return v.encode() if isinstance(v, str) else v

    async def hgetall(self, k):
        h = self.hashes.get(k, {})
        return {kk.encode(): vv.encode() for kk, vv in h.items()}

    # ────────────────────── ZSET ──────────────────────
    async def zadd(self, k, mapping):
        z = self.zsets.setdefault(k, {})
        for m, s in mapping.items():
            z[m] = float(s)
        return len(mapping)

    async def zrangebyscore(self, k, min_, max_):
        self.sequential_zrange_calls += 1
        z = self.zsets.get(k, {})
        lo = float("-inf") if min_ == "-inf" else float(min_)
        hi = float("+inf") if max_ == "+inf" else float(max_)
        items = [(m, s) for m, s in z.items() if lo <= s <= hi]
        items.sort(key=lambda x: x[1])
        return [m.encode() for m, _ in items]

    async def zrevrangebyscore(self, k, max_, min_, start=0, num=10):
        z = self.zsets.get(k, {})
        lo = float("-inf") if min_ == "-inf" else float(min_)
        hi = float("+inf") if max_ == "+inf" else float(max_)
        items = [(m, s) for m, s in z.items() if lo <= s <= hi]
        items.sort(key=lambda x: x[1], reverse=True)
        out = items[start: start + num]
        return [m.encode() for m, _ in out]

    # ────────────────────── STREAM ──────────────────────
    async def xadd(self, stream, fields):
        self.streams.setdefault(stream, []).append(dict(fields))
        return f"{len(self.streams[stream])}-0"

    # ────────────────────── PUB/SUB ──────────────────────
    async def publish(self, channel, payload):
        """Records the (channel, payload) for assertion; no real fan-out."""
        self.publishes.append((channel, payload))
        return 1
