"""Sprint 7 — Redis cache for the threat-intel layer.

Per-tenant keyspace. The "_global" tenant is a cross-tenant overlay —
runtime.match consults both the tenant's own IOCs and the global
overlay so curated defaults reach every tenant without per-tenant
seeding.

24 h TTL on the value sets so a broken feed doesn't permanently poison
policy. The feed config + meta hash carry no TTL — they're configured
state, not cached state.
"""
from __future__ import annotations

import json
import time
from typing import Any, Iterable

from .ioc import IOCRecord, make_id


_TTL_SECONDS    = 24 * 3600
GLOBAL_TENANT_ID = "_global"


# ---------------------------------------------------------------------------
# Key builders — central so writer + reader can't drift.
# ---------------------------------------------------------------------------
def _values_key(tenant_id: str, kind: str) -> str:
    return f"acp:ti:iocs:{tenant_id}:{kind}"


def _meta_key(tenant_id: str, ioc_id: str) -> str:
    return f"acp:ti:iocs_meta:{tenant_id}:{ioc_id}"


def _index_key(tenant_id: str) -> str:
    return f"acp:ti:iocs_index:{tenant_id}"


def _feeds_key(tenant_id: str) -> str:
    return f"acp:ti:feeds:{tenant_id}"


def _last_refresh_key(tenant_id: str) -> str:
    return f"acp:ti:last_refresh:{tenant_id}"


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
# Writer
# ---------------------------------------------------------------------------
async def upsert_ioc(
    redis: Any, *, tenant_id: str, kind: str, value: str,
    severity: str, source: str, actor: str = "system",
    now_ts: float | None = None,
) -> IOCRecord:
    """Idempotent insert. Same (tenant, kind, value) → same id."""
    ts = float(now_ts if now_ts is not None else time.time())
    # Substring-matched kinds get lowercased on write so the runtime can
    # check `candidate.lower() in members` directly without per-call
    # normalisation cost. Regex kinds (destructive_shell) keep their
    # case so the operator's pattern intent is preserved.
    stored_value = value if kind == "destructive_shell" else value.lower()
    ioc_id = make_id(tenant_id, kind, stored_value)
    record = IOCRecord(
        id=ioc_id, tenant_id=tenant_id, kind=kind, value=stored_value,
        severity=severity, source=source, created_ts=ts, actor=actor,
    )
    # Value set — what runtime.match consults.
    vk = _values_key(tenant_id, kind)
    await redis.sadd(vk, stored_value)
    await redis.expire(vk, _TTL_SECONDS)
    # Meta hash — what GET /threat-intel/iocs returns.
    await redis.hset(_meta_key(tenant_id, ioc_id), mapping={
        "id":         ioc_id,
        "tenant_id":  tenant_id,
        "kind":       kind,
        "value":      stored_value,
        "severity":   severity,
        "source":     source,
        "created_ts": str(ts),
        "actor":      actor,
    })
    # Index — for delete-by-id and enumeration.
    await redis.sadd(_index_key(tenant_id), ioc_id)
    return record


async def delete_ioc(redis: Any, *, tenant_id: str, ioc_id: str) -> bool:
    """Remove one IOC by id. Returns True if a record was deleted."""
    meta_raw = await redis.hgetall(_meta_key(tenant_id, ioc_id))
    if not meta_raw:
        return False
    meta = {_decode(k): _decode(v) for k, v in meta_raw.items()}
    kind = meta.get("kind", "")
    value = meta.get("value", "")
    if kind and value:
        await redis.srem(_values_key(tenant_id, kind), value)
    await redis.delete(_meta_key(tenant_id, ioc_id))
    await redis.srem(_index_key(tenant_id), ioc_id)
    return True


async def upsert_many(
    redis: Any, *, tenant_id: str, records: Iterable[IOCRecord],
) -> int:
    """Batch upsert from a provider. Returns the count actually written."""
    n = 0
    for r in records:
        await upsert_ioc(
            redis,
            tenant_id=tenant_id, kind=r.kind, value=r.value,
            severity=r.severity, source=r.source, actor=r.actor,
            now_ts=r.created_ts,
        )
        n += 1
    return n


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------
async def list_iocs(
    redis: Any, *, tenant_id: str,
    kind: str | None = None, source: str | None = None,
    limit: int = 500,
) -> list[IOCRecord]:
    """Enumerate IOCs for a tenant, optionally filtered.

    Walks the index set, hydrates each meta hash; cost is O(N) over the
    tenant's IOC count. For huge tenants the caller should pass a
    `kind` filter.
    """
    ids = _decode_set(await redis.smembers(_index_key(tenant_id)))
    out: list[IOCRecord] = []
    for ioc_id in sorted(ids):
        if len(out) >= limit:
            break
        meta_raw = await redis.hgetall(_meta_key(tenant_id, ioc_id))
        if not meta_raw:
            continue
        d = {_decode(k): _decode(v) for k, v in meta_raw.items()}
        if kind and d.get("kind") != kind:
            continue
        if source and d.get("source") != source:
            continue
        try:
            created = float(d.get("created_ts") or 0.0)
        except ValueError:
            created = 0.0
        out.append(IOCRecord(
            id=d.get("id", ioc_id),
            tenant_id=d.get("tenant_id", tenant_id),
            kind=d.get("kind", ""),
            value=d.get("value", ""),
            severity=d.get("severity", ""),
            source=d.get("source", ""),
            created_ts=created,
            actor=d.get("actor", ""),
        ))
    return out


async def values_for_kind(
    redis: Any, *, tenant_id: str, kind: str,
) -> set[str]:
    """Return the value set for one kind. Used by runtime.match for
    substring kinds."""
    return _decode_set(await redis.smembers(_values_key(tenant_id, kind)))


# ---------------------------------------------------------------------------
# Feed config
# ---------------------------------------------------------------------------
async def upsert_feed(
    redis: Any, *, tenant_id: str, name: str,
    url: str, format: str = "text", refresh_seconds: int = 3600,
    enabled: bool = True,
) -> dict[str, Any]:
    cfg = {
        "url":             url,
        "format":          format,
        "refresh_seconds": int(refresh_seconds),
        "enabled":         bool(enabled),
        "last_pulled_ts":  0.0,
    }
    await redis.hset(_feeds_key(tenant_id), mapping={name: json.dumps(cfg)})
    return cfg


async def list_feeds(redis: Any, *, tenant_id: str) -> dict[str, dict[str, Any]]:
    raw = await redis.hgetall(_feeds_key(tenant_id))
    out: dict[str, dict[str, Any]] = {}
    if not raw:
        return out
    for k, v in raw.items():
        name = _decode(k)
        try:
            out[name] = json.loads(_decode(v))
        except Exception:
            continue
    return out


async def stamp_refresh(redis: Any, *, tenant_id: str, now_ts: float | None = None) -> None:
    ts = float(now_ts if now_ts is not None else time.time())
    await redis.set(_last_refresh_key(tenant_id), str(ts))


async def get_last_refresh(redis: Any, *, tenant_id: str) -> float:
    raw = await redis.get(_last_refresh_key(tenant_id))
    if not raw:
        return 0.0
    try:
        return float(_decode(raw))
    except ValueError:
        return 0.0
