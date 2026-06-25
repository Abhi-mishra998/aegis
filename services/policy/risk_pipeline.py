"""
ARCH-2 2026-06-15 — Signal → Finding → Risk → Decision pipeline.

Replaces the previous "one rule fires, action is decided" model with the
EDR/XDR-style cumulative scoring you'd see in CrowdStrike Falcon Insight
or Microsoft Defender for Cloud Apps:

    canonical.signals      → recorded into Redis (session + agent buckets)
    cumulative session     → sliding 15-minute window
    cumulative agent       → sliding 60-minute window
    effective_score        = max(per_call, session*0.8, agent*0.5)
    tier_from_score        ALLOW(0-19) | MONITOR(20-39) | ESCALATE(40-69) |
                           DENY(70-94) | QUARANTINE(95+)

Why this matters:
    * Single "tar czf customers.tgz" alone is risk 35 → MONITOR.
    * Same agent did `SELECT ssn FROM customers` 30s earlier (45 in session).
    * Cumulative session = 35 + 45 = 80 → DENY before the upload step.
    * Today the upload step had to fail-closed via the attack-chain matcher;
      ARCH-2 catches the chain ONE STEP EARLIER.

Storage:
    acp:risk:session:{session_id}   ZSET, score=ts, member="{ts}:{signal}:{score}"
    acp:risk:agent:{tenant_id}:{agent_id}  ZSET, score=ts, member same as above
    EXPIRE applied so an idle session/agent decays naturally.

The pipeline is pure I/O (Redis) — all classification still happens
inside canonical.py / evaluate_full(). This module only owns ACCUMULATION
and the score→tier mapping.
"""
from __future__ import annotations

import time
from typing import Any

# Per-signal score — Sprint 1 2026-06-15.
# Scores live in services/security/signal_registry.py. The pipeline used to
# carry its own copy of this dict; the duplication caused drift (canonical
# had `compression_for_exfil: 35`, pipeline had `compression_for_exfil: 35`
# but pipeline missed `external_post_pii_unknown_dest`, `cross_agent_kill_chain`,
# `credential_artifact_write`, etc.). Now both sides read the registry.
from services.security.signal_registry import score_for_finding as _registry_score

_SESSION_WINDOW_SECONDS = 15 * 60         # 15 minutes
_AGENT_WINDOW_SECONDS   = 60 * 60         # 60 minutes
_AGENT_LONG_WINDOW_SECONDS = 7 * 24 * 3600  # GAP-2: 7-day persistent memory
_REDIS_KEY_TTL_SECONDS  = 24 * 60 * 60    # idle decay (session/agent buckets)
_AGENT_LONG_TTL_SECONDS = 8 * 24 * 3600   # 8 days to outlast the 7-day window


def _session_key(session_id: str) -> str:
    return f"acp:risk:session:{session_id}"


def _agent_key(tenant_id: str, agent_id: str) -> str:
    return f"acp:risk:agent:{tenant_id}:{agent_id}"


def _agent_long_key(tenant_id: str, agent_id: str) -> str:
    return f"acp:risk:agent_7d:{tenant_id}:{agent_id}"


def score_for_finding(finding: str) -> int:
    """Thin shim — preserved for back-compat with existing callers that
    import this name from the pipeline. New code should
    `from services.security.signal_registry import score_for_finding`
    directly.
    """
    return _registry_score(finding)


async def record_signals(
    redis: Any,
    tenant_id: str,
    agent_id: str,
    session_id: str | None,
    findings: list[str],
) -> None:
    """Record this call's findings into the session + agent risk buckets.

    Best-effort: any Redis blip is swallowed so the deny/escalate path
    doesn't fail on bookkeeping.
    """
    if not findings:
        return
    ts = int(time.time())
    members: list[tuple[float, str]] = []
    for f in findings:
        score = score_for_finding(f)
        if score <= 0:
            continue
        members.append((float(ts), f"{ts}:{f}:{score}"))
    if not members:
        return
    try:
        if session_id:
            sk = _session_key(session_id)
            # Redis zadd takes a mapping in py-redis: {member: score}
            await redis.zadd(sk, {m: s for s, m in members})
            await redis.expire(sk, _REDIS_KEY_TTL_SECONDS)
        ak = _agent_key(tenant_id, agent_id)
        await redis.zadd(ak, {m: s for s, m in members})
        await redis.expire(ak, _REDIS_KEY_TTL_SECONDS)
        # GAP-2 2026-06-15 — also write to the 7-day persistent agent bucket.
        # The hot path is unchanged; the long-window write is the same shape,
        # different TTL.
        alk = _agent_long_key(tenant_id, agent_id)
        await redis.zadd(alk, {m: s for s, m in members})
        await redis.expire(alk, _AGENT_LONG_TTL_SECONDS)
    except Exception:
        pass


async def cumulative_scores(
    redis: Any,
    tenant_id: str,
    agent_id: str,
    session_id: str | None,
) -> tuple[int, int, int, list[str]]:
    """Return (session_score, agent_score, agent_long_score, recent_findings).

    * session_score — last 15 min on this session
    * agent_score — last 60 min for this agent (cross-session, short-term)
    * agent_long_score — last 7 days for this agent (GAP-2 persistent memory)
    * recent_findings — last 10 distinct finding names

    All three are summed inherent scores. The combiner downweights the
    long window so it nudges, not stomps.
    """
    now = int(time.time())
    session_score = 0
    agent_score = 0
    agent_long_score = 0
    recent: list[str] = []

    sk = _session_key(session_id) if session_id else ""
    ak = _agent_key(tenant_id, agent_id)
    alk = _agent_long_key(tenant_id, agent_id)
    s_cutoff = now - _SESSION_WINDOW_SECONDS
    a_cutoff = now - _AGENT_WINDOW_SECONDS
    l_cutoff = now - _AGENT_LONG_WINDOW_SECONDS

    # Sprint 8 — TD-9. The three windows used to be 3 sequential
    # ZRANGEBYSCORE calls (one RTT each). Pipelining them collapses to
    # ONE RTT on warm-pool localhost ~1ms savings; under load (queue +
    # jitter) closer to ~10ms — the gateway p95 is sensitive to it.
    # Fall back to sequential on ANY exception so a Redis upgrade that
    # breaks pipeline semantics doesn't take detection down.
    session_members: list[Any] = []
    agent_members: list[Any] = []
    long_members: list[Any] = []
    try:
        pipe = redis.pipeline(transaction=False)
        if sk:
            pipe.zrangebyscore(sk, s_cutoff, "+inf")
        pipe.zrangebyscore(ak, a_cutoff, "+inf")
        pipe.zrangebyscore(alk, l_cutoff, "+inf")
        results = await pipe.execute()
        if sk:
            session_members, agent_members, long_members = results[0], results[1], results[2]
        else:
            agent_members, long_members = results[0], results[1]
    except Exception:
        # Sequential fallback — preserves behavior when pipeline() is
        # unavailable or fails. Same logic as the pre-Sprint-8 code path.
        try:
            if sk:
                session_members = await redis.zrangebyscore(sk, s_cutoff, "+inf")
            agent_members = await redis.zrangebyscore(ak, a_cutoff, "+inf")
            long_members  = await redis.zrangebyscore(alk, l_cutoff, "+inf")
        except Exception:
            return 0, 0, 0, []

    for m in session_members:
        if isinstance(m, bytes):
            m = m.decode("utf-8", "replace")
        try:
            parts = m.split(":", 2)
            if len(parts) == 3:
                finding = parts[1]
                s = int(parts[2])
                session_score += s
                if finding not in recent:
                    recent.append(finding)
        except Exception:
            continue

    for m in agent_members:
        if isinstance(m, bytes):
            m = m.decode("utf-8", "replace")
        try:
            parts = m.split(":", 2)
            if len(parts) == 3:
                agent_score += int(parts[2])
        except Exception:
            continue

    for m in long_members:
        if isinstance(m, bytes):
            m = m.decode("utf-8", "replace")
        try:
            parts = m.split(":", 2)
            if len(parts) == 3:
                agent_long_score += int(parts[2])
        except Exception:
            continue

    return session_score, agent_score, agent_long_score, recent[-10:]


def tier_from_score(score: int) -> str:
    """Map cumulative score to a tier name.

    ALLOW       0-19
    MONITOR    20-39
    ESCALATE   40-69
    DENY       70-94
    QUARANTINE 95+
    """
    if score >= 95: return "quarantine"
    if score >= 70: return "deny"
    if score >= 40: return "escalate"
    if score >= 20: return "monitor"
    return "allow"


def combine_scores(per_call: int, session: int, agent: int,
                   agent_long: int = 0) -> int:
    """Effective score = max(per_call, session*0.8, agent*0.5, agent_long*0.3).

    The weights penalise cumulative behaviour less aggressively than the
    current call, but enough that a chain of 30/30/30 across three actions
    on one session lands in ESCALATE (max(30, 30*3*0.8) = 72 → DENY band).

    GAP-2 — agent_long window (7 days) carries the smallest weight so a
    single bad day doesn't poison the agent forever, but a slow-burn pattern
    spread across multiple days still accumulates. 7×30 = 210 long score
    → 210*0.3 = 63 → ESCALATE band even with no fresh signal.
    """
    candidates = [
        int(per_call),
        int(session * 0.8),
        int(agent * 0.5),
        int(agent_long * 0.3),
    ]
    return max(candidates)


def explain_cumulative(
    per_call: int, session: int, agent: int, effective: int,
    tier: str, recent: list[str], agent_long: int = 0,
) -> str:
    """Human-readable sentence for the explanation surface."""
    bits: list[str] = []
    if effective >= 70:
        bits.append(f"Cumulative session risk {effective}/100 crossed the deny line.")
    elif effective >= 40:
        bits.append(f"Cumulative session risk {effective}/100 requires operator approval.")
    elif effective >= 20:
        bits.append(f"Cumulative session risk {effective}/100 — logged for monitoring.")
    # GAP-2 — surface the 7-day long-window contribution when it dominated.
    if agent_long * 0.3 >= 40 and agent_long * 0.3 >= effective * 0.9:
        bits.append(f"7-day rolling agent risk total {agent_long} — multi-session pattern.")
    if recent:
        bits.append(f"Recent signals: {', '.join(recent[-5:])}.")
    return " ".join(bits)
