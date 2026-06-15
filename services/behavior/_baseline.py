"""
Per-Agent Behavioral Baseline — Sprint ADR-shift 2026-06-15 (P1).

Datadog approach: every agent gets a moving baseline of "normal":

    {
      "normal_tools":   ["tool.sql_query", "tool.http_request"],
      "tool_freqs":     {"tool.sql_query": 412, "tool.http_request": 28, …},
      "actions_per_day": 200,
      "actions_stddev":  35,
      "hour_histogram": [0, 0, 0, …, 12, 38, 41, …, 0, 0],  # 24 buckets
      "target_tables":  {"customers": 120, "orders": 200},
    }

On each /execute we sample the call against the baseline and emit a
deviation signal as a *finding* that flows into the existing behavior
risk band:

    behavior_anomaly:unusual_tool       — tool never used in baseline
    behavior_anomaly:unusual_hour       — hour-of-day far from baseline
    behavior_anomaly:burst_3sigma       — >3σ over daily avg
    behavior_anomaly:unusual_target     — accessing a table the agent
                                          has never touched

The baseline is bumped on every call so it stays fresh. The rolling
window is 7 days — old samples expire from the histogram.

We intentionally don't make this an ML model. A buyer can read every
finding above and reproduce it from the Redis state.
"""
from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redis.asyncio import Redis as _Redis

BASELINE_PREFIX = "acp:baseline:"
BASELINE_TTL = 7 * 24 * 3600  # 7 days


def _key(agent_id: str, kind: str) -> str:
    return f"{BASELINE_PREFIX}{agent_id}:{kind}"


async def record_risk_score(
    redis: "_Redis",
    *,
    agent_id: str,
    risk_score: int,
    timestamp: float | None = None,
) -> list[str]:
    """ARCH-5 2026-06-15 — record this call's inherent risk score into a
    rolling 100-call buffer and return drift findings.

    Drift finding emitted when:
        * agent has >= 30 calls of history, AND
        * current_score > rolling_avg + 3 * stddev, AND
        * current_score > 20  (don't fire on benign jitter)

    Storage:
        acp:baseline:{agent_id}:risk  Redis LIST (LPUSH + LTRIM 0 99)
    """
    findings: list[str] = []
    now = timestamp or time.time()
    try:
        key = _key(agent_id, "risk")
        await redis.lpush(key, f"{int(now)}:{int(risk_score)}")
        await redis.ltrim(key, 0, 99)
        await redis.expire(key, BASELINE_TTL)
        raw = await redis.lrange(key, 0, 99)
    except Exception:
        return findings

    scores: list[int] = []
    for r in raw:
        if isinstance(r, (bytes, bytearray)):
            r = r.decode("utf-8", "replace")
        try:
            parts = r.split(":", 1)
            if len(parts) == 2:
                scores.append(int(parts[1]))
        except Exception:
            continue
    if len(scores) < 30:
        return findings
    # Exclude the current call from the baseline; it's already in there.
    prior = scores[1:]
    if not prior:
        return findings
    avg = sum(prior) / len(prior)
    var = sum((x - avg) ** 2 for x in prior) / len(prior)
    stddev = math.sqrt(var) or 1.0
    if risk_score > avg + 3 * stddev and risk_score > 20:
        findings.append(
            f"behavior_anomaly:risk_drift:{int(risk_score)}_vs_avg_{int(avg)}"
        )
    return findings


async def record_and_score(
    redis: "_Redis",
    *,
    tenant_id: str,
    agent_id: str,
    tool: str,
    table_norm: str | None,
    timestamp: float | None = None,
) -> list[str]:
    """
    Update the agent's baseline with this call AND return a list of
    deviation findings. Each finding is a string like
    `behavior_anomaly:unusual_tool` that the behavior service folds into
    the response findings array.

    Best-effort: a Redis failure returns [] so the call path keeps
    flowing.
    """
    findings: list[str] = []
    now = timestamp or time.time()
    hour = int((now // 3600) % 24)
    day_bucket = int(now // 86400)  # UTC day

    try:
        pipe = redis.pipeline()
        # Tool frequency histogram (lifetime, but capped per write).
        pipe.hincrby(_key(agent_id, "tools"), tool, 1)
        pipe.expire(_key(agent_id, "tools"), BASELINE_TTL)
        # Hour-of-day histogram (24 buckets).
        pipe.hincrby(_key(agent_id, "hours"), str(hour), 1)
        pipe.expire(_key(agent_id, "hours"), BASELINE_TTL)
        # Daily action counter.
        pipe.hincrby(_key(agent_id, "daily"), str(day_bucket), 1)
        pipe.expire(_key(agent_id, "daily"), BASELINE_TTL)
        # Target-table histogram.
        if table_norm:
            pipe.hincrby(_key(agent_id, "tables"), table_norm, 1)
            pipe.expire(_key(agent_id, "tables"), BASELINE_TTL)
        await pipe.execute()
    except Exception:
        return findings

    # Now compute deviations against the *current* baseline.
    try:
        # 1. Unusual tool: this tool has < 3 prior uses AND the agent has
        # at least 30 calls in total → "new tool for established agent".
        tools = await redis.hgetall(_key(agent_id, "tools"))
        tools_dec = _decode_dict(tools)
        total_calls = sum(int(v) for v in tools_dec.values())
        if total_calls >= 30:
            tool_count = int(tools_dec.get(tool, 0))
            if tool_count <= 3:
                findings.append(f"behavior_anomaly:unusual_tool:{tool}")

        # 2. Unusual hour: this hour has 0 prior calls AND the agent has
        # at least 5 hours with non-zero counts (i.e. baseline is
        # established).
        hours = await redis.hgetall(_key(agent_id, "hours"))
        hours_dec = _decode_dict(hours)
        active_hours = sum(1 for v in hours_dec.values() if int(v) > 0)
        if active_hours >= 5:
            if int(hours_dec.get(str(hour), 0)) <= 1:
                findings.append(f"behavior_anomaly:unusual_hour:{hour:02d}")

        # 3. Daily burst: today's count > avg + 3σ over last 7 days.
        daily = await redis.hgetall(_key(agent_id, "daily"))
        daily_dec = _decode_dict(daily)
        if len(daily_dec) >= 3:
            other_days = [int(v) for k, v in daily_dec.items()
                          if k != str(day_bucket)]
            if other_days:
                avg = sum(other_days) / len(other_days)
                var = sum((x - avg) ** 2 for x in other_days) / len(other_days)
                stddev = math.sqrt(var) or 1.0
                today = int(daily_dec.get(str(day_bucket), 0))
                if today > avg + 3 * stddev and today > 50:
                    findings.append(
                        f"behavior_anomaly:burst_3sigma:{today}_vs_avg_{int(avg)}"
                    )

        # 4. Unusual target table.
        if table_norm:
            tables_dec = _decode_dict(await redis.hgetall(_key(agent_id, "tables")))
            total_tables = sum(int(v) for v in tables_dec.values())
            if total_tables >= 20:
                if int(tables_dec.get(table_norm, 0)) <= 2:
                    findings.append(
                        f"behavior_anomaly:unusual_target:{table_norm}"
                    )
    except Exception:
        pass
    return findings


def _decode_dict(h: dict) -> dict[str, str]:
    out = {}
    for k, v in h.items():
        if isinstance(k, (bytes, bytearray)):
            k = k.decode("utf-8", "replace")
        if isinstance(v, (bytes, bytearray)):
            v = v.decode("utf-8", "replace")
        out[k] = v
    return out
