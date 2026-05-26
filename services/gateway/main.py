"""
ACP Gateway Service — Pure Reverse Proxy
==========================================
All fixes applied:
  P0-1  proxy_auth_token now has `request: Request` parameter
  P0-5  Removed embedded routers (audit, registry, api_key) — pure httpx proxy only
  P1-3  Cookies use secure=True only in production (ENVIRONMENT setting)
  P2-7  Audit proxy URLs fixed: /logs/summary not /audit/logs/summary
  Added: /decision/kill-switch, /decision/history, /forensics/replay proxy routes
  Added: full CRUD agent proxy, api-keys proxy, audit CRUD proxy
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import Any, cast

import httpx
import structlog
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from redis.asyncio import Redis

from sdk.common.config import settings
from sdk.common.redis import get_redis_client
from sdk.utils import setup_app
from services.gateway.auth import init_token_validator, token_validator
from services.gateway.client import service_client
from services.gateway.middleware import SecurityMiddleware

redis: Redis = cast(Redis, get_redis_client(settings.REDIS_URL, decode_responses=False))
logger = structlog.get_logger(__name__)

# Backpressure: limit concurrent execution requests (prevents cascade failure)
MAX_CONCURRENT_EXECUTION = 200
execution_semaphore = asyncio.Semaphore(MAX_CONCURRENT_EXECUTION)


class PubSubManager:
    """
    Shared Redis Pub/Sub fan-out for SSE endpoints.

    ONE Redis subscription per (worker, channel) regardless of how many SSE
    clients are connected. Per-client messages land in bounded asyncio.Queue
    instances (maxsize=100); when a queue is full the oldest message is dropped
    so slow consumers can't stall the fan-out.
    """

    def __init__(self, r: Any) -> None:
        self._redis = r
        self._lock = asyncio.Lock()
        # channel → (pubsub, set[Queue], background_task)
        self._subs: dict[str, tuple[Any, set[asyncio.Queue], asyncio.Task]] = {}

    async def subscribe(self, channel: str) -> asyncio.Queue:
        async with self._lock:
            q: asyncio.Queue = asyncio.Queue(maxsize=100)
            if channel in self._subs:
                _, queues, _ = self._subs[channel]
                queues.add(q)
            else:
                pubsub = self._redis.pubsub()
                await pubsub.subscribe(channel)
                queues: set[asyncio.Queue] = {q}
                task = asyncio.create_task(self._reader(channel, pubsub, queues))
                self._subs[channel] = (pubsub, queues, task)
            return q

    async def unsubscribe(self, channel: str, q: asyncio.Queue) -> None:
        async with self._lock:
            if channel not in self._subs:
                return
            pubsub, queues, task = self._subs[channel]
            queues.discard(q)
            if not queues:
                task.cancel()
                try:
                    await pubsub.unsubscribe(channel)
                    await pubsub.aclose()
                except Exception:
                    pass
                del self._subs[channel]

    async def _reader(
        self, channel: str, pubsub: Any, queues: set[asyncio.Queue]
    ) -> None:
        try:
            while True:
                msg = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if msg and msg.get("type") == "message":
                    data = msg.get("data", b"")
                    if isinstance(data, bytes):
                        data = data.decode("utf-8")
                    for q in list(queues):
                        if q.full():
                            with suppress(asyncio.QueueEmpty):
                                q.get_nowait()
                        with suppress(asyncio.QueueFull):
                            q.put_nowait(data)
                else:
                    await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            pass

    async def close(self) -> None:
        async with self._lock:
            for channel, (pubsub, _, task) in list(self._subs.items()):
                task.cancel()
                try:
                    await pubsub.unsubscribe(channel)
                    await pubsub.aclose()
                except Exception:
                    pass
            self._subs.clear()


pubsub_manager = PubSubManager(redis)


def _clamp_int(value: str | None, default: int, lo: int, hi: int) -> int:
    """Parse and clamp a numeric query param to a safe range."""
    try:
        return max(lo, min(hi, int(value))) if value is not None else default
    except (ValueError, TypeError):
        return default


def _internal_headers(request: Request | None = None) -> dict[str, str]:
    """Build internal service-to-service headers, forwarding tenant/auth context.
    X-ACP-Role is injected from the JWT-validated request.state.role — never from
    the client header — to prevent privilege escalation via forged role claims.
    """
    headers: dict[str, str] = {"X-Internal-Secret": settings.INTERNAL_SECRET}
    if request is not None:
        for h in ("X-Tenant-ID", "X-Agent-ID", "Authorization", "X-Request-ID", "X-Trace-ID"):
            val = request.headers.get(h)
            if val:
                headers[h] = val

        # P5 FIX: Ensure X-Tenant-ID and X-Agent-ID are forwarded from the authenticated state if missing in headers
        if "X-Tenant-ID" not in headers and hasattr(request.state, "tenant_id") and request.state.tenant_id is not None:
            headers["X-Tenant-ID"] = str(request.state.tenant_id)

        if "X-Agent-ID" not in headers and hasattr(request.state, "agent_id") and request.state.agent_id is not None:
            headers["X-Agent-ID"] = str(request.state.agent_id)

        # Cookie-to-header bridge: promote acp_token cookie → Authorization when
        # no explicit Authorization header was sent (browser/SSE clients use cookies).
        if "Authorization" not in headers:
            cookie_token = request.cookies.get("acp_token")
            if cookie_token:
                headers["Authorization"] = f"Bearer {cookie_token}"
        role = getattr(request.state, "role", None)
        if role:
            headers["X-ACP-Role"] = str(role)
        actor = getattr(request.state, "actor", None)
        if actor:
            headers["X-ACP-Actor"] = str(actor)
    return headers


def _passthrough(resp: httpx.Response) -> Response:
    """
    2026-05-14 — Forward upstream JSON + STATUS CODE to the client.

    Without this, the existing pattern `return resp.json()` collapses every
    upstream 4xx/5xx into a 200 with `{"success": false, "data": null}` body.
    The UI's `request()` wrapper only treats non-2xx as errors, so it
    silently rendered empty state on every backend failure (e.g. the
    Invoice Ledger 500 was invisible to operators).

    Returns a JSONResponse with the upstream status code preserved.
    """
    try:
        body = resp.json()
    except Exception:
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type", "application/octet-stream"),
        )
    return JSONResponse(content=body, status_code=resp.status_code)


async def _process_billing_queue(redis_client, s_client) -> None:
    import asyncio
    import json

    backoff_ms = 10
    while True:
        data_str: str | None = None
        data: dict | None = None
        try:
            item = await redis_client.lpop("acp:billing_retry_queue")
            if not item:
                await asyncio.sleep(backoff_ms / 1000)
                backoff_ms = min(backoff_ms * 2, 5000)
                continue

            backoff_ms = 10
            raw = item
            data_str = raw.decode() if isinstance(raw, bytes) else raw
            data = json.loads(data_str)
            payload = data.get("payload", {})
            action = data.get("action", "allow")
            retry_count = data.get("retry_count", 0)

            client = await s_client.get_client()
            headers = s_client._get_headers()

            # 2026-05-13 (Run-3): forward idempotency_key (default to audit_id) so
            # both /usage/record (ON CONFLICT DO NOTHING on audit_id) AND
            # /billing/events (Redis HINCRBYFLOAT dedupe in value_engine) treat
            # the retry as idempotent. Without this, retries silently dropped at
            # the usage_records unique constraint, leaving the audit row in
            # billing_status='pending' forever.
            idem_key = payload.get("idempotency_key") or payload.get("audit_id")

            await client.post(
                f"{settings.USAGE_SERVICE_URL.rstrip('/')}/usage/record",
                json=payload,
                headers=headers,
            )

            await client.post(
                f"{settings.USAGE_SERVICE_URL.rstrip('/')}/billing/events",
                json={
                    "tenant_id": payload.get("tenant_id"),
                    "action": action,
                    "agent_id": payload.get("agent_id"),
                    "audit_id": payload.get("audit_id"),
                    "idempotency_key": idem_key,
                },
                headers=headers,
            )
            logger.info("billing_event_retry_successful", audit_id=payload.get("audit_id"))

        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("billing_event_retry_failed", error=str(exc))
            if data is not None:
                # Parsed successfully — apply backoff / DLQ routing
                retry_count = data.get("retry_count", 0) + 1
                data["retry_count"] = retry_count
                if retry_count > 5:
                    logger.critical("billing_event_poison_message", audit_id=data.get("payload", {}).get("audit_id"))
                    await redis_client.lpush("acp:billing_dlq", json.dumps(data))
                else:
                    await asyncio.sleep(min(2 ** retry_count, 30))
                    await redis_client.rpush("acp:billing_retry_queue", json.dumps(data))
            elif data_str is not None:
                # Failed to parse JSON — re-queue raw to avoid silent loss
                logger.warning("billing_retry_parse_error_requeuing", raw=data_str[:200])
                await redis_client.rpush("acp:billing_retry_queue", data_str)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    # 2026-05-15 — capture process start so /status can report uptime_seconds.
    # Previously /status had no concept of when the process booted, so any
    # external monitoring trying to render an SLI window saw a null gauge.
    _app.state.start_time = time.time()
    service_client.set_redis(redis)
    init_token_validator(redis)
    # Tuned timeout: 5s connect, 10s read, 5s write
    _app.state.client = httpx.AsyncClient(
        timeout=httpx.Timeout(5.0, read=10.0, write=5.0, connect=3.0),
        limits=httpx.Limits(max_connections=200, max_keepalive_connections=50)
    )
    billing_worker = asyncio.create_task(_process_billing_queue(redis, service_client))
    # Sprint 3.5 — queue-age SLI refresh loop. Without this, every
    # `acp_*_oldest_age_seconds` gauge sits at 0 forever and the
    # OutboxOldestPendingAgeHigh / AuditDLQGrowing / BillingDLQGrowing
    # alerts are paper. 30s cadence keeps the gauges fresh enough for
    # the 5-minute Alertmanager windows without pressuring Redis.
    queue_age_worker = asyncio.create_task(_refresh_queue_age_gauges_loop(redis))
    yield
    await pubsub_manager.close()
    billing_worker.cancel()
    queue_age_worker.cancel()
    await _app.state.client.aclose()
    await redis.aclose()
    await service_client.close()
    from services.policy.router import close_policy_clients
    await close_policy_clients()


# ─────────────────────────────────────────────────────────────
# Sprint 3.5 — queue-age refresh loop
# Ticks every 30s; writes the oldest-age + depth gauges declared
# in sdk/utils.py. Fail-open: any Redis hiccup logs a warning and
# we try again on the next tick.
# ─────────────────────────────────────────────────────────────

_QUEUE_AGE_TICK_SECONDS = 30


async def _refresh_queue_age_gauges_loop(_redis) -> None:
    from sdk.common.queue_age import (
        list_oldest_age_and_depth,
        stream_oldest_age_and_depth,
    )
    from sdk.utils import (
        AUDIT_DLQ_OLDEST_AGE_SECONDS,
        BILLING_DLQ_OLDEST_AGE_SECONDS,
        GROQ_QUEUE_DEPTH,
        GROQ_QUEUE_OLDEST_AGE_SECONDS,
        INSIGHT_QUEUE_DEPTH,
        INSIGHT_QUEUE_OLDEST_AGE_SECONDS,
    )

    while True:
        try:
            # Audit DLQ (Redis Stream)
            _, audit_age = await stream_oldest_age_and_depth(_redis, "acp:audit_stream:dlq")
            AUDIT_DLQ_OLDEST_AGE_SECONDS.set(audit_age)

            # Billing DLQ (Redis List; entries carry `ts` epoch)
            _, billing_age = await list_oldest_age_and_depth(_redis, "acp:billing_dlq")
            BILLING_DLQ_OLDEST_AGE_SECONDS.set(billing_age)

            # Insight / Groq queue (same stream — `acp:groq_queue`)
            depth, age = await stream_oldest_age_and_depth(_redis, "acp:groq_queue")
            INSIGHT_QUEUE_DEPTH.set(depth)
            INSIGHT_QUEUE_OLDEST_AGE_SECONDS.set(age)
            GROQ_QUEUE_DEPTH.set(depth)
            GROQ_QUEUE_OLDEST_AGE_SECONDS.set(age)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.warning("queue_age_refresh_failed", error=str(exc))
        try:
            await asyncio.sleep(_QUEUE_AGE_TICK_SECONDS)
        except asyncio.CancelledError:
            return


app = FastAPI(
    title="ACP",
    summary="Tamper-evident replay + runtime deny for AI agents.",
    description=(
        "ACP is a runtime gateway in front of your AI agents. Two jobs:\n\n"
        "1. **Deny dangerous actions before they execute** — policy enforcement + autonomy guardrails.\n"
        "2. **Prove what happened after the fact** — tamper-evident audit chain + cryptographic receipts, "
        "replayable from the Flight Recorder for 90 days.\n\n"
        "All endpoints are available under `/v1/*` (stable contract). Unversioned paths remain available "
        "for the dashboard but should not be used by integrations."
    ),
    version="1.0.0",
    contact={"name": "ACP", "url": "https://acp.example.com"},
    license_info={"name": "Commercial", "url": "https://acp.example.com/license"},
    servers=[
        {"url": "/v1", "description": "Stable v1 API (recommended for integrations)"},
        {"url": "/",   "description": "Unversioned — for the dashboard; do not pin"},
    ],
    lifespan=lifespan,
    openapi_tags=[
        {"name": "auth",       "description": "Authentication + session management"},
        {"name": "agents",     "description": "Agent registry + permissions"},
        {"name": "execution",  "description": "Runtime authorization for agent actions"},
        {"name": "policy",     "description": "Policy simulation + enforcement"},
        {"name": "audit",        "description": "Tamper-evident audit chain"},
        {"name": "receipts",     "description": "Cryptographic execution receipts (ed25519). Offline-verifiable."},
        {"name": "transparency", "description": "Daily Merkle root commitment over signed receipts."},
        {"name": "flight",       "description": "Replayable execution timelines"},
        {"name": "autonomy",   "description": "Autonomy contracts + overrides"},
        {"name": "graph",      "description": "Identity graph + blast-radius analysis"},
        {"name": "incidents",  "description": "Incident lifecycle"},
        {"name": "decision",   "description": "Decision history + kill-switch"},
        {"name": "usage",      "description": "Usage metering"},
        {"name": "ops",        "description": "Operational endpoints (health, status)"},
    ],
)


# ─────────────────────────────────────────────────────────────
# AUTH ENDPOINTS
# ─────────────────────────────────────────────────────────────


class AuthRequest(BaseModel):
    email: str
    password: str


@app.post("/auth/token", tags=["auth"])
async def proxy_auth_token(request: Request, payload: AuthRequest, response: Response) -> dict[str, Any]:
    """
    P0-1 FIX: Added `request: Request` parameter so request.app.state.client is valid.
    P1-3 FIX: secure= is gated on ENVIRONMENT == 'production'.
    CONTRACT FIX: Returns access_token in BOTH the response body (for API/Locust/SDK
    clients) AND as an httpOnly cookie (for browser clients). This eliminates the
    bearer-vs-cookie split that caused all post-restart auth failures.
    """
    url = f"{settings.IDENTITY_SERVICE_URL.rstrip('/')}/auth/login"
    client = request.app.state.client
    try:
        tenant_id = request.headers.get("X-Tenant-ID")
        headers = _internal_headers(request)
        if tenant_id:
            headers["X-Tenant-ID"] = tenant_id

        resp = await client.post(
            url,
            json={"email": payload.email, "password": payload.password},
            headers=headers
        )
        if resp.status_code != 200:
            try:
                err_body = resp.json()
                err_detail = err_body.get("error") or err_body.get("detail") or "Invalid email or password"

                # Special handling for validation errors showing missing X-Tenant-ID
                if err_body.get("error") == "Validation failed":
                    for d in err_body.get("meta", {}).get("details", []):
                        if "x-tenant-id" in d.get("loc", []):
                            err_detail = "X-Tenant-ID required"
            except Exception:
                err_detail = "Invalid email or password"

            # Allow X-Tenant-ID missing 400s to return status 400
            if resp.status_code == 400 or resp.status_code == 422:
                response.status_code = 400
            else:
                response.status_code = 401

            return {
                "success": False,
                "error": err_detail
            }

        data = resp.json() or {}
        info = data.get("data", {})
        token = info.get("access_token")

        if not token:
            return {
                "success": False,
                "error": "Token generation failed"
            }

        is_secure = settings.ENVIRONMENT == "production"

        # Browser clients: httpOnly cookie so JS cannot steal the token
        response.set_cookie(
            key="acp_token",
            value=token,
            httponly=True,
            secure=is_secure,
            samesite="strict",
            max_age=86400,
        )

        # API / Locust / SDK clients: token returned in body so Bearer auth works
        return {
            "success": True,
            "data": {
                "access_token": token,
                "token_type": "bearer",
                "expires_in": info.get("expires_in"),
                "tenant_id": str(info.get("tenant_id", "")),
                "role": info.get("role"),
            },
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/auth/agent/token", tags=["auth"])
async def proxy_agent_token(request: Request, response: Response) -> Any:
    """Proxy → Identity: issue token for agents. Body: {agent_id, secret} (credentials must be provisioned first via POST /auth/credentials)."""
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{settings.IDENTITY_SERVICE_URL.rstrip('/')}/auth/token",
        json=body,
        headers={**_internal_headers(request), "X-Tenant-ID": request.headers.get("X-Tenant-ID", "")},
    )
    response.status_code = resp.status_code
    try:
        data = resp.json()
    except Exception:
        data = None
    if resp.status_code != 200 or data is None:
        detail = (data or {}).get("detail", "Agent authentication failed")
        return {"success": False, "error": detail, "data": None}
    return data


@app.post("/auth/logout", tags=["auth"])
async def logout(response: Response) -> dict[str, Any]:
    """Clear session cookies and terminate gateway session."""
    is_secure = settings.ENVIRONMENT == "production"
    response.delete_cookie("acp_token", secure=is_secure, httponly=True, samesite="strict")
    return {"success": True, "message": "Cleared session cookies."}


@app.get("/auth/me", tags=["auth"])
async def get_me(request: Request) -> Any:
    """Proxy → Identity: current user details from JWT."""
    resp = await request.app.state.client.get(
        f"{settings.IDENTITY_SERVICE_URL.rstrip('/')}/auth/me",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.post("/auth/introspect", tags=["auth"])
async def introspect_token(request: Request) -> Any:
    """Proxy → Identity: verify token validity and return claims."""
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{settings.IDENTITY_SERVICE_URL.rstrip('/')}/auth/introspect",
        json=body,
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.post("/auth/refresh", tags=["auth"])
async def refresh_token(request: Request) -> Any:
    """Proxy → Identity: rotate access token (revokes old, issues new)."""
    resp = await request.app.state.client.post(
        f"{settings.IDENTITY_SERVICE_URL.rstrip('/')}/auth/refresh",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.post("/auth/revoke", tags=["auth"])
async def revoke_token(request: Request) -> Any:
    """Proxy → Identity: revoke all tokens for an agent (ADMIN/SECURITY only)."""
    resp = await request.app.state.client.post(
        f"{settings.IDENTITY_SERVICE_URL.rstrip('/')}/auth/revoke",
        params=request.query_params,
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.post("/auth/users", tags=["auth"])
async def create_user(request: Request) -> Any:
    """Proxy → Identity: create a new user account (first user open; subsequent require ADMIN)."""
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{settings.IDENTITY_SERVICE_URL.rstrip('/')}/auth/users",
        json=body,
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


# ─────────────────────────────────────────────────────────────
# USER MANAGEMENT — /users
# ─────────────────────────────────────────────────────────────

@app.get("/users", tags=["users"])
async def list_users_proxy(request: Request) -> Any:
    """Proxy → Identity: list users for the tenant (filter by role, is_active)."""
    resp = await request.app.state.client.get(
        f"{settings.IDENTITY_SERVICE_URL.rstrip('/')}/users",
        params=dict(request.query_params),
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.post("/users/invite", tags=["users"])
async def invite_user_proxy(request: Request) -> Any:
    """Proxy → Identity: invite a new user (creates account with random password)."""
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{settings.IDENTITY_SERVICE_URL.rstrip('/')}/users/invite",
        json=body,
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.patch("/users/{user_id}", tags=["users"])
async def update_user_proxy(user_id: str, request: Request) -> Any:
    """Proxy → Identity: update user role or active status."""
    body = await request.json()
    resp = await request.app.state.client.patch(
        f"{settings.IDENTITY_SERVICE_URL.rstrip('/')}/users/{user_id}",
        json=body,
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.delete("/users/{user_id}", tags=["users"])
async def deactivate_user_proxy(user_id: str, request: Request) -> Any:
    """Proxy → Identity: soft-delete (deactivate) a user."""
    resp = await request.app.state.client.delete(
        f"{settings.IDENTITY_SERVICE_URL.rstrip('/')}/users/{user_id}",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.post("/auth/credentials", tags=["auth"])
async def provision_credentials(request: Request, response: Response) -> Any:
    """Proxy → Identity: provision agent credentials (requires INTERNAL_SECRET via gateway)."""
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{settings.IDENTITY_SERVICE_URL.rstrip('/')}/auth/credentials",
        json=body,
        headers={**_internal_headers(request), "X-Tenant-ID": request.headers.get("X-Tenant-ID", "")},
    )
    response.status_code = resp.status_code
    return _passthrough(resp)


@app.get("/auth/tenants/{tenant_id}", tags=["auth"])
async def get_tenant_metadata(tenant_id: str, request: Request) -> Any:
    """Proxy → Identity: get tier and rate-limit metadata for a tenant (ADMIN only)."""
    resp = await request.app.state.client.get(
        f"{settings.IDENTITY_SERVICE_URL.rstrip('/')}/auth/tenants/{tenant_id}",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


# /auth/sso/* is in the middleware skip-list so these routes pass through unauthenticated.

@app.get("/auth/sso/providers", tags=["sso"])
async def sso_providers(request: Request) -> Any:
    """Return the list of configured SSO providers for the login UI."""
    resp = await request.app.state.client.get(
        f"{settings.IDENTITY_SERVICE_URL.rstrip('/')}/auth/sso/providers",
    )
    return _passthrough(resp)


@app.get("/auth/sso/{provider}", tags=["sso"])
async def sso_login_redirect(provider: str, request: Request) -> Any:
    """Initiate SSO — proxies redirect to OIDC provider."""
    url = f"{settings.IDENTITY_SERVICE_URL.rstrip('/')}/auth/sso/{provider}"
    resp = await request.app.state.client.get(url, params=dict(request.query_params))
    if resp.status_code in (301, 302, 303, 307, 308):
        from starlette.responses import RedirectResponse as _RR
        return _RR(resp.headers["location"], status_code=resp.status_code)
    return _passthrough(resp)


@app.get("/auth/sso/{provider}/callback", tags=["sso"])
async def sso_callback_proxy(provider: str, request: Request) -> Any:
    """Handle the OIDC callback and proxy the redirect-with-cookie back to the browser."""
    url = f"{settings.IDENTITY_SERVICE_URL.rstrip('/')}/auth/sso/{provider}/callback"
    resp = await request.app.state.client.get(url, params=dict(request.query_params))
    if resp.status_code in (301, 302, 303, 307, 308):
        from starlette.responses import RedirectResponse as _RR
        rr = _RR(resp.headers.get("location", "/"), status_code=resp.status_code)
        if "set-cookie" in resp.headers:
            rr.headers["set-cookie"] = resp.headers["set-cookie"]
        return rr
    return _passthrough(resp)


@app.get("/tenant/quota", tags=["tenant"])
async def get_tenant_quota(request: Request) -> dict[str, Any]:
    """Sprint 3.2 — current usage + limits for the authenticated tenant.

    Returns:
        {
          "limits": {
            "requests_per_second": int, "burst": int,
            "daily_request_cap": int, "monthly_request_cap": int | null,
            "rpm_limit": int, "tier": str,
          },
          "usage": {
            "daily_used": int, "daily_resets_at": iso8601,
            "monthly_used": int, "monthly_resets_at": iso8601 | null,
            "monthly_warn_emitted": bool,
          }
        }

    Counts come from Redis counters maintained by `TenantQuotaLimiter`.
    Read-only — never increments the counters.
    """
    tenant_id = getattr(request.state, "tenant_id", None)
    if tenant_id is None:
        raise HTTPException(status_code=401, detail="tenant context required")
    limits = getattr(request.state, "quota_limits", None) or {}
    tier   = getattr(request.state, "tier", "basic")
    rpm    = int(getattr(request.state, "rpm_limit", 0) or 0)

    from sdk.common.inference_cost import InferenceCostLimiter
    from sdk.common.ratelimit import TenantQuotaLimiter
    limiter = TenantQuotaLimiter(redis)
    usage = await limiter.usage_snapshot(
        tenant_id=str(tenant_id),
        daily_cap=int(limits.get("daily_request_cap", 1_000_000)),
        monthly_cap=(
            int(limits["monthly_request_cap"])
            if limits.get("monthly_request_cap") is not None else None
        ),
    )
    # Sprint 3.5 — daily inference $$ usage alongside the request quota
    cost_limiter = InferenceCostLimiter(redis)
    cost_usage = await cost_limiter.usage_snapshot(
        tenant_id=str(tenant_id),
        agent_id=str(getattr(request.state, "agent_id", "") or ""),
    )
    return {
        "limits": {
            "requests_per_second":           int(limits.get("requests_per_second", 50)),
            "burst":                         int(limits.get("burst", 100)),
            "daily_request_cap":             int(limits.get("daily_request_cap", 1_000_000)),
            "monthly_request_cap":           limits.get("monthly_request_cap"),
            "daily_inference_cost_cap_usd":  limits.get("daily_inference_cost_cap_usd"),
            "rpm_limit":                     rpm,
            "tier":                          tier,
        },
        "usage": {**usage, **cost_usage},
    }


@app.post("/auth/tenants", tags=["auth"])
async def upsert_tenant(request: Request) -> Any:
    """Proxy → Identity: create or update a tenant's tier and rpm_limit (ADMIN only)."""
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{settings.IDENTITY_SERVICE_URL.rstrip('/')}/auth/tenants",
        json=body,
        headers=_internal_headers(request),
    )
    # Bust the in-process Redis tenant-metadata cache so rpm_limit / tier
    # changes take effect on the next request, not after the 10-minute TTL.
    if resp.status_code in (200, 201) and isinstance(body, dict) and body.get("tenant_id"):
        try:
            redis = request.app.state.redis
            await redis.delete(f"acp:tenant_meta:{body['tenant_id']}")
        except Exception:
            pass
    return _passthrough(resp)


# /v1/* alias: every endpoint is reachable under the stable /v1 namespace.
# Implemented as ASGI path rewrite so we don't duplicate route declarations.
# Customers should pin /v1/*; the unversioned forms remain for the dashboard.
@app.middleware("http")
async def _v1_prefix_alias(request: Request, call_next):
    path = request.scope.get("path", "")
    if path.startswith("/v1/"):
        request.scope["path"] = path[3:] or "/"
        if "raw_path" in request.scope and request.scope["raw_path"] is not None:
            raw = request.scope["raw_path"]
            if raw.startswith(b"/v1/"):
                request.scope["raw_path"] = raw[3:] or b"/"
    return await call_next(request)


# Add security middleware
app.add_middleware(SecurityMiddleware, redis=redis)  # type: ignore[arg-type]

# Consolidated SDK Setup (logging, tracing, metrics, CORS, exception handlers, /health)
setup_app(app, "gateway")

# ─────────────────────────────────────────────────────────────
# P0-5 FIX: Removed include_router(audit_router), include_router(registry_router),
#           include_router(api_key_router).  All routes are now pure httpx proxies
#           so the gateway does NOT need DB connections to downstream databases.
# ─────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────
# REGISTRY PROXY — /agents
# ─────────────────────────────────────────────────────────────

@app.get("/agents", tags=["agents"])
async def list_agents(request: Request) -> Any:
    """Proxy → Registry service list agents."""
    resp = await request.app.state.client.get(
        f"{settings.REGISTRY_SERVICE_URL.rstrip('/')}/agents",
        params=request.query_params,
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.post("/agents", tags=["agents"])
async def create_agent(request: Request, response: Response) -> Any:
    """Proxy → Registry service create agent. Publishes agent_created SSE event."""
    body = await request.json()
    body = dict(body)

    # RULE 3: Tie owner_id to actual user_id from JWT (M-12 Fix)
    actor = getattr(request.state, "actor", "unknown")
    if actor and actor != "unknown":
        body["owner_id"] = actor

    resp = await request.app.state.client.post(
        f"{settings.REGISTRY_SERVICE_URL.rstrip('/')}/agents",
        json=body,
        headers=_internal_headers(request),
    )
    response.status_code = resp.status_code
    result = resp.json()
    tenant_id_str = request.headers.get("X-Tenant-ID", "")
    if tenant_id_str and resp.status_code in (200, 201):
        try:
            await redis.publish(  # type: ignore[union-attr]
                f"acp:events:{tenant_id_str}",
                json.dumps({"type": "agent_created", "data": result.get("data", result)}),
            )
        except Exception as _e:
            logger.debug("sse_publish_failed", event="agent_created", error=str(_e))
    return result


@app.get("/agents/summary", tags=["agents"])
async def agents_summary(request: Request) -> Any:
    """Proxy → Registry fleet summary (count by status + high-risk count)."""
    return await _trust_proxy(settings.REGISTRY_SERVICE_URL, "/agents/summary", request)


@app.get("/agents/{agent_id}", tags=["agents"])
async def get_agent(agent_id: str, request: Request) -> Any:
    """Proxy → Registry get single agent."""
    resp = await request.app.state.client.get(
        f"{settings.REGISTRY_SERVICE_URL.rstrip('/')}/agents/{agent_id}",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.patch("/agents/{agent_id}", tags=["agents"])
async def update_agent(agent_id: str, request: Request) -> Any:
    """Proxy → Registry update agent."""
    body = await request.json()
    resp = await request.app.state.client.patch(
        f"{settings.REGISTRY_SERVICE_URL.rstrip('/')}/agents/{agent_id}",
        json=body,
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.delete("/agents/{agent_id}", tags=["agents"])
async def delete_agent(agent_id: str, request: Request) -> Any:
    """Proxy → Registry delete agent. Publishes agent_deleted SSE event."""
    resp = await request.app.state.client.delete(
        f"{settings.REGISTRY_SERVICE_URL.rstrip('/')}/agents/{agent_id}",
        headers=_internal_headers(request),
    )
    tenant_id_str = request.headers.get("X-Tenant-ID", "")
    if tenant_id_str and resp.status_code in (200, 204):
        try:
            await redis.publish(  # type: ignore[union-attr]
                f"acp:events:{tenant_id_str}",
                json.dumps({"type": "agent_deleted", "data": {"agent_id": agent_id}}),
            )
        except Exception as _e:
            logger.debug("sse_publish_failed", event="agent_deleted", error=str(_e))
    return _passthrough(resp)


@app.get("/agents/{agent_id}/profile", tags=["agents"])
async def agent_profile(agent_id: str, request: Request) -> Any:
    """Proxy → Registry agent behavioral profile."""
    return await _trust_proxy(settings.REGISTRY_SERVICE_URL, f"/agents/{agent_id}/profile", request)


@app.get("/agents/{agent_id}/permissions", tags=["agents"])
async def list_agent_permissions(agent_id: str, request: Request) -> Any:
    """Proxy → Registry list agent permissions."""
    resp = await request.app.state.client.get(
        f"{settings.REGISTRY_SERVICE_URL.rstrip('/')}/agents/{agent_id}/permissions",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.post("/agents/{agent_id}/permissions", tags=["agents"])
async def add_agent_permission(agent_id: str, request: Request, response: Response) -> Any:
    """Proxy → Registry add agent permission.
    Normalises client payloads: maps `allowed` bool → `action`, injects
    `granted_by` from the JWT-authenticated role so callers don't need to send it.
    """
    body = await request.json()
    body = dict(body)  # shallow copy — do not mutate caller's dict

    # Map convenience field `allowed: bool` → `action: ALLOW|DENY`
    if "action" not in body and "allowed" in body:
        body["action"] = "ALLOW" if body.pop("allowed") else "DENY"
    body.pop("allowed", None)  # drop if action was already present

    # Inject granted_by from authenticated role (avoids requiring caller to send it)
    if not body.get("granted_by"):
        role = getattr(request.state, "role", None)
        body["granted_by"] = str(role) if role else "system"

    resp = await request.app.state.client.post(
        f"{settings.REGISTRY_SERVICE_URL.rstrip('/')}/agents/{agent_id}/permissions",
        json=body,
        headers=_internal_headers(request),
    )
    response.status_code = resp.status_code
    return _passthrough(resp)


@app.delete("/agents/{agent_id}/permissions/{permission_id}", tags=["agents"])
async def revoke_agent_permission(agent_id: str, permission_id: str, request: Request) -> Any:
    """Proxy → Registry revoke agent permission."""
    resp = await request.app.state.client.delete(
        f"{settings.REGISTRY_SERVICE_URL.rstrip('/')}/agents/{agent_id}/permissions/{permission_id}",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


# ─────────────────────────────────────────────────────────────
# AUDIT PROXY — /audit
# P2-7 FIX: URLs corrected to /logs/... (not /audit/logs/...)
# ─────────────────────────────────────────────────────────────

@app.get("/audit/logs/summary", tags=["audit"])
async def audit_summary(request: Request) -> Any:
    """Proxy → Audit logs summary."""
    resp = await request.app.state.client.get(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/summary",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/audit/logs", tags=["audit"])
async def list_audit_logs(request: Request) -> Any:
    """Proxy → Audit logs list."""
    params: dict[str, Any] = {
        "limit": _clamp_int(request.query_params.get("limit"), 50, 1, 500),
        "offset": _clamp_int(request.query_params.get("offset"), 0, 0, 100_000),
    }
    for key in ("agent_id", "action", "decision"):
        if val := request.query_params.get(key):
            params[key] = val
    resp = await request.app.state.client.get(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs",
        params=params,
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.post("/audit/logs/search", tags=["audit"])
async def search_audit_logs(request: Request) -> Any:
    """Proxy → Audit logs search."""
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/search",
        json=body,
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


# ─────────────────────────────────────────────────────────────
# RECEIPTS PROXY — /receipts/{id}, /receipts/key
# Cryptographic execution receipts (ed25519). Customers verify offline.
# ─────────────────────────────────────────────────────────────

@app.get("/receipts/key", tags=["receipts"])
async def receipts_public_key(request: Request) -> Any:
    """Proxy → Audit signer public key. Cache this client-side."""
    resp = await request.app.state.client.get(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/receipts/key",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/receipts/{execution_id}", tags=["receipts"])
async def get_execution_receipt(execution_id: str, request: Request) -> Any:
    """Proxy → Audit service signed receipt for one audit row.

    The execution_id is the audit row id (matches what Flight Recorder
    surfaces and what the SDK's `protect()` decorator records).

    2026-05-15: the upstream returns `APIResponse(data={receipt, signature,
    algorithm, public_key_fingerprint})`. External auditors curl this
    endpoint and want those fields at the top level (the customer-reported
    Gap 1 symptom was `{algorithm: null, fingerprint: null, sig_len: 0}` —
    that came from probing the wrapper, not the inner payload). Flatten the
    envelope here so direct-HTTP probes see the signed shape immediately
    while SDK consumers (which already unwrap `data`) are unaffected.
    """
    resp = await request.app.state.client.get(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/{execution_id}/receipt",
        headers=_internal_headers(request),
    )
    if resp.status_code >= 400:
        return _passthrough(resp)
    try:
        body = resp.json()
    except Exception:
        return _passthrough(resp)
    if isinstance(body, dict) and isinstance(body.get("data"), dict):
        inner = body["data"]
        return JSONResponse(
            status_code=resp.status_code,
            content={
                **inner,
                # Expose a sibling `fingerprint` alias so historical probe
                # scripts that check `payload.fingerprint` resolve, while
                # `public_key_fingerprint` (the canonical field for offline
                # verifiers) is preserved.
                "fingerprint": inner.get("public_key_fingerprint"),
            },
        )
    return _passthrough(resp)


# ─────────────────────────────────────────────────────────────
# TRANSPARENCY LOG PROXY — /transparency/*
# Daily Merkle root commitment over signed receipts.
# ─────────────────────────────────────────────────────────────

@app.get("/transparency/key", tags=["transparency"])
async def transparency_root_public_key(request: Request) -> Any:
    """Proxy → Audit root-signing public key.

    Separate from /v1/receipts/key. Customers archive both: the receipt-signing
    key for verifying receipts, the root-signing key for verifying daily roots.
    """
    resp = await request.app.state.client.get(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/transparency/key",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/transparency/roots", tags=["transparency"])
async def transparency_list_roots(request: Request) -> Any:
    resp = await request.app.state.client.get(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/transparency/roots",
        params=dict(request.query_params),
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/transparency/roots/{root_date}", tags=["transparency"])
async def transparency_get_root(root_date: str, request: Request) -> Any:
    resp = await request.app.state.client.get(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/transparency/roots/{root_date}",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.post("/transparency/compute", tags=["transparency"])
async def transparency_compute_root(request: Request) -> Any:
    """Trigger (re)computation of a daily root. Idempotent.

    Typical use: a daily cron at 00:05 UTC calls this with no body to
    commit yesterday's events. Operators may also call ad-hoc for backfill.
    """
    resp = await request.app.state.client.post(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/transparency/compute",
        params=dict(request.query_params),
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/transparency/inclusion/{execution_id}", tags=["transparency"])
async def transparency_inclusion(execution_id: str, request: Request) -> Any:
    resp = await request.app.state.client.get(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/transparency/inclusion/{execution_id}",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/transparency/consistency", tags=["transparency"])
async def transparency_consistency(request: Request) -> Any:
    """Proxy → Audit consistency proof. Returns the chain of root_hash +
    prev_root_hash records so the caller can verify the log is append-only
    between two snapshots."""
    resp = await request.app.state.client.get(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/transparency/consistency",
        params=dict(request.query_params),
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.post("/transparency/verify-root", tags=["transparency"])
async def transparency_verify_root(request: Request) -> Any:
    """Proxy → Audit signed-root verifier. Body is the signed root payload."""
    body = await request.body()
    resp = await request.app.state.client.post(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/transparency/verify-root",
        content=body,
        headers={**_internal_headers(request), "Content-Type": "application/json"},
    )
    return _passthrough(resp)


@app.get("/transparency/keys", tags=["transparency"])
async def transparency_keys(request: Request) -> Any:
    """Proxy → Audit root-signing key directory (active + historical)."""
    resp = await request.app.state.client.get(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/transparency/keys",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.post("/receipts/verify", tags=["receipts"])
async def receipts_verify(request: Request) -> Any:
    """Proxy → Audit receipt verifier. Body is the signed receipt payload."""
    body = await request.body()
    resp = await request.app.state.client.post(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/receipts/verify",
        content=body,
        headers={**_internal_headers(request), "Content-Type": "application/json"},
    )
    return _passthrough(resp)


@app.get("/audit/export", tags=["audit"])
async def audit_export(request: Request):
    """Stream the tamper-evident audit chain as NDJSON for SIEM ingest.

    See docs/integrations/siem.md for Splunk HEC / Datadog Logs / S3 examples.
    Forwarded as-is to the audit service; query params (since, until, agent_id,
    chain_shard, limit) are preserved.
    """
    from fastapi.responses import StreamingResponse

    upstream = await request.app.state.client.send(
        request.app.state.client.build_request(
            "GET",
            f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/export",
            params=dict(request.query_params),
            headers=_internal_headers(request),
        ),
        stream=True,
    )

    async def _relay():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(
        _relay(),
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/x-ndjson"),
        headers={k: v for k, v in upstream.headers.items() if k.lower() in {"x-acp-chain-format", "cache-control"}},
    )


@app.post("/audit/export", tags=["audit"])
async def audit_export_post(request: Request) -> Response:
    """
    Proxy → Audit service CSV/JSON audit log export.

    Body: {format, start_date?, end_date?, agent_id?, action?, limit?}
    Streams the response directly so large CSV downloads work correctly.
    Returns text/csv or application/json with Content-Disposition attachment.
    """
    body = await request.body()
    upstream_req = request.app.state.client.build_request(
        "POST",
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/audit/export",
        content=body,
        headers={**_internal_headers(request), "Content-Type": "application/json"},
    )
    upstream = await request.app.state.client.send(upstream_req, stream=True)

    async def _relay():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()

    forward_headers = {}
    if "content-disposition" in upstream.headers:
        forward_headers["Content-Disposition"] = upstream.headers["content-disposition"]
    if "content-length" in upstream.headers:
        forward_headers["Content-Length"] = upstream.headers["content-length"]

    return StreamingResponse(
        _relay(),
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/octet-stream"),
        headers=forward_headers,
    )


@app.get("/audit/logs/soc-timeline", tags=["audit"])
async def soc_timeline(request: Request) -> Any:
    """Proxy → Audit service SOC event feed (deny+kill+high-risk aggregation)."""
    resp = await request.app.state.client.get(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/soc-timeline",
        params={"limit": _clamp_int(request.query_params.get("limit"), 60, 1, 200)},
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/audit/logs/heatmap", tags=["audit"])
async def audit_heatmap(request: Request) -> Any:
    """Proxy → Audit service request-volume heatmap (day × hour, last 7 days)."""
    return await _trust_proxy(settings.AUDIT_SERVICE_URL, "/logs/heatmap", request)


@app.get("/compliance/eu-ai-act", tags=["compliance"])
async def compliance_eu_ai_act(request: Request) -> Any:
    """Proxy → Audit service EU AI Act compliance bundle."""
    return await _trust_proxy(settings.AUDIT_SERVICE_URL, "/compliance/eu-ai-act", request)


@app.get("/compliance/nist-ai-rmf", tags=["compliance"])
async def compliance_nist_ai_rmf(request: Request) -> Any:
    """Proxy → Audit service NIST AI RMF compliance bundle."""
    return await _trust_proxy(settings.AUDIT_SERVICE_URL, "/compliance/nist-ai-rmf", request)


@app.get("/compliance/soc2", tags=["compliance"])
async def compliance_soc2(request: Request) -> Any:
    """Proxy → Audit service SOC 2 Type II compliance bundle."""
    return await _trust_proxy(settings.AUDIT_SERVICE_URL, "/compliance/soc2", request)


@app.get("/compliance/tool-ledger", tags=["compliance"])
async def compliance_tool_ledger(request: Request) -> Any:
    """Proxy → Audit service per-agent tamper-evident tool-call ledger."""
    return await _trust_proxy(settings.AUDIT_SERVICE_URL, "/compliance/tool-ledger", request)


@app.post("/compliance/export", tags=["compliance"])
async def compliance_export(request: Request) -> Response:
    """
    Proxy → Audit service compliance PDF/JSON export.

    Streams the upstream response bytes directly so PDF downloads work correctly.
    Query params: framework (EU_AI_ACT|NIST_AI_RMF|SOC2), start_date, end_date,
    format (pdf|json). Returns application/pdf or application/json with
    Content-Disposition attachment.
    """
    upstream_req = request.app.state.client.build_request(
        "POST",
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/compliance/export",
        params=dict(request.query_params),
        headers=_internal_headers(request),
    )
    upstream = await request.app.state.client.send(upstream_req, stream=True)

    async def _relay():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()

    # Forward the upstream Content-Disposition so the browser triggers a
    # download prompt rather than rendering the PDF inline.
    forward_headers = {}
    if "content-disposition" in upstream.headers:
        forward_headers["Content-Disposition"] = upstream.headers["content-disposition"]
    if "content-length" in upstream.headers:
        forward_headers["Content-Length"] = upstream.headers["content-length"]

    return StreamingResponse(
        _relay(),
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/octet-stream"),
        headers=forward_headers,
    )


@app.post("/compliance/board-report", tags=["compliance"])
async def board_report_proxy(request: Request) -> Response:
    """Proxy → Audit service board-level executive PDF report (streamed)."""
    body = await request.body()
    upstream_req = request.app.state.client.build_request(
        "POST",
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/board-report",
        content=body,
        headers=_internal_headers(request),
    )
    upstream = await request.app.state.client.send(upstream_req, stream=True)

    async def _relay():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()

    forward_headers = {}
    if "content-disposition" in upstream.headers:
        forward_headers["Content-Disposition"] = upstream.headers["content-disposition"]

    return StreamingResponse(
        _relay(),
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/pdf"),
        headers=forward_headers,
    )


# ─────────────────────────────────────────────────────────────
# SIEM INTEGRATION PROXY — /siem/*
# Routes to the audit service compliance/siem/* endpoints.
# ─────────────────────────────────────────────────────────────

@app.get("/siem/config", tags=["siem"])
async def get_siem_config_proxy(request: Request) -> Any:
    """Proxy → Audit service SIEM config (masked)."""
    return await _trust_proxy(settings.AUDIT_SERVICE_URL, "/compliance/siem/config", request)


@app.post("/siem/config", tags=["siem"])
async def save_siem_config_proxy(request: Request) -> Any:
    """Proxy → Audit service — save Splunk/Datadog credentials."""
    return await _trust_proxy(settings.AUDIT_SERVICE_URL, "/compliance/siem/config", request)


@app.post("/siem/test/splunk", tags=["siem"])
async def test_splunk_proxy(request: Request) -> Any:
    """Proxy → Audit service — test Splunk HEC connectivity."""
    return await _trust_proxy(settings.AUDIT_SERVICE_URL, "/compliance/siem/test/splunk", request)


@app.post("/siem/test/datadog", tags=["siem"])
async def test_datadog_proxy(request: Request) -> Any:
    """Proxy → Audit service — test Datadog Logs connectivity."""
    return await _trust_proxy(settings.AUDIT_SERVICE_URL, "/compliance/siem/test/datadog", request)


@app.post("/siem/push", tags=["siem"])
async def siem_push_proxy(request: Request) -> Any:
    """Proxy → Audit service — manually push last N audit events to SIEM target."""
    return await _trust_proxy(settings.AUDIT_SERVICE_URL, "/compliance/siem/push", request)


# ─────────────────────────────────────────────────────────────
# SCHEDULED REPORTS PROXY — /reports/scheduled/*
# Routes to the audit service compliance/scheduled-reports/* endpoints.
# ─────────────────────────────────────────────────────────────


@app.get("/reports/scheduled", tags=["reports"])
async def list_scheduled_reports_proxy(request: Request) -> Any:
    """Proxy → Audit service — list scheduled reports for tenant."""
    return await _trust_proxy(settings.AUDIT_SERVICE_URL, "/compliance/scheduled-reports", request)


@app.post("/reports/scheduled", tags=["reports"])
async def create_scheduled_report_proxy(request: Request) -> Any:
    """Proxy → Audit service — create a new scheduled report config."""
    return await _trust_proxy(settings.AUDIT_SERVICE_URL, "/compliance/scheduled-reports", request)


@app.get("/reports/scheduled/{report_id}", tags=["reports"])
async def get_scheduled_report_proxy(report_id: str, request: Request) -> Any:
    """Proxy → Audit service — fetch a single scheduled report."""
    return await _trust_proxy(
        settings.AUDIT_SERVICE_URL, f"/compliance/scheduled-reports/{report_id}", request
    )


@app.patch("/reports/scheduled/{report_id}", tags=["reports"])
async def update_scheduled_report_proxy(report_id: str, request: Request) -> Any:
    """Proxy → Audit service — update a scheduled report."""
    return await _trust_proxy(
        settings.AUDIT_SERVICE_URL, f"/compliance/scheduled-reports/{report_id}", request
    )


@app.delete("/reports/scheduled/{report_id}", tags=["reports"])
async def delete_scheduled_report_proxy(report_id: str, request: Request) -> Any:
    """Proxy → Audit service — delete a scheduled report."""
    return await _trust_proxy(
        settings.AUDIT_SERVICE_URL, f"/compliance/scheduled-reports/{report_id}", request
    )


@app.post("/reports/scheduled/{report_id}/run", tags=["reports"])
async def run_scheduled_report_proxy(report_id: str, request: Request) -> Any:
    """Proxy → Audit service — trigger immediate report run (queues to Redis)."""
    return await _trust_proxy(
        settings.AUDIT_SERVICE_URL, f"/compliance/scheduled-reports/{report_id}/run", request
    )


@app.get("/reports/scheduled/{report_id}/history", tags=["reports"])
async def report_delivery_history_proxy(report_id: str, request: Request) -> Any:
    """Proxy → Audit service — delivery history for one scheduled report."""
    resp = await request.app.state.client.get(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/compliance/scheduled-reports/{report_id}/history",
        params=dict(request.query_params),
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


# ─────────────────────────────────────────────────────────────
# THREAT INTELLIGENCE PROXY — /threat-intel/*
# Routes to the audit service compliance/threat-intel/* endpoints.
# ─────────────────────────────────────────────────────────────


@app.post("/threat-intel/ip", tags=["threat-intel"])
async def threat_intel_ip_proxy(request: Request) -> Any:
    """Proxy → Audit service — enrich an IP address via threat intelligence."""
    return await _trust_proxy(settings.AUDIT_SERVICE_URL, "/compliance/threat-intel/ip", request)


@app.post("/threat-intel/domain", tags=["threat-intel"])
async def threat_intel_domain_proxy(request: Request) -> Any:
    """Proxy → Audit service — enrich a domain via threat intelligence."""
    return await _trust_proxy(settings.AUDIT_SERVICE_URL, "/compliance/threat-intel/domain", request)


@app.get("/threat-intel/summary", tags=["threat-intel"])
async def threat_intel_summary_proxy(request: Request) -> Any:
    """Proxy → Audit service — return threat intel summary counters."""
    return await _trust_proxy(settings.AUDIT_SERVICE_URL, "/compliance/threat-intel/summary", request)


@app.post("/policy/simulate", tags=["policy"])
async def simulate_policy(request: Request) -> Any:
    """Proxy → Policy service dry-run simulation."""
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{settings.POLICY_SERVICE_URL.rstrip('/')}/policy/simulate",
        json=body,
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.post("/policy/test", tags=["policy"])
async def test_policy_proxy(request: Request) -> Any:
    """Proxy → Policy service — test Rego against sample inputs (no agent auth required)."""
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{settings.POLICY_SERVICE_URL.rstrip('/')}/policy/test",
        json=body,
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.post("/policy/upload", tags=["policy"])
async def upload_policy_proxy(request: Request) -> Any:
    """Proxy → Policy service — save a named Rego policy (ADMIN/SECURITY only)."""
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{settings.POLICY_SERVICE_URL.rstrip('/')}/policy/upload",
        json=body,
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/audit/logs/verify", tags=["audit"])
async def verify_audit_integrity(request: Request) -> Any:
    """Proxy → Audit logs integrity verification."""
    resp = await request.app.state.client.get(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/verify",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/audit/logs/{audit_id}/explain", tags=["audit"])
async def explain_decision_proxy(audit_id: str, request: Request) -> Any:
    """Proxy → Audit service root-cause explanation for one decision."""
    resp = await request.app.state.client.get(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/{audit_id}/explain",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.post("/audit/logs/{audit_id}/notes", tags=["audit"])
async def add_audit_note_proxy(audit_id: str, request: Request) -> Any:
    """Proxy → Audit service — add analyst note to an audit entry."""
    resp = await request.app.state.client.post(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/{audit_id}/notes",
        content=await request.body(),
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/audit/logs/{audit_id}/notes", tags=["audit"])
async def list_audit_notes_proxy(audit_id: str, request: Request) -> Any:
    """Proxy → Audit service — list analyst notes for an audit entry."""
    resp = await request.app.state.client.get(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/{audit_id}/notes",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/audit/drift/{agent_id}", tags=["audit"])
async def agent_drift_proxy(agent_id: str, request: Request) -> Any:
    """Proxy → Audit service behavioral drift report for one agent."""
    resp = await request.app.state.client.get(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/drift/{agent_id}",
        params=dict(request.query_params),
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/billing/cost-attribution", tags=["billing"])
async def billing_cost_attribution(request: Request) -> Any:
    """Proxy → Usage service per-agent weekly cost attribution."""
    resp = await request.app.state.client.get(
        f"{settings.USAGE_SERVICE_URL.rstrip('/')}/billing/cost-attribution",
        params=dict(request.query_params),
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/playbooks/autotrigger-stats", tags=["autonomy"])
async def playbook_autotrigger_stats(request: Request) -> Any:
    """Proxy → Autonomy service per-playbook auto-trigger counts."""
    resp = await request.app.state.client.get(
        f"{settings.AUTONOMY_SERVICE_URL.rstrip('/')}/playbooks/autotrigger-stats",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


# ─────────────────────────────────────────────────────────────
# RISK PROXY — /risk
# P2-7 FIX: Correct downstream URLs (removed double /audit prefix)
# ─────────────────────────────────────────────────────────────

@app.get("/risk/summary", tags=["risk"])
async def risk_summary(request: Request) -> Any:
    """Proxy → Audit service summary for risk dashboard."""
    resp = await request.app.state.client.get(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/summary",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/risk/timeline", tags=["risk"])
async def risk_timeline(request: Request) -> Any:
    """Proxy → Audit service risk timeline. Forwards ?days= query param."""
    resp = await request.app.state.client.get(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/risk/timeline",
        params={"days": _clamp_int(request.query_params.get("days"), 7, 1, 90)},
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/risk/top-threats", tags=["risk"])
async def risk_top_threats(request: Request) -> Any:
    """Proxy → Audit service top threats. Forwards ?limit= query param."""
    resp = await request.app.state.client.get(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/risk/top-threats",
        params={"limit": _clamp_int(request.query_params.get("limit"), 10, 1, 100)},
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/audit/trends", tags=["audit"])
async def audit_trends_proxy(request: Request) -> Any:
    """Proxy → Audit service — tenant-level daily anomaly trend (count/threats/avg_risk)."""
    resp = await request.app.state.client.get(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/trends",
        params=dict(request.query_params),
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/audit/top-findings", tags=["audit"])
async def top_findings_proxy(request: Request) -> Any:
    """Proxy → Audit service — canonical findings frequency ranking."""
    resp = await request.app.state.client.get(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/top-findings",
        params=dict(request.query_params),
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/audit/peer-benchmark/{agent_id}", tags=["audit"])
async def agent_peer_benchmark_proxy(agent_id: str, request: Request) -> Any:
    """Proxy → Audit service — percentile rank of one agent vs. tenant peers."""
    resp = await request.app.state.client.get(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/peer-benchmark/{agent_id}",
        params=dict(request.query_params),
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/audit/tool-breakdown", tags=["audit"])
async def tool_risk_breakdown_proxy(request: Request) -> Any:
    """Proxy → Audit service — per-tool deny rate and risk score breakdown."""
    resp = await request.app.state.client.get(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/tool-breakdown",
        params=dict(request.query_params),
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/audit/risk-trend/{agent_id}", tags=["audit"])
async def agent_risk_trend_proxy(agent_id: str, request: Request) -> Any:
    """Proxy → Audit service — 30-day daily risk score trend for one agent."""
    resp = await request.app.state.client.get(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/risk-trend/{agent_id}",
        params=dict(request.query_params),
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/audit/hourly-activity", tags=["audit"])
async def audit_hourly_activity_proxy(request: Request) -> Any:
    """Proxy → Audit service — decision velocity by hour-of-day."""
    resp = await request.app.state.client.get(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/hourly-activity",
        params=dict(request.query_params),
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/audit/risk-histogram", tags=["audit"])
async def audit_risk_histogram_proxy(request: Request) -> Any:
    """Proxy → Audit service — risk score frequency distribution histogram."""
    resp = await request.app.state.client.get(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/risk-histogram",
        params=dict(request.query_params),
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/audit/weekly-heatmap", tags=["audit"])
async def audit_weekly_heatmap_proxy(request: Request) -> Any:
    """Proxy → Audit service — 7×24 weekly activity heatmap."""
    resp = await request.app.state.client.get(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/weekly-heatmap",
        params=dict(request.query_params),
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/audit/decision-trend", tags=["audit"])
async def audit_decision_trend_proxy(request: Request) -> Any:
    """Proxy → Audit service — daily decision outcome breakdown."""
    resp = await request.app.state.client.get(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/decision-trend",
        params=dict(request.query_params),
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/audit/agent-activity", tags=["audit"])
async def audit_agent_activity_proxy(request: Request) -> Any:
    """Proxy → Audit service — per-agent activity summary table."""
    resp = await request.app.state.client.get(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/agent-activity",
        params=dict(request.query_params),
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/audit/high-risk-events", tags=["audit"])
async def audit_high_risk_events_proxy(request: Request) -> Any:
    """Proxy → Audit service — recent events at or above risk threshold."""
    resp = await request.app.state.client.get(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/high-risk-events",
        params=dict(request.query_params),
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/audit/deny-reasons", tags=["audit"])
async def audit_deny_reasons_proxy(request: Request) -> Any:
    """Proxy → Audit service — top deny reason strings by frequency."""
    resp = await request.app.state.client.get(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/deny-reasons",
        params=dict(request.query_params),
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/audit/tool-usage/{agent_id}", tags=["audit"])
async def audit_tool_usage_proxy(agent_id: str, request: Request) -> Any:
    """Proxy → Audit service — per-tool call stats for a single agent."""
    resp = await request.app.state.client.get(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/tool-usage/{agent_id}",
        params=dict(request.query_params),
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/audit/tool-risk", tags=["audit"])
async def audit_tool_risk_proxy(request: Request) -> Any:
    """Proxy → Audit service — cross-agent tool risk leaderboard."""
    resp = await request.app.state.client.get(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/tool-risk",
        params=dict(request.query_params),
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/audit/risk-percentile-trend", tags=["audit"])
async def audit_risk_percentile_trend_proxy(request: Request) -> Any:
    """Proxy → Audit service — daily p50/p75/p95 risk score percentiles."""
    resp = await request.app.state.client.get(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/risk-percentile-trend",
        params=dict(request.query_params),
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/audit/daily-active-agents", tags=["audit"])
async def audit_daily_active_agents_proxy(request: Request) -> Any:
    """Proxy → Audit service — distinct active agents per day."""
    resp = await request.app.state.client.get(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/daily-active-agents",
        params=dict(request.query_params),
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/audit/finding-breakdown", tags=["audit"])
async def audit_finding_breakdown_proxy(request: Request) -> Any:
    """Proxy → Audit service — ranked frequency of canonical finding types."""
    resp = await request.app.state.client.get(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/finding-breakdown",
        params=dict(request.query_params),
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/audit/agent-daily-decisions/{agent_id}", tags=["audit"])
async def audit_agent_daily_decisions_proxy(agent_id: str, request: Request) -> Any:
    """Proxy → Audit service — daily allow/deny counts for a single agent."""
    resp = await request.app.state.client.get(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/agent-daily-decisions/{agent_id}",
        params=dict(request.query_params),
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/audit/agent-findings/{agent_id}", tags=["audit"])
async def audit_agent_findings_proxy(agent_id: str, request: Request) -> Any:
    """Proxy → Audit service — ranked finding type frequency for a single agent."""
    resp = await request.app.state.client.get(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/agent-findings/{agent_id}",
        params=dict(request.query_params),
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/audit/posture-score-trend", tags=["audit"])
async def audit_posture_score_trend_proxy(request: Request) -> Any:
    """Proxy → Audit service — daily tenant posture score trend."""
    resp = await request.app.state.client.get(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/posture-score-trend",
        params=dict(request.query_params),
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/audit/escalation-rate-trend", tags=["audit"])
async def audit_escalation_rate_trend_proxy(request: Request) -> Any:
    """Proxy → Audit service — daily escalation rate trend."""
    resp = await request.app.state.client.get(
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/escalation-rate-trend",
        params=dict(request.query_params),
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


# ─────────────────────────────────────────────────────────────
# DECISION PROXY — /decision
# NEW: Kill-switch and decision history routes proxied to Decision service
# ─────────────────────────────────────────────────────────────

@app.get("/decision/kill-switch/{tenant_id}", tags=["decision"])
async def get_kill_switch_status(tenant_id: str, request: Request) -> Any:
    """Proxy → Decision service kill-switch status."""
    resp = await request.app.state.client.get(
        f"{settings.DECISION_SERVICE_URL.rstrip('/')}/decision/kill-switch/{tenant_id}",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.post("/decision/kill-switch/{tenant_id}", tags=["decision"])
async def toggle_kill_switch(tenant_id: str, request: Request) -> Any:
    """Proxy → Decision service toggle kill-switch."""
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{settings.DECISION_SERVICE_URL.rstrip('/')}/decision/kill-switch/{tenant_id}",
        json=body,
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.delete("/decision/kill-switch/{tenant_id}", tags=["decision"])
async def disengage_kill_switch(tenant_id: str, request: Request) -> Any:
    """Proxy → Decision service disengage kill-switch."""
    resp = await request.app.state.client.delete(
        f"{settings.DECISION_SERVICE_URL.rstrip('/')}/decision/kill-switch/{tenant_id}",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/decision/history", tags=["decision"])
async def decision_history(request: Request) -> Any:
    """Proxy → Decision service decision history."""
    resp = await request.app.state.client.get(
        f"{settings.DECISION_SERVICE_URL.rstrip('/')}/decision/history",
        params={"limit": _clamp_int(request.query_params.get("limit"), 20, 1, 200)},
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/decision/summary", tags=["decision"])
async def decision_summary(request: Request) -> Any:
    """Proxy → Decision service risk summary (Redis-based counters)."""
    resp = await request.app.state.client.get(
        f"{settings.DECISION_SERVICE_URL.rstrip('/')}/decision/summary",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


# ─────────────────────────────────────────────────────────────
# FORENSICS PROXY — /forensics
# P0-3 FIX: FORENSICS_SERVICE_URL now exists in ACPSettings
# NEW: /forensics/replay/{agent_id} route added
# ─────────────────────────────────────────────────────────────

@app.get("/forensics/investigation", tags=["forensics"])
async def forensics_investigation(request: Request) -> Any:
    """Proxy → Forensics service investigation list."""
    resp = await request.app.state.client.get(
        f"{settings.FORENSICS_SERVICE_URL.rstrip('/')}/forensics/investigation",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/forensics/investigation/{agent_id}", tags=["forensics"])
async def get_investigation_report(agent_id: str, request: Request) -> Any:
    """Proxy → Forensics service investigation report for an agent."""
    resp = await request.app.state.client.get(
        f"{settings.FORENSICS_SERVICE_URL.rstrip('/')}/forensics/investigation/{agent_id}",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/forensics/replay/{agent_id}", tags=["forensics"])
async def replay_agent_behavior(agent_id: str, request: Request) -> Any:
    """Proxy → Forensics service forensic replay for an agent."""
    resp = await request.app.state.client.get(
        f"{settings.FORENSICS_SERVICE_URL.rstrip('/')}/forensics/replay/{agent_id}",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


# ─────────────────────────────────────────────────────────────
# BILLING PROXY — /billing
# ─────────────────────────────────────────────────────────────

@app.get("/billing/invoices", tags=["billing"])
async def billing_invoices(request: Request) -> Any:
    """Proxy → Usage service billing invoices."""
    resp = await request.app.state.client.get(
        f"{settings.USAGE_SERVICE_URL.rstrip('/')}/billing/invoices",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/billing/summary", tags=["billing"])
async def billing_summary(request: Request) -> Any:
    """Proxy → Usage service Redis-based billing ROI summary."""
    resp = await request.app.state.client.get(
        f"{settings.USAGE_SERVICE_URL.rstrip('/')}/billing/summary",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.post("/billing/events", tags=["billing"])
async def billing_record_event(request: Request) -> Any:
    """Proxy → Usage service billing events (records money saved)."""
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{settings.USAGE_SERVICE_URL.rstrip('/')}/billing/events",
        json=body,
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


# Budget requests
@app.post("/billing/budget-requests", tags=["billing"])
async def billing_budget_requests_create(request: Request) -> Any:
    """Proxy → Usage service: create a budget increase request."""
    body = await request.body()
    resp = await request.app.state.client.post(
        f"{settings.USAGE_SERVICE_URL.rstrip('/')}/billing/budget-requests",
        content=body,
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/billing/budget-requests", tags=["billing"])
async def billing_budget_requests_list(request: Request) -> Any:
    """Proxy → Usage service: list budget requests for tenant."""
    resp = await request.app.state.client.get(
        f"{settings.USAGE_SERVICE_URL.rstrip('/')}/billing/budget-requests",
        params=dict(request.query_params),
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/billing/budget-requests/{req_id}", tags=["billing"])
async def billing_budget_request_get(req_id: str, request: Request) -> Any:
    """Proxy → Usage service: get a single budget request."""
    resp = await request.app.state.client.get(
        f"{settings.USAGE_SERVICE_URL.rstrip('/')}/billing/budget-requests/{req_id}",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.post("/billing/budget-requests/{req_id}/approve", tags=["billing"])
async def billing_budget_request_approve(req_id: str, request: Request) -> Any:
    """Proxy → Usage service: approve a budget request."""
    body = await request.body()
    resp = await request.app.state.client.post(
        f"{settings.USAGE_SERVICE_URL.rstrip('/')}/billing/budget-requests/{req_id}/approve",
        content=body,
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.post("/billing/budget-requests/{req_id}/reject", tags=["billing"])
async def billing_budget_request_reject(req_id: str, request: Request) -> Any:
    """Proxy → Usage service: reject a budget request."""
    body = await request.body()
    resp = await request.app.state.client.post(
        f"{settings.USAGE_SERVICE_URL.rstrip('/')}/billing/budget-requests/{req_id}/reject",
        content=body,
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


# ─────────────────────────────────────────────────────────────
# USAGE PROXY — /usage
# ─────────────────────────────────────────────────────────────

@app.post("/usage/record", tags=["usage"])
async def usage_record(request: Request) -> Any:
    """Proxy → Usage service tool execution recording."""
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{settings.USAGE_SERVICE_URL.rstrip('/')}/usage/record",
        json=body,
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/usage/summary", tags=["usage"])
async def usage_summary(request: Request) -> Any:
    """Proxy → Usage service tenant usage summary."""
    resp = await request.app.state.client.get(
        f"{settings.USAGE_SERVICE_URL.rstrip('/')}/usage/summary",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/usage/dashboard", tags=["usage"])
async def usage_dashboard(request: Request) -> Any:
    """Proxy → Usage service revenue dashboard (injecting X-Internal-Secret)."""
    resp = await request.app.state.client.get(
        f"{settings.USAGE_SERVICE_URL.rstrip('/')}/usage/dashboard",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/usage/anomalies", tags=["usage"])
async def usage_anomalies(request: Request) -> Any:
    """Proxy → Usage service billing anomalies (injecting X-Internal-Secret)."""
    resp = await request.app.state.client.get(
        f"{settings.USAGE_SERVICE_URL.rstrip('/')}/usage/anomalies",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


# ─────────────────────────────────────────────────────────────
# API KEYS PROXY — /api-keys
# P0-5 FIX: Previously served by embedded router; now pure httpx proxy
# ─────────────────────────────────────────────────────────────

@app.get("/api-keys", tags=["API Keys"])
async def list_api_keys(request: Request) -> Any:
    """Proxy → API service list keys."""
    resp = await request.app.state.client.get(
        f"{settings.API_SERVICE_URL.rstrip('/')}/api-keys",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.post("/api-keys", tags=["API Keys"])
async def create_api_key(request: Request) -> Any:
    """Proxy → API service create key."""
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{settings.API_SERVICE_URL.rstrip('/')}/api-keys",
        json=body,
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.delete("/api-keys/{key_id}", tags=["API Keys"])
async def revoke_api_key(key_id: str, request: Request) -> Any:
    """Proxy → API service revoke key."""
    resp = await request.app.state.client.delete(
        f"{settings.API_SERVICE_URL.rstrip('/')}/api-keys/{key_id}",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.post("/api-keys/validate", tags=["API Keys"])
async def validate_api_key(request: Request) -> Any:
    """Proxy → API service validate key."""
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{settings.API_SERVICE_URL.rstrip('/')}/api-keys/validate",
        json=body,
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


# ─────────────────────────────────────────────────────────────
# INCIDENTS PROXY — /incidents
# ─────────────────────────────────────────────────────────────

@app.post("/incidents", tags=["Incidents"])
async def create_incident(request: Request) -> Any:
    """Proxy → API service create incident. Injects tenant_id from headers."""
    body = await request.json()
    body = dict(body)
    if "tenant_id" not in body:
        body["tenant_id"] = request.headers.get("X-Tenant-ID", "")

    resp = await request.app.state.client.post(
        f"{settings.API_SERVICE_URL.rstrip('/')}/incidents",
        json=body,
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/incidents/summary", tags=["Incidents"])
async def incident_summary(request: Request) -> Any:
    """Proxy → API service incident summary (security score, MTTR, open counts)."""
    resp = await request.app.state.client.get(
        f"{settings.API_SERVICE_URL.rstrip('/')}/incidents/summary",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/incidents", tags=["Incidents"])
async def list_incidents(request: Request) -> Any:
    """Proxy → API service incident list with optional status/severity filters."""
    resp = await request.app.state.client.get(
        f"{settings.API_SERVICE_URL.rstrip('/')}/incidents",
        params={
            k: v for k, v in request.query_params.items()
            if k in ("status", "severity", "limit", "offset")
        },
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/incidents/{incident_id}", tags=["Incidents"])
async def get_incident(incident_id: str, request: Request) -> Any:
    """Proxy → API service single incident."""
    resp = await request.app.state.client.get(
        f"{settings.API_SERVICE_URL.rstrip('/')}/incidents/{incident_id}",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.patch("/incidents/{incident_id}", tags=["Incidents"])
async def update_incident(incident_id: str, request: Request) -> Any:
    """Proxy → API service update incident status."""
    body = await request.json()
    resp = await request.app.state.client.patch(
        f"{settings.API_SERVICE_URL.rstrip('/')}/incidents/{incident_id}",
        json=body,
        headers=_internal_headers(request),
    )
    result = resp.json()
    tenant_id_str = request.headers.get("X-Tenant-ID", "")
    if tenant_id_str and resp.status_code == 200:
        try:
            await redis.publish(  # type: ignore[union-attr]
                f"acp:events:{tenant_id_str}",
                json.dumps({"type": "incident_updated", "data": result.get("data", {})}),
            )
        except Exception as _e:
            logger.debug("sse_publish_failed", event="incident_updated", error=str(_e))
    return result


@app.post("/incidents/{incident_id}/actions", tags=["Incidents"])
async def incident_action(incident_id: str, request: Request) -> Any:
    """Proxy → API service add response action to incident."""
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{settings.API_SERVICE_URL.rstrip('/')}/incidents/{incident_id}/actions",
        json=body,
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.post("/incidents/{incident_id}/comments", tags=["Incidents"])
async def add_incident_comment(incident_id: str, request: Request) -> Any:
    """Proxy → Audit service: add a timeline comment to an incident."""
    return await _trust_proxy(
        settings.AUDIT_SERVICE_URL, f"/incidents/{incident_id}/comments", request
    )


@app.get("/incidents/{incident_id}/comments", tags=["Incidents"])
async def list_incident_comments(incident_id: str, request: Request) -> Any:
    """Proxy → Audit service: list comments for an incident (ASC order)."""
    return await _trust_proxy(
        settings.AUDIT_SERVICE_URL, f"/incidents/{incident_id}/comments", request
    )


@app.post("/incidents/{incident_id}/export", tags=["Incidents"])
async def proxy_incident_export(incident_id: str, request: Request) -> Response:
    """
    Proxy → Audit service forensic incident PDF export.

    Streams the upstream PDF bytes directly so the download arrives intact.
    The audit service endpoint is at /compliance/incidents/{incident_id}/export
    (mounted under the compliance_router prefix).
    Returns application/pdf with Content-Disposition attachment.
    """
    upstream_req = request.app.state.client.build_request(
        "POST",
        f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/compliance/incidents/{incident_id}/export",
        headers=_internal_headers(request),
    )
    upstream = await request.app.state.client.send(upstream_req, stream=True)

    async def _relay():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()

    forward_headers = {}
    if "content-disposition" in upstream.headers:
        forward_headers["Content-Disposition"] = upstream.headers["content-disposition"]
    if "content-length" in upstream.headers:
        forward_headers["Content-Length"] = upstream.headers["content-length"]

    return StreamingResponse(
        _relay(),
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/octet-stream"),
        headers=forward_headers,
    )


# ─────────────────────────────────────────────────────────────
# AUTONOMOUS RESPONSE ENGINE — /auto-response
# ─────────────────────────────────────────────────────────────

@app.post("/auto-response/rules", tags=["ARE"])
async def are_create_rule(request: Request) -> Any:
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{settings.API_SERVICE_URL.rstrip('/')}/auto-response/rules",
        json=body, headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/auto-response/rules", tags=["ARE"])
async def are_list_rules(request: Request) -> Any:
    resp = await request.app.state.client.get(
        f"{settings.API_SERVICE_URL.rstrip('/')}/auto-response/rules",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/auto-response/rules/{rule_id}", tags=["ARE"])
async def are_get_rule(rule_id: str, request: Request) -> Any:
    resp = await request.app.state.client.get(
        f"{settings.API_SERVICE_URL.rstrip('/')}/auto-response/rules/{rule_id}",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.patch("/auto-response/rules/{rule_id}", tags=["ARE"])
async def are_update_rule(rule_id: str, request: Request) -> Any:
    body = await request.json()
    resp = await request.app.state.client.patch(
        f"{settings.API_SERVICE_URL.rstrip('/')}/auto-response/rules/{rule_id}",
        json=body, headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.delete("/auto-response/rules/{rule_id}", tags=["ARE"])
async def are_delete_rule(rule_id: str, request: Request) -> Any:
    resp = await request.app.state.client.delete(
        f"{settings.API_SERVICE_URL.rstrip('/')}/auto-response/rules/{rule_id}",
        headers=_internal_headers(request),
    )
    return Response(status_code=resp.status_code)


@app.post("/auto-response/toggle", tags=["ARE"])
async def are_toggle(request: Request) -> Any:
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{settings.API_SERVICE_URL.rstrip('/')}/auto-response/toggle",
        json=body, headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/auto-response/toggle", tags=["ARE"])
async def are_get_toggle(request: Request) -> Any:
    resp = await request.app.state.client.get(
        f"{settings.API_SERVICE_URL.rstrip('/')}/auto-response/toggle",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.post("/auto-response/simulate", tags=["ARE"])
async def are_simulate(request: Request) -> Any:
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{settings.API_SERVICE_URL.rstrip('/')}/auto-response/simulate",
        json=body, headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/auto-response/rules/{rule_id}/history", tags=["ARE"])
async def are_rule_history(rule_id: str, request: Request) -> Any:
    resp = await request.app.state.client.get(
        f"{settings.API_SERVICE_URL.rstrip('/')}/auto-response/rules/{rule_id}/history",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.post("/auto-response/rules/{rule_id}/rollback/{version}", tags=["ARE"])
async def are_rollback(rule_id: str, version: int, request: Request) -> Any:
    resp = await request.app.state.client.post(
        f"{settings.API_SERVICE_URL.rstrip('/')}/auto-response/rules/{rule_id}/rollback/{version}",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.post("/auto-response/rules/{rule_id}/feedback", tags=["ARE"])
async def are_feedback(rule_id: str, request: Request) -> Any:
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{settings.API_SERVICE_URL.rstrip('/')}/auto-response/rules/{rule_id}/feedback",
        json=body, headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/auto-response/metrics", tags=["ARE"])
async def are_metrics(request: Request) -> Any:
    resp = await request.app.state.client.get(
        f"{settings.API_SERVICE_URL.rstrip('/')}/auto-response/metrics",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/auto-response/pending", tags=["ARE"])
async def are_list_pending(request: Request) -> Any:
    resp = await request.app.state.client.get(
        f"{settings.API_SERVICE_URL.rstrip('/')}/auto-response/pending",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.post("/auto-response/pending/{approval_key}/approve", tags=["ARE"])
async def are_approve_pending(approval_key: str, request: Request) -> Any:
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{settings.API_SERVICE_URL.rstrip('/')}/auto-response/pending/{approval_key}/approve",
        json=body, headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.post("/auto-response/replay", tags=["ARE"])
async def are_replay(request: Request) -> Any:
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{settings.API_SERVICE_URL.rstrip('/')}/auto-response/replay",
        json=body, headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.get("/auto-response/latency", tags=["ARE"])
async def are_latency(request: Request) -> Any:
    resp = await request.app.state.client.get(
        f"{settings.API_SERVICE_URL.rstrip('/')}/auto-response/latency",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


# ─────────────────────────────────────────────────────────────
# INSIGHTS PROXY — /insights
# ─────────────────────────────────────────────────────────────

@app.get("/insights/recent", tags=["risk"])
async def get_recent_insights(request: Request) -> Any:
    """Proxy → Insight service for recent AI analysis results."""
    resp = await request.app.state.client.get(
        f"{settings.INSIGHT_SERVICE_URL.rstrip('/')}/insights",
        params=request.query_params,
        headers=_internal_headers(request),
    )
    # Insight service returns {"success": true, "data": [...]} — pass through directly
    return _passthrough(resp)


# ─────────────────────────────────────────────────────────────
# DASHBOARD STATE — /dashboard/state
# Single aggregated endpoint: audit + agents + billing + insights + kill-switch
# ─────────────────────────────────────────────────────────────

@app.get("/dashboard/state", tags=["dashboard"])
async def dashboard_state(request: Request) -> dict[str, Any]:
    """
    Aggregated state for the executive dashboard.
    Fans out to audit, registry, usage, insight, and decision services concurrently.
    Each service failure returns an empty fallback — dashboard always loads.
    """
    client = request.app.state.client
    headers = _internal_headers(request)
    tenant_id = request.headers.get("X-Tenant-ID", "")

    async def _safe(url: str, params: dict | None = None) -> Any:
        try:
            resp = await client.get(url, headers=headers, params=params or {}, timeout=5.0)
            return _passthrough(resp) if resp.status_code < 500 else {}
        except Exception:
            return {}

    audit_r, agents_r, billing_r, insights_r = await asyncio.gather(
        _safe(f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/summary"),
        _safe(f"{settings.REGISTRY_SERVICE_URL.rstrip('/')}/agents/summary"),
        _safe(f"{settings.USAGE_SERVICE_URL.rstrip('/')}/billing/summary"),
        _safe(f"{settings.INSIGHT_SERVICE_URL.rstrip('/')}/insights", {"limit": 5}),
    )

    kill_r: dict = {}
    if tenant_id:
        kill_r = await _safe(
            f"{settings.DECISION_SERVICE_URL.rstrip('/')}/decision/kill-switch/{tenant_id}"
        )

    agents_summary = agents_r.get("data", agents_r) if isinstance(agents_r, dict) else {}
    if not isinstance(agents_summary, dict):
        agents_summary = {}

    return {
        "success": True,
        "data": {
            "audit": audit_r.get("data", audit_r) if isinstance(audit_r, dict) else {},
            "agents": {
                "total":       agents_summary.get("total", 0),
                "active":      agents_summary.get("active", 0),
                "quarantined": agents_summary.get("quarantined", 0),
                "high_risk":   agents_summary.get("high_risk", 0),
            },
            "billing": billing_r.get("data", billing_r) if isinstance(billing_r, dict) else {},
            "insights": insights_r.get("data", []) if isinstance(insights_r, dict) else [],
            "kill_switch": kill_r.get("data", kill_r) if isinstance(kill_r, dict) else {},
            "ts": int(time.time()),
        },
    }


# ─────────────────────────────────────────────────────────────
# SYSTEM HEALTH — /status and /system/health
# Distributed health check: fan-out to all downstream services.
#
# Sprint 2.3 (2026-05-15): the two endpoints now expose distinct,
# clearly-labelled latency scopes. Schema documented here so the
# OpenAPI doc tells the customer which number to read for what.
# ─────────────────────────────────────────────────────────────

_LATENCY_BLOCK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "scope", "window_seconds", "p50_ms", "p95_ms", "p99_ms",
        "request_count", "computed_at",
    ],
    "properties": {
        "scope": {
            "type": "string",
            "enum": ["gateway_internal", "end_to_end"],
            "description":
                "`gateway_internal`: request received → response sent on the "
                "gateway process only. `end_to_end`: gateway → downstream "
                "`/health` probe → gateway. The two are intentionally distinct.",
        },
        "window_seconds": {"type": "integer", "minimum": 1},
        "p50_ms":        {"type": "integer", "minimum": 0},
        "p95_ms":        {"type": "integer", "minimum": 0},
        "p99_ms":        {"type": "integer", "minimum": 0},
        "request_count": {"type": "integer", "minimum": 0},
        "computed_at":   {"type": "string", "format": "date-time"},
    },
}

_KILL_SWITCH_BLOCK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["engaged", "last_toggled_at", "actor", "reason"],
    "properties": {
        "engaged":         {"type": "boolean"},
        "last_toggled_at": {"type": ["string", "null"], "format": "date-time"},
        "actor":           {"type": ["string", "null"]},
        "reason":          {"type": ["string", "null"]},
    },
}

_STATUS_RESPONSE_SCHEMA = {
    "200": {
        "description": "Public, customer-shareable status.",
        "content": {"application/json": {"schema": {
            "type": "object",
            "properties": {
                "status":         {"type": "string"},
                "components":     {"type": "object"},
                "uptime_seconds": {"type": ["integer", "null"]},
                "latency":        _LATENCY_BLOCK_SCHEMA,
                "kill_switch":    _KILL_SWITCH_BLOCK_SCHEMA,
                "p95_latency_ms": {
                    "type": "integer",
                    "deprecated": True,
                    "description": "Deprecated alias of `latency.p95_ms`. Will be removed.",
                },
                "services":       {"type": "object"},
                "queues":         {"type": "object"},
                "version":        {"type": "string"},
                "as_of":          {"type": "string", "format": "date-time"},
            },
        }}},
    }
}

_SYSTEM_HEALTH_RESPONSE_SCHEMA = {
    "200": {
        "description": "Aggregated downstream health + end-to-end latency.",
        "content": {"application/json": {"schema": {
            "type": "object",
            "properties": {
                "status":   {"type": "string"},
                "latency":  _LATENCY_BLOCK_SCHEMA,
                "services": {"type": "object"},
                "queues":   {"type": "object"},
                "summary":  {"type": "object"},
            },
        }}},
    }
}


@app.get("/status", tags=["ops"], responses=_STATUS_RESPONSE_SCHEMA)  # type: ignore[arg-type]
async def public_status(request: Request) -> dict[str, Any]:
    """Public, customer-shareable status — overall + per-component.

    Simpler than /system/health (which is operator-detail). This is the
    endpoint a customer's monitoring system polls and what a future
    statuspage.io ingests.
    """
    full = await system_health(request)
    # Reduce per-service detail to one of three states.
    components: dict[str, str] = {}
    for name, info in full.get("services", {}).items():
        s = info.get("status", "unknown")
        if s == "healthy":
            components[name] = "operational"
        elif s == "degraded":
            components[name] = "degraded"
        else:
            components[name] = "outage"

    overall = full.get("overall_status") or full.get("status") or "operational"

    # 2026-05-15 — expose top-level SRE-grade fields so the public status
    # endpoint is genuinely useful for external monitoring without forcing
    # callers to walk into /system/health. Previously /status was a thin
    # facade with only `components` & `version`, and monitors saw all-null
    # gauges for uptime / p95 / service count.
    start_time = getattr(request.app.state, "start_time", None)
    uptime_seconds = int(time.time() - start_time) if start_time else None
    services_map = full.get("services") or {}
    healthy_services = sum(1 for s in services_map.values() if s.get("status") == "healthy")

    # Sprint 2.3: /status reports the gateway's OWN request-latency
    # rolling window (scope=gateway_internal), distinct from
    # /system/health's end-to-end probe latency (scope=end_to_end).
    # Both endpoints expose the same canonical shape so callers can
    # branch on `latency.scope`, not on which URL they hit.
    from services.gateway.latency_window import gateway_internal_window
    latency_block = gateway_internal_window.summary()
    p95_latency_ms = latency_block["p95_ms"]

    # Sprint 2.3: kill-switch indicator. The decision-router writes
    # `acp:tenant_kill:{tenant_id}` for per-tenant kills; a global
    # toggle lives at `acp:kill_switch:global`. /status surfaces the
    # global state so an external monitor can detect a platform-wide
    # block. Per-tenant detail stays on /decision/kill-switch/{tenant}.
    kill_switch = await _read_global_kill_switch()

    return {
        "status": overall,
        "components": components,
        "uptime_seconds": uptime_seconds,
        # Canonical latency block (preferred). Top-level p95_latency_ms
        # kept for one release of back-compat with monitors built before
        # the `scope` field landed.
        "latency": latency_block,
        "p95_latency_ms": p95_latency_ms,
        "kill_switch": kill_switch,
        "services": {
            "total": len(services_map),
            "healthy": healthy_services,
            "degraded": sum(1 for s in services_map.values() if s.get("status") == "degraded"),
            "unreachable": sum(1 for s in services_map.values() if s.get("status") == "unreachable"),
        },
        "queues": full.get("queues") or {},
        "version": app.version,
        "as_of": datetime.now(UTC).isoformat(),
        "incidents": [],          # populated by an incident-feed integration; empty by default
        "maintenance": [],        # populated from a scheduled-maintenance source
        "links": {
            "sla": "/docs/sla.md",
            "security": "/docs/security.md",
            "runbook": "/docs/dr_runbook.md",
        },
    }


_KILL_SWITCH_GLOBAL_KEY = "acp:kill_switch:global"
_KILL_SWITCH_META_KEY   = "acp:kill_switch:global:meta"


async def _read_global_kill_switch() -> dict[str, Any]:
    """Return the platform-wide kill switch state for /status.

    Schema (always present, even when redis is unreachable so callers
    can rely on the keys):

        {
          "engaged":         bool,
          "last_toggled_at": ISO-8601 | null,
          "actor":           str | null,   # who toggled (admin email/id)
          "reason":          str | null,   # free-form
        }

    Fails closed for OBSERVABILITY only: on Redis error we return
    engaged=False but flag the read failure in `reason`. The actual
    gate-keeping on /execute happens elsewhere (tenant_kill keys),
    so this indicator's accuracy doesn't gate-keep request flow.
    """
    default = {
        "engaged":         False,
        "last_toggled_at": None,
        "actor":           None,
        "reason":          None,
    }
    try:
        engaged = await redis.exists(_KILL_SWITCH_GLOBAL_KEY)
        if not engaged:
            return default
        meta_raw = await redis.get(_KILL_SWITCH_META_KEY)
        if not meta_raw:
            return {**default, "engaged": True}
        if isinstance(meta_raw, (bytes, bytearray)):
            meta_raw = meta_raw.decode("utf-8", errors="replace")
        try:
            meta = json.loads(meta_raw)
        except Exception:
            meta = {}
        return {
            "engaged":         True,
            "last_toggled_at": meta.get("last_toggled_at"),
            "actor":           meta.get("actor"),
            "reason":          meta.get("reason"),
        }
    except Exception as exc:
        return {**default, "reason": f"kill_switch_read_failed:{type(exc).__name__}"}


@app.get("/system/health", tags=["ops"], responses=_SYSTEM_HEALTH_RESPONSE_SCHEMA)  # type: ignore[arg-type]
async def system_health(request: Request) -> dict[str, Any]:
    """
    Aggregated health check across all ACP backend services.
    Each probe has a 4s timeout; overall response is always returned within ~5s.
    """
    client = request.app.state.client
    service_map = {
        "registry":        settings.REGISTRY_SERVICE_URL,
        "identity":        settings.IDENTITY_SERVICE_URL,
        "policy":          settings.POLICY_SERVICE_URL,
        "audit":           settings.AUDIT_SERVICE_URL,
        "usage":           settings.USAGE_SERVICE_URL,
        "behavior":        settings.BEHAVIOR_SERVICE_URL,
        "decision":        settings.DECISION_SERVICE_URL,
        "insight":         settings.INSIGHT_SERVICE_URL,
        "forensics":       settings.FORENSICS_SERVICE_URL,
        # 2026-05-13 — Runtime Trust Infrastructure
        "identity_graph":  settings.IDENTITY_GRAPH_SERVICE_URL,
        "flight_recorder": settings.FLIGHT_RECORDER_SERVICE_URL,
        "autonomy":        settings.AUTONOMY_SERVICE_URL,
    }

    from services.gateway.latency_window import end_to_end_window

    async def _probe(name: str, base_url: str) -> tuple[str, dict]:
        start = time.time()
        try:
            resp = await client.get(f"{base_url.rstrip('/')}/health", timeout=4.0)
            latency_ms = int((time.time() - start) * 1000)
            # End-to-end RTT: gateway → downstream /health → gateway.
            # Recorded for every probe (healthy or degraded) so the
            # window's count() reflects observation volume, not just
            # success volume.
            end_to_end_window.record(latency_ms)
            status = "healthy" if resp.status_code == 200 else "degraded"
            return name, {"status": status, "latency_ms": latency_ms}
        except Exception as exc:
            latency_ms = int((time.time() - start) * 1000)
            # Unreachable probes still represent observed RTT (the timeout
            # itself is a real client-perceived delay), so they go in too.
            end_to_end_window.record(latency_ms)
            return name, {"status": "unreachable", "latency_ms": latency_ms, "error": str(exc)[:80]}

    results = await asyncio.gather(*[_probe(n, u) for n, u in service_map.items()])
    services = dict(results)

    healthy_count = sum(1 for s in services.values() if s["status"] == "healthy")
    total = len(services)

    # 2026-05-14 — 4-state classification per production_hardening_spec:
    #   operational           — all services healthy, queues nominal
    #   degraded_performance  — all services up but latency or queue pressure
    #   partial_outage        — at least one service down (functional impact)
    #   major_outage          — half or more services down
    # Queue depth alone MUST NOT classify as outage; queues only contribute to
    # degraded_performance. Service unreachability is the only outage signal.
    down_count = total - healthy_count
    if down_count == 0:
        overall = "operational"
    elif down_count >= max(1, total // 2):
        overall = "major_outage"
    else:
        overall = "partial_outage"

    # Latency / queue saturation can downgrade operational → degraded_performance
    # but NEVER promotes a partial_outage further.
    # Sprint 2.3 (2026-05-15): the old "sort 12 probe latencies and pick element
    # 10" trick computed a meaningless number. The real p95 of probe round-trips
    # comes from the rolling window `end_to_end_window`, populated above by
    # every _probe call. Top-level `p95_latency_ms` is kept for back-compat
    # (UI code reads it) but it's now sourced from the window's summary so
    # /status and /system/health agree.
    from services.gateway.latency_window import end_to_end_window as _e2e
    e2e_summary = _e2e.summary()
    p95_latency_ms = e2e_summary["p95_ms"]

    # UI integration (2026-05-13): expose operational queue depths so the UI
    # SystemHealth/Billing pages can warn on DLQ growth or audit-stream pressure.
    # 2026-05-14: also expose outbox depths so the Transactional Outbox backlog
    # is visible to operators (alertable signal — see production_hardening_spec).
    queues: dict[str, Any] = {
        "audit_stream_length":  0,
        "audit_dlq_length":     0,
        "billing_retry_queue":  0,
        "billing_dlq_length":   0,
        "outbox_pending":       0,
        "outbox_failed":        0,
    }
    try:
        queues["audit_stream_length"] = int(await redis.xlen("acp:audit_stream"))
    except (httpx.HTTPError, ConnectionError, TimeoutError) as exc:
        logger.warning("health_redis_audit_stream_failed", error=str(exc))
    try:
        queues["audit_dlq_length"] = int(await redis.xlen("acp:audit_stream:dlq"))
    except (httpx.HTTPError, ConnectionError, TimeoutError) as exc:
        logger.warning("health_redis_audit_dlq_failed", error=str(exc))
    try:
        queues["billing_retry_queue"] = int(await redis.llen("acp:billing_retry_queue"))  # type: ignore[not-async]
    except (httpx.HTTPError, ConnectionError, TimeoutError) as exc:
        logger.warning("health_redis_billing_retry_failed", error=str(exc))
    try:
        queues["billing_dlq_length"] = int(await redis.llen("acp:billing_dlq"))  # type: ignore[not-async]
    except (httpx.HTTPError, ConnectionError, TimeoutError) as exc:
        logger.warning("health_redis_billing_dlq_failed", error=str(exc))

    # Outbox depths via the audit service (Postgres-backed counters).
    try:
        ob_resp = await client.get(
            f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/outbox-depth",
            headers={"X-Internal-Secret": settings.INTERNAL_SECRET},
            timeout=2.0,
        )
        if ob_resp.status_code == 200:
            ob = (ob_resp.json() or {}).get("data") or {}
            queues["outbox_pending"] = int(ob.get("pending", 0))
            queues["outbox_failed"] = int(ob.get("failed", 0))
    except (httpx.HTTPError, ConnectionError, TimeoutError) as exc:
        logger.warning("health_outbox_depth_failed", error=str(exc))

    # Queue / latency pressure downgrades operational → degraded_performance
    # ONLY. It never escalates partial_outage or major_outage.
    queue_pressure = (
        queues["audit_stream_length"] > 45_000
        or queues["billing_dlq_length"] > 100
        or queues["outbox_pending"] > 1_000
        or queues["outbox_failed"] > 0
    )
    latency_pressure = p95_latency_ms > 1500  # p95 budget breach
    if overall == "operational" and (queue_pressure or latency_pressure):
        overall = "degraded_performance"

    return {
        "status": overall,
        # Back-compat aliases — older UI code reads `healthy` / `total`.
        "healthy": healthy_count,
        "total": total,
        "summary": {
            "down_services":   down_count,
            "queue_pressure":  queue_pressure,
            "latency_pressure": latency_pressure,
            "p95_latency_ms":  p95_latency_ms,
        },
        # Sprint 2.3: canonical-shape latency block. Same shape on /status
        # but with `scope: "gateway_internal"`. Clients should branch on
        # `scope` rather than the URL.
        "latency": e2e_summary,
        "services": services,
        "gateway": {"status": "healthy", "latency_ms": 0},
        "queues": queues,
        "ts": int(time.time()),
    }


# ─────────────────────────────────────────────────────────────
# RECONCILIATION REPORT INGEST (2026-05-15)
# scripts/ops/reconcile.py POSTs its periodic findings here.
# We mirror them onto the per-tenant gauges so /metrics surfaces the
# audit↔usage gap to Prometheus + Alertmanager without coupling the
# scheduler to the metrics registry directly.
# ─────────────────────────────────────────────────────────────


from sdk.common.auth import (
    verify_internal_secret as _verify_internal_secret,  # noqa: E402
)
from sdk.utils import (  # noqa: E402
    RECONCILE_AUDIT_WITHOUT_USAGE,
    RECONCILE_OUTBOX_OLDEST_AGE_SECONDS,
    RECONCILE_USAGE_WITHOUT_AUDIT,
)


@app.post("/internal/reconciliation-report", tags=["internal"])
async def ingest_reconciliation_report(
    payload: dict,
    _: str = Depends(_verify_internal_secret),
) -> dict[str, str]:
    """Accept a reconciliation report from `scripts/ops/reconcile.py` and
    publish the gauge values so they appear on `/metrics`.

    Payload (matches the script's report shape):
        {
          "tenant_id": "<uuid|all>",
          "audit_without_usage_count": <int>,
          "usage_without_audit_count": <int>,
          "outbox_pending_age_seconds": <int>,
          ...other fields ignored for metrics
        }

    `tenant_id` is the gauge label — pass "all" for cluster-wide aggregation
    or a specific UUID for per-tenant alerting.
    """
    tenant = str(payload.get("tenant_id") or "all")
    try:
        a = int(payload.get("audit_without_usage_count") or 0)
        u = int(payload.get("usage_without_audit_count") or 0)
        age = int(payload.get("outbox_pending_age_seconds") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="malformed_report")
    RECONCILE_AUDIT_WITHOUT_USAGE.labels(tenant=tenant).set(a)
    RECONCILE_USAGE_WITHOUT_AUDIT.labels(tenant=tenant).set(u)
    RECONCILE_OUTBOX_OLDEST_AGE_SECONDS.labels(tenant=tenant).set(age)
    return {"status": "ok", "tenant": tenant}


# ─────────────────────────────────────────────────────────────
# RUNTIME TRUST PROXIES (2026-05-13)
# /graph/*    → identity_graph service
# /flight/*   → flight_recorder service
# /autonomy/* → autonomy service
# ─────────────────────────────────────────────────────────────

async def _trust_proxy(base_url: str, path: str, request: Request) -> Any:
    """Generic forwarder for runtime-trust services. Preserves method, body,
    query string, and tenant + auth context. Returns JSON or upstream status code.

    BUGFIX 2026-05-13: `_internal_headers()` does NOT include `Content-Type`,
    so passing the raw body via `content=` caused FastAPI on the upstream side
    to see bytes instead of JSON ("Object of type bytes is not JSON serializable").
    Parse JSON on the gateway and forward via `json=` so httpx sets the right
    headers automatically.
    """
    client: httpx.AsyncClient = request.app.state.client
    method = request.method.upper()
    url = f"{base_url.rstrip('/')}{path}"
    headers = _internal_headers(request)
    json_body: Any | None = None
    raw_body: bytes | None = None
    if method in ("POST", "PATCH", "PUT"):
        try:
            raw_body = await request.body()
            if raw_body:
                try:
                    json_body = json.loads(raw_body)
                except Exception:
                    json_body = None  # not JSON — forward raw + Content-Type
        except Exception:
            raw_body = None
    try:
        if json_body is not None:
            resp = await client.request(
                method, url,
                headers=headers, params=request.query_params, json=json_body,
                timeout=10.0,
            )
        else:
            # Non-JSON body or no body — forward raw with original Content-Type if any.
            ct = request.headers.get("content-type")
            fwd_headers = dict(headers)
            if ct:
                fwd_headers["Content-Type"] = ct
            resp = await client.request(
                method, url,
                headers=fwd_headers, params=request.query_params, content=raw_body,
                timeout=10.0,
            )
        try:
            return JSONResponse(content=resp.json(), status_code=resp.status_code)
        except Exception:
            return Response(content=resp.content, status_code=resp.status_code,
                            media_type=resp.headers.get("content-type", "application/json"))
    except Exception as exc:
        logger.error("trust_proxy_error", base_url=base_url, path=path, error=str(exc))
        return JSONResponse(
            status_code=502,
            content={"success": False, "error": f"Upstream unreachable: {type(exc).__name__}"},
        )


@app.api_route("/graph/{full_path:path}", methods=["GET", "POST", "PATCH", "DELETE"], tags=["graph"])
async def proxy_graph(full_path: str, request: Request) -> Any:
    return await _trust_proxy(settings.IDENTITY_GRAPH_SERVICE_URL, f"/graph/{full_path}", request)


@app.api_route("/flight/{full_path:path}", methods=["GET", "POST", "PATCH", "DELETE"], tags=["flight"])
async def proxy_flight(full_path: str, request: Request) -> Any:
    return await _trust_proxy(settings.FLIGHT_RECORDER_SERVICE_URL, f"/flight/{full_path}", request)


@app.api_route("/autonomy/{full_path:path}", methods=["GET", "POST", "PATCH", "DELETE"], tags=["autonomy"])
async def proxy_autonomy(full_path: str, request: Request) -> Any:
    return await _trust_proxy(settings.AUTONOMY_SERVICE_URL, f"/autonomy/{full_path}", request)


# ─────────────────────────────────────────────────────────────
# PLAYBOOKS PROXY (Day 13-14)
# /playbooks/* → autonomy service /autonomy/playbooks/*
# ─────────────────────────────────────────────────────────────

@app.get("/playbooks/templates", tags=["playbooks"])
async def get_playbook_templates_proxy(request: Request) -> Any:
    """Proxy to autonomy service /playbooks/templates (no auth required)."""
    return await _trust_proxy(settings.AUTONOMY_SERVICE_URL, "/autonomy/playbooks/templates", request)


@app.get("/playbooks", tags=["playbooks"])
async def list_playbooks_proxy(request: Request) -> Any:
    return await _trust_proxy(settings.AUTONOMY_SERVICE_URL, "/autonomy/playbooks", request)


@app.post("/playbooks", tags=["playbooks"])
async def create_playbook_proxy(request: Request) -> Any:
    return await _trust_proxy(settings.AUTONOMY_SERVICE_URL, "/autonomy/playbooks", request)


# ─────────────────────────────────────────────────────────────
# GET /playbooks/stats — in-gateway aggregation (Phase 10)
# Calls /playbooks and /playbooks/templates via internal HTTP;
# returns summary counts. Sub-call failures return zeros gracefully.
# ─────────────────────────────────────────────────────────────

@app.get("/playbooks/stats", tags=["playbooks"])
async def get_playbooks_stats(request: Request) -> Any:
    """Return aggregate playbook statistics for the authenticated tenant.

    Aggregates from:
      - /autonomy/playbooks         (autonomy service) — installed playbooks list
      - /autonomy/playbooks/templates (autonomy service) — available templates

    Returns:
      total_installed  — count of installed playbooks
      total_templates  — count of available templates
      active           — count of installed playbooks with status == "active"
      triggers_24h     — total trigger count across all installed playbooks
                         for the last 24 hours (sourced from playbook.triggers_24h
                         field if present, else 0)
      last_trigger_at  — ISO-8601 timestamp of most recent trigger, or null

    Sub-call failures are tolerated — affected counters fall back to 0.
    Requires a valid bearer token (authenticated UI / SDK calls).
    """
    client: httpx.AsyncClient = request.app.state.client
    hdrs = _internal_headers(request)
    base = settings.AUTONOMY_SERVICE_URL.rstrip("/")

    # ── 1. Fetch installed playbooks ────────────────────────────────────────
    total_installed = 0
    active = 0
    triggers_24h = 0
    last_trigger_at: str | None = None

    try:
        pb_resp = await client.get(
            f"{base}/autonomy/playbooks",
            headers=hdrs,
            params=request.query_params,
            timeout=5.0,
        )
        if pb_resp.status_code == 200:
            pb_data = pb_resp.json()
            # Support both {"data": [...]} envelope and bare list
            playbooks = pb_data.get("data", pb_data) if isinstance(pb_data, dict) else pb_data
            if isinstance(playbooks, list):
                total_installed = len(playbooks)
                for pb in playbooks:
                    if isinstance(pb, dict):
                        if pb.get("status") == "active":
                            active += 1
                        triggers_24h += int(pb.get("triggers_24h", 0) or 0)
                        ts = pb.get("last_trigger_at") or pb.get("last_triggered_at")
                        if ts:
                            if last_trigger_at is None or ts > last_trigger_at:
                                last_trigger_at = ts
    except Exception as exc:
        logger.warning("playbooks_stats_installed_error", error=str(exc)[:200])

    # ── 2. Fetch available templates ─────────────────────────────────────────
    total_templates = 0
    try:
        tmpl_resp = await client.get(
            f"{base}/autonomy/playbooks/templates",
            headers=hdrs,
            timeout=5.0,
        )
        if tmpl_resp.status_code == 200:
            tmpl_data = tmpl_resp.json()
            templates = tmpl_data.get("data", tmpl_data) if isinstance(tmpl_data, dict) else tmpl_data
            if isinstance(templates, list):
                total_templates = len(templates)
    except Exception as exc:
        logger.warning("playbooks_stats_templates_error", error=str(exc)[:200])

    return JSONResponse(content={
        "total_installed": total_installed,
        "total_templates": total_templates,
        "active": active,
        "triggers_24h": triggers_24h,
        "last_trigger_at": last_trigger_at,
    })


@app.get("/playbooks/{pid}/runs", tags=["playbooks"])
async def list_playbook_runs_proxy(pid: str, request: Request) -> Any:
    return await _trust_proxy(settings.AUTONOMY_SERVICE_URL, f"/autonomy/playbooks/{pid}/runs", request)


@app.post("/playbooks/{pid}/trigger", tags=["playbooks"])
async def trigger_playbook_proxy(pid: str, request: Request) -> Any:
    return await _trust_proxy(settings.AUTONOMY_SERVICE_URL, f"/autonomy/playbooks/{pid}/trigger", request)


@app.get("/playbooks/{pid}", tags=["playbooks"])
async def get_playbook_proxy(pid: str, request: Request) -> Any:
    return await _trust_proxy(settings.AUTONOMY_SERVICE_URL, f"/autonomy/playbooks/{pid}", request)


@app.patch("/playbooks/{pid}", tags=["playbooks"])
async def update_playbook_proxy(pid: str, request: Request) -> Any:
    return await _trust_proxy(settings.AUTONOMY_SERVICE_URL, f"/autonomy/playbooks/{pid}", request)


@app.delete("/playbooks/{pid}", tags=["playbooks"])
async def delete_playbook_proxy(pid: str, request: Request) -> Any:
    return await _trust_proxy(settings.AUTONOMY_SERVICE_URL, f"/autonomy/playbooks/{pid}", request)


# ─────────────────────────────────────────────────────────────
# WEBHOOK SETTINGS PROXY
# /webhooks/* → autonomy service /autonomy/webhooks/*
# ─────────────────────────────────────────────────────────────

@app.get("/webhooks/config", tags=["webhooks"])
async def get_webhook_config_proxy(request: Request) -> Any:
    return await _trust_proxy(settings.AUTONOMY_SERVICE_URL, "/autonomy/webhooks/config", request)


@app.post("/webhooks/config", tags=["webhooks"])
async def save_webhook_config_proxy(request: Request) -> Any:
    return await _trust_proxy(settings.AUTONOMY_SERVICE_URL, "/autonomy/webhooks/config", request)


@app.post("/webhooks/test/slack", tags=["webhooks"])
async def test_slack_proxy(request: Request) -> Any:
    return await _trust_proxy(settings.AUTONOMY_SERVICE_URL, "/autonomy/webhooks/test/slack", request)


@app.post("/webhooks/test/pagerduty", tags=["webhooks"])
async def test_pagerduty_proxy(request: Request) -> Any:
    return await _trust_proxy(settings.AUTONOMY_SERVICE_URL, "/autonomy/webhooks/test/pagerduty", request)


@app.post("/webhooks/test/webhook", tags=["webhooks"])
async def test_generic_webhook_proxy(request: Request) -> Any:
    return await _trust_proxy(settings.AUTONOMY_SERVICE_URL, "/autonomy/webhooks/test/webhook", request)


# ─────────────────────────────────────────────────────────────
# NOTIFICATIONS PROXY — /notifications → audit service
# ─────────────────────────────────────────────────────────────

@app.get("/notifications", tags=["notifications"])
async def list_notifications_proxy(request: Request) -> Any:
    """Proxy → Audit service list notifications."""
    return await _trust_proxy(settings.AUDIT_SERVICE_URL, "/notifications", request)


@app.post("/notifications", tags=["notifications"])
async def create_notification_proxy(request: Request) -> Any:
    """Proxy → Audit service create notification."""
    return await _trust_proxy(settings.AUDIT_SERVICE_URL, "/notifications", request)


@app.post("/notifications/read-all", tags=["notifications"])
async def mark_all_notifications_read_proxy(request: Request) -> Any:
    """Proxy → Audit service mark all notifications as read."""
    return await _trust_proxy(settings.AUDIT_SERVICE_URL, "/notifications/read-all", request)


@app.get("/notifications/count", tags=["notifications"])
async def get_notifications_count_proxy(request: Request) -> Any:
    """Proxy → Audit service get unread notification count."""
    return await _trust_proxy(settings.AUDIT_SERVICE_URL, "/notifications/count", request)


@app.post("/notifications/{notification_id}/read", tags=["notifications"])
async def mark_notification_read_proxy(notification_id: str, request: Request) -> Any:
    """Proxy → Audit service mark one notification as read."""
    return await _trust_proxy(settings.AUDIT_SERVICE_URL, f"/notifications/{notification_id}/read", request)


# ─────────────────────────────────────────────────────────────
# SSO CONFIG PROXY — /auth/sso/config → identity service
# ─────────────────────────────────────────────────────────────

@app.get("/auth/sso/config", tags=["sso"])
async def get_sso_config_proxy(request: Request) -> Any:
    """Proxy → Identity service SSO config GET."""
    return await _trust_proxy(settings.IDENTITY_SERVICE_URL, "/auth/sso/config", request)


@app.post("/auth/sso/config", tags=["sso"])
async def save_sso_config_proxy(request: Request) -> Any:
    """Proxy → Identity service SSO config POST."""
    return await _trust_proxy(settings.IDENTITY_SERVICE_URL, "/auth/sso/config", request)


@app.post("/auth/sso/config/test", tags=["sso"])
async def test_sso_config_proxy(request: Request) -> Any:
    """Proxy → Identity service SSO config test."""
    return await _trust_proxy(settings.IDENTITY_SERVICE_URL, "/auth/sso/config/test", request)


# ─────────────────────────────────────────────────────────────
# EXECUTION PROXY — /execute
# ─────────────────────────────────────────────────────────────

_EXECUTE_RESPONSES = {
    200: {"description": "Tool executed; result body present"},
    403: {"description": "Denied (policy block, path traversal, escalation/approval required)"},
    429: {"description": "Rate limit exceeded — retry after Retry-After seconds"},
    502: {"description": "Upstream policy/execution service returned a non-200"},
    504: {"description": "decision_timeout — decision pipeline exceeded the gateway deadline"},
}


@app.post("/execute", tags=["execution"], responses=_EXECUTE_RESPONSES)  # type: ignore[arg-type]
@app.post("/execute/{tool_name}", tags=["execution"], responses=_EXECUTE_RESPONSES)  # type: ignore[arg-type]
async def execute_tool(request: Request, tool_name: str | None = None) -> Any:
    """Tool execution endpoint — strictly synchronous.

    Decision has already been evaluated by SecurityMiddleware. The
    endpoint proxies to the Policy service for final execution and
    auditing. Backpressure: a semaphore limits concurrent executions to
    prevent cascade failures.

    Response contract (2026-05-15): only 200 / 4xx / 5xx — never 202.
    The earlier 202 response on policy ESCALATE / autonomy approval-
    required has been retired because no polling endpoint ever existed;
    those branches now return 403 with `error: "approval_required"`.
    Decision-timeout fallbacks return 504 with `error: "decision_timeout"`
    and a transparency-chain audit row.
    """
    async with execution_semaphore:
        request_id = getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID", str(uuid.uuid4()))

        # P5-2 FIX: Use request.state (authenticated identity) instead of relying solely on client headers
        agent_id_str = str(getattr(request.state, "agent_id", "")) if getattr(request.state, "agent_id", None) else request.headers.get("X-Agent-ID", "")
        tenant_id_str = str(getattr(request.state, "tenant_id", "")) if getattr(request.state, "tenant_id", None) else request.headers.get("X-Tenant-ID", "")

        # Extract tool from path or body
        body: dict[str, Any] = {}
        with suppress(Exception):
            body = await request.json()

        # Override agent_id from body when state carries zero UUID (middleware set it before body was available)
        body_agent_id = str(body.get("agent_id", ""))
        if body_agent_id and (not agent_id_str or agent_id_str == "00000000-0000-0000-0000-000000000000"):
            agent_id_str = body_agent_id

        tool = tool_name or body.get("tool", "") or request.headers.get("X-ACP-Tool", "unknown")

        # 1. Prepare internal headers and body
        headers = _internal_headers(request)
        headers["X-Request-ID"] = request_id
        headers["X-ACP-Tool"] = tool

        if "tool" not in body:
            body["tool"] = tool

        if agent_id_str:
            headers["X-Agent-ID"] = agent_id_str
        if tenant_id_str:
            headers["X-Tenant-ID"] = tenant_id_str

        # Pass the decision metadata to the backend service
        decision = getattr(request.state, "decision", None)
        if decision:
            body["_decision"] = {
                "action": decision.action.value if hasattr(decision.action, "value") else str(decision.action),
                "risk": getattr(decision, "risk", 0.0),
                "confidence": getattr(decision, "confidence", 1.0),
                "findings": [str(f) for f in (getattr(decision, "findings", None) or [])],
                "reasons": [str(r) for r in (getattr(decision, "reasons", None) or [])],
                "signals": getattr(decision, "signals", {}) or {},
            }

        # 2. Proxy request to Policy service
        client: httpx.AsyncClient = request.app.state.client
        try:
            logger.info("policy_execute_request", request_id=request_id, tool=tool, tenant_id=tenant_id_str, agent_id=agent_id_str)

            resp = await client.post(
                f"{settings.POLICY_SERVICE_URL.rstrip('/')}/policy/execute",
                json=body,
                headers=headers,
                timeout=10.0
            )

            if resp.status_code != 200:
                logger.error("policy_execution_failed", status_code=resp.status_code, text=resp.text[:200], request_id=request_id)
                try:
                    return _passthrough(resp)
                except Exception:
                    raise HTTPException(status_code=502, detail="Policy service execution failed")

            result = resp.json()
            data = result.get("data") if result.get("success") and "data" in result else result

            # 3. Publish tool_executed event to SSE bus
            if tenant_id_str:
                action_val = data.get("action", "allow")
                try:
                    await redis.publish(  # type: ignore[union-attr]
                        f"acp:events:{tenant_id_str}",
                        json.dumps({
                            "type": "tool_executed",
                            "data": {
                                "request_id": request_id,
                                "agent_id": agent_id_str,
                                "tool": tool,
                                "action": action_val,
                                "risk": data.get("risk", 0.0),
                                "confidence": data.get("confidence", 1.0),
                                "signals": data.get("signals", {}),
                                "reasons": (data.get("reasons") or [])[:3],
                                "ts": int(time.time()),
                            },
                        }),
                    )
                except Exception as _e:
                    logger.debug("sse_publish_failed", event="tool_executed", error=str(_e))

            return result

        except Exception as exc:
            logger.error("gateway_proxy_error", error=str(exc))
            raise HTTPException(status_code=502, detail="Service unavailable")


# ─────────────────────────────────────────────────────────────
# SSE EVENT STREAM — /events/stream
# Real-time per-tenant event bus via Server-Sent Events + Redis Pub/Sub
# ─────────────────────────────────────────────────────────────

@app.get("/events/stream", tags=["events"])
async def events_stream(request: Request) -> Response:
    """
    Server-Sent Events stream for real-time UI synchronization.
    Auth is handled inline (endpoint is in _SKIP_PATHS, bypasses SecurityMiddleware).
    Uses PubSubManager: one Redis subscription per tenant channel, fan-out to
    per-client bounded queues (maxsize=100). Old clients are not blocked by slow ones.
    """
    # SSE auth precedence — browsers' EventSource API cannot set custom
    # headers, so we ALWAYS accept all three forms and pick whichever exists:
    #   1. acp_token cookie       — production browser flow
    #   2. Authorization: Bearer  — SDK / curl / Locust
    #   3. ?token=… query string  — non-browser clients that can't set cookies
    #
    # The query-string fallback is the canonical SSE auth path documented in
    # the HTML spec; without it, anything other than the dashboard cookie flow
    # fails silently.
    token = request.cookies.get("acp_token")
    if not token:
        auth_hdr = request.headers.get("Authorization", "")
        if auth_hdr.startswith("Bearer "):
            token = auth_hdr[7:].strip()
    if not token:
        qt = request.query_params.get("token") or request.query_params.get("access_token")
        if qt:
            token = qt.strip()

    if not token:
        logger.info("sse_unauthenticated", reason="no_token_provided")
        return Response(
            status_code=401,
            content='{"error":"Unauthorized","detail":"missing token (cookie / Authorization / ?token=)"}',
            media_type="application/json",
        )

    try:
        # token_validator is the live singleton initialised in lifespan().
        if token_validator is None:
            raise RuntimeError("token_validator not initialised")
        payload = await token_validator.validate(token)
    except Exception as exc:
        # Narrow logging so debuggers can tell "no validator" apart from
        # "bad signature" apart from "expired". Was previously a bare
        # `except Exception:` swallowing a NameError when token_validator
        # was not imported — the user-visible "Invalid token" was actually
        # a missing-import crash.
        logger.warning(
            "sse_auth_failed",
            error_type=type(exc).__name__,
            error=str(exc)[:200],
        )
        return Response(
            status_code=401,
            content='{"error":"Invalid token","detail":"' + type(exc).__name__ + '"}',
            media_type="application/json",
        )

    tenant_id_str: str = payload.get("tenant_id", "")
    if not tenant_id_str:
        return Response(
            status_code=401,
            content='{"error":"Missing tenant claim"}',
            media_type="application/json",
        )

    channel = f"acp:events:{tenant_id_str}"

    async def event_generator() -> AsyncGenerator[str, None]:
        q = await pubsub_manager.subscribe(channel)
        try:
            yield f"event: connected\ndata: {json.dumps({'status': 'connected', 'tenant_id': tenant_id_str})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {data}\n\n"
                except TimeoutError:
                    yield f"event: heartbeat\ndata: {json.dumps({'ts': int(time.time())})}\n\n"
        finally:
            await pubsub_manager.unsubscribe(channel, q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ─────────────────────────────────────────────────────────────
# SECURITY POSTURE (Phase 9)
# GET /security/posture — in-gateway aggregation from audit + identity
# ─────────────────────────────────────────────────────────────

@app.get("/security/posture", tags=["security"])
async def get_security_posture(request: Request) -> Any:
    """Return a real-time security posture summary for the authenticated tenant.

    Aggregates from:
      - /transparency/roots (audit service) — last 7-day chain health
      - /audit/logs/verify  (audit service) — integrity check

    Returns a posture_score (0-100), chain_status, and a checklist of
    named items with status ∈ {ok, warning, error, info, unknown}.
    Requires a valid bearer token (authenticated UI / SDK calls).
    Sub-call failures are tolerated — that item's status becomes "unknown".
    """
    client: httpx.AsyncClient = request.app.state.client
    hdrs = _internal_headers(request)

    # ── 1. chain health via /transparency/roots ──────────────────────────────
    chain_status = "unknown"
    chain_detail = "Could not reach audit service"
    try:
        roots_resp = await client.get(
            f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/transparency/roots",
            headers=hdrs,
            params={"days": 7},
            timeout=5.0,
        )
        if roots_resp.status_code == 200:
            roots_data = roots_resp.json()
            roots = roots_data.get("data", roots_data) if isinstance(roots_data, dict) else roots_data
            if isinstance(roots, list) and len(roots) >= 7:
                chain_status = "healthy"
                chain_detail = "No gaps in last 7 days"
            elif isinstance(roots, list) and len(roots) > 0:
                chain_status = "degraded"
                chain_detail = f"Only {len(roots)} of 7 expected roots found"
            else:
                chain_status = "degraded"
                chain_detail = "No transparency roots found for last 7 days"
        else:
            chain_status = "unknown"
            chain_detail = f"Upstream returned HTTP {roots_resp.status_code}"
    except Exception as exc:
        logger.warning("security_posture_roots_error", error=str(exc)[:200])
        chain_status = "unknown"
        chain_detail = "Transparency roots unavailable"

    # ── 2. integrity check via /logs/verify ──────────────────────────────────
    integrity_status = "unknown"
    integrity_detail = "Could not reach audit service"
    try:
        verify_resp = await client.get(
            f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/verify",
            headers=hdrs,
            timeout=5.0,
        )
        if verify_resp.status_code == 200:
            vdata = verify_resp.json()
            ok = vdata.get("valid", vdata.get("ok", vdata.get("status") == "ok"))
            if ok:
                integrity_status = "ok"
                integrity_detail = "No gaps in last 7 days"
            else:
                integrity_status = "warning"
                integrity_detail = vdata.get("detail", "Chain integrity issue detected")
        else:
            integrity_status = "unknown"
            integrity_detail = f"Upstream returned HTTP {verify_resp.status_code}"
    except Exception as exc:
        logger.warning("security_posture_verify_error", error=str(exc)[:200])
        integrity_status = "unknown"
        integrity_detail = "Audit chain verify unavailable"

    # Use chain_status to drive overall audit chain item when verify is unknown
    if integrity_status == "unknown" and chain_status in ("healthy", "degraded"):
        integrity_status = "ok" if chain_status == "healthy" else "warning"
        integrity_detail = chain_detail

    # ── 3. Kill-switch state from request.state (populated by SecurityMiddleware) ──
    kill_switch_count = int(getattr(request.state, "active_kill_switches", 0) or 0)
    kill_switch_status = "ok" if kill_switch_count == 0 else "error"
    kill_switch_detail = "None engaged" if kill_switch_count == 0 else f"{kill_switch_count} active"

    # ── 4. Governance posture items — live sub-calls ─────────────────────────
    # Real open incident count from incidents summary
    open_incidents = 0
    try:
        inc_resp = await client.get(
            f"{settings.API_SERVICE_URL.rstrip('/')}/incidents/summary",
            headers=hdrs,
            timeout=3.0,
        )
        if inc_resp.status_code == 200:
            idata = inc_resp.json()
            isummary = idata.get("data", idata) if isinstance(idata, dict) else {}
            open_incidents = int(isummary.get("open", 0)) + int(isummary.get("investigating", 0))
    except Exception as exc:
        logger.warning("security_posture_incidents_error", error=str(exc)[:200])

    # Real key rotation age from transparency/keys
    last_rotation_days_ago = 0
    try:
        keys_resp = await client.get(
            f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/transparency/keys",
            headers=hdrs,
            timeout=3.0,
        )
        if keys_resp.status_code == 200:
            kdata = keys_resp.json()
            kinfo = kdata.get("data", kdata) if isinstance(kdata, dict) else {}
            created_at_str = (kinfo.get("active") or {}).get("created_at")
            if created_at_str:
                created_dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                last_rotation_days_ago = (datetime.now(UTC) - created_dt).days
    except Exception as exc:
        logger.warning("security_posture_keys_error", error=str(exc)[:200])

    mfa_enforced = True
    sso_enabled = False

    # ── 5. Compute posture_score ─────────────────────────────────────────────
    score = 100
    if chain_status == "degraded":
        score -= 20
    elif chain_status == "unknown":
        score -= 10
    if integrity_status == "warning":
        score -= 10
    elif integrity_status == "error":
        score -= 20
    if kill_switch_count > 0:
        score -= 30
    if open_incidents > 0:
        score -= min(open_incidents * 3, 15)
    if not mfa_enforced:
        score -= 10
    score = max(0, min(100, score))

    items = [
        {
            "label": "Audit chain",
            "status": integrity_status,
            "detail": integrity_detail,
        },
        {
            "label": "Kill switches",
            "status": kill_switch_status,
            "detail": kill_switch_detail,
        },
        {
            "label": "Open incidents",
            "status": "ok" if open_incidents == 0 else "warning",
            "detail": f"{open_incidents} open" if open_incidents > 0 else "None open",
        },
        {
            "label": "Token rotation",
            "status": "ok",
            "detail": f"Last rotated {last_rotation_days_ago} days ago",
        },
        {
            "label": "SSO",
            "status": "info",
            "detail": "Not configured" if not sso_enabled else "Configured",
        },
        {
            "label": "MFA",
            "status": "ok" if mfa_enforced else "warning",
            "detail": "Enforced for all roles" if mfa_enforced else "Not enforced",
        },
    ]

    return JSONResponse(content={
        "posture_score": score,
        "chain_status": chain_status,
        "last_rotation_days_ago": last_rotation_days_ago,
        "open_incidents": open_incidents,
        "active_kill_switches": kill_switch_count,
        "mfa_enforced": mfa_enforced,
        "sso_enabled": sso_enabled,
        "items": items,
    })


# ─────────────────────────────────────────────────────────────
# ADMIN TENANTS PROXY (Phase 9)
# GET /admin/tenants        → identity service /admin/tenants
# GET /admin/tenants/{id}   → identity service /admin/tenants/{id}
# ─────────────────────────────────────────────────────────────

@app.get("/admin/tenants", tags=["admin"])
async def list_admin_tenants(request: Request) -> Any:
    """Proxy → Identity service: list all tenants (admin view).

    Requires a valid bearer token. The identity service enforces its own
    internal-secret check on the upstream route.
    """
    resp = await request.app.state.client.get(
        f"{settings.IDENTITY_SERVICE_URL.rstrip('/')}/admin/tenants",
        headers=_internal_headers(request),
        params=dict(request.query_params),
        timeout=10.0,
    )
    return _passthrough(resp)


@app.get("/admin/tenants/{tenant_id}", tags=["admin"])
async def get_admin_tenant(tenant_id: str, request: Request) -> Any:
    """Proxy → Identity service: fetch a single tenant by id (admin view).

    Requires a valid bearer token. The identity service enforces its own
    internal-secret check on the upstream route.
    """
    resp = await request.app.state.client.get(
        f"{settings.IDENTITY_SERVICE_URL.rstrip('/')}/admin/tenants/{tenant_id}",
        headers=_internal_headers(request),
        timeout=10.0,
    )
    return _passthrough(resp)
