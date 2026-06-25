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

N14 cold-start lock (2026-06-21) — Sprint P2 finding fix.
================================================================
A compromised agent that floods 30+ calls of a newly-unlocked tool
(e.g. ``http.post`` to IMDS) used to "train out" the unusual_tool
finding: the baseline was bumped on every call BEFORE the anomaly
check, so by call 30 the new tool was "normal" and never flagged
again. The single ``behavior_anomaly:unusual_tool`` finding emitted at
call ~5 was easily lost in noise.

Fix: a cold-start lock. For the first ``_BASELINE_LOCK_AFTER_CALLS``
calls of an agent (default 100, env var override), the baseline learns
freely. Once that threshold is crossed the baseline is *locked* —
every subsequent call still scores against the frozen baseline and
emits findings, but the baseline hashes are never written again. A
tool that first appears post-lock is flagged on every single call,
forever.

State:
    acp:baseline:{agent_id}:call_count   — INCR'd on every call (lifetime
                                            counter; never frozen). Used
                                            to decide lock state.
    acp:baseline:{agent_id}:tools/hours/daily/tables — frozen after lock.

Prometheus:
    acp_behavior_baseline_locked{agent_id} — 0 or 1 gauge so ops can
                                              see which agents are
                                              locked.

We intentionally don't make this an ML model. A buyer can read every
finding above and reproduce it from the Redis state.
"""
from __future__ import annotations

import math
import os
import time
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from redis.asyncio import Redis as _Redis

logger = structlog.get_logger(__name__)

BASELINE_PREFIX = "acp:baseline:"
BASELINE_TTL = 7 * 24 * 3600  # 7 days


def _resolve_lock_threshold() -> int:
    """Read the cold-start lock threshold from env (``ACP_BASELINE_LOCK_AFTER_CALLS``).

    Default 100. Treat unparseable values as the default — never crash
    the call path on a bad config knob.
    """
    raw = os.environ.get("ACP_BASELINE_LOCK_AFTER_CALLS")
    if raw is None:
        return 100
    try:
        v = int(raw)
        return v if v > 0 else 100
    except (TypeError, ValueError):
        return 100


_BASELINE_LOCK_AFTER_CALLS = _resolve_lock_threshold()


# Prometheus gauge — best-effort registration. If prometheus_client is
# unavailable (e.g. minimal test env) the helper becomes a no-op so the
# behavior pipeline keeps flowing.
try:
    from prometheus_client import Gauge

    BASELINE_LOCKED_GAUGE = Gauge(
        "acp_behavior_baseline_locked",
        "Per-agent cold-start baseline lock state (1 = locked, 0 = still learning).",
        ["agent_id"],
    )
except Exception:  # pragma: no cover — only hit when prometheus_client missing
    BASELINE_LOCKED_GAUGE = None


def _set_lock_gauge(agent_id: str, locked: bool) -> None:
    """Best-effort gauge update — never raise into the call path."""
    if BASELINE_LOCKED_GAUGE is None:
        return
    try:
        BASELINE_LOCKED_GAUGE.labels(agent_id=agent_id).set(1 if locked else 0)
    except Exception as exc:
        logger.warning("baseline_gauge_set_failed", agent_id=agent_id, error=str(exc))


def is_baseline_locked(call_count: int) -> bool:
    """Pure predicate — true once an agent has crossed the lock threshold.

    Exposed for tests and for any operator tool that wants to surface
    lock state without round-tripping Redis.

    Boundary: the first ``_BASELINE_LOCK_AFTER_CALLS`` calls (inclusive)
    are the *learning* window. The (N+1)-th call onwards is *locked*.
    Concretely, with threshold=100, ``call_count`` 1..100 returns False;
    101 onwards returns True.
    """
    return call_count > _BASELINE_LOCK_AFTER_CALLS


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
    Score this call against the agent's baseline AND (only while the
    cold-start window is open) update the baseline. Returns a list of
    deviation findings like ``behavior_anomaly:unusual_tool`` that the
    behavior service folds into the response findings array.

    N14 cold-start lock semantics:
        * Lifetime ``call_count`` (separate Redis key) is incremented on
          every call — it is *not* frozen.
        * Until ``call_count > _BASELINE_LOCK_AFTER_CALLS`` the baseline
          hashes (tools / hours / daily / tables) are written. Once the
          threshold is crossed the baseline freezes: subsequent calls
          score against the frozen distribution but never extend it. A
          brand-new tool seen post-lock therefore stays at count 0 and
          flags ``unusual_tool`` on every single call.
        * The lock state is exported as the Prometheus gauge
          ``acp_behavior_baseline_locked{agent_id}``.

    Best-effort: a Redis failure returns [] so the call path keeps
    flowing.
    """
    findings: list[str] = []
    now = timestamp or time.time()
    hour = int((now // 3600) % 24)
    day_bucket = int(now // 86400)  # UTC day

    # ------------------------------------------------------------------
    # Step 0 — bump the lifetime call counter and decide lock state.
    # The counter lives in its own key so the lock decision is O(1) and
    # never depends on summing the (potentially poisoned) tools hash.
    # ------------------------------------------------------------------
    call_count = 0
    try:
        call_count_key = _key(agent_id, "call_count")
        call_count = int(await redis.incr(call_count_key))
        await redis.expire(call_count_key, BASELINE_TTL)
    except Exception:
        # If we can't even read the counter we degrade to "still
        # learning" so the historical learn-on-every-call behaviour
        # remains intact. This matches the rest of the function which
        # is best-effort by design.
        call_count = 0

    locked = is_baseline_locked(call_count)
    _set_lock_gauge(agent_id, locked)

    # ------------------------------------------------------------------
    # Step 1 — bump the baseline ONLY while the cold-start window is
    # open. Post-lock, the histograms are frozen — that's the whole
    # point of the N14 fix.
    # ------------------------------------------------------------------
    if not locked:
        try:
            pipe = redis.pipeline()
            # Tool frequency histogram.
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
    else:
        # Lock just crossed — emit once per lock transition. The gauge
        # already records the steady-state value; this log line gives
        # ops a clean breadcrumb when the freeze fires.
        if call_count == _BASELINE_LOCK_AFTER_CALLS + 1:
            try:
                logger.info(
                    "baseline_locked",
                    agent_id=agent_id,
                    tenant_id=tenant_id,
                    call_count=call_count,
                    threshold=_BASELINE_LOCK_AFTER_CALLS,
                )
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Step 2 — score against the (now possibly frozen) baseline.
    # ------------------------------------------------------------------
    try:
        # 1. Unusual tool: this tool has <= 3 prior uses AND the agent
        # has at least 30 calls in total → "new tool for established
        # agent". Post-lock a brand-new tool stays at 0 forever, so the
        # finding fires on every subsequent call.
        tools = await redis.hgetall(_key(agent_id, "tools"))
        tools_dec = _decode_dict(tools)
        total_calls = sum(int(v) for v in tools_dec.values())
        # Post-lock the frozen sum may be slightly below the live
        # call_count (the agent kept calling after we stopped writing).
        # Use the live call_count to satisfy the >= 30 gate so the
        # finding keeps firing during the steady-state attack window.
        effective_total = max(total_calls, call_count)
        if effective_total >= 30:
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
    except Exception as exc:
        logger.warning("baseline_score_failed", agent_id=agent_id, error=str(exc))
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
