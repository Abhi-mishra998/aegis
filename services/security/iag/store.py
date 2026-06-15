"""Sprint 5 — Identity & Access Graph Redis cache.

Two responsibilities:

  1. Writer — pulled by `ingestion.py` adapters to upsert the four edge
     sets (agent_roles, role_perms, perm_resources, resource_meta) into
     Redis under per-tenant keyspaces with a TTL refresh on every pass.
  2. Reader — `load_graph(redis, tenant_id, agent_id)` returns exactly the
     dicts `graph.compute_blast_radius()` wants. Bytes → str decoding
     happens at this boundary.

Key layout (24 h TTL refreshed on every ingestion pass):

    acp:iag:agent_roles:{tenant_id}:{agent_id}        SET   role_id
    acp:iag:role_perms:{tenant_id}:{role_id}          SET   perm_id
    acp:iag:perm_resources:{tenant_id}:{perm_id}      SET   resource_id
    acp:iag:resource_meta:{tenant_id}:{resource_id}   HASH  {kind, label, sensitivity}
    acp:iag:resource_index:{tenant_id}                SET   resource_id (for cleanup)

A side effect of going through a typed write API is that callers can't
accidentally Redis-serialise a bytes vs. str mismatch — every value is
forced to str at the boundary.
"""
from __future__ import annotations

import time
from typing import Any

from .graph import ResourceMeta


# 24 h TTL — same as the incident store (Sprint 4) so the two caches age
# out together. Hourly ingestion refreshes; if ingestion is down the cache
# decays after 24 h and the IAG-augmented fields disappear from the
# storyline JSON until the next pass.
_IAG_TTL_SECONDS = 24 * 3600


# ---------------------------------------------------------------------------
# Key builders. Centralised so the writer + reader can't drift.
# ---------------------------------------------------------------------------
def _agent_roles_key(tid: str, aid: str) -> str:
    return f"acp:iag:agent_roles:{tid}:{aid}"


def _role_perms_key(tid: str, rid: str) -> str:
    return f"acp:iag:role_perms:{tid}:{rid}"


def _perm_resources_key(tid: str, pid: str) -> str:
    return f"acp:iag:perm_resources:{tid}:{pid}"


def _resource_meta_key(tid: str, res_id: str) -> str:
    return f"acp:iag:resource_meta:{tid}:{res_id}"


def _resource_index_key(tid: str) -> str:
    return f"acp:iag:resource_index:{tid}"


def _last_ingest_key(tid: str) -> str:
    return f"acp:iag:last_ingest:{tid}"


# ---------------------------------------------------------------------------
# Decoding helpers
# ---------------------------------------------------------------------------
def _decode(v: Any) -> str:
    if isinstance(v, (bytes, bytearray)):
        return v.decode("utf-8", "replace")
    return "" if v is None else str(v)


def _decode_set(s: Any) -> set[str]:
    if not s:
        return set()
    return {_decode(x) for x in s}


# ---------------------------------------------------------------------------
# Writer — used by ingestion adapters.
# ---------------------------------------------------------------------------
async def upsert_agent_roles(
    redis: Any, tenant_id: str, agent_id: str, role_ids: set[str],
) -> None:
    """Replace the agent's role set with `role_ids`. Idempotent.

    Implemented as DEL + SADD rather than SADD-only because an agent
    losing a role (revoked permission) must remove the old edge, not
    accumulate it forever.
    """
    if not agent_id:
        return
    k = _agent_roles_key(tenant_id, agent_id)
    await redis.delete(k)
    if role_ids:
        await redis.sadd(k, *[str(r) for r in role_ids])
        await redis.expire(k, _IAG_TTL_SECONDS)


async def upsert_role_perms(
    redis: Any, tenant_id: str, role_id: str, perm_ids: set[str],
) -> None:
    if not role_id:
        return
    k = _role_perms_key(tenant_id, role_id)
    await redis.delete(k)
    if perm_ids:
        await redis.sadd(k, *[str(p) for p in perm_ids])
        await redis.expire(k, _IAG_TTL_SECONDS)


async def upsert_perm_resources(
    redis: Any, tenant_id: str, perm_id: str, resource_ids: set[str],
) -> None:
    if not perm_id:
        return
    k = _perm_resources_key(tenant_id, perm_id)
    await redis.delete(k)
    if resource_ids:
        await redis.sadd(k, *[str(r) for r in resource_ids])
        await redis.expire(k, _IAG_TTL_SECONDS)


async def upsert_resource_meta(
    redis: Any, tenant_id: str, meta: ResourceMeta,
) -> None:
    """Write one resource node. The resource_index set is used by the
    reader to enumerate every resource the tenant has — we don't have to
    KEYS-scan, which would be O(database) on a large tenant.
    """
    if not meta.resource_id:
        return
    k = _resource_meta_key(tenant_id, meta.resource_id)
    await redis.hset(k, mapping={
        "kind":        meta.kind,
        "label":       meta.label,
        "sensitivity": meta.sensitivity,
    })
    await redis.expire(k, _IAG_TTL_SECONDS)
    idx = _resource_index_key(tenant_id)
    await redis.sadd(idx, meta.resource_id)
    await redis.expire(idx, _IAG_TTL_SECONDS)


async def stamp_ingestion_done(redis: Any, tenant_id: str, now_ts: float | None = None) -> None:
    """Mark the most recent successful ingestion pass. Surfaced into
    `/iag/agents/{id}` so callers can spot a stale graph."""
    ts = float(now_ts if now_ts is not None else time.time())
    await redis.set(_last_ingest_key(tenant_id), str(ts), ex=_IAG_TTL_SECONDS)


# ---------------------------------------------------------------------------
# Reader — used by the gateway router + storyline augmentation.
# ---------------------------------------------------------------------------
async def get_agent_roles(redis: Any, tenant_id: str, agent_id: str) -> set[str]:
    return _decode_set(await redis.smembers(_agent_roles_key(tenant_id, agent_id)))


async def get_role_perms(redis: Any, tenant_id: str, role_id: str) -> set[str]:
    return _decode_set(await redis.smembers(_role_perms_key(tenant_id, role_id)))


async def get_perm_resources(redis: Any, tenant_id: str, perm_id: str) -> set[str]:
    return _decode_set(await redis.smembers(_perm_resources_key(tenant_id, perm_id)))


async def get_resource_meta(
    redis: Any, tenant_id: str, resource_id: str,
) -> ResourceMeta | None:
    raw = await redis.hgetall(_resource_meta_key(tenant_id, resource_id))
    if not raw:
        return None
    d: dict[str, str] = {}
    for k, v in raw.items():
        d[_decode(k)] = _decode(v)
    if not d:
        return None
    return ResourceMeta(
        resource_id=resource_id,
        kind=d.get("kind", ""),
        label=d.get("label", ""),
        sensitivity=d.get("sensitivity", ""),
    )


async def load_graph(
    redis: Any, tenant_id: str, agent_id: str,
) -> tuple[set[str], dict[str, set[str]], dict[str, set[str]], dict[str, ResourceMeta]]:
    """Materialise the slice of the graph reachable from one agent.

    Walks agent → role → permission → resource ONCE, fetching only the
    role / perm / resource entries actually in scope. Cost is bounded by
    the agent's true privilege set, not the tenant's total identity
    surface — important on a tenant with thousands of unused roles.
    """
    agent_roles = await get_agent_roles(redis, tenant_id, agent_id)
    role_perms: dict[str, set[str]] = {}
    perm_ids_needed: set[str] = set()
    for role_id in agent_roles:
        perms = await get_role_perms(redis, tenant_id, role_id)
        role_perms[role_id] = perms
        perm_ids_needed |= perms

    perm_resources: dict[str, set[str]] = {}
    resource_ids_needed: set[str] = set()
    for perm_id in perm_ids_needed:
        resources = await get_perm_resources(redis, tenant_id, perm_id)
        perm_resources[perm_id] = resources
        resource_ids_needed |= resources

    resource_meta: dict[str, ResourceMeta] = {}
    for rid in resource_ids_needed:
        meta = await get_resource_meta(redis, tenant_id, rid)
        if meta is not None:
            resource_meta[rid] = meta

    return agent_roles, role_perms, perm_resources, resource_meta


async def get_last_ingest_ts(redis: Any, tenant_id: str) -> float:
    raw = await redis.get(_last_ingest_key(tenant_id))
    if not raw:
        return 0.0
    try:
        return float(_decode(raw))
    except ValueError:
        return 0.0
