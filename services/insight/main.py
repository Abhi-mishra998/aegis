import json

import httpx
import structlog
from fastapi import Depends, FastAPI, Header, HTTPException

from sdk.common.auth import verify_internal_secret
from sdk.common.config import settings
from sdk.common.redis import get_redis_client
from sdk.utils import setup_app

logger = structlog.get_logger(__name__)

redis = get_redis_client(settings.REDIS_URL, decode_responses=False)


async def _synthetic_insights_from_audit(tenant_id: str, limit: int) -> list[dict]:
    """
    Run-3 fallback (2026-05-13): when the Groq sorted set is empty for this tenant,
    pull recent high-risk audit decisions and shape them as 'pending-analysis'
    insights. This keeps the UI panel meaningful at fresh-tenant boot before the
    worker has produced any Groq output.

    Synthetic insights are clearly marked with `source: "audit_signal"` so the UI
    can render them differently from real Groq output if desired.
    """
    if not tenant_id:
        return []
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(
                f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/soc-timeline",
                params={"limit": min(limit, 20)},
                headers={
                    "X-Internal-Secret": settings.INTERNAL_SECRET,
                    "X-Tenant-ID": tenant_id,
                },
            )
            if resp.status_code != 200:
                return []
            rows = (resp.json() or {}).get("data") or []
    except Exception as exc:
        logger.warning("insight_fallback_audit_fetch_failed", error=str(exc))
        return []

    out: list[dict] = []
    for r in rows[:limit]:
        decision = (r.get("decision") or "").lower()
        if decision not in ("deny", "escalate", "kill", "block"):
            continue
        out.append({
            "source": "audit_signal",
            "threat_classification": (r.get("action") or decision or "DECISION").upper(),
            "confidence": "MEDIUM",
            "narrative": (
                f"Audit decision '{decision}' on tool='{r.get('tool') or 'n/a'}' "
                f"(reason: {r.get('reason') or 'unspecified'})."
            ),
            "agent_id": r.get("agent_id"),
            "tool": r.get("tool"),
            "request_id": r.get("request_id"),
            "ts": r.get("timestamp"),
        })
    return out

_TIMELINE_KEY_PREFIX = "acp:groq:insights:timeline"  # per-tenant: {prefix}:{tenant_id}

app = FastAPI(
    title="ACP Groq Insight Service",
    description="AI-powered threat explanation and recommendations API",
    version="1.0.0",
)

setup_app(app, "insight")


@app.get("/insights/{event_id}", dependencies=[Depends(verify_internal_secret)])
async def get_insight(event_id: str):
    data = await redis.get(f"acp:groq:insight:{event_id}")
    if not data:
        raise HTTPException(status_code=404, detail="Insight not found")
    return json.loads(data)


@app.get("/insights", dependencies=[Depends(verify_internal_secret)])
async def list_recent_insights(
    limit: int = 20,
    tenant_id: str = "",
    x_tenant_id: str = Header(default="", alias="X-Tenant-ID"),
):
    """
    Return the most recent AI-generated threat insights, newest first.
    Scoped to tenant when X-Tenant-ID is provided via query param or header.

    2026-05-13 (Run-3): accept tenant from `X-Tenant-ID` header too — the gateway
    proxy forwards the header but doesn't append it as a query param, so without
    this the per-tenant sorted set lookup was always falling back to SCAN.

    Primary path: sorted set acp:groq:insights:timeline:{tenant_id} (O(log N) range query).
    Fallback path: SCAN when the sorted set is absent (e.g. fresh deployment).
    """
    tenant_id = tenant_id or x_tenant_id or ""
    insights = []
    _TIMELINE_KEY = f"{_TIMELINE_KEY_PREFIX}:{tenant_id}" if tenant_id else _TIMELINE_KEY_PREFIX

    # Primary: sorted set gives us chronological order cheaply
    if await redis.exists(_TIMELINE_KEY):
        # ZREVRANGE returns newest-first (highest score = most recent timestamp)
        event_ids = await redis.zrevrange(_TIMELINE_KEY, 0, limit - 1)
        for raw_id in event_ids:
            event_id = raw_id.decode() if isinstance(raw_id, bytes) else raw_id
            raw = await redis.get(f"acp:groq:insight:{event_id}")
            if raw:
                try:
                    item = json.loads(raw)
                    item.setdefault("event_id", event_id)
                    insights.append(item)
                except Exception as exc:
                    # Malformed insight blob in Redis. Skip it but log so
                    # a poison-pill entry is debuggable instead of silently
                    # truncating the response.
                    logger.warning(
                        "insight_parse_failed_indexed",
                        event_id=event_id, error=str(exc),
                    )
        if insights:
            return {"success": True, "data": insights}

    # Fallback: SCAN (no ordering guarantee, used only before first worker run)
    cursor = 0
    while len(insights) < limit:
        cursor, keys = await redis.scan(cursor, match="acp:groq:insight:*", count=50)
        for k in keys:
            raw = await redis.get(k)
            if raw:
                try:
                    parsed = json.loads(raw)
                    event_id = (k.decode() if isinstance(k, bytes) else k).split(":")[-1]
                    parsed.setdefault("event_id", event_id)
                    insights.append(parsed)
                    if len(insights) >= limit:
                        break
                except Exception as exc:
                    # Malformed insight blob discovered in SCAN fallback.
                    # Skip but log so the responsible writer can be tracked.
                    logger.warning(
                        "insight_parse_failed_scan",
                        key=str(k), error=str(exc),
                    )
        if cursor == 0:
            break

    # Run-3 (2026-05-13): if neither the sorted set nor SCAN produced anything,
    # synthesize from recent high-risk audit decisions so the UI panel renders
    # something meaningful while Groq is warming up or unavailable.
    if not insights:
        insights = await _synthetic_insights_from_audit(tenant_id, limit)

    return {"success": True, "data": insights}
