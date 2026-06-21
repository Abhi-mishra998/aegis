from __future__ import annotations

import time

import structlog
from redis.asyncio import Redis

from sdk.common.config import settings

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# LUA SCRIPT
# Loaded once at class init; sha cached via redis.register_script()
# ---------------------------------------------------------------------------

_LUA_TOKEN_BUCKET = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2]) -- tokens per second
local cost = tonumber(ARGV[3]) or 1
local now = tonumber(ARGV[4]) -- current timestamp in seconds

local bucket_data = redis.call('HMGET', key, 'tokens', 'last_refill')
local tokens = tonumber(bucket_data[1])
local last_refill = tonumber(bucket_data[2])

if tokens == nil then
    tokens = capacity
    last_refill = now
else
    local time_passed = math.max(0, now - last_refill)
    local refill = time_passed * refill_rate
    tokens = math.min(capacity, tokens + refill)
    last_refill = now
end

local allowed = 0
if tokens >= cost then
    tokens = tokens - cost
    allowed = 1
end

redis.call('HMSET', key, 'tokens', tokens, 'last_refill', last_refill)
local expire_time = math.ceil(capacity / refill_rate) * 2
if expire_time < 60 then
    expire_time = 60
end
redis.call('EXPIRE', key, expire_time)

return allowed
"""


# Atomic INCR-with-cap check for daily/monthly windows. Replaces the
# previous non-atomic INCR + read-back pattern that allowed concurrent
# callers to both observe `used <= cap` after their respective INCRs
# (N13: race that could 10-20× over-spend a $5/day inference cap).
#
# KEYS[1] = window counter key
# ARGV[1] = cap (integer, > 0)
# ARGV[2] = ttl_seconds for first INCR (sets EXPIRE so the window rolls
#           over cleanly without a separate round-trip)
# Returns {allowed (0|1), used_after_incr}
_LUA_WINDOW_INCR = """
local used = redis.call('INCR', KEYS[1])
if used == 1 then
    redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
end
local cap = tonumber(ARGV[1])
if used > cap then
    return {0, used}
else
    return {1, used}
end
"""


class RateLimiter:
    """
    Atomic Redis Lua rate limiter (Token Bucket).
    All public methods return True (allowed) or False (denied).
    """

    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        # Register script once
        self._script = redis.register_script(_LUA_TOKEN_BUCKET)

    # ------------------------------------------------------------------
    # PUBLIC API (request-based)
    # ------------------------------------------------------------------

    async def check_limit(
        self, key: str, limit: int, window_seconds: int, tier: str = "basic", check_pool: bool = True
    ) -> bool:
        """
        Token Bucket rate check with Priority-Aware isolation.

        Tiers:
          - enterprise: Reserved capacity. Only checks per-tenant bucket.
          - premium/basic: Fair share. Checks both per-tenant bucket AND global best-effort pool.

        check_pool=False skips the shared pool decrement — pass False for agent/token calls
        when the tenant call on the same request has already decremented it.
        """
        now = time.time()

        # 1. Check Global Best-Effort Pool for non-enterprise tiers
        if tier != "enterprise" and check_pool:
            # Best Effort pool is shared across all non-enterprise tenants
            # We use a 50% system-wide capacity for best effort
            be_key = "acp:ratelimit:best_effort_pool"
            be_limit = int(settings.GLOBAL_RATE_LIMIT * 0.5)
            be_refill_rate = be_limit / 60

            be_allowed = await self._script(
                keys=[be_key],
                args=[be_limit, be_refill_rate, 1, now],
            )
            if not int(be_allowed):
                logger.warning("best_effort_pool_exhausted", tenant_key=key, tier=tier)
                return False

        # 2. Check individual tenant bucket
        capacity = limit
        refill_rate = limit / window_seconds if window_seconds > 0 else limit

        result = await self._script(
            keys=[key],
            args=[capacity, refill_rate, 1, now],
        )
        allowed = int(result) > 0

        if not allowed:
            logger.warning("rate_limit_exceeded", key=key, tier=tier, limit=limit)

        return allowed

    # ------------------------------------------------------------------
    # TOKEN-BASED LIMITING
    # ------------------------------------------------------------------

    async def check_token_limit(
        self,
        jti: str | None,
        agent_id: str,
        limit: int,
        window_seconds: int,
        tier: str = "basic",
        check_pool: bool = True,
    ) -> bool:
        """
        Per-token rate limit. Uses JWT `jti` as the primary key.
        Falls back to agent_id key when jti is absent.
        """
        key = f"rate:token:{jti}" if jti else f"rate:agent:{agent_id}"

        return await self.check_limit(key, limit, window_seconds, tier=tier, check_pool=check_pool)


# --------------------------------------------------------------------------- #
# Sprint 3.2 — TenantQuotaLimiter                                             #
# --------------------------------------------------------------------------- #

import json as _json  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402
from typing import Any  # noqa: E402


@dataclass
class QuotaDecision:
    """Outcome of a TenantQuotaLimiter.check() call.

    `limit_type` is None when allowed; one of `rps | daily | monthly`
    when denied. `reset_at` is the ISO-8601 UTC timestamp at which the
    relevant bucket / counter will allow the next request.
    """
    allowed:        bool
    limit_type:     str | None = None
    reset_at:       str | None = None
    retry_after_s:  int = 0
    usage:          dict[str, Any] = field(default_factory=dict)


class TenantQuotaLimiter:
    """Per-tenant rate + cap enforcement layered on top of RateLimiter.

    Three layers, checked in order — first denial short-circuits:

      1. RPS / burst (Lua token-bucket — capacity = burst, refill = rps)
      2. Daily cap   (INCR on key `acp:quota:daily:{tenant}:{YYYY-MM-DD}`,
                      TTL ≈ 36h so counter rolls over cleanly)
      3. Monthly cap (INCR on key `acp:quota:monthly:{tenant}:{YYYY-MM}`,
                      TTL ≈ 35 days)

    Crossing 80% of the monthly cap emits a warning side-effect exactly
    once per tenant per month (Redis SETNX flag + a Redis Stream event
    on `acp:billing_alerts` for the existing notification worker).
    """

    # Stream the 80% warning event lands on so external consumers
    # (notification worker, ops-side email/Slack relay) can pick it up
    # without coupling to this module.
    BILLING_ALERTS_STREAM = "acp:billing_alerts"
    BILLING_ALERTS_MAXLEN = 10_000

    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self._bucket_script = redis.register_script(_LUA_TOKEN_BUCKET)
        self._window_script = redis.register_script(_LUA_WINDOW_INCR)

    # ── Public API ────────────────────────────────────────────────────
    async def check(
        self,
        *,
        tenant_id: str,
        requests_per_second: int,
        burst: int,
        daily_cap: int,
        monthly_cap: int | None,
    ) -> QuotaDecision:
        """Atomic per-tenant quota check. Returns a QuotaDecision."""
        now = datetime.now(tz=UTC)
        # 1. RPS / burst token bucket
        rps_allowed = await self._check_rps_burst(
            tenant_id=tenant_id,
            requests_per_second=requests_per_second,
            burst=burst,
            now_epoch=now.timestamp(),
        )
        if not rps_allowed:
            reset_at = now + timedelta(seconds=max(1, int(1.0 / max(1, requests_per_second))))
            return QuotaDecision(
                allowed=False, limit_type="rps",
                reset_at=reset_at.isoformat(),
                retry_after_s=1,
                usage={"requests_per_second": requests_per_second, "burst": burst},
            )

        # 2. Daily cap — atomic INCR + cap-check (Lua, single round-trip)
        daily_allowed, daily_used, daily_reset = await self._incr_window(
            kind="daily", tenant_id=tenant_id, now=now,
            ttl_seconds=36 * 3600, cap=daily_cap,
        )
        if not daily_allowed:
            # We've already incremented — that's correct: the operator's
            # forensics need to see the OVER-the-cap traffic too. The
            # response says no; the counter doesn't lie.
            return QuotaDecision(
                allowed=False, limit_type="daily",
                reset_at=daily_reset.isoformat(),
                retry_after_s=max(1, int((daily_reset - now).total_seconds())),
                usage={"daily_used": daily_used, "daily_cap": daily_cap,
                       "daily_resets_at": daily_reset.isoformat()},
            )

        # 3. Monthly cap (NULL = no cap → _incr_window short-circuits to bare INCR)
        monthly_used = 0
        monthly_reset = now
        monthly_allowed = True
        if monthly_cap is not None:
            monthly_allowed, monthly_used, monthly_reset = await self._incr_window(
                kind="monthly", tenant_id=tenant_id, now=now,
                ttl_seconds=35 * 24 * 3600, cap=monthly_cap,
            )
            await self._maybe_emit_monthly_warning(
                tenant_id=tenant_id,
                used=monthly_used, cap=monthly_cap,
                now=now, reset_at=monthly_reset,
            )
            if not monthly_allowed:
                return QuotaDecision(
                    allowed=False, limit_type="monthly",
                    reset_at=monthly_reset.isoformat(),
                    retry_after_s=max(1, int((monthly_reset - now).total_seconds())),
                    usage={"monthly_used": monthly_used, "monthly_cap": monthly_cap,
                           "monthly_resets_at": monthly_reset.isoformat()},
                )

        return QuotaDecision(
            allowed=True,
            usage={
                "daily_used":           daily_used,
                "daily_cap":            daily_cap,
                "daily_resets_at":      daily_reset.isoformat(),
                "monthly_used":         monthly_used,
                "monthly_cap":          monthly_cap,
                "monthly_resets_at":    monthly_reset.isoformat() if monthly_cap is not None else None,
            },
        )

    async def usage_snapshot(
        self,
        *,
        tenant_id: str,
        daily_cap: int,
        monthly_cap: int | None,
    ) -> dict[str, Any]:
        """Read-only counters for `/tenant/quota` — never increments."""
        now = datetime.now(tz=UTC)
        daily_key = self._daily_key(tenant_id, now)
        monthly_key = self._monthly_key(tenant_id, now)
        try:
            d_raw = await self._redis.get(daily_key)
            daily_used = int(d_raw or 0)
        except Exception:
            daily_used = 0
        monthly_used = 0
        if monthly_cap is not None:
            try:
                m_raw = await self._redis.get(monthly_key)
                monthly_used = int(m_raw or 0)
            except Exception:
                monthly_used = 0

        warn_key = self._monthly_warn_key(tenant_id, now)
        try:
            warn_emitted = bool(await self._redis.exists(warn_key))
        except Exception:
            warn_emitted = False

        return {
            "daily_used":          daily_used,
            "daily_cap":           daily_cap,
            "daily_resets_at":     self._next_utc_midnight(now).isoformat(),
            "monthly_used":        monthly_used,
            "monthly_cap":         monthly_cap,
            "monthly_resets_at":   self._next_utc_month(now).isoformat() if monthly_cap is not None else None,
            "monthly_warn_emitted": warn_emitted,
        }

    # ── Internals ─────────────────────────────────────────────────────
    async def _check_rps_burst(
        self, *, tenant_id: str, requests_per_second: int, burst: int,
        now_epoch: float,
    ) -> bool:
        capacity = max(1, int(burst))
        refill = max(1, int(requests_per_second))
        key = f"acp:quota:rps:{tenant_id}"
        result = await self._bucket_script(
            keys=[key], args=[capacity, refill, 1, now_epoch],
        )
        return int(result) > 0

    async def _incr_window(
        self,
        *,
        kind: str,
        tenant_id: str,
        now: datetime,
        ttl_seconds: int,
        cap: int | None,
    ) -> tuple[bool, int, datetime]:
        """Atomically INCR the window counter and decide allow/deny in
        the same Redis round-trip.

        Replaces the prior non-atomic INCR + read-back pattern (N13):
        two concurrent callers could both INCR, both read back
        `used <= cap`, and both proceed — exceeding the cap by the
        number of racing callers.

        Returns ``(allowed, used_after_incr, reset_at)``.

        When ``cap is None`` the window is effectively unbounded
        (e.g. monthly_cap=None); we still INCR so /tenant/quota can
        report usage, but always return ``allowed=True`` without invoking
        the Lua cap check.
        """
        if kind == "daily":
            key = self._daily_key(tenant_id, now)
            reset_at = self._next_utc_midnight(now)
        elif kind == "monthly":
            key = self._monthly_key(tenant_id, now)
            reset_at = self._next_utc_month(now)
        else:
            raise ValueError(f"unknown window {kind!r}")

        if cap is None:
            # Unbounded — short-circuit to a bare INCR (still atomic w.r.t.
            # itself; no cap to enforce).
            try:
                used = int(await self._redis.incr(key))
                if used == 1:
                    await self._redis.expire(key, ttl_seconds)
            except Exception:
                # Redis hiccup: fail-open on the counters (rps was already
                # checked atomically above). Caller's existing 429/SLO
                # alerting will catch any pathological load.
                used = 0
            return True, used, reset_at

        try:
            raw = await self._window_script(
                keys=[key], args=[int(cap), int(ttl_seconds)],
            )
            # Lua returns a Lua array → redis-py decodes to a Python list
            # of ints (possibly bytes-encoded ints under some clients).
            allowed_raw, used_raw = raw[0], raw[1]
            allowed = int(allowed_raw) == 1
            used = int(used_raw)
        except Exception:
            # Redis hiccup: fail-open (matches the pre-fix behaviour;
            # rps is the authoritative atomic gate above).
            return True, 0, reset_at
        return allowed, used, reset_at

    async def _maybe_emit_monthly_warning(
        self, *, tenant_id: str, used: int, cap: int,
        now: datetime, reset_at: datetime,
    ) -> None:
        if cap <= 0:
            return
        if used < int(cap * 0.80):
            return
        warn_key = self._monthly_warn_key(tenant_id, now)
        try:
            # SETNX returns True only the first time. Idempotent per
            # tenant per month — no flood if the cap is hit by a burst
            # and we cross 80% multiple times in the same minute.
            first_time = await self._redis.setnx(warn_key, "1")
            if first_time:
                # Match the monthly key's TTL so the flag clears
                # automatically on month rollover.
                await self._redis.expire(warn_key, 35 * 24 * 3600)
                payload = {
                    "kind":            "monthly_quota_warning",
                    "tenant_id":       tenant_id,
                    "monthly_used":    used,
                    "monthly_cap":     cap,
                    "percent":         round(used / cap * 100.0, 2),
                    "monthly_resets_at": reset_at.isoformat(),
                    "ts":              int(now.timestamp()),
                }
                await self._redis.xadd(
                    self.BILLING_ALERTS_STREAM,
                    {"data": _json.dumps(payload)},
                    maxlen=self.BILLING_ALERTS_MAXLEN, approximate=True,
                )
                try:
                    # Optional Prometheus signal so dashboards can plot
                    # "warning fired this month" without consuming the
                    # alerts stream.
                    from sdk.utils import TENANT_QUOTA_WARNING_TOTAL
                    TENANT_QUOTA_WARNING_TOTAL.labels(tenant=tenant_id).inc()
                except Exception:
                    pass
        except Exception:
            # Warning is best-effort — never block a request because
            # we couldn't notify.
            pass

    # ── Key helpers ───────────────────────────────────────────────────
    @staticmethod
    def _daily_key(tenant_id: str, now: datetime) -> str:
        return f"acp:quota:daily:{tenant_id}:{now.strftime('%Y-%m-%d')}"

    @staticmethod
    def _monthly_key(tenant_id: str, now: datetime) -> str:
        return f"acp:quota:monthly:{tenant_id}:{now.strftime('%Y-%m')}"

    @staticmethod
    def _monthly_warn_key(tenant_id: str, now: datetime) -> str:
        return f"acp:quota:monthly_warn:{tenant_id}:{now.strftime('%Y-%m')}"

    @staticmethod
    def _next_utc_midnight(now: datetime) -> datetime:
        tmrw = (now + timedelta(days=1)).date()
        return datetime(tmrw.year, tmrw.month, tmrw.day, tzinfo=UTC)

    @staticmethod
    def _next_utc_month(now: datetime) -> datetime:
        if now.month == 12:
            return datetime(now.year + 1, 1, 1, tzinfo=UTC)
        return datetime(now.year, now.month + 1, 1, tzinfo=UTC)
