"""Sprint 4 — Incident Storyline read API.

EDR vendors (CrowdStrike Falcon, SentinelOne) ship a single "Detection"
or "Storyline" record per kill chain — not one row per signal. Sprint 4
adds that surface to Aegis.

Routes:

  GET /storylines               — list open storylines for the tenant
  GET /storylines/{incident_id} — fetch one storyline (steps + chain)

Data lives in Redis (TTL-decayed), written by
``services/security/incidents/recorder.py`` from the gateway middleware
on every deny / escalate / quarantine outcome. Reads are served by
``services/security/incidents/store.py``.

Both routes require the standard tenant JWT (the gateway auth
middleware enforces this before the request reaches the handler). The
tenant scope is taken from ``request.state.tenant_id`` — no upstream
service hop.

This is deliberately a separate namespace from the existing
``/incidents/*`` routes (which proxy the API service's owner-managed
incident records). Storylines are detection-side; incidents are
operations-side. Sprints 5-6 connect the two.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from redis.asyncio import Redis

from sdk.common.config import settings
from sdk.common.redis import get_redis_client
from services.security.incidents import store

router = APIRouter()

# Same constructor + decode policy as the gateway's existing module-level
# Redis clients. Bytes in, str at the storyline boundary — store.get() and
# store.list_recent() handle decoding internally.
_redis: Redis = get_redis_client(settings.REDIS_URL, decode_responses=False)


def _tenant_id(request: Request) -> str:
    tid = getattr(request.state, "tenant_id", "") or request.headers.get("X-Tenant-ID", "")
    if not tid:
        # No tenant context = no storylines. Better to fail loudly than to
        # leak another tenant's incidents on a missing-header bug.
        raise HTTPException(status_code=401, detail="tenant_id missing on request")
    return str(tid)


@router.get("/storylines", tags=["Storylines"])
async def list_storylines(
    request: Request,
    since_minutes: int = Query(default=1440, ge=1, le=10080,
                               description="Window in minutes; default 24h."),
    limit: int = Query(default=50, ge=1, le=500),
) -> Any:
    """List open storylines newest-first.

    Returns a JSON object ``{items: [Storyline, …], count: int}`` rather
    than a bare array so future fields (pagination cursor, kill-chain
    summary) can be added without breaking SDK consumers.
    """
    tenant_id = _tenant_id(request)
    import time as _t
    since_ts = _t.time() - (since_minutes * 60)
    rows = await store.list_recent(_redis, tenant_id, since_ts=since_ts, limit=limit)
    return {"items": [r.to_dict() for r in rows], "count": len(rows)}


@router.get("/storylines/{incident_id}", tags=["Storylines"])
async def get_storyline(incident_id: str, request: Request) -> Any:
    """Fetch one storyline by ``incident_id``.

    Returns 404 when the incident is unknown (never opened) or has aged
    out of Redis (the TTL is 24 h on the meta/step keys — once they decay,
    Sprint 6's DB persistence is the cold-storage answer).
    """
    tenant_id = _tenant_id(request)
    if not incident_id or not incident_id.startswith("INC-"):
        raise HTTPException(status_code=400, detail="incident_id must look like INC-…")
    s = await store.get(_redis, tenant_id, incident_id)
    if s is None:
        raise HTTPException(status_code=404, detail=f"storyline {incident_id} not found")
    return s.to_dict()
