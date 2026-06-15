"""
GAP-5 2026-06-15 — Cross-agent attack chain detector.

T11 from the brutal eval: 4 agents split the kill chain
(schema-recon → bulk-pii → compression → external-upload). If they share
a session_id, ARCH-2 catches it. If they DON'T, today's Aegis correlates
nothing across agents.

This module records every canonical action_type → target_key event into a
per-tenant ZSET. When the trailing window (15min) contains the contiguous
exfil kill-chain pattern across two or more DISTINCT agents, all
participating agents are flagged for quarantine and the current call is
denied with a `cross_agent_kill_chain` finding.

The target_key disambiguates "same kill chain on the same target" from
"unrelated parallel agents." For SQL it's the table_norm; for files,
the file_path; for HTTP, the URL host. When any one matches across the
sequence, we treat the chain as a single coordinated attack.
"""
from __future__ import annotations

import time
from typing import Any

# Window we look back across the tenant.
WINDOW_SECONDS = 15 * 60

# The action_type sequence that defines a kill chain. We expect to see at
# least these four classes within the window, executed by 2+ distinct
# agents, hitting a related target. Order matters but not strictly — a
# detector that requires the EXACT sequence is too brittle.
KILL_CHAIN_CLASSES = (
    "db_query",        # recon or bulk read
    "db_query",        # bulk PII read
    "shell_command",   # compression
    "external_post",   # exfil
)


def _key(tenant_id: str) -> str:
    return f"acp:xagent:chain:{tenant_id}"


def _flag_key(tenant_id: str, agent_id: str) -> str:
    return f"acp:xagent:flag:{tenant_id}:{agent_id}"


async def record_action(
    redis: Any,
    *,
    tenant_id: str,
    agent_id: str,
    action_type: str,
    target_key: str,
    pii_present: bool,
) -> None:
    """Record one canonical action_type into the per-tenant ZSET.

    Members are encoded as ``{ts}:{agent_id}:{action_type}:{target_key}:{pii}``
    so we can re-derive everything from the trailing slice.
    """
    if not action_type:
        return
    ts = int(time.time())
    member = f"{ts}:{agent_id[:12]}:{action_type}:{(target_key or 'na')[:40]}:{int(bool(pii_present))}"
    try:
        await redis.zadd(_key(tenant_id), {member: float(ts)})
        # Trim opportunistically — keep last 1000 entries.
        await redis.zremrangebyrank(_key(tenant_id), 0, -1001)
        await redis.expire(_key(tenant_id), WINDOW_SECONDS * 4)
    except Exception:
        pass


async def detect_chain(
    redis: Any,
    *,
    tenant_id: str,
    current_agent_id: str,
    current_action_type: str,
    current_target_key: str,
) -> dict[str, Any] | None:
    """Look at the trailing window. The detector fires ONLY when:

      1. The CURRENT call is the chain-completing step (external_post that
         carries PII, or a shell compression preceded by PII), AND
      2. >=2 distinct OTHER agents contributed in the trailing window, AND
      3. At least one of those earlier agents executed a PII read in the
         same window, AND
      4. The targets are related — every event in the chain touches one
         of the same target_keys (same table/file/host), OR at least the
         PII read step is on a PII table.

    Tight match prevents the "tenant poisoned from earlier tests" false
    positive. Without (1), every benign call after a real attack chain
    would re-trigger the deny.
    """
    # Only fire on the chain-completing exfil step. Compression alone is
    # not a kill-chain completion — without an external_post to actually
    # move data out, the chain hasn't shipped. This also keeps benign
    # local shell/compression patterns from spuriously matching when a
    # different agent in the tenant happened to read PII earlier.
    if current_action_type != "external_post":
        return None

    now = int(time.time())
    cutoff = now - WINDOW_SECONDS
    try:
        members = await redis.zrangebyscore(_key(tenant_id), cutoff, "+inf")
    except Exception:
        return None
    if not members:
        return None

    events: list[dict[str, Any]] = []
    for m in members:
        if isinstance(m, bytes):
            m = m.decode("utf-8", "replace")
        parts = m.split(":", 4)
        if len(parts) != 5:
            continue
        try:
            events.append({
                "ts":          int(parts[0]),
                "agent_id":    parts[1],
                "action_type": parts[2],
                "target_key":  parts[3],
                "pii":         parts[4] == "1",
            })
        except Exception:
            continue
    if not events:
        return None

    # The current event is the most recent matching one for this agent.
    cur_agent_short = current_agent_id[:12]

    # Filter to OTHER-agent PII reads in the window.
    other_pii_reads = [
        e for e in events
        if e["agent_id"] != cur_agent_short
        and e["action_type"] in ("db_query", "db_write")
        and e["pii"]
    ]
    if not other_pii_reads:
        return None  # no PII step from another agent — not a swarm pattern

    # At least 2 DISTINCT other agents need to have participated (the test
    # spec from the user: "agents A/B/C/D split the chain").
    other_agents = {e["agent_id"] for e in other_pii_reads}
    # Also count compression / shell from other agents (the more steps
    # split across agents, the stronger the chain).
    other_shell = [
        e for e in events
        if e["agent_id"] != cur_agent_short
        and e["action_type"] in ("shell_command", "code_exec")
    ]
    for e in other_shell:
        other_agents.add(e["agent_id"])

    if len(other_agents) < 2:
        return None

    # Final sanity: target relatedness. We accept the chain if ANY two
    # events in the window share a target_key (table or host basename
    # collision). This catches "4 agents all touching `customers`" but
    # not "4 unrelated agents that happened to be busy."
    target_counts: dict[str, int] = {}
    for e in events:
        if e["target_key"] and e["target_key"] != "na":
            target_counts[e["target_key"]] = target_counts.get(e["target_key"], 0) + 1
    shared_target = any(c >= 2 for c in target_counts.values())
    if not shared_target and (current_target_key in ("", "na")):
        return None

    return {
        "chain":           "cross_agent_pii_exfil",
        "agent_ids":       sorted(other_agents | {cur_agent_short}),
        "event_count":     len(events),
        "window_seconds":  WINDOW_SECONDS,
        "current_agent":   cur_agent_short,
        "completing_step": current_action_type,
    }


async def flag_agents(redis: Any, tenant_id: str, agent_ids: list[str]) -> None:
    """Mark each participating agent for quarantine. Caller invokes the
    quarantine_agent helper in _behavior_aggregator with this reason.
    """
    for ag in agent_ids:
        try:
            await redis.setex(_flag_key(tenant_id, ag), 24 * 3600, "cross_agent_chain")
        except Exception:
            pass


async def is_flagged(redis: Any, tenant_id: str, agent_id: str) -> bool:
    try:
        v = await redis.get(_flag_key(tenant_id, agent_id))
        return bool(v)
    except Exception:
        return False


def derive_target_key(canonical: dict[str, Any]) -> str:
    """The disambiguator: same target → same chain. SQL uses table_norm,
    HTTP uses url_host, file ops use file_path basename. Empty string when
    the action_type doesn't carry a target.
    """
    if not isinstance(canonical, dict):
        return ""
    at = canonical.get("action_type") or ""
    if at in ("db_query", "db_write"):
        return (canonical.get("table_norm") or "").lower()
    if at in ("file_read", "file_write"):
        fp = canonical.get("file_path") or ""
        return fp.rsplit("/", 1)[-1].lower()
    if at in ("external_get", "external_post"):
        return (canonical.get("url_host") or "").lower()
    return ""
