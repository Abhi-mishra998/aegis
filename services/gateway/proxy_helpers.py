"""Shared helpers for the LLM proxy routes.

Anthropic (``/v1/messages``) and OpenAI (``/v1/chat/completions``) both
need the same per-employee budget bookkeeping, approval lookup, Slack
notification, and policy-pack fetch. The original implementations
lived twice — once in each router — because the two modules can't
import from each other without a circular reference. Moving them to a
LEAF module (no upstream imports of either router) closes the
duplication cleanly: both routers import from here, neither here from
them.

Discovered during the 2026-06-17 dead-code audit (24 commits, 14
sprints, ~150 lines of duplicated body) — see SPRINT.md ledger.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
import structlog
from fastapi import Request

from sdk.common.config import settings
from services.gateway import slack_approvals
from services.gateway._helpers import internal_headers


logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Per-employee daily / monthly spend tracking.
#
# Day buckets live 7 days, month buckets 70 days — comfortably past
# any billing reconciliation window. ``_record_spend`` uses a redis
# pipeline so the four ops are sent as one network round-trip.
# ─────────────────────────────────────────────────────────────────────


def spend_key_day(tenant_id: str, email: str, day: str) -> str:
    return f"acp:llm_spend:emp:{tenant_id}:{email}:{day}"


def spend_key_month(tenant_id: str, email: str, month: str) -> str:
    return f"acp:llm_spend:emp:{tenant_id}:{email}:{month}"


async def current_spend(redis: Any, tenant_id: str, email: str) -> tuple[float, float]:
    """Return ``(today_usd, month_usd)`` for one employee. NEVER throws."""
    now = datetime.now(tz=timezone.utc)
    day_str   = now.strftime("%Y-%m-%d")
    month_str = now.strftime("%Y-%m")
    try:
        today_raw = await redis.get(spend_key_day(tenant_id, email, day_str))
        month_raw = await redis.get(spend_key_month(tenant_id, email, month_str))
    except Exception as exc:  # noqa: BLE001
        logger.warning("llm_proxy_spend_read_failed", error=str(exc))
        return 0.0, 0.0
    return (float(today_raw or 0), float(month_raw or 0))


async def record_spend(redis: Any, tenant_id: str, email: str, cost: float) -> None:
    """Increment both day + month counters. NEVER throws."""
    if cost <= 0:
        return
    now = datetime.now(tz=timezone.utc)
    day_str   = now.strftime("%Y-%m-%d")
    month_str = now.strftime("%Y-%m")
    try:
        pipe = redis.pipeline()
        pipe.incrbyfloat(spend_key_day(tenant_id, email, day_str), cost)
        pipe.expire(spend_key_day(tenant_id, email, day_str), 7 * 24 * 3600)
        pipe.incrbyfloat(spend_key_month(tenant_id, email, month_str), cost)
        pipe.expire(spend_key_month(tenant_id, email, month_str), 70 * 24 * 3600)
        await pipe.execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("llm_proxy_spend_record_failed", error=str(exc), cost=cost)


# ─────────────────────────────────────────────────────────────────────
# Approval lookup. Joins the original escalate audit row with any
# operator override row, returning the normalized record both the
# Anthropic + OpenAI proxies use for the X-Aegis-Approval-ID replay
# path and for /approvals/{id}/status.
# ─────────────────────────────────────────────────────────────────────


async def lookup_approval(
    request: Request, tenant_id: str, approval_id: str,
) -> dict | None:
    """Return the normalized approval record or None if not found.

    Shape::

        {
          "approval_id":     str,
          "status":          "pending" | "approved" | "rejected",
          "approver_role":   str | None,
          "matched_pattern": str | None,
          "employee_email":  str | None,
          "requested_at":    str | None,
          "decided_at":      str | None,
          "decided_by":      str | None,
          "reason":          str | None,
          "prompt_excerpt":  str | None,
        }
    """
    headers = internal_headers(request)
    esc_row: dict | None = None
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.post(
                f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/search",
                json={"decision": "escalate", "limit": 50},
                headers=headers,
            )
        if resp.status_code == 200:
            data = (resp.json() or {}).get("data") or {}
            items = data.get("items", []) if isinstance(data, dict) else []
            for r in items:
                if (r.get("request_id") or "") == approval_id:
                    esc_row = r
                    break
    except httpx.HTTPError as exc:
        logger.warning("approval_lookup_audit_failed", error=str(exc))

    if esc_row is None:
        return None

    meta = esc_row.get("metadata_json") or esc_row.get("metadata") or {}
    if isinstance(meta, str):
        try:
            import json as _json
            meta = _json.loads(meta)
        except Exception:
            meta = {}

    status_str = "pending"
    decided_at = decided_by = reason = None
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            ov_resp = await client.get(
                f"{settings.AUTONOMY_SERVICE_URL.rstrip('/')}/autonomy/overrides",
                params={
                    "minutes":     43200,
                    "target_kind": "request",
                    "target_id":   approval_id,
                    "limit":       10,
                },
                headers=headers,
            )
        if ov_resp.status_code == 200:
            ov_body = ov_resp.json() or {}
            ov_items = ov_body.get("data") if isinstance(ov_body, dict) else ov_body
            if isinstance(ov_items, list) and ov_items:
                ov = ov_items[0]
                et = (ov.get("event_type") or "").lower()
                if et == "approval":
                    status_str = "approved"
                elif et == "override":
                    status_str = "rejected"
                decided_at = ov.get("occurred_at") or None
                decided_by = ov.get("actor") or None
                reason = ov.get("reason") or None
    except httpx.HTTPError as exc:
        logger.warning("approval_lookup_override_failed", error=str(exc))

    return {
        "approval_id":     approval_id,
        "status":          status_str,
        "approver_role":   meta.get("approver_role"),
        "matched_pattern": meta.get("matched_pattern"),
        "employee_email":  meta.get("employee_email"),
        "requested_at":    esc_row.get("timestamp") or esc_row.get("created_at"),
        "decided_at":      decided_at,
        "decided_by":      decided_by,
        "reason":          reason,
        "prompt_excerpt":  meta.get("prompt_excerpt"),
    }


# ─────────────────────────────────────────────────────────────────────
# Tenant Slack-approvals config + card poster. Both proxies hit these
# the same way; failures NEVER fail the proxy response (the in-app
# Approval Inbox is still the authoritative resolution path).
# ─────────────────────────────────────────────────────────────────────


async def fetch_tenant_slack_config(
    request: Request, tenant_id: str,
) -> tuple[str | None, str | None]:
    """Return ``(webhook_url, signing_secret)`` for the tenant, or
    ``(None, None)`` if Slack approvals aren't configured."""
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(
                f"{settings.IDENTITY_SERVICE_URL.rstrip('/')}/workspace/slack-config",
                headers=internal_headers(request),
            )
        if resp.status_code != 200:
            return (None, None)
        body = resp.json() or {}
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            return (None, None)
        return (data.get("webhook_url") or None, data.get("signing_secret") or None)
    except httpx.HTTPError as exc:
        logger.warning("slack_config_fetch_failed", error=str(exc))
        return (None, None)


async def post_slack_card(
    *,
    webhook_url: str,
    secret: str,
    tenant_id: str,
    approval_id: str,
    approver_role: str,
    matched_pattern: str,
    employee_email: str,
    prompt_excerpt: str,
    base_url: str,
) -> None:
    """Best-effort post to the tenant's Slack webhook. Logs and swallows
    every error; the audit row + Inbox remain authoritative."""
    try:
        card = slack_approvals.build_slack_card(
            base_url=base_url,
            tenant_id=tenant_id,
            secret=secret,
            approval_id=approval_id,
            approver_role=approver_role,
            matched_pattern=matched_pattern,
            employee_email=employee_email,
            prompt_excerpt=prompt_excerpt,
            requested_at_iso=datetime.now(tz=timezone.utc).isoformat(),
        )
        async with httpx.AsyncClient(timeout=4.0) as client:
            await client.post(webhook_url, json=card)
    except Exception as exc:  # noqa: BLE001
        logger.warning("slack_card_post_failed", error=str(exc))


# ─────────────────────────────────────────────────────────────────────
# Tenant policy-pack fetch. Used by both proxies to extend the base
# escalation pattern set.
# ─────────────────────────────────────────────────────────────────────


async def fetch_enabled_policy_packs(
    request: Request, tenant_id: str,
) -> list[str]:
    """Return the tenant's enabled policy-pack IDs (empty list on any
    fetch error so the proxy keeps serving with base rules)."""
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(
                f"{settings.IDENTITY_SERVICE_URL.rstrip('/')}/workspace/policy-packs",
                headers=internal_headers(request),
            )
        if resp.status_code != 200:
            return []
        body = resp.json() or {}
        data = body.get("data") if isinstance(body, dict) else None
        return list((data or {}).get("enabled") or [])
    except httpx.HTTPError as exc:
        logger.warning("policy_packs_fetch_failed", error=str(exc))
        return []
