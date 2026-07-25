"""Per-tenant admin toggle for opt-in features (Sprint UI-3, ATF §9.2).

Two feature flags today (`c3_sampling`, `behavior_fingerprinting`)
carry ATF-level opt-in constraints and are also cost/privacy-sensitive
enough that a per-tenant admin knob is the honest surface. Historically
they were env-var comma-lists (`ACP_C3_SAMPLING_TENANTS`,
`ACP_BEHAVIOR_FINGERPRINTING_TENANTS`) — this module adds a Redis-
backed per-tenant override so the UI can flip the flag without ops
touching env / redeploy.

Semantics:
  * If the Redis flag is explicitly SET (true or false) → that wins.
  * If unset → fall back to the historical env-var comma-list check.
  * In-process 60s TTL cache keeps the hot path cheap (should_sample
    runs on every C3 request; get_mode_for on every learned-signal
    consumption).

Never fabricates: an unset Redis flag reads as `None`, and the
env-var fallback is honest about its own comma-list rules.
"""
from __future__ import annotations

import os
import time
from typing import Any

_REDIS_KEY_PREFIX = "acp:tenant_settings:"
_CACHE_TTL_SECONDS = 60.0

# in-process cache: {(tenant_id, flag_name): (expiry_epoch, value_or_None)}
_cache: dict[tuple[str, str], tuple[float, bool | None]] = {}


def _redis_key(tenant_id: str) -> str:
    return f"{_REDIS_KEY_PREFIX}{tenant_id}"


def _now() -> float:
    return time.monotonic()


def _parse_bool(raw: Any) -> bool | None:
    """Redis returns bytes or str depending on decode_responses config.
    We accept both, plus python bool for tests. Unrecognised → None."""
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("ascii", errors="replace")
    if isinstance(raw, bool):
        return raw
    s = str(raw).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    return None


async def get_flag(
    redis: Any,
    tenant_id: str,
    flag_name: str,
    *,
    env_var: str | None = None,
) -> bool:
    """Return the effective value of `flag_name` for `tenant_id`.

    Order:
      1. In-process cache (60s).
      2. Redis hash field `acp:tenant_settings:{tenant_id}[flag_name]`.
      3. env-var fallback: tenant_id in the comma-list at `env_var`.

    Env fallback only runs when `env_var` is passed AND the Redis flag
    is unset. This preserves the historical opt-in surface while
    letting per-tenant admins flip the flag via UI.

    `redis` is an aioredis-style client with async `hget(key, field)`;
    failures degrade to env fallback (cache-miss ok — the important
    invariant is that a Redis outage never accidentally ENABLES a
    feature that was explicitly disabled).
    """
    cache_key = (tenant_id, flag_name)
    cached = _cache.get(cache_key)
    if cached is not None and cached[0] > _now():
        val = cached[1]
        if val is not None:
            return val
        # cached "unset" → fall through to env-var check below

    parsed: bool | None = None
    if cached is None or cached[0] <= _now():
        try:
            raw = await redis.hget(_redis_key(tenant_id), flag_name)
            parsed = _parse_bool(raw)
        except Exception:
            # Redis unreachable — don't cache; retry next call.
            parsed = None
        else:
            _cache[cache_key] = (_now() + _CACHE_TTL_SECONDS, parsed)

    if parsed is not None:
        return parsed

    # Env-var fallback: tenant is in the comma-list at `env_var`.
    if env_var:
        raw_env = os.getenv(env_var, "")
        enabled = {t.strip() for t in raw_env.split(",") if t.strip()}
        return tenant_id in enabled

    return False


async def set_flag(
    redis: Any,
    tenant_id: str,
    flag_name: str,
    value: bool,
) -> None:
    """Persist the per-tenant flag override + invalidate the local cache.

    `value=True` writes "1"; `value=False` writes "0". The read path
    treats both as explicit (not-None) so an explicit False overrides
    the env-var enable-list.
    """
    await redis.hset(_redis_key(tenant_id), flag_name, "1" if value else "0")
    _cache.pop((tenant_id, flag_name), None)


async def get_all_flags(redis: Any, tenant_id: str) -> dict[str, bool | None]:
    """Return the raw Redis hash for a tenant (unset flags are None,
    not env-fallback-resolved). Used by the admin GET endpoint to show
    what the UI-set value actually is vs. env fallback."""
    try:
        raw = await redis.hgetall(_redis_key(tenant_id))
    except Exception:
        return {}
    out: dict[str, bool | None] = {}
    for k, v in (raw or {}).items():
        key = k.decode("ascii") if isinstance(k, (bytes, bytearray)) else str(k)
        out[key] = _parse_bool(v)
    return out


def _reset_cache_for_tests() -> None:
    _cache.clear()


if __name__ == "__main__":
    # Runnable self-check — the security invariant is "an explicit UI
    # false MUST override an env-var enable-list, and a Redis outage
    # MUST NOT flip an explicit-false to fallback-true." Verify both.
    import asyncio

    class _FakeRedis:
        def __init__(self, store: dict[str, dict[str, bytes]] | None = None,
                     fail: bool = False) -> None:
            self.store = store or {}
            self.fail = fail

        async def hget(self, key: str, field: str):
            if self.fail:
                raise RuntimeError("redis down")
            return self.store.get(key, {}).get(field)

        async def hset(self, key: str, field: str, value):
            self.store.setdefault(key, {})[field] = (
                value.encode() if isinstance(value, str) else value
            )

        async def hgetall(self, key: str):
            return self.store.get(key, {})

    async def _run() -> None:
        os.environ.pop("ACP_C3_SAMPLING_TENANTS", None)
        _reset_cache_for_tests()

        r = _FakeRedis()

        # 1. Unset + no env → False (default off)
        assert await get_flag(r, "t1", "c3_sampling", env_var="ACP_C3_SAMPLING_TENANTS") is False

        # 2. Env-var enable list → True when Redis flag unset
        os.environ["ACP_C3_SAMPLING_TENANTS"] = "t1,t2"
        _reset_cache_for_tests()
        assert await get_flag(r, "t1", "c3_sampling", env_var="ACP_C3_SAMPLING_TENANTS") is True
        assert await get_flag(r, "t3", "c3_sampling", env_var="ACP_C3_SAMPLING_TENANTS") is False

        # 3. Explicit UI FALSE MUST override env-var enable
        _reset_cache_for_tests()
        await set_flag(r, "t1", "c3_sampling", False)
        assert await get_flag(r, "t1", "c3_sampling", env_var="ACP_C3_SAMPLING_TENANTS") is False

        # 4. Explicit UI TRUE for tenant not in env-var list works
        _reset_cache_for_tests()
        await set_flag(r, "t3", "c3_sampling", True)
        assert await get_flag(r, "t3", "c3_sampling", env_var="ACP_C3_SAMPLING_TENANTS") is True

        # 5. Redis outage MUST fall back to env-var (never grants a
        #    disabled tenant a flag it hasn't earned)
        _reset_cache_for_tests()
        fail_r = _FakeRedis(fail=True)
        assert await get_flag(fail_r, "t1", "c3_sampling", env_var="ACP_C3_SAMPLING_TENANTS") is True   # env allows
        assert await get_flag(fail_r, "t3", "c3_sampling", env_var="ACP_C3_SAMPLING_TENANTS") is False  # env denies

        # 6. bytes vs str Redis payload both parse
        assert _parse_bool(b"1") is True
        assert _parse_bool(b"false") is False
        assert _parse_bool("on") is True
        assert _parse_bool(None) is None
        assert _parse_bool("garbage") is None

        os.environ.pop("ACP_C3_SAMPLING_TENANTS", None)
        _reset_cache_for_tests()
        print("tenant_settings OK")

    asyncio.run(_run())
