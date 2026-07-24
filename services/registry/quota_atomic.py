"""ATF v3.2 §4.4 — atomic tenant issuance quota.

Same TOCTOU-safe pattern as `services/gateway/proxy_helpers._RESERVE_LUA`
(the LLM budget compare-and-charge from S7 / P1-6). Read + increment
happen inside a single Redis EVAL call so two concurrent create_agent
requests at count == quota-1 can't both succeed.

Return contract:
    ('ok', new_count, quota, headroom_alert: bool)
      → mint permitted. If headroom_alert is True, emit a C2 audit event
        (the mint crossed the 95%-full threshold this call).
    ('exceeded', current_count, quota, False)
      → mint refused; return HTTP 429 and emit a C2 audit event.
    ('err', 0, 0, False)
      → Redis unreachable; caller decides how to fall back. The current
        registry policy fails OPEN (would DoS every legitimate mint on
        Redis blip) — this module returns the error, callers choose.
"""
from __future__ import annotations

from typing import Any

import structlog

from sdk.common.background import swallow_log

_logger = structlog.get_logger(__name__)

# KEYS[1]     = quota counter key (per-tenant)
# ARGV[1]     = quota ceiling (int)
# ARGV[2]     = headroom threshold count (int) — first count at/above → alert
# Returns:
#   {'ok',  new_count, quota, alert_flag}
#   {'exceeded', current_count, quota, 0}
_QUOTA_RESERVE_LUA = """
local cur = tonumber(redis.call('GET', KEYS[1])) or 0
local cap = tonumber(ARGV[1])
local warn = tonumber(ARGV[2])
if cur >= cap then
    return {'exceeded', tostring(cur), tostring(cap), '0'}
end
local newv = redis.call('INCR', KEYS[1])
local alert = '0'
if newv >= warn and (newv - 1) < warn then
    alert = '1'
end
return {'ok', tostring(newv), tostring(cap), alert}
"""


def _headroom_threshold(cap: int) -> int:
    """Match `evaluate_mint` in `sdk/common/tenant_quota.py`:
    ``max(cap - 5, int(cap * 0.95))`` — the higher of the two so the
    alert fires closer to the ceiling as caps scale up. The atomic-op
    form emits the alert exactly ONCE — on the mint that crosses the
    boundary — not once per mint above 95%."""
    return max(cap - 5, int(cap * 0.95))


async def reserve_quota_slot(
    redis: Any,
    tenant_id: str,
    quota_cap: int,
    *,
    key_prefix: str = "acp:tenant:profile_count:",
) -> tuple[str, int, int, bool]:
    """Atomic compare-and-INCR against the per-tenant quota ceiling."""
    key = f"{key_prefix}{tenant_id}"
    try:
        raw = await redis.execute_command(
            "EVAL", _QUOTA_RESERVE_LUA, 1, key,
            str(int(quota_cap)), str(_headroom_threshold(int(quota_cap))),
        )
    except Exception:
        return ("err", 0, 0, False)

    # Redis returns list of bytes/str depending on decode_responses.
    def _s(v: Any) -> str:
        return v.decode() if isinstance(v, (bytes, bytearray)) else str(v)

    status = _s(raw[0])
    current_or_new = int(_s(raw[1]))
    cap = int(_s(raw[2]))
    alert = _s(raw[3]) == "1"
    return status, current_or_new, cap, alert


async def release_quota_slot(
    redis: Any,
    tenant_id: str,
    *,
    key_prefix: str = "acp:tenant:profile_count:",
) -> None:
    """Roll back a reserved slot on downstream failure. DECR is safe:
    the Lua guard above never lets us go past `cap`, and the mint that's
    being rolled back was just incremented so `cur >= 1`. Redis DECR does
    not floor at 0 by itself, but a stray DECR without a matching INCR
    can only surface as "profile count says -1" which is a monitoring
    signal, never a security issue.

    Rollback failures are surfaced via `swallow_log` so the shared
    `EXCEPTION_SWALLOWED_TOTAL{event="tenant_quota_release_failed"}`
    counter fires and ops can page on drift. Silent swallow via bare
    `pass` would hide a Redis outage during a burst of failed mints —
    the counter drifts high with no signal.
    """
    key = f"{key_prefix}{tenant_id}"
    try:
        await redis.decr(key)
    except Exception as exc:
        # Rollback failure is a monitoring problem, not a security one —
        # the counter drifts high by 1; the 95% alert catches drift.
        # We still want the metric though, so ops sees rollback failures
        # cluster (which would indicate a Redis-side outage).
        swallow_log(_logger, "tenant_quota_release_failed", exc,
                    tenant_id=tenant_id[:64])


if __name__ == "__main__":
    # Fake-redis test — atomic EVAL is exercised in the integration test
    # suite. Here we prove the headroom math matches evaluate_mint.
    assert _headroom_threshold(1000) == 995   # cap-5 > 0.95*cap
    assert _headroom_threshold(100)  == 95    # tied — 100-5 == 95
    assert _headroom_threshold(50)   == 47    # cap-5 = 45, 0.95*50 = 47 → higher wins
    assert _headroom_threshold(10)   == 9     # 0.95*10 = 9.5 → int() = 9
    print("quota_atomic OK")
