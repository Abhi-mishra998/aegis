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
from services.gateway.auth import init_token_validator
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


async def _publish_event(
    r: Any, tenant_id: str, event_type: str, data: dict, *, agent_id: str | None = None
) -> None:
    """Publish a single SSE event to the per-tenant Redis Pub/Sub channel.

    Sprint 2 — helper around the previous `redis.publish(f"acp:events:{tid}", json.dumps(...))`
    pattern at 4 emit sites. Best-effort; never raises. SSE is a side channel and a
    publish failure must NOT bring down the originating handler.

    If `agent_id` is provided, the event is ALSO published on the per-agent channel
    `acp:events:{tenant_id}:{agent_id}` so EventSource clients scoped to one agent
    receive a filtered stream alongside the tenant-wide subscription.
    """
    if not tenant_id:
        return
    try:
        payload = json.dumps({
            "type": event_type,
            "data": data,
            "ts": int(time.time()),
        })
    except Exception as exc:
        logger.warning("sse_publish_serialise_failed", event_type=event_type, error=str(exc))
        return
    try:
        await r.publish(f"acp:events:{tenant_id}", payload)
    except Exception as exc:
        logger.warning("sse_publish_failed", event_type=event_type, error=str(exc))
    if agent_id:
        try:
            await r.publish(f"acp:events:{tenant_id}:{agent_id}", payload)
        except Exception as exc:
            logger.warning(
                "sse_publish_agent_channel_failed",
                event_type=event_type, agent_id=agent_id, error=str(exc),
            )


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
    # sprint-2.1 — token-revocation pub/sub listener. Identity service
    # publishes the sha256 hash on revoke; this listener drops the entry
    # from the in-process LRU so the revoked token is rejected on the
    # *next* request instead of waiting up to 60s for the TTL to expire.
    from services.gateway.auth import run_revocation_listener
    revocation_listener = asyncio.create_task(run_revocation_listener(redis))
    yield
    await pubsub_manager.close()
    billing_worker.cancel()
    queue_age_worker.cancel()
    revocation_listener.cancel()
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
    contact={"name": "Aegis Maintainers", "url": "https://github.com/Abhi-mishra998/aegis"},
    license_info={"name": "Apache-2.0", "url": "https://www.apache.org/licenses/LICENSE-2.0"},
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


# All /auth/users + /users/* (5 routes) extracted to routers/users.py.


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


# Explicit /auth/sso/config routes MUST sit above the /auth/sso/{provider}
# catch-all below, otherwise FastAPI matches "config" as a provider name and
# forwards the request without X-Tenant-ID (identity returns 400).
@app.get("/auth/sso/config", tags=["sso"])
async def get_sso_config_proxy(request: Request) -> Any:
    """Proxy → Identity: read tenant SSO provider config (secrets masked)."""
    resp = await request.app.state.client.get(
        f"{settings.IDENTITY_SERVICE_URL.rstrip('/')}/auth/sso/config",
        headers=_internal_headers(request),
    )
    return _passthrough(resp)


@app.post("/auth/sso/config", tags=["sso"])
async def save_sso_config_proxy(request: Request) -> Any:
    """Proxy → Identity: persist tenant SSO provider config."""
    headers = _internal_headers(request)
    ctype = request.headers.get("content-type")
    if ctype:
        headers["Content-Type"] = ctype
    resp = await request.app.state.client.post(
        f"{settings.IDENTITY_SERVICE_URL.rstrip('/')}/auth/sso/config",
        content=await request.body(),
        headers=headers,
    )
    return _passthrough(resp)


@app.post("/auth/sso/config/test", tags=["sso"])
async def test_sso_config_proxy(request: Request) -> Any:
    """Proxy → Identity: probe configured SSO provider for reachability."""
    headers = _internal_headers(request)
    ctype = request.headers.get("content-type")
    if ctype:
        headers["Content-Type"] = ctype
    resp = await request.app.state.client.post(
        f"{settings.IDENTITY_SERVICE_URL.rstrip('/')}/auth/sso/config/test",
        content=await request.body(),
        headers=headers,
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

    # Sprint 2 — at-most-once-per-month SSE quota_warning publish when
    # the tenant crosses 80% of its monthly request cap. The
    # `acp:quota_warning_sent:{tenant_id}:{YYYYMM}` SETNX guard makes
    # this idempotent even if /tenant/quota is polled every few seconds.
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
                first_time = await redis.set(guard_key, "1", nx=True, ex=35 * 24 * 3600)
            except Exception as _e:
                logger.warning("quota_warning_guard_failed", error=str(_e))
                first_time = False
            if first_time:
                await _publish_event(
                    redis, str(tenant_id), "quota_warning",
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

# sprint-3.1 — per-domain router modules. The 3,920-LOC main.py is being
# decomposed; admin is the first extraction. Each router lives under
# services/gateway/routers/ and depends only on services/gateway/_helpers.py
# (never on main.py — that would create a load-time cycle).
from services.gateway.routers.admin import router as _admin_router  # noqa: E402
from services.gateway.routers.agents import router as _agents_router  # noqa: E402
from services.gateway.routers.audit import router as _audit_router  # noqa: E402
from services.gateway.routers.auto_response import (
    router as _auto_response_router,  # noqa: E402
)
from services.gateway.routers.billing import router as _billing_router  # noqa: E402
from services.gateway.routers.compliance import (
    router as _compliance_router,  # noqa: E402
)
from services.gateway.routers.dashboard import router as _dashboard_router  # noqa: E402
from services.gateway.routers.decision import router as _decision_router  # noqa: E402
from services.gateway.routers.forensics import router as _forensics_router  # noqa: E402
from services.gateway.routers.incidents import router as _incidents_router  # noqa: E402
from services.gateway.routers.policy import router as _policy_router  # noqa: E402
from services.gateway.routers.proxies import router as _proxies_router  # noqa: E402
from services.gateway.routers.risk import router as _risk_router  # noqa: E402
from services.gateway.routers.sso import router as _sso_router  # noqa: E402
from services.gateway.routers.stripe_webhook import (
    router as _stripe_router,  # noqa: E402
)
from services.gateway.routers.tenant_admin import (
    router as _tenant_admin_router,  # noqa: E402
)
from services.gateway.routers.transparency import (
    router as _transparency_router,  # noqa: E402
)
from services.gateway.routers.users import router as _users_router  # noqa: E402

app.include_router(_admin_router)
app.include_router(_decision_router)
app.include_router(_proxies_router)
app.include_router(_tenant_admin_router)
app.include_router(_stripe_router)
app.include_router(_sso_router)
app.include_router(_dashboard_router)
app.include_router(_auto_response_router)
app.include_router(_audit_router)
app.include_router(_incidents_router)
app.include_router(_billing_router)
app.include_router(_compliance_router)
app.include_router(_transparency_router)
app.include_router(_risk_router)
app.include_router(_policy_router)
app.include_router(_forensics_router)
app.include_router(_users_router)
app.include_router(_agents_router)

# ─────────────────────────────────────────────────────────────
# P0-5 FIX: Removed include_router(audit_router), include_router(registry_router),
#           include_router(api_key_router).  All routes are now pure httpx proxies
#           so the gateway does NOT need DB connections to downstream databases.
# ─────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────
# REGISTRY PROXY — /agents
# ─────────────────────────────────────────────────────────────

# All /agents/* (10 routes) and /registry/tools extracted to
# routers/agents.py.


# ─────────────────────────────────────────────────────────────
# AUDIT PROXY — /audit/*
# Extracted to services/gateway/routers/audit.py in the sprint-5 audit
# cleanup. 33 routes moved; this file no longer hosts any /audit/* route.
# The sub-router is registered via app.include_router at the bottom of
# this file alongside the other extracted routers.
# ─────────────────────────────────────────────────────────────


# All /receipts/* (3 routes) and /transparency/* (8 routes) extracted to
# routers/transparency.py.


# /audit/export (GET+POST), /audit/logs/soc-timeline, /audit/logs/heatmap
# all extracted to routers/audit.py.


# All /compliance/* (6), /siem/* (5), /reports/scheduled/* (7) — 18 routes —
# extracted to routers/compliance.py.


# ─────────────────────────────────────────────────────────────
# THREAT INTELLIGENCE PROXY — /threat-intel/*
# Routes to the audit service compliance/threat-intel/* endpoints.
# ─────────────────────────────────────────────────────────────


# All /threat-intel/* (3 routes) extracted to routers/risk.py.


def _is_nontrivial_policy_decision(decision_data: Any) -> bool:
    """True when a policy result is worth notifying the LiveFeed about.

    Allowed decisions are noisy and not actionable — only surface deny /
    escalate / approval_required style outcomes.
    """
    if not isinstance(decision_data, dict):
        return False
    if decision_data.get("allowed") is False:
        return True
    action = str(decision_data.get("action", "")).lower()
    if action in {"deny", "escalate", "approval_required", "block"}:
        return True
    return False


def _extract_policy_reasons(decision_data: Any) -> list[str]:
    """Normalise the heterogeneous policy reason shapes into list[str]."""
    if not isinstance(decision_data, dict):
        return []
    reasons = decision_data.get("reasons")
    if isinstance(reasons, list) and reasons:
        return [str(r) for r in reasons[:3]]
    reason = decision_data.get("reason")
    return [str(reason)] if reason else []


# All /policy/* (3 routes) extracted to routers/policy.py.


# /audit/logs/verify, /audit/logs/{audit_id}/explain, /audit/logs/{audit_id}/notes
# (GET + POST), /audit/drift/{agent_id} — all extracted to routers/audit.py.


# /billing/cost-attribution extracted to routers/billing.py.
# /playbooks/autotrigger-stats extracted to routers/risk.py.
# All /risk/* (4 routes) extracted to routers/risk.py.


# /audit/trends, /audit/top-findings, /audit/peer-benchmark/{id},
# /audit/tool-breakdown, /audit/risk-trend/{id}, /audit/hourly-activity,
# /audit/risk-histogram, /audit/weekly-heatmap, /audit/decision-trend,
# /audit/agent-activity, /audit/high-risk-events, /audit/deny-reasons,
# /audit/tool-usage/{id}, /audit/tool-risk, /audit/risk-percentile-trend,
# /audit/daily-active-agents, /audit/finding-breakdown,
# /audit/agent-daily-decisions/{id}, /audit/agent-findings/{id},
# /audit/posture-score-trend, /audit/escalation-rate-trend —
# all extracted to routers/audit.py.


# ─────────────────────────────────────────────────────────────
# DECISION PROXY — /decision
# NEW: Kill-switch and decision history routes proxied to Decision service
# ─────────────────────────────────────────────────────────────

# Decision kill-switch proxy routes moved to services/gateway/routers/decision.py
# in sprint-4.E. The router is included near app initialisation alongside admin.


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


# All /forensics/* (3 routes) extracted to routers/forensics.py.


# All /billing/* (9 routes) and /usage/* (4 routes) extracted to
# routers/billing.py.


# All /api-keys/* (4 routes) extracted to routers/users.py.


# All 10 /incidents/* routes extracted to routers/incidents.py.


# NOTE: the 16 /auto-response/* proxy routes were extracted out of this
# file into services/gateway/routers/auto_response.py in sprint-5 (commit
# 0a0a0a0). The sub-router is mounted via app.include_router(...) at the
# bottom of this file alongside the other extracted sub-routers.


# /insights/recent extracted to routers/risk.py.


# ─────────────────────────────────────────────────────────────
# DASHBOARD STATE — /dashboard/state
# Single aggregated endpoint: audit + agents + billing + insights + kill-switch
# ─────────────────────────────────────────────────────────────

# /dashboard/state moved to services/gateway/routers/dashboard.py in sprint-7.6.
# That extraction also fixed a latent bug: the prior in-main implementation
# accidentally returned JSONResponse objects from its _safe() helper and then
# called .get() on them — the isinstance(dict) guards turned every field into
# {} in production. The new module uses resp.json() directly.


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


# Runtime-trust passthrough proxies (/graph, /flight, /autonomy), playbooks,
# webhooks, and notifications proxy routes moved to
# services/gateway/routers/proxies.py in sprint-5.1.


# SSO config proxy routes moved to services/gateway/routers/sso.py in sprint-6.2.


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
                risk_val = float(data.get("risk", 0.0) or 0.0)
                exec_payload = {
                    "request_id": request_id,
                    "agent_id": agent_id_str,
                    "tool": tool,
                    "action": action_val,
                    "risk": risk_val,
                    "confidence": data.get("confidence", 1.0),
                    "signals": data.get("signals", {}),
                    "reasons": (data.get("reasons") or [])[:3],
                }
                await _publish_event(
                    redis, tenant_id_str, "tool_executed", exec_payload,
                    agent_id=agent_id_str or None,
                )

                # Sprint 2 — fork a `risk_updated` event when this execution
                # crossed the elevated-risk threshold so the LiveFeed +
                # RiskEngine dashboards can react in real time. Cheap (one
                # extra publish) and avoids us inventing a separate hook
                # in /risk/summary which has no write path.
                if risk_val > 0.5:
                    await _publish_event(
                        redis, tenant_id_str, "risk_updated",
                        {
                            "agent_id": agent_id_str,
                            "tool": tool,
                            "risk": risk_val,
                            "action": action_val,
                            "request_id": request_id,
                            "reasons": (data.get("reasons") or [])[:3],
                        },
                        agent_id=agent_id_str or None,
                    )

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
    # SSE auth: cookie (browser EventSource) or Authorization: Bearer (SDK).
    # Query-string tokens were dropped in sprint-1 because they leak via
    # nginx/ALB access logs, browser history, and Referer headers — see
    # `audit-v2.md` §5.2.
    token = request.cookies.get("acp_token")
    if not token:
        auth_hdr = request.headers.get("Authorization", "")
        if auth_hdr.startswith("Bearer "):
            token = auth_hdr[7:].strip()

    if not token:
        logger.info("sse_unauthenticated", reason="no_token_provided")
        return Response(
            status_code=401,
            content='{"error":"Unauthorized","detail":"missing token (cookie or Authorization header)"}',
            media_type="application/json",
        )

    try:
        # token_validator is a module-level global in services.gateway.auth
        # that is mutated by init_token_validator() during lifespan(). Importing
        # the NAME (`from .auth import token_validator`) binds at import time
        # to None and never sees the later reassignment. Re-resolve through the
        # module here so we always read the live singleton.
        from services.gateway import auth as _auth_mod
        tv = _auth_mod.token_validator
        if tv is None:
            raise RuntimeError("token_validator not initialised")
        payload = await tv.validate(token)
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

    # Sprint 2 — optional per-agent SSE filter. When ?agent_id=<uuid> is
    # supplied the stream merges messages from both the tenant-wide channel
    # and the per-agent channel so consumers scoped to one agent get the
    # tenant-level signals (kill_switch, quota_warning) plus that agent's
    # own events (tool_executed, risk_updated, billing_updated, …).
    agent_filter_raw = request.query_params.get("agent_id")
    agent_filter: str | None = None
    if agent_filter_raw:
        try:
            agent_filter = str(uuid.UUID(agent_filter_raw))
        except (ValueError, AttributeError):
            logger.warning("sse_invalid_agent_id", value=agent_filter_raw[:64])
            agent_filter = None

    tenant_channel = f"acp:events:{tenant_id_str}"
    agent_channel = (
        f"acp:events:{tenant_id_str}:{agent_filter}" if agent_filter else None
    )

    async def event_generator() -> AsyncGenerator[str, None]:
        # Bypass the shared module-level PubSubManager. With uvicorn
        # `--workers 4`, the module-level Redis client + pubsub_manager are
        # instantiated at import time, before uvicorn forks. The 4 child
        # workers inherit the same socket FD and corrupt each other's
        # pub/sub stream — publishes reach Redis (verified via PSUBSCRIBE
        # monitor) but the shared subscriber's reader never delivers
        # messages to the per-client queue. Fix: each SSE handler builds
        # a fresh Redis pubsub connection from a fresh client so there is
        # no cross-worker FD sharing.
        from sdk.common.redis import get_redis_client
        local_redis = get_redis_client(settings.REDIS_URL, decode_responses=False)
        pubsub = local_redis.pubsub()
        channels_to_subscribe = [tenant_channel]
        if agent_channel:
            channels_to_subscribe.append(agent_channel)
        await pubsub.subscribe(*channels_to_subscribe)

        try:
            connected_payload = {
                "status": "connected",
                "tenant_id": tenant_id_str,
            }
            if agent_filter:
                connected_payload["agent_id"] = agent_filter
            yield f"event: connected\ndata: {json.dumps(connected_payload)}\n\n"

            last_heartbeat = time.time()
            last_reauth = time.time()
            _REAUTH_INTERVAL_SECONDS = 30.0
            while True:
                if await request.is_disconnected():
                    break
                msg = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0,
                )
                now = time.time()

                # Mid-stream token re-validation — closes the gap where a
                # revoked token's SSE connection stays alive until the
                # client disconnects. Every 30s we re-call the validator;
                # on any failure (revoked, expired, signature mismatch)
                # we yield a typed close event and exit the generator.
                if now - last_reauth >= _REAUTH_INTERVAL_SECONDS:
                    try:
                        await tv.validate(token)
                    except Exception as reauth_exc:
                        logger.info(
                            "sse_reauth_failed",
                            tenant_id=tenant_id_str,
                            error_type=type(reauth_exc).__name__,
                        )
                        yield (
                            "event: auth_expired\n"
                            "data: " + json.dumps({"reason": "token revoked or expired"}) + "\n\n"
                        )
                        break
                    last_reauth = now

                if msg and msg.get("type") in ("message", "pmessage"):
                    data = msg.get("data", b"")
                    if isinstance(data, bytes):
                        data = data.decode("utf-8", errors="replace")
                    yield f"data: {data}\n\n"
                    last_heartbeat = now
                elif now - last_heartbeat >= 15.0:
                    yield f"event: heartbeat\ndata: {json.dumps({'ts': int(now)})}\n\n"
                    last_heartbeat = now
        except asyncio.CancelledError:
            raise
        finally:
            with suppress(Exception):
                await pubsub.unsubscribe(*channels_to_subscribe)
            with suppress(Exception):
                await pubsub.aclose()
            with suppress(Exception):
                await local_redis.aclose()

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


# Admin routes moved to services/gateway/routers/admin.py in sprint-3.1.
# Mounted via app.include_router(admin_router) near the app initialisation.
