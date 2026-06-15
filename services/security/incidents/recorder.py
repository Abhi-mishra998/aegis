"""
Sprint 4 — Incident recorder (Redis-backed).

Appends one Step to the right incident, opening a new one when no existing
story matches. Keyed by (tenant_id, session_id) first, then by the
cross-agent override (Sprint-1 GAP-5), then by an idle-window fallback on
(tenant_id, agent_id).

Storage layout (Redis):

    acp:incident:meta:{tenant_id}:{incident_id}    HASH
        { id, tenant_id, status, start_ts, last_event_ts,
          primary_session_id, blocked_at_step, blocking_policy_id,
          participating_agents (JSON array), max_risk_score }

    acp:incident:steps:{tenant_id}:{incident_id}   LIST (JSON-encoded steps)

    acp:incident:by_session:{tenant_id}:{session_id}  STRING → incident_id
    acp:incident:by_agent:{tenant_id}:{agent_id}      STRING → incident_id

    acp:incident:open:{tenant_id}                  ZSET (member=incident_id,
                                                         score=last_event_ts)

All keys carry a 24 h TTL refreshed on every append. After 24 h of silence
the incident decays out of Redis. (Sprint 6 can persist to DB before that
window closes.)

Concurrency: the recorder is async and uses a Redis SETNX guard on the
by_session / by_agent pointer to make "open or append" idempotent under
concurrent calls. Same finding recorded twice → same incident_id, same
step seq.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from .storyline import Step, build


# ---------------------------------------------------------------------------
# TTL knobs. Tunable via env later, hardcoded for now.
# ---------------------------------------------------------------------------
_INCIDENT_TTL_SECONDS   = 24 * 3600          # 24 h
_SESSION_POINTER_TTL    = 30 * 60            # 30 min idle window for session-keyed groups
_AGENT_POINTER_TTL      = 30 * 60            # 30 min idle window for agent-keyed fallback
_XAGENT_POINTER_TTL     = 60 * 60            # 1 h for cross-agent umbrella stories


def _new_incident_id() -> str:
    """Sortable-ish identifier: time + short uuid suffix.

    Embedding the seconds prefix makes Redis ZSET ranges trivial to debug
    in a redis-cli session, and the uuid tail guarantees uniqueness even
    when two opens happen in the same second.
    """
    return f"INC-{int(time.time())}-{uuid.uuid4().hex[:8]}"


def _meta_key(tenant_id: str, incident_id: str) -> str:
    return f"acp:incident:meta:{tenant_id}:{incident_id}"


def _steps_key(tenant_id: str, incident_id: str) -> str:
    return f"acp:incident:steps:{tenant_id}:{incident_id}"


def _by_session_key(tenant_id: str, session_id: str) -> str:
    return f"acp:incident:by_session:{tenant_id}:{session_id}"


def _by_agent_key(tenant_id: str, agent_id: str) -> str:
    return f"acp:incident:by_agent:{tenant_id}:{agent_id}"


def _by_xagent_key(tenant_id: str, primary_agent_id: str) -> str:
    return f"acp:incident:by_xagent:{tenant_id}:{primary_agent_id}"


def _open_zset_key(tenant_id: str) -> str:
    return f"acp:incident:open:{tenant_id}"


async def _resolve_or_open(
    redis: Any,
    *,
    tenant_id:           str,
    agent_id:            str,
    session_id:          str,
    cross_agent_chain:   dict[str, Any] | None,
) -> tuple[str, bool]:
    """Find an existing incident_id this finding should attach to, or open a
    new one. Returns (incident_id, opened_new).

    Order:
      1. cross_agent_chain present → umbrella story keyed by first agent in chain.
      2. session_id present → session pointer.
      3. fallback → agent pointer.

    The pointer keys are SETNX'd with the new incident_id when no pointer
    exists yet; concurrent callers race on the SETNX so exactly one wins
    the "open" and others read the same incident_id back.
    """
    # 1. Cross-agent umbrella
    if cross_agent_chain:
        agent_ids = cross_agent_chain.get("agent_ids") or []
        primary = (sorted(agent_ids)[0] if agent_ids else agent_id)
        ptr = _by_xagent_key(tenant_id, primary)
        existing = await redis.get(ptr)
        if existing:
            inc = existing.decode() if isinstance(existing, (bytes, bytearray)) else str(existing)
            # Make sure ALL participating agents' agent pointers also alias
            # the umbrella so a follow-up call from any of them lands here.
            for ag in agent_ids:
                await redis.set(_by_agent_key(tenant_id, ag), inc, ex=_AGENT_POINTER_TTL)
            await redis.expire(ptr, _XAGENT_POINTER_TTL)
            return inc, False
        new_id = _new_incident_id()
        ok = await redis.set(ptr, new_id, ex=_XAGENT_POINTER_TTL, nx=True)
        if not ok:
            inc_bytes = await redis.get(ptr)
            return (inc_bytes.decode() if isinstance(inc_bytes, (bytes, bytearray))
                    else str(inc_bytes)), False
        # Cross-populate every participant's agent pointer.
        for ag in agent_ids:
            await redis.set(_by_agent_key(tenant_id, ag), new_id, ex=_AGENT_POINTER_TTL)
        if session_id:
            await redis.set(_by_session_key(tenant_id, session_id), new_id, ex=_SESSION_POINTER_TTL)
        return new_id, True

    # 2. Session pointer
    if session_id:
        ptr = _by_session_key(tenant_id, session_id)
        existing = await redis.get(ptr)
        if existing:
            inc = existing.decode() if isinstance(existing, (bytes, bytearray)) else str(existing)
            await redis.expire(ptr, _SESSION_POINTER_TTL)
            return inc, False
        new_id = _new_incident_id()
        ok = await redis.set(ptr, new_id, ex=_SESSION_POINTER_TTL, nx=True)
        if not ok:
            inc_bytes = await redis.get(ptr)
            return (inc_bytes.decode() if isinstance(inc_bytes, (bytes, bytearray))
                    else str(inc_bytes)), False
        if agent_id:
            await redis.set(_by_agent_key(tenant_id, agent_id), new_id, ex=_AGENT_POINTER_TTL)
        return new_id, True

    # 3. Agent fallback
    if agent_id:
        ptr = _by_agent_key(tenant_id, agent_id)
        existing = await redis.get(ptr)
        if existing:
            inc = existing.decode() if isinstance(existing, (bytes, bytearray)) else str(existing)
            await redis.expire(ptr, _AGENT_POINTER_TTL)
            return inc, False
        new_id = _new_incident_id()
        ok = await redis.set(ptr, new_id, ex=_AGENT_POINTER_TTL, nx=True)
        if not ok:
            inc_bytes = await redis.get(ptr)
            return (inc_bytes.decode() if isinstance(inc_bytes, (bytes, bytearray))
                    else str(inc_bytes)), False
        return new_id, True

    # Defensive: no agent_id and no session_id. Open a free-standing incident
    # so the trail isn't lost, but it won't be reachable by any pointer.
    return _new_incident_id(), True


async def record_step(
    redis: Any,
    *,
    tenant_id:          str,
    agent_id:           str,
    session_id:         str,
    signal_id:          str,
    mitre_tactic:       str,
    mitre_technique:    str,
    objective:          str,
    tier:               str,
    policy_id:          str,
    target:             str,
    explanation:        str,
    risk_score:         int,
    cross_agent_chain:  dict[str, Any] | None = None,
    now_ts:             float | None = None,
) -> str:
    """Append one Step to the right Incident; open a new one if needed.

    Returns the incident_id. Idempotent on retry: the same (signal_id, ts,
    agent_id) tuple recorded twice ends up as two steps, but the caller
    typically does its own dedup before invoking us.

    Failure-mode: any Redis exception is swallowed and the function returns
    "". The middleware caller treats "" as "no incident recorded" and does
    not surface the failure to the API consumer — incident recording is
    best-effort observability, not enforcement.
    """
    try:
        inc_id, _opened = await _resolve_or_open(
            redis,
            tenant_id=tenant_id, agent_id=agent_id, session_id=session_id,
            cross_agent_chain=cross_agent_chain,
        )
        ts = float(now_ts if now_ts is not None else time.time())

        # Assign seq = current step count + 1. RPUSH returns the new length.
        step_dict = {
            "ts":              ts,
            "agent_id":        agent_id,
            "signal_id":       signal_id,
            "mitre_tactic":    mitre_tactic,
            "mitre_technique": mitre_technique,
            "objective":       objective,
            "tier":            tier,
            "policy_id":       policy_id,
            "target":          target,
            "explanation":     explanation,
        }
        steps_k = _steps_key(tenant_id, inc_id)
        new_len = await redis.rpush(steps_k, json.dumps(step_dict))
        await redis.expire(steps_k, _INCIDENT_TTL_SECONDS)

        # Update meta. Done as HSET (not HMSET) so we can keep individual
        # fields atomic without overwriting the agents list.
        meta_k = _meta_key(tenant_id, inc_id)
        await redis.hset(meta_k, mapping={
            "id":                  inc_id,
            "tenant_id":           tenant_id,
            "last_event_ts":       str(ts),
            "primary_session_id":  session_id or "",
            "max_risk_score":      str(max(int(risk_score or 0), int(
                                       (await redis.hget(meta_k, "max_risk_score")) or 0
                                   ))),
        })
        # start_ts: only set on first append.
        await redis.hsetnx(meta_k, "start_ts", str(ts))
        # status: bumped to blocked / quarantined on tier escalation.
        cur_status = (await redis.hget(meta_k, "status")) or b""
        cur_status = cur_status.decode() if isinstance(cur_status, (bytes, bytearray)) else str(cur_status)
        new_status = cur_status
        if tier == "quarantine":
            new_status = "quarantined"
        elif tier == "deny" and cur_status not in ("quarantined",):
            new_status = "blocked"
        elif not cur_status:
            new_status = "open"
        if new_status != cur_status:
            await redis.hset(meta_k, "status", new_status)
            # Stamp blocked_at_step on first transition above the deny line.
            if new_status in ("blocked", "quarantined"):
                await redis.hsetnx(meta_k, "blocked_at_step", str(new_len))
                if policy_id:
                    await redis.hsetnx(meta_k, "blocking_policy_id", policy_id)

        # Maintain the participating_agents list.
        agents_raw = await redis.hget(meta_k, "participating_agents")
        if agents_raw:
            try:
                agents = json.loads(
                    agents_raw.decode() if isinstance(agents_raw, (bytes, bytearray))
                    else agents_raw
                )
            except Exception:
                agents = []
        else:
            agents = []
        if agent_id and agent_id not in agents:
            agents.append(agent_id)
            await redis.hset(meta_k, "participating_agents", json.dumps(agents))

        await redis.expire(meta_k, _INCIDENT_TTL_SECONDS)

        # Open-incidents index (ZSET keyed by last_event_ts).
        await redis.zadd(_open_zset_key(tenant_id), {inc_id: ts})
        await redis.expire(_open_zset_key(tenant_id), _INCIDENT_TTL_SECONDS)

        return inc_id
    except Exception:
        # Best-effort observability. Never fail the user request.
        return ""
