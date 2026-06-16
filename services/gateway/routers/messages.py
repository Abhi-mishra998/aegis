"""Sprint 17 — Aegis for Teams: Anthropic-compatible /v1/messages proxy.

The user pattern this endpoint serves:

    Company has a corporate Anthropic API key.
    Company has 10 employees who want to use Claude in their day job.
    Company doesn't want to give the raw corporate key to all 10.

Aegis answer: the company admin mints one ``acp_emp_…`` virtual key
PER EMPLOYEE in /api-keys/employees. Each employee replaces their
local ``ANTHROPIC_API_KEY`` with the virtual key and points the
official Anthropic SDK at ``https://ha.aegisagent.in`` instead of
``api.anthropic.com``. From the SDK's point of view nothing changed.
From Aegis's point of view:

  - every message is attributed to ``subject_email`` for the per-team
    spend dashboard (Sprint 17.3)
  - daily / monthly budget caps refuse the request BEFORE the corporate
    Anthropic key is touched
  - the existing Aegis signal registry (Sprint 7) will run on the
    prompt body in a follow-on round so harmful prompts get blocked
  - usage is metered in Redis + audit-rowed for the Merkle chain

This module deliberately stays Anthropic-compatible: same path
(``/v1/messages``), same headers (``x-api-key``, ``anthropic-version``),
same request + response schema. The SDK swap is one env var on the
employee's machine.
"""
from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from typing import Any

import httpx
import structlog
import uuid
from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sdk.common.audit_stream import push_audit_event
from sdk.common.config import settings
from sdk.common.redis import get_redis_client
from sdk.common.response import APIResponse
from services.api.models.api_key import APIKey
from services.gateway.anthropic_pricing import cost_usd

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["llm-proxy"])

_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION_DEFAULT = "2023-06-01"
_UPSTREAM_TIMEOUT_S = 60.0


# ─────────────────────────────────────────────────────────────────────
# Lightweight DB session for /v1/messages — the gateway doesn't own
# the api_keys table (the api service does), but we need to read the
# key row to authenticate. Keep the engine module-cached so we don't
# pay a fresh connect on every request.
# ─────────────────────────────────────────────────────────────────────

_api_db_engine = None
_api_db_session: async_sessionmaker[AsyncSession] | None = None


def _get_api_session() -> async_sessionmaker[AsyncSession]:
    global _api_db_engine, _api_db_session
    if _api_db_session is None:
        url = settings.API_DATABASE_URL or settings.DATABASE_URL
        _api_db_engine = create_async_engine(url, pool_pre_ping=True, pool_size=5)
        _api_db_session = async_sessionmaker(
            _api_db_engine, expire_on_commit=False, class_=AsyncSession,
        )
    return _api_db_session


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


# ─────────────────────────────────────────────────────────────────────
# Spend tracking — Redis counter per (tenant, employee_email, day) and
# per (tenant, employee_email, month). Daily + monthly budget enforced
# BEFORE we forward to Anthropic so the corporate key is never spent
# above the cap.
# ─────────────────────────────────────────────────────────────────────


def _spend_key_day(tenant_id: str, email: str, day: str) -> str:
    return f"acp:llm_spend:emp:{tenant_id}:{email}:{day}"


def _spend_key_month(tenant_id: str, email: str, month: str) -> str:
    return f"acp:llm_spend:emp:{tenant_id}:{email}:{month}"


async def _current_spend(redis, tenant_id: str, email: str) -> tuple[float, float]:
    """Return (today_usd, this_month_usd) for one employee. NEVER throws."""
    now = datetime.now(tz=timezone.utc)
    day_str   = now.strftime("%Y-%m-%d")
    month_str = now.strftime("%Y-%m")
    try:
        today_raw = await redis.get(_spend_key_day(tenant_id, email, day_str))
        month_raw = await redis.get(_spend_key_month(tenant_id, email, month_str))
    except Exception as exc:
        logger.warning("llm_proxy_spend_read_failed", error=str(exc))
        return 0.0, 0.0
    return (
        float(today_raw or 0),
        float(month_raw or 0),
    )


async def _record_spend(
    redis, tenant_id: str, email: str, cost: float,
) -> None:
    """Increment both day + month counters. NEVER throws — spend tracking
    must not break the proxy itself; reconciliation against the upstream
    Anthropic invoice catches any drift."""
    now = datetime.now(tz=timezone.utc)
    day_str   = now.strftime("%Y-%m-%d")
    month_str = now.strftime("%Y-%m")
    try:
        pipe = redis.pipeline()
        pipe.incrbyfloat(_spend_key_day(tenant_id, email, day_str), cost)
        pipe.expire(_spend_key_day(tenant_id, email, day_str), 7 * 24 * 3600)
        pipe.incrbyfloat(_spend_key_month(tenant_id, email, month_str), cost)
        pipe.expire(_spend_key_month(tenant_id, email, month_str), 60 * 24 * 3600)
        await pipe.execute()
    except Exception as exc:
        logger.warning("llm_proxy_spend_record_failed", error=str(exc), cost=cost)


# ─────────────────────────────────────────────────────────────────────
# /v1/messages — Anthropic-compatible. The SDK behaves as if it's
# talking to api.anthropic.com directly. We just sit in front of it.
# ─────────────────────────────────────────────────────────────────────


@router.post("/v1/messages")
async def proxy_anthropic_messages(request: Request) -> Response:
    """Anthropic-compatible /v1/messages proxy with per-employee accounting.

    Auth: ``x-api-key`` carries an ``acp_emp_…`` virtual key minted via
    ``POST /api-keys/employees``.

    Pre-call: validates key → checks daily + monthly budget against
    Redis-stored spend → refuses with 402 if either cap would be
    exceeded. The corporate Anthropic key is NEVER touched on a
    refused call.

    Forward: dispatches to ``api.anthropic.com/v1/messages`` using the
    tenant's stored Anthropic key (``ACP_UPSTREAM_ANTHROPIC_KEY`` env
    var for now — Sprint 17.3 will move this to a per-tenant encrypted
    column). Response is returned to the SDK verbatim.

    Post-call: parses ``usage.input_tokens`` + ``usage.output_tokens``,
    multiplies by the model's per-1M rate, increments the Redis day +
    month counters, and pushes an audit event tagged with the employee
    email so the Sprint 17.3 /team UI can roll spend up per human.
    """
    # 1. extract auth
    auth_key = request.headers.get("x-api-key") or ""
    if not auth_key.startswith("acp_emp_"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="x-api-key must be an Aegis employee virtual key (acp_emp_…)",
        )

    # 2. validate against api_keys table
    session_factory = _get_api_session()
    async with session_factory() as db:
        stmt = (
            select(APIKey)
            .where(APIKey.key_hash == _hash_key(auth_key))
            .where(APIKey.is_active.is_(True))
            .where(APIKey.subject_kind == "employee")
        )
        result = await db.execute(stmt)
        key_row = result.scalar_one_or_none()

    if key_row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid, revoked, or non-employee API key",
        )
    if key_row.subject_email is None:
        # Defensive — should never happen for subject_kind='employee'
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Employee key row missing subject_email",
        )

    tenant_id_str = str(key_row.tenant_id)
    employee_email = key_row.subject_email

    # 3. budget pre-check
    redis = get_redis_client(settings.REDIS_URL, decode_responses=True)
    try:
        today_usd, month_usd = await _current_spend(redis, tenant_id_str, employee_email)

        if key_row.daily_budget_usd is not None and today_usd >= float(key_row.daily_budget_usd):
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=(
                    f"Daily LLM budget reached for {employee_email}: "
                    f"${today_usd:.2f} / ${float(key_row.daily_budget_usd):.2f}"
                ),
            )
        if key_row.monthly_budget_usd is not None and month_usd >= float(key_row.monthly_budget_usd):
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=(
                    f"Monthly LLM budget reached for {employee_email}: "
                    f"${month_usd:.2f} / ${float(key_row.monthly_budget_usd):.2f}"
                ),
            )

        # 4. upstream Anthropic key
        # Sprint 17.2 reads it from a single env var. Sprint 17.3 will
        # move this to a per-tenant encrypted column so each customer
        # supplies their own. Keeping it env-var-based here means a
        # single-tenant deploy works today without a schema change.
        upstream_key = getattr(settings, "UPSTREAM_ANTHROPIC_KEY", None) or ""
        if not upstream_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Aegis-for-Teams is not configured: UPSTREAM_ANTHROPIC_KEY "
                    "missing from the deployment. Set it in your environment + "
                    "redeploy, or contact your workspace OWNER."
                ),
            )

        # 5. read body verbatim — Anthropic schema is preserved untouched
        raw_body = await request.body()
        anthropic_version = request.headers.get(
            "anthropic-version", _ANTHROPIC_VERSION_DEFAULT,
        )
        try:
            req_json = await request.json() if raw_body else {}
        except Exception:
            req_json = {}
        model = (req_json or {}).get("model") or "claude-haiku-4-5"

        # 6. forward to api.anthropic.com
        forward_headers = {
            "x-api-key":         upstream_key,
            "anthropic-version": anthropic_version,
            "Content-Type":      "application/json",
        }
        # Anthropic also accepts the `anthropic-beta` header for opt-in
        # features (extended-thinking, computer-use, etc.). Forward it if
        # the SDK sent one.
        beta = request.headers.get("anthropic-beta")
        if beta:
            forward_headers["anthropic-beta"] = beta

        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=_UPSTREAM_TIMEOUT_S) as client:
                upstream_resp = await client.post(
                    _ANTHROPIC_URL, content=raw_body, headers=forward_headers,
                )
        except httpx.HTTPError as exc:
            logger.error("llm_proxy_upstream_failed", error=str(exc))
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Anthropic upstream unreachable: {type(exc).__name__}",
            ) from exc
        latency_ms = (time.monotonic() - t0) * 1000

        # 7. parse usage + meter spend (best-effort — never fails the
        # proxy if the post-call accounting hits a glitch).
        usage_input = 0
        usage_output = 0
        try:
            body_json = upstream_resp.json()
            if isinstance(body_json, dict):
                u = body_json.get("usage") or {}
                usage_input  = int(u.get("input_tokens")  or 0)
                usage_output = int(u.get("output_tokens") or 0)
        except Exception:
            pass

        call_cost = cost_usd(model, usage_input, usage_output)
        await _record_spend(redis, tenant_id_str, employee_email, call_cost)

        # 8. audit trail — this is the row that lights up the Sprint
        # 17.3 /team page + the cryptographic Merkle chain. Action name
        # 'llm_proxy_call' is dedicated so future filters can pick it
        # out without colliding with the existing tool-call decisions.
        try:
            await push_audit_event(
                redis=redis,
                tenant_id=tenant_id_str,
                agent_id=None,
                action="llm_proxy_call",
                tool="anthropic_messages",
                decision="allow" if upstream_resp.is_success else "error",
                reason=None,
                metadata={
                    "employee_email": employee_email,
                    "model":          model,
                    "input_tokens":   usage_input,
                    "output_tokens":  usage_output,
                    "cost_usd":       call_cost,
                    "status_code":    upstream_resp.status_code,
                    "latency_ms":     int(latency_ms),
                    "anthropic_version": anthropic_version,
                },
                request_id=request.headers.get("X-Request-ID"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("llm_proxy_audit_failed", error=str(exc))

        # 9. return upstream verbatim so the Anthropic SDK can't tell
        # we're in the middle.
        return Response(
            content=upstream_resp.content,
            status_code=upstream_resp.status_code,
            media_type=upstream_resp.headers.get(
                "content-type", "application/json",
            ),
        )
    finally:
        try:
            await redis.aclose()
        except Exception:  # noqa: BLE001
            pass


# ─────────────────────────────────────────────────────────────────────
# /team/employees — Sprint 17.3 UI rollup.
#
# Returns one row per employee virtual key currently provisioned for
# the signed-in tenant, joined with today's + this-month's spend from
# Redis. The /team page in the UI renders this as a table with the
# email, key prefix, daily / monthly budgets, current spend, and a
# "view → revoke" affordance per row.
#
# Auth is the standard tenant JWT (the middleware authenticates because
# /team is NOT in the skip-list). The route reads the api_keys table
# directly — same pattern as the /v1/messages handler — because
# spreading it across two services (api-svc list + gateway spend join)
# would have meant 3 hops per row.
# ─────────────────────────────────────────────────────────────────────


@router.get("/team/employees")
async def list_team_employees(request: Request) -> APIResponse[list[dict]]:
    """List employee virtual keys + current-period spend for the tenant.

    Response shape (one row per employee):
    ```
    [
      {
        "key_id": "<uuid>",
        "key_prefix": "acp_emp_a…",
        "email": "alice@acme.com",
        "name": "alice",
        "is_active": true,
        "daily_budget_usd": 50.0,
        "monthly_budget_usd": 1000.0,
        "today_usd": 4.27,
        "month_usd": 184.91,
        "created_at": "2026-06-16T17:00:00Z",
        "last_used_at": null
      }
    ]
    ```
    """
    # Tenant comes from the gateway's authenticated request state.
    tenant_id_str = (
        request.headers.get("X-Tenant-ID")
        or (getattr(request.state, "jwt_claims", {}) or {}).get("tenant_id", "")
    )
    if not tenant_id_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tenant context missing — please sign in again.",
        )
    try:
        tenant_uuid = uuid.UUID(tenant_id_str)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid tenant_id: {tenant_id_str!r}",
        )

    # Load all employee keys for the tenant.
    session_factory = _get_api_session()
    async with session_factory() as db:
        stmt = (
            select(APIKey)
            .where(APIKey.tenant_id == tenant_uuid)
            .where(APIKey.subject_kind == "employee")
            .order_by(APIKey.created_at.desc())
        )
        result = await db.execute(stmt)
        keys = list(result.scalars().all())

    # Join with Redis spend.
    redis = get_redis_client(settings.REDIS_URL, decode_responses=True)
    try:
        rows: list[dict] = []
        for k in keys:
            email = k.subject_email or ""
            today_usd, month_usd = (0.0, 0.0)
            if email:
                today_usd, month_usd = await _current_spend(
                    redis, str(k.tenant_id), email,
                )
            rows.append({
                "key_id":           str(k.id),
                "key_prefix":       k.key_prefix,
                "email":            email,
                "name":             k.name,
                "is_active":        bool(k.is_active),
                "daily_budget_usd":   float(k.daily_budget_usd)   if k.daily_budget_usd   is not None else None,
                "monthly_budget_usd": float(k.monthly_budget_usd) if k.monthly_budget_usd is not None else None,
                "today_usd":        round(today_usd, 4),
                "month_usd":        round(month_usd, 4),
                "created_at":       k.created_at.isoformat() if k.created_at else None,
                "last_used_at":     k.last_used_at.isoformat() if k.last_used_at else None,
            })
        return APIResponse(data=rows)
    finally:
        try:
            await redis.aclose()
        except Exception:  # noqa: BLE001
            pass
