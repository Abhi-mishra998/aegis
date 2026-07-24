"""ATF v3.2 §6 — Redis-backed observation store.

Observations are per-gate-decision; TTL bounded so a decayed
gate_decision_id doesn't grow the store forever.

Redis backing means:
  * Multi-worker Witness containers share state — a probe recording
    an observation on worker A is visible to a verdict request on
    worker B.
  * Restart survives — evidence isn't lost when the container recycles
    inside the anchor window.
  * Per-key TTL prevents an attacker-controlled stream of bogus
    gate_decision_ids from unbounded-growing the observation set.

Falls back to in-process dict if REDIS_URL isn't reachable at import.
The fallback is EXPLICIT and LOGGED — a Witness running without shared
state degrades to single-instance evidence, never silently claims
shared visibility. Health endpoint surfaces which backend is live.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any

import structlog

from sdk.common.background import swallow_log
from services.witness.schemas import Observation

logger = structlog.get_logger(__name__)

# Bound per-gate observation retention. 1h covers the anchor window +
# clock skew; anything older is out of the verdict-consumption horizon.
_OBS_TTL_SECONDS = int(os.getenv("WITNESS_OBS_TTL_SECONDS", "3600"))
_HEARTBEAT_TTL_SECONDS = int(os.getenv("WITNESS_HEARTBEAT_TTL_SECONDS", "300"))

# Hard cap per gate_decision_id so an abusive probe stream can't OOM
# a witness worker — an attacker can flood one gate id at most this many
# events; observations past the cap are dropped + counter emitted.
_OBS_MAX_PER_GATE = int(os.getenv("WITNESS_OBS_MAX_PER_GATE", "1000"))

# Global cap on DISTINCT gate_decision_ids in the memory fallback. The
# Redis backend inherits its bound from Redis TTL + maxmemory-policy,
# but the in-process fallback has no such bound — a mesh-authenticated
# caller could exhaust worker memory by streaming observations for
# many distinct gate ids. 50k covers a legitimate 30-min window at the
# §12.1 reference workload (~11.6 rps * 1800s * 4 (C1-C3 subset)) with
# headroom; anything larger under the fallback is either a config
# error (Redis down for a long time) or an attack.
_MEMFB_MAX_GATE_IDS = int(os.getenv("WITNESS_MEMFB_MAX_GATE_IDS", "50000"))

_OBS_KEY = "acp:witness:obs:"
_HEARTBEAT_KEY = "acp:witness:heartbeat:"


def _obs_key(gate_decision_id: str) -> str:
    return f"{_OBS_KEY}{gate_decision_id}"


def _heartbeat_key(witness_id: str) -> str:
    return f"{_HEARTBEAT_KEY}{witness_id}"


class _RedisBackend:
    """Redis LIST + expiring key backend."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def record(self, obs: Observation) -> None:
        key = _obs_key(obs.gate_decision_id)
        # pydantic v2 model → dict → JSON. mode='json' handles Literal + datetime.
        payload = json.dumps(obs.model_dump(mode="json"))
        # Cap-then-append via pipeline; LTRIM is O(1) amortized so this
        # is safe even under abusive floods.
        pipe = self._client.pipeline()
        pipe.rpush(key, payload)
        pipe.ltrim(key, -_OBS_MAX_PER_GATE, -1)
        pipe.expire(key, _OBS_TTL_SECONDS)
        await pipe.execute()

    async def fetch(self, gate_decision_id: str) -> list[Observation]:
        raw = await self._client.lrange(_obs_key(gate_decision_id), 0, -1)
        out: list[Observation] = []
        for item in raw:
            if isinstance(item, (bytes, bytearray)):
                item = item.decode()
            try:
                d = json.loads(item)
                out.append(Observation(**d))
            except (ValueError, TypeError, KeyError) as exc:
                # Corrupted entry — count via the shared swallow counter
                # so ops alerts fire on drift + we don't lose visibility
                # by silently skipping. Attacker-injected garbage would
                # push this metric; so would a code-level model drift.
                swallow_log(logger, "witness_obs_parse_failed", exc,
                            gate_decision_id=gate_decision_id[:32])
        return out

    async def heartbeat(self, witness_id: str, ts: float) -> None:
        await self._client.setex(_heartbeat_key(witness_id), _HEARTBEAT_TTL_SECONDS, str(ts))

    async def last_heartbeat(self, witness_id: str) -> float | None:
        raw = await self._client.get(_heartbeat_key(witness_id))
        if raw is None:
            return None
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode()
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None


class _MemoryFallback:
    """Single-process fallback when Redis isn't reachable. NOT shared
    across workers — health endpoint surfaces `backend=memory` so ops
    knows the deployment is degraded.

    Bounded on TWO axes to keep memory finite:
    (1) per-gate observation cap (`_OBS_MAX_PER_GATE`) — same as Redis
    backend, defeats abusive floods against one gate id;
    (2) global distinct-gate-id cap (`_MEMFB_MAX_GATE_IDS`) — LRU
    eviction of the oldest gate when the total crosses the ceiling.
    Redis has TTL for this; the fallback needs an explicit bound.

    An eviction WARN is CRITICAL-logged because it means the witness
    lost evidence for a gate id it had previously recorded. Ops sees
    the log + should restore Redis to prevent recurrence.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # OrderedDict preserves insertion order — cheapest LRU. Every
        # write touches the key (move_to_end) to keep hot keys warm.
        from collections import OrderedDict
        self._observations: OrderedDict[str, list[Observation]] = OrderedDict()
        self._heartbeat_last_ts: dict[str, float] = {}

    async def record(self, obs: Observation) -> None:
        with self._lock:
            gid = obs.gate_decision_id
            if gid in self._observations:
                # Move to newest position — insertion-order LRU.
                self._observations.move_to_end(gid)
                lst = self._observations[gid]
            else:
                lst = []
                self._observations[gid] = lst
                # Evict the oldest gate id if we're over the global cap.
                # Loop rather than pop-once so a burst of new gates
                # correctly evicts back to the ceiling.
                while len(self._observations) > _MEMFB_MAX_GATE_IDS:
                    evicted_gid, evicted_obs = self._observations.popitem(last=False)
                    logger.critical(
                        "witness_memfb_gate_evicted",
                        evicted_gate_id=evicted_gid[:64],
                        evicted_obs_count=len(evicted_obs),
                        current_gates=len(self._observations),
                        cap=_MEMFB_MAX_GATE_IDS,
                    )
            lst.append(obs)
            if len(lst) > _OBS_MAX_PER_GATE:
                # Same abusive-flood ceiling as Redis backend.
                del lst[:-_OBS_MAX_PER_GATE]

    async def fetch(self, gate_decision_id: str) -> list[Observation]:
        with self._lock:
            return list(self._observations.get(gate_decision_id, []))

    async def heartbeat(self, witness_id: str, ts: float) -> None:
        with self._lock:
            self._heartbeat_last_ts[witness_id] = ts

    async def last_heartbeat(self, witness_id: str) -> float | None:
        with self._lock:
            return self._heartbeat_last_ts.get(witness_id)


_backend: _RedisBackend | _MemoryFallback | None = None
_backend_kind: str = "unknown"


def _init_backend() -> None:
    """Lazy backend init on first call. Redis if REDIS_URL reachable;
    memory fallback otherwise (logged CRITICAL — degraded deployment)."""
    global _backend, _backend_kind
    if _backend is not None:
        return
    try:
        from sdk.common.config import settings
        from sdk.common.redis import get_redis_client
        client = get_redis_client(settings.REDIS_URL, decode_responses=False)
        _backend = _RedisBackend(client)
        _backend_kind = "redis"
        logger.info("witness_store_backend", kind="redis")
    except Exception as exc:
        # Fallback path — surface CRITICAL so ops sees the degraded state.
        logger.critical(
            "witness_store_redis_unavailable_falling_back_to_memory",
            error=str(exc),
        )
        _backend = _MemoryFallback()
        _backend_kind = "memory"


def get_backend_kind() -> str:
    """Health endpoint surfaces this so ops sees whether the deployment
    is running the shared Redis backend or the degraded single-process
    fallback. Never claims 'redis' if Redis is unreachable."""
    _init_backend()
    return _backend_kind


# ─────────────────────────────────────────────────────────────
# Public interface — async now (Redis calls are async).
# ─────────────────────────────────────────────────────────────


async def record(obs: Observation) -> None:
    _init_backend()
    assert _backend is not None
    await _backend.record(obs)


async def fetch(gate_decision_id: str) -> list[Observation]:
    _init_backend()
    assert _backend is not None
    return await _backend.fetch(gate_decision_id)


async def heartbeat(witness_id: str, ts: float) -> None:
    _init_backend()
    assert _backend is not None
    await _backend.heartbeat(witness_id, ts)


async def last_heartbeat(witness_id: str) -> float | None:
    _init_backend()
    assert _backend is not None
    return await _backend.last_heartbeat(witness_id)


# ─────────────────────────────────────────────────────────────
# Test-only reset (used by unit tests to swap the backend).
# ─────────────────────────────────────────────────────────────


def _reset_for_tests(backend: _RedisBackend | _MemoryFallback, kind: str) -> None:
    global _backend, _backend_kind
    _backend = backend
    _backend_kind = kind
