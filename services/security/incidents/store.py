"""
Sprint 4 — Incident store / read API.

Reads what `recorder.py` wrote and reconstructs a Storyline via the pure
`storyline.build()`. The HTTP router (`services/gateway/routers/
incidents.py`) returns this as JSON.
"""
from __future__ import annotations

import json
import time
from typing import Any

from .recorder import _meta_key, _open_zset_key, _steps_key
from .storyline import Step, Storyline, build


def _decode(v: Any) -> str:
    if isinstance(v, (bytes, bytearray)):
        return v.decode("utf-8", "replace")
    return "" if v is None else str(v)


async def get(
    redis: Any, tenant_id: str, incident_id: str,
) -> Storyline | None:
    """Fetch a single Storyline. Returns None if the incident isn't in Redis
    (decayed past TTL or never existed)."""
    try:
        meta = await redis.hgetall(_meta_key(tenant_id, incident_id))
        if not meta:
            return None
        meta_d: dict[str, str] = {}
        for k, v in meta.items():
            meta_d[_decode(k)] = _decode(v)

        steps_raw = await redis.lrange(_steps_key(tenant_id, incident_id), 0, -1)
        steps: list[Step] = []
        for i, raw in enumerate(steps_raw, 1):
            try:
                d = json.loads(_decode(raw))
            except Exception:
                continue
            steps.append(Step(
                seq=i,
                ts=float(d.get("ts") or 0.0),
                agent_id=str(d.get("agent_id") or ""),
                signal_id=str(d.get("signal_id") or ""),
                mitre_tactic=str(d.get("mitre_tactic") or ""),
                mitre_technique=str(d.get("mitre_technique") or ""),
                objective=str(d.get("objective") or ""),
                tier=str(d.get("tier") or ""),
                policy_id=str(d.get("policy_id") or ""),
                target=str(d.get("target") or ""),
                explanation=str(d.get("explanation") or ""),
            ))

        primary_session_id = meta_d.get("primary_session_id", "")
        try:
            max_risk_score = int(meta_d.get("max_risk_score") or 0)
        except ValueError:
            max_risk_score = 0

        story = build(
            incident_id=incident_id,
            tenant_id=tenant_id,
            steps=steps,
            primary_session_id=primary_session_id,
            max_risk_score=max_risk_score,
        )
        # Honour Redis-meta over computed status when meta records a higher
        # tier — protects against the case where a step JSON parse fails
        # but the writer already bumped the meta to "blocked".
        meta_status = meta_d.get("status", "")
        if meta_status in ("quarantined", "blocked") and story.status == "open":
            story.status = meta_status
        # Same for blocked_at_step.
        try:
            meta_blocked = int(meta_d.get("blocked_at_step") or 0)
            if meta_blocked and not story.blocked_at_step:
                story.blocked_at_step = meta_blocked
        except ValueError:
            pass
        if meta_d.get("blocking_policy_id") and not story.blocking_policy_id:
            story.blocking_policy_id = meta_d["blocking_policy_id"]
        return story
    except Exception:
        return None


async def list_recent(
    redis: Any, tenant_id: str, *,
    since_ts: float | None = None,
    limit: int = 50,
) -> list[Storyline]:
    """List incidents whose last_event_ts ≥ since_ts (default = last 24 h).

    Returns Storylines newest-first. Use the limit to cap response size.
    """
    try:
        floor = float(since_ts) if since_ts is not None else (time.time() - 24 * 3600)
        ids_with_scores = await redis.zrevrangebyscore(
            _open_zset_key(tenant_id), "+inf", floor, start=0, num=limit,
        )
        out: list[Storyline] = []
        for raw in ids_with_scores:
            inc_id = _decode(raw)
            s = await get(redis, tenant_id, inc_id)
            if s is not None:
                out.append(s)
        return out
    except Exception:
        return []
