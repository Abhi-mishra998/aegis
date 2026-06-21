"""Gateway proxy route for the per-tenant request quota panel.

GET /tenant/quota lifted out of services/gateway/main.py in the sprint-5
audit cleanup. The endpoint returns the tenant's limits + current usage
read out of Redis counters maintained by ``TenantQuotaLimiter`` /
``InferenceCostLimiter``, and may publish a one-shot SSE
``quota_warning`` event when the tenant crosses the 80% monthly cap
threshold.

Read-only: the endpoint never increments the counters. The
quota-warning publish is guarded by a Redis SETNX key so polling
``/tenant/quota`` every few seconds doesn't spam the channel.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request
from redis.asyncio import Redis

from sdk.common.config import settings
from sdk.common.redis import get_redis_client
from services.gateway._helpers import publish_event

router = APIRouter()
logger = structlog.get_logger(__name__)

_redis: Redis = get_redis_client(settings.REDIS_URL, decode_responses=False)


@router.get("/tenant/quota", tags=["tenant"])
async def get_tenant_quota(request: Request) -> dict[str, Any]:
    """Current usage + limits for the authenticated tenant.

    Returns::

        {
          "limits": {
            "requests_per_second": int, "burst": int,
            "daily_request_cap": int, "monthly_request_cap": int | null,
            "daily_inference_cost_cap_usd": float | null,
            "rpm_limit": int,
          },
          "usage": {
            "daily_used": int, "daily_resets_at": iso8601,
            "monthly_used": int, "monthly_resets_at": iso8601 | null,
            "monthly_warn_emitted": bool,
            ... cost usage merged from InferenceCostLimiter ...
          }
        }

    P3-2 (2026-06-21): the `tier` field was dropped from the public response.
    An attacker who breached one tenant could previously read `"tier":"enterprise"`
    on every neighbouring tenant they could enumerate, narrowing the next
    target. Tier still drives quota internally — it's just no longer leaked
    on the public surface. Stripe-side billing and the admin/* routes (gated
    by ROOT) retain authoritative tier visibility.
    """
    tenant_id = getattr(request.state, "tenant_id", None)
    if tenant_id is None:
        raise HTTPException(status_code=401, detail="tenant context required")
    limits = getattr(request.state, "quota_limits", None) or {}
    rpm    = int(getattr(request.state, "rpm_limit", 0) or 0)

    # Lazy imports because TenantQuotaLimiter pulls a chunk of unrelated
    # state and we don't want it on the gateway's cold-start path.
    from sdk.common.inference_cost import InferenceCostLimiter
    from sdk.common.ratelimit import TenantQuotaLimiter

    limiter = TenantQuotaLimiter(_redis)
    usage = await limiter.usage_snapshot(
        tenant_id=str(tenant_id),
        daily_cap=int(limits.get("daily_request_cap", 1_000_000)),
        monthly_cap=(
            int(limits["monthly_request_cap"])
            if limits.get("monthly_request_cap") is not None else None
        ),
    )
    cost_limiter = InferenceCostLimiter(_redis)
    cost_usage = await cost_limiter.usage_snapshot(
        tenant_id=str(tenant_id),
        agent_id=str(getattr(request.state, "agent_id", "") or ""),
    )

    # At-most-once-per-month SSE quota_warning publish when the tenant
    # crosses 80% of its monthly request cap. The
    # acp:quota_warning_sent:{tenant_id}:{YYYYMM} SETNX guard makes this
    # idempotent even if /tenant/quota is polled every few seconds.
    monthly_cap = usage.get("monthly_cap") if isinstance(usage, dict) else None
    monthly_used = usage.get("monthly_used") if isinstance(usage, dict) else None
    if monthly_cap and monthly_used is not None:
        try:
            cap_int = int(monthly_cap)
            used_int = int(monthly_used)
        except (TypeError, ValueError):
            cap_int, used_int = 0, 0
        if cap_int > 0 and used_int >= int(cap_int * 0.80):
            now = datetime.now(UTC)
            guard_key = f"acp:quota_warning_sent:{tenant_id}:{now.strftime('%Y%m')}"
            try:
                first_time = await _redis.set(guard_key, "1", nx=True, ex=35 * 24 * 3600)
            except Exception as exc:
                logger.warning("quota_warning_guard_failed", error=str(exc))
                first_time = False
            if first_time:
                await publish_event(
                    _redis, str(tenant_id), "quota_warning",
                    {
                        "tenant_id":         str(tenant_id),
                        "monthly_used":      used_int,
                        "monthly_cap":       cap_int,
                        "percent":           round(used_int / cap_int * 100.0, 2),
                        "monthly_resets_at": usage.get("monthly_resets_at"),
                        "threshold":         80,
                    },
                )

    return {
        "limits": {
            "requests_per_second":           int(limits.get("requests_per_second", 50)),
            "burst":                         int(limits.get("burst", 100)),
            "daily_request_cap":             int(limits.get("daily_request_cap", 1_000_000)),
            "monthly_request_cap":           limits.get("monthly_request_cap"),
            "daily_inference_cost_cap_usd":  limits.get("daily_inference_cost_cap_usd"),
            "rpm_limit":                     rpm,
        },
        "usage": {**usage, **cost_usage},
    }
