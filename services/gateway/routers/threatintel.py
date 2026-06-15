"""Sprint 7 — Threat-Intel control API.

Routes:

  GET    /threat-intel/iocs?kind=&source=&limit=    list IOCs
  POST   /threat-intel/iocs                         add one
  DELETE /threat-intel/iocs/{id}                    remove one
  GET    /threat-intel/feeds                        list configured feeds
  PUT    /threat-intel/feeds/{name}                 configure a feed
  POST   /threat-intel/refresh                      run the global defaults
                                                    provider now (seeds the
                                                    cache for an empty tenant)

All routes require the tenant JWT. Substring kinds are lowercased
before storage; regex kinds (destructive_shell) are validated for
syntax before write.
"""
from __future__ import annotations

import re
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from redis.asyncio import Redis

from sdk.common.config import settings
from sdk.common.redis import get_redis_client
from services.security.threatintel import ioc as ti_ioc
from services.security.threatintel import providers as ti_providers
from services.security.threatintel import store as ti_store

router = APIRouter()

_redis: Redis = get_redis_client(settings.REDIS_URL, decode_responses=False)


def _tenant_id(request: Request) -> str:
    tid = getattr(request.state, "tenant_id", "") or request.headers.get("X-Tenant-ID", "")
    if not tid:
        raise HTTPException(status_code=401, detail="tenant_id missing on request")
    return str(tid)


@router.get("/threat-intel/iocs", tags=["ThreatIntel"])
async def list_iocs(
    request: Request,
    kind: str | None = Query(default=None),
    source: str | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
    include_global: bool = Query(default=True,
                                  description="Also return IOCs in the curated global overlay."),
) -> Any:
    tenant_id = _tenant_id(request)
    if kind and kind not in ti_ioc.ALL_KINDS:
        raise HTTPException(status_code=400, detail=f"kind must be one of {sorted(ti_ioc.ALL_KINDS)}")
    items = await ti_store.list_iocs(
        _redis, tenant_id=tenant_id, kind=kind, source=source, limit=limit,
    )
    if include_global:
        items.extend(await ti_store.list_iocs(
            _redis, tenant_id=ti_store.GLOBAL_TENANT_ID,
            kind=kind, source=source, limit=limit,
        ))
    return {
        "items": [i.to_dict() for i in items],
        "count": len(items),
    }


@router.post("/threat-intel/iocs", tags=["ThreatIntel"])
async def add_ioc(request: Request) -> Any:
    tenant_id = _tenant_id(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="request body must be JSON")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="request body must be a JSON object")
    kind = str(body.get("kind") or "")
    value = str(body.get("value") or "")
    severity = str(body.get("severity") or ti_ioc.SEV_HIGH)
    if kind not in ti_ioc.ALL_KINDS:
        raise HTTPException(status_code=400, detail=f"kind must be one of {sorted(ti_ioc.ALL_KINDS)}")
    if not value:
        raise HTTPException(status_code=400, detail="value required")
    if severity not in ti_ioc.ALL_SEVERITIES:
        raise HTTPException(status_code=400, detail=f"severity must be one of {sorted(ti_ioc.ALL_SEVERITIES)}")
    if kind == ti_ioc.KIND_DESTRUCTIVE_SHELL:
        try:
            re.compile(value)
        except re.error as exc:
            raise HTTPException(status_code=400, detail=f"invalid regex: {exc}")
    actor = getattr(request.state, "actor", "") or "operator"
    rec = await ti_store.upsert_ioc(
        _redis, tenant_id=tenant_id, kind=kind, value=value,
        severity=severity, source=ti_ioc.SOURCE_OPERATOR, actor=str(actor),
    )
    return rec.to_dict()


@router.delete("/threat-intel/iocs/{ioc_id}", tags=["ThreatIntel"])
async def delete_ioc(ioc_id: str, request: Request) -> Any:
    tenant_id = _tenant_id(request)
    deleted = await ti_store.delete_ioc(_redis, tenant_id=tenant_id, ioc_id=ioc_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"ioc {ioc_id} not found")
    return {"deleted": True, "id": ioc_id}


@router.get("/threat-intel/feeds", tags=["ThreatIntel"])
async def list_feeds(request: Request) -> Any:
    tenant_id = _tenant_id(request)
    feeds = await ti_store.list_feeds(_redis, tenant_id=tenant_id)
    last_refresh = await ti_store.get_last_refresh(_redis, tenant_id=tenant_id)
    return {"feeds": feeds, "last_refresh_ts": last_refresh}


@router.put("/threat-intel/feeds/{name}", tags=["ThreatIntel"])
async def put_feed(name: str, request: Request) -> Any:
    tenant_id = _tenant_id(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="request body must be JSON")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="request body must be a JSON object")
    url = str(body.get("url") or "")
    fmt = str(body.get("format") or "text")
    refresh_seconds = int(body.get("refresh_seconds") or 3600)
    enabled = bool(body.get("enabled", True))
    if not url:
        raise HTTPException(status_code=400, detail="url required")
    if fmt not in ("text", "json"):
        raise HTTPException(status_code=400, detail="format must be text or json")
    if refresh_seconds < 60:
        raise HTTPException(status_code=400, detail="refresh_seconds must be >= 60")
    cfg = await ti_store.upsert_feed(
        _redis, tenant_id=tenant_id, name=name,
        url=url, format=fmt, refresh_seconds=refresh_seconds, enabled=enabled,
    )
    return {"name": name, **cfg}


@router.post("/threat-intel/refresh", tags=["ThreatIntel"])
async def refresh(request: Request) -> Any:
    """Run the curated-defaults providers now. Seeds the GLOBAL overlay
    on an empty deployment so a brand-new tenant has the Aegis defaults
    immediately.

    Tenant-specific feeds (configured via PUT /threat-intel/feeds) need
    a background runner that's out of scope for Sprint 7 — operators can
    call this endpoint manually until the orchestrator daemon ships in
    Sprint 8."""
    summary = await ti_providers.run_providers(
        _redis, ti_providers.global_defaults_providers(),
    )
    return {
        "ran_providers": summary,
        "ts":            time.time(),
    }
