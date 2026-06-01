from __future__ import annotations

import uuid
from typing import Annotated, Literal

import httpx
import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Path, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sdk.common.auth import verify_internal_secret
from sdk.common.config import settings
from sdk.common.db import get_db, get_tenant_id
from sdk.common.redis import get_redis_client
from sdk.common.response import APIResponse

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/decision", tags=["decision"])

# ---------------------------------------------------------------------------
# CONSTANTS
# ---------------------------------------------------------------------------

_KS_ALLOWED_ROLES = frozenset(["ADMIN", "SECURITY"])
_KILL_SWITCH_TTL = 86400 * 7  # 7 days


# ---------------------------------------------------------------------------
# REDIS (singleton-style)
# ---------------------------------------------------------------------------

_redis = None


def _get_redis():
    global _redis
    if _redis is None:
        _redis = get_redis_client(settings.REDIS_URL, decode_responses=True)
    return _redis


# ---------------------------------------------------------------------------
# SCHEMAS
# ---------------------------------------------------------------------------

class KillSwitchAction(BaseModel):
    action: Literal["engage", "disengage"]


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------

def _require_admin_or_security(
    x_acp_role: str | None = Header(default=None),
    _secret: str = Depends(verify_internal_secret),
) -> str:
    """
    RBAC for kill switch: requires both a valid X-Internal-Secret (proves request
    came from the Gateway after JWT validation) and ADMIN or SECURITY role injected
    by the Gateway from the validated JWT claims.
    """
    role = (x_acp_role or "").upper()
    if role not in _KS_ALLOWED_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Kill switch requires ADMIN or SECURITY role",
        )
    return role


def _assert_authenticated_tenant_matches(
    tenant_id: str = Path(...),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
) -> None:
    """Defence-in-depth: reject mismatch between path tenant and gateway-attested tenant.

    The gateway forwards the authenticated tenant via X-Tenant-ID (see
    `services/gateway/main.py:_internal_headers`). If the URL's path tenant
    differs, this is a cross-tenant escalation attempt.
    """
    if not x_tenant_id or x_tenant_id != tenant_id:
        logger.critical(
            "kill_switch_cross_tenant_blocked",
            path_tenant=tenant_id,
            attested_tenant=x_tenant_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cross-tenant kill-switch operation rejected",
        )


# ---------------------------------------------------------------------------
# KILL SWITCH
# ---------------------------------------------------------------------------

@router.post("/kill-switch/{tenant_id}", response_model=APIResponse[dict])
async def toggle_kill_switch(
    tenant_id: str,
    payload: KillSwitchAction,
    _role: Annotated[str, Depends(_require_admin_or_security)],
    _tenant_match: Annotated[None, Depends(_assert_authenticated_tenant_matches)],
    db: AsyncSession = Depends(get_db),
) -> APIResponse[dict]:

    redis = _get_redis()
    key = f"acp:tenant_kill:{tenant_id}"

    if payload.action == "engage":
        await redis.setex(key, _KILL_SWITCH_TTL, "manual_admin_lockdown")
        try:
            await db.execute(text("""
                INSERT INTO kill_switches (tenant_id, engaged, reason)
                VALUES (:tid, true, :reason)
                ON CONFLICT (tenant_id) DO UPDATE
                SET engaged = true, reason = EXCLUDED.reason,
                    engaged_at = now(), disengaged_at = NULL
            """), {"tid": tenant_id, "reason": "manual_admin_lockdown"})
            await db.commit()
        except Exception as exc:
            logger.error("kill_switch_db_persist_failed", error=str(exc), tenant_id=tenant_id)
        return APIResponse(data={"status": "engaged", "tenant_id": tenant_id})

    await redis.delete(key)
    try:
        await db.execute(text("""
            UPDATE kill_switches SET engaged = false, disengaged_at = now()
            WHERE tenant_id = :tid
        """), {"tid": tenant_id})
        await db.commit()
    except Exception as exc:
        logger.error("kill_switch_db_disengage_failed", error=str(exc), tenant_id=tenant_id)
    return APIResponse(data={"status": "disengaged", "tenant_id": tenant_id})


@router.delete("/kill-switch/{tenant_id}", response_model=APIResponse[dict])
async def disengage_kill_switch(
    tenant_id: str,
    _role: Annotated[str, Depends(_require_admin_or_security)],
    _tenant_match: Annotated[None, Depends(_assert_authenticated_tenant_matches)],
    db: AsyncSession = Depends(get_db),
) -> APIResponse[dict]:

    redis = _get_redis()
    await redis.delete(f"acp:tenant_kill:{tenant_id}")
    try:
        await db.execute(text("""
            UPDATE kill_switches SET engaged = false, disengaged_at = now()
            WHERE tenant_id = :tid
        """), {"tid": tenant_id})
        await db.commit()
    except Exception as exc:
        logger.error("kill_switch_db_disengage_failed", error=str(exc), tenant_id=tenant_id)
    return APIResponse(data={"status": "disengaged", "tenant_id": tenant_id})


@router.get("/kill-switch/{tenant_id}", response_model=APIResponse[dict])
async def get_kill_switch_status(
    tenant_id: str,
    _role: Annotated[str, Depends(_require_admin_or_security)],
    _tenant_match: Annotated[None, Depends(_assert_authenticated_tenant_matches)],
) -> APIResponse[dict]:

    redis = _get_redis()
    key = f"acp:tenant_kill:{tenant_id}"

    is_engaged = await redis.exists(key)
    reason = await redis.get(key) if is_engaged else None

    return APIResponse(
        data={
            "status": "engaged" if is_engaged else "disengaged",
            "tenant_id": tenant_id,
            "reason": reason,
        }
    )


# ---------------------------------------------------------------------------
# RISK SUMMARY
# ---------------------------------------------------------------------------

def _safe_int(value) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


@router.get("/summary", response_model=APIResponse[dict])
async def get_risk_summary(
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[dict]:

    redis = _get_redis()
    tid = str(tenant_id)

    blocked = _safe_int(await redis.get(f"acp:metrics:total_denials:{tid}"))
    total = _safe_int(await redis.get(f"acp:metrics:total_calls:{tid}"))
    high_risk = _safe_int(await redis.get(f"acp:metrics:risk_distribution:{tid}:high"))
    critical_risk = _safe_int(await redis.get(f"acp:metrics:risk_distribution:{tid}:critical"))

    # Sprint 3 — replaced synthetic `variance = (i % 3) * (high_risk or 1)` formula
    # with a real call to the audit service's /logs/risk/timeline endpoint
    # (24h window, hourly buckets). Falls back to [] on any error so the
    # dashboard renders cleanly instead of showing fabricated trends.
    metrics: list[dict] = []
    try:
        audit_url = f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/risk/timeline"
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(
                audit_url,
                params={"days": 1},
                headers={
                    "X-Tenant-ID": tid,
                    "X-Internal-Secret": settings.INTERNAL_SECRET,
                },
            )
        if resp.status_code == 200:
            payload = resp.json()
            rows = payload.get("data") or []
            if isinstance(rows, list):
                import datetime
                for row in rows:
                    date_str = row.get("date") or ""
                    try:
                        dt = datetime.datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                        time_label = dt.strftime("%H:00")
                    except (ValueError, TypeError):
                        time_label = (date_str[:13] + ":00") if date_str else "00:00"
                    # avg_risk is a 0..1 float; the UI legend uses an integer
                    # score so we scale to 0..100 to keep the chart readable.
                    avg_risk = row.get("avg_risk")
                    threats = row.get("threats") or 0
                    try:
                        score = int(round(float(avg_risk or 0.0) * 100)) if avg_risk else int(threats)
                    except (TypeError, ValueError):
                        score = 0
                    metrics.append({"time": time_label, "score": max(0, score)})
        else:
            logger.warning("risk_summary_timeline_non_200", status=resp.status_code)
    except Exception as exc:
        logger.warning("risk_summary_timeline_error", error=str(exc))

    return APIResponse(
        data={
            "threats_blocked": blocked,
            "high_risk_agents": high_risk + critical_risk,
            "total_requests": total,
            "metrics": metrics,
        }
    )


# ---------------------------------------------------------------------------
# SIGNAL WEIGHTS
# ---------------------------------------------------------------------------

_SIGNAL_WEIGHTS_KEY = "acp:signal_weights:{tenant_id}"
_SIGNAL_WEIGHT_KEYS = frozenset(["inference", "behavior", "anomaly", "cost", "cross_agent"])


async def _load_tenant_weights(tenant_id: str) -> dict[str, float]:
    """Sprint 5 — per-tenant signal weights from Redis with DEFAULT_WEIGHTS fallback.

    Storage: `acp:signal_weights:{tenant_id}` JSON-encoded dict. Missing /
    malformed values silently fall back to the engine defaults so a config
    blip can't poison the live decision pipeline.
    """
    from services.decision.engine import DEFAULT_WEIGHTS
    try:
        raw = await _get_redis().get(_SIGNAL_WEIGHTS_KEY.format(tenant_id=tenant_id))
    except Exception as exc:
        logger.warning("signal_weights_read_failed", error=str(exc), tenant_id=tenant_id)
        return dict(DEFAULT_WEIGHTS)
    if not raw:
        return dict(DEFAULT_WEIGHTS)
    try:
        import json as _json
        parsed = _json.loads(raw)
        if not isinstance(parsed, dict):
            return dict(DEFAULT_WEIGHTS)
        merged = dict(DEFAULT_WEIGHTS)
        for k, v in parsed.items():
            if k in _SIGNAL_WEIGHT_KEYS:
                try:
                    merged[k] = float(v)
                except (TypeError, ValueError):
                    continue
        return merged
    except Exception as exc:
        logger.warning("signal_weights_parse_failed", error=str(exc), tenant_id=tenant_id)
        return dict(DEFAULT_WEIGHTS)


class SignalWeightsPayload(BaseModel):
    weights: dict[str, float]


@router.get("/signal-weights", response_model=APIResponse[dict])
async def get_signal_weights(
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[dict]:
    """Return the risk signal weights used by the decision engine.

    Loads per-tenant overrides from Redis (`acp:signal_weights:{tenant_id}`)
    and falls back to the in-code DEFAULT_WEIGHTS when no override exists.
    """
    weights = await _load_tenant_weights(str(tenant_id))
    return APIResponse(data={
        "weights": weights,
        "signals": [
            {"key": "inference_risk",   "label": "Inference",   "weight": weights["inference"]},
            {"key": "behavior_risk",    "label": "Behavior",    "weight": weights["behavior"]},
            {"key": "anomaly_risk",     "label": "Anomaly",     "weight": weights["anomaly"]},
            {"key": "cost_risk",        "label": "Cost",        "weight": weights["cost"]},
            {"key": "cross_agent_risk", "label": "Cross-Agent", "weight": weights["cross_agent"]},
        ],
    })


@router.put("/signal-weights", response_model=APIResponse[dict])
async def put_signal_weights(
    payload: SignalWeightsPayload,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    _role: Annotated[str, Depends(_require_admin_or_security)],
) -> APIResponse[dict]:
    """Persist per-tenant signal weights (ADMIN/SECURITY only).

    Unknown keys are dropped; values are coerced to float; partial updates
    are merged on top of DEFAULT_WEIGHTS so callers can override one signal
    without resending the whole table.
    """
    import json as _json

    from services.decision.engine import DEFAULT_WEIGHTS

    cleaned = dict(DEFAULT_WEIGHTS)
    for k, v in (payload.weights or {}).items():
        if k in _SIGNAL_WEIGHT_KEYS:
            try:
                cleaned[k] = float(v)
            except (TypeError, ValueError):
                continue
    try:
        await _get_redis().set(
            _SIGNAL_WEIGHTS_KEY.format(tenant_id=str(tenant_id)),
            _json.dumps(cleaned),
        )
    except Exception as exc:
        logger.error("signal_weights_write_failed", error=str(exc), tenant_id=str(tenant_id))
        raise HTTPException(status_code=503, detail="Unable to persist signal weights") from exc
    return APIResponse(data={"weights": cleaned})


# ---------------------------------------------------------------------------
# DECISION HISTORY
# ---------------------------------------------------------------------------

@router.get("/history", response_model=APIResponse[dict])
async def get_decision_history(
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    limit: int = 20,
) -> APIResponse[dict]:
    audit_url = f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs"
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(
                audit_url,
                params={"limit": limit, "action": "execute_tool"},
                headers={
                    "X-Tenant-ID": str(tenant_id),
                    "X-Internal-Secret": settings.INTERNAL_SECRET,
                },
            )
        if resp.status_code == 200:
            payload = resp.json()
            items = (payload.get("data") or {}).get("items", [])
            return APIResponse(data={"items": items})
        logger.warning("audit_history_fetch_failed", status=resp.status_code)
    except Exception as exc:
        logger.error("audit_history_error", error=str(exc))
    return APIResponse(data={"items": []})
