"""
ACP Gateway — Service Client
==============================
Responsibilities:
  - Inter-service HTTP communication (persistent clients — no per-request creation)
  - PolicyCache            : Redis-backed OPA decision cache (TTL = 60s)
  - CircuitBreaker         : Fault isolation for OPA and Audit service
  - AgentMetadataCache     : Redis-backed agent metadata cache (TTL = 300s)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from enum import StrEnum
from typing import Any

import httpx
import structlog
from jose import JWTError, jwt
from redis.asyncio import Redis

from sdk.common.auth import mint_service_token
from sdk.common.background import safe_bg as _safe_bg
from sdk.common.config import settings
from sdk.common.constants import REDIS_REVOKE_PREFIX
from sdk.common.resilient_client import ResilientClient
from sdk.utils import SLO_AUDIT_DURABILITY_TOTAL

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# POLICY CACHE
# ---------------------------------------------------------------------------

_POLICY_CACHE_TTL: int = 60   # M-3 (2026-05-13): shortened from 300s so policy
                              # publishes propagate within a minute even without
                              # an explicit invalidation broadcast.
_POLICY_CACHE_PREFIX: str = "acp:policy:"
_POLICY_INVALIDATION_CHANNEL: str = "acp:policy:invalidate"  # M-3: pub/sub bus


class PolicyCache:
    """
    Redis-backed cache for OPA policy decisions.
    key = acp:policy:{agent_id}:{tool}
    value = JSON {"allowed": bool, "reason": str}
    TTL = 300 seconds
    """

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    def _key(self, tenant_id: uuid.UUID, agent_id: uuid.UUID, tool: str) -> str:
        # P-5 FIX: include tenant_id for cross-tenant isolation
        return f"{_POLICY_CACHE_PREFIX}t:{tenant_id}:a:{agent_id}:{tool}"

    async def get(self, tenant_id: uuid.UUID, agent_id: uuid.UUID, tool: str) -> dict[str, Any] | None:
        raw = await self._redis.get(self._key(tenant_id, agent_id, tool))
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    async def set(
        self, tenant_id: uuid.UUID, agent_id: uuid.UUID, tool: str, decision: dict[str, Any]
    ) -> None:
        try:
            await self._redis.setex(
                self._key(tenant_id, agent_id, tool),
                _POLICY_CACHE_TTL,
                json.dumps(decision),
            )
        except Exception as exc:
            logger.warning("policy_cache_set_failed", error=str(exc))

    async def invalidate(self, tenant_id: uuid.UUID, agent_id: uuid.UUID, tool: str | None = None) -> None:
        """PE-1 FIX: Use SCAN instead of KEYS (non-blocking O(N) cursor scan).
        M-3 (2026-05-13): Also publish on the invalidation channel so peer
        gateway instances drop their in-process caches immediately.
        """
        if tool:
            await self._redis.delete(self._key(tenant_id, agent_id, tool))
        else:
            pattern = f"{_POLICY_CACHE_PREFIX}t:{tenant_id}:a:{agent_id}:*"
            async for key in self._redis.scan_iter(match=pattern, count=100):
                await self._redis.delete(key)
        try:
            await self._redis.publish(
                _POLICY_INVALIDATION_CHANNEL,
                json.dumps({"tenant_id": str(tenant_id), "agent_id": str(agent_id), "tool": tool}),
            )
        except Exception as exc:
            logger.warning("policy_invalidation_publish_failed", error=str(exc))


# ---------------------------------------------------------------------------
# AGENT METADATA CACHE
# ---------------------------------------------------------------------------

# 2026-06-15: dropped from 300 → 30 to bound the post-permission-grant
# race window. Buyer-visible bug: first /execute after POST /permissions
# saw "tool not in agent's allow-list" because this cache served stale
# metadata for up to 5 minutes. 30s + the perm_dirty tombstone below
# closes the window to a few seconds (registry invalidates instantly;
# the TTL is just the catch-all in case the invalidate publish failed).
_AGENT_CACHE_TTL: int = 30
_AGENT_CACHE_PREFIX: str = "acp:agent:meta:"
_AGENT_DIRTY_PREFIX: str = "acp:agent:perms_dirty:"  # write-through tombstone


class AgentMetadataCache:
    """
    Redis-backed cache for agent metadata from Registry.
    Reduces Registry DB calls per request.
    key = acp:agent:meta:{agent_id}
    TTL = 30 seconds
    Tombstone: when registry.add_permission lands it SETEXs the dirty key
    for 15s; .get() honours the tombstone and returns None to force a
    refetch from Registry (which always reads fresh DB).
    """

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    def _key(self, agent_id: uuid.UUID) -> str:
        return f"{_AGENT_CACHE_PREFIX}{agent_id}"

    def _dirty_key(self, agent_id: uuid.UUID) -> str:
        return f"{_AGENT_DIRTY_PREFIX}{agent_id}"

    async def get(self, agent_id: uuid.UUID) -> dict[str, Any] | None:
        # 2026-06-15 — write-through tombstone check.
        # Registry sets acp:agent:perms_dirty:{agent_id} on each permission
        # change with TTL=15s. While the tombstone exists this cache lies
        # dormant and every fetch hits Registry directly. After it expires
        # the cache resumes.
        try:
            if await self._redis.get(self._dirty_key(agent_id)) is not None:
                return None
        except Exception:
            pass
        raw = await self._redis.get(self._key(agent_id))
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    async def set(self, agent_id: uuid.UUID, metadata: dict[str, Any]) -> None:
        # Skip caching while the dirty tombstone is live — caching now
        # would just bake in whatever stale snapshot the writer saw.
        try:
            if await self._redis.get(self._dirty_key(agent_id)) is not None:
                return
            await self._redis.setex(
                self._key(agent_id),
                _AGENT_CACHE_TTL,
                json.dumps(metadata),
            )
        except Exception as exc:
            logger.warning("agent_cache_set_failed", error=str(exc))


# ---------------------------------------------------------------------------
# TENANT METADATA CACHE
# ---------------------------------------------------------------------------

_TENANT_CACHE_TTL: int = 600  # 10 minutes
_TENANT_CACHE_PREFIX: str = "acp:tenant:meta:"


class TenantMetadataCache:
    """
    Redis-backed cache for tenant metadata (tier, reserved capacity).
    key = acp:tenant:meta:{tenant_id}
    """

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    def _key(self, tenant_id: uuid.UUID) -> str:
        return f"{_TENANT_CACHE_PREFIX}{tenant_id}"

    async def get(self, tenant_id: uuid.UUID) -> dict[str, Any] | None:
        raw = await self._redis.get(self._key(tenant_id))
        return json.loads(raw) if raw else None

    async def set(self, tenant_id: uuid.UUID, metadata: dict[str, Any]) -> None:
        await self._redis.setex(
            self._key(tenant_id), _TENANT_CACHE_TTL, json.dumps(metadata)
        )


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """
    Hybrid circuit breaker — local state + Redis-coordinated trip signal.

    H-7 FIX (2026-05-13): Reads from a shared Redis flag, so a trip in any
    worker/pod opens the circuit for all of them. Local counters still bound
    failure tracking (we don't write to Redis on every failure — only when we
    actually trip). The breaker degrades cleanly to local-only if Redis is
    unavailable, which is the same safety posture as before.

    Redis key: acp:cb:open:{name}  (value = ts; TTL = recovery_timeout)
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        sample_window: float = 10.0,
        recovery_timeout: float = 30.0,
    ) -> None:
        self.name = name
        self._failure_threshold = failure_threshold
        self._sample_window = sample_window
        self._recovery_timeout = recovery_timeout

        self._state: CircuitState = CircuitState.CLOSED
        self._failure_count: int = 0
        self._last_failure_time: float = 0.0
        self._opened_at: float = 0.0
        self._redis: Redis | None = None
        self._redis_key: str = f"acp:cb:open:{name}"

    def attach_redis(self, redis: Redis | None) -> None:
        """Wire the shared-state Redis client. None = local-only fallback."""
        self._redis = redis

    @property
    def is_open(self) -> bool:
        # Local recovery check
        if self._state == CircuitState.OPEN:
            if time.monotonic() - self._opened_at >= self._recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                logger.info("circuit_breaker_half_open", name=self.name)
                return False
            return True
        return False

    async def is_open_async(self) -> bool:
        """Cluster-aware open check — consults Redis flag too."""
        if self.is_open:
            return True
        if self._redis is None:
            return False
        try:
            if await self._redis.exists(self._redis_key):
                # Another instance tripped; mirror locally so subsequent checks are cheap.
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()
                return True
        except Exception:
            return False  # Redis hiccup: rely on local state
        return False

    def record_success(self) -> None:
        if self._state == CircuitState.HALF_OPEN:
            logger.info("circuit_breaker_closed", name=self.name)
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0

    def record_failure(self) -> None:
        now = time.monotonic()

        if now - self._last_failure_time > self._sample_window:
            self._failure_count = 0

        self._failure_count += 1
        self._last_failure_time = now

        tripped_now = False
        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
            self._opened_at = now
            tripped_now = True
            logger.error("circuit_breaker_reopened", name=self.name)
        elif self._failure_count >= self._failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = now
            tripped_now = True
            logger.error(
                "circuit_breaker_opened",
                name=self.name,
                failures=self._failure_count,
                threshold=self._failure_threshold,
            )

        if tripped_now and self._redis is not None:
            # Best-effort cluster-wide trip; _publish_open() catches Exception
            # internally, but _safe_bg prevents CancelledError from surfacing
            # as an unhandled task exception if the event loop shuts down first.
            asyncio.create_task(_safe_bg(self._publish_open()))

    async def _publish_open(self) -> None:
        try:
            await self._redis.setex(self._redis_key, int(self._recovery_timeout), str(time.time()))
        except Exception as exc:
            logger.warning("circuit_breaker_redis_publish_failed", name=self.name, error=str(exc))


# ---------------------------------------------------------------------------
# SERVICE CLIENT
# ---------------------------------------------------------------------------


class ServiceClient:
    """
    Internal client for inter-service communication within the ACP.

    Design:
    - Single persistent ResilientClient instance (reused across requests)
    - Circuit breaker per critical downstream (OPA, Audit)
    - Policy decisions cached in Redis
    - Agent metadata cached in Redis
    """

    def __init__(self) -> None:
        self._client: ResilientClient | None = None

        # Circuit breakers (Host/Service isolated)
        self._opa_cb = CircuitBreaker("opa", failure_threshold=5, recovery_timeout=30.0)
        self._identity_cb = CircuitBreaker("identity", failure_threshold=5, recovery_timeout=30.0)
        self._registry_cb = CircuitBreaker("registry", failure_threshold=5, recovery_timeout=30.0)
        self._api_cb = CircuitBreaker("api", failure_threshold=5, recovery_timeout=30.0)

        # Caches (injected after Redis is available — see set_redis())
        self._policy_cache: PolicyCache | None = None
        self._agent_cache: AgentMetadataCache | None = None

    def set_redis(self, redis: Redis) -> None:
        """Called once at startup after Redis client is created."""
        self._redis = redis
        self._policy_cache = PolicyCache(redis)
        self._agent_cache = AgentMetadataCache(redis)
        self._tenant_cache = TenantMetadataCache(redis)
        # H-7 (2026-05-13): share circuit breaker state across workers/pods.
        for cb in (self._opa_cb, self._identity_cb, self._registry_cb, self._api_cb):
            cb.attach_redis(redis)

    async def get_client(self) -> ResilientClient:
        if self._client is None:
            # 1 attempt, 2s timeout — retry backoff jitter was pushing past the 2s SLA budget
            self._client = ResilientClient(timeout=2.0, retries=1)
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.close()

    def _get_headers(self) -> dict[str, str]:
        ctx = structlog.contextvars.get_contextvars()
        headers = {}
        if ctx.get("request_id"):
            headers["X-Request-ID"] = str(ctx["request_id"])
        if ctx.get("trace_id"):
            headers["X-Trace-ID"] = str(ctx["trace_id"])
        if ctx.get("deadline"):
            headers["X-ACP-Deadline"] = str(ctx["deadline"])
        # Dual-header: X-Mesh-Token (new) + X-Internal-Secret (legacy fallback).
        # Services that have been updated to verify_internal_secret() accept both;
        # old services that only check X-Internal-Secret continue to work.
        headers["X-Mesh-Token"] = mint_service_token("gateway")
        headers["X-Internal-Secret"] = settings.INTERNAL_SECRET
        return headers

    # ------------------------------------------------------------------
    # TOKEN INTROSPECT
    # ------------------------------------------------------------------

    async def introspect_token(self, token: str) -> dict[str, Any]:
        """
        Call Identity service to verify JWT.
        FALLBACK: Local JWT verification if Identity service is down.
        """
        url = f"{settings.IDENTITY_SERVICE_URL.rstrip('/')}/auth/introspect"
        client = await self.get_client()
        try:
            resp = await client.post(
                url, json={"token": token}, headers=self._get_headers()
            )
            if resp.status_code == 200:
                return dict(resp.json().get("data", resp.json()))
            raise httpx.HTTPError("Identity service returned non-200")
        except Exception as exc:
            logger.warning("identity_service_down_using_local_fallback", error=str(exc))
            return await self._introspect_token_local(token)

    async def _introspect_token_local(self, token: str) -> dict[str, Any]:
        """Perform local JWT verification as a disaster-recovery fallback."""
        try:
            # 1. Decode and verify signature
            payload = jwt.decode(
                token,
                settings.JWT_SECRET_KEY,
                algorithms=[settings.JWT_ALGORITHM]
            )

            # 2. Check Redis Revocation (mandatory - fail closed if unavailable)
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            if not hasattr(self, "_redis"):
                logger.error("local_fallback_redis_unavailable")
                return {"active": False}

            is_revoked = await self._redis.exists(f"{REDIS_REVOKE_PREFIX}{token_hash}")
            if is_revoked:
                logger.error("local_fallback_detected_revoked_token")
                return {"active": False}

            return {
                "active": True,
                "agent_id": payload.get("agent_id"),
                "tenant_id": payload.get("tenant_id"),
                "role": payload.get("role")
            }
        except JWTError as e:
            logger.error("local_fallback_verification_failed", error=str(e))
            return {"active": False}
        except Exception as e:
            logger.error("local_fallback_unexpected_error", error=str(e))
            return {"active": False}

    # ------------------------------------------------------------------
    # POLICY EVALUATION (Direct, no cache)
    # ------------------------------------------------------------------

    async def evaluate_policy(
        self,
        tenant_id: uuid.UUID,
        agent_id: uuid.UUID,
        tool: str,
        risk_score: float = 0.0,
        behavior_history: list[dict[str, Any]] | None = None,
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        jwt_claims: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Evaluate agent policy — LOCAL first, OPA HTTP as fallback.

        When jwt_claims are provided (embedded at token issuance) the evaluation
        is done in-process in < 1ms with no network hop.
        Only falls back to the Policy Service HTTP call when claims are absent
        (e.g. old tokens or agent_id == UUID(0) management context).
        """
        # Fast-path: JWT has embedded permissions → evaluate locally
        if jwt_claims and jwt_claims.get("agent_status") is not None:
            from services.policy.local_eval import evaluate_from_jwt_claims
            allowed, reason, adjustment = evaluate_from_jwt_claims(
                jwt_claims, tool, risk_score
            )
            logger.debug("local_policy_eval", allowed=allowed, reason=reason, tool=tool)
            return {"allowed": allowed, "reason": reason, "risk_adjustment": adjustment}

        # Slow-path: circuit breaker → remote OPA call
        # H-7 (2026-05-13): use cluster-aware open check so a trip in one worker
        # protects all of them.
        if await self._opa_cb.is_open_async():
            logger.warning("opa_circuit_open_deny", agent_id=str(agent_id), tool=tool)
            return {"allowed": False, "reason": "fail safe deny", "risk_adjustment": 0.0}

        return await self._evaluate_policy_remote(
            tenant_id, agent_id, tool, risk_score, behavior_history, request_id, metadata
        )

    async def _evaluate_policy_remote(
        self,
        tenant_id: uuid.UUID,
        agent_id: uuid.UUID,
        tool: str,
        risk_score: float = 0.0,
        behavior_history: list[dict[str, Any]] | None = None,
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Performs the actual remote OPA call and handles errors."""
        url = f"{settings.POLICY_SERVICE_URL.rstrip('/')}/policy/evaluate"
        client = await self.get_client()
        payload = {
            "tenant_id": str(tenant_id),
            "agent_id": str(agent_id),
            "tool": tool,
            "risk_score": risk_score,
            "behavior_history": behavior_history or [],
            "request_id": request_id,
            "metadata": metadata or {},
        }
        # One-shot retry on connect/read timeouts. Policy decisions are
        # idempotent (no side effects) so a retry is safe. Catches the
        # rare cold-pool reconnect that surfaced as
        # "Policy engine error: system_unavailable" in load tests.
        try:
            try:
                resp = await client.post(url, json=payload, headers=self._get_headers())
            except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError) as _exc:
                logger.warning("opa_call_retry", error=str(_exc))
                resp = await client.post(url, json=payload, headers=self._get_headers())

            # Handle ENFORCED 403 from Policy Service
            if resp.status_code == 403:
                reason = "Access Denied"
                adjustment = 0.0
                try:
                    data = resp.json()
                    reason = data.get("detail", reason)
                except Exception:
                    pass
                return {"allowed": False, "reason": reason, "risk_adjustment": adjustment}

            if resp.status_code != 200:
                raise httpx.HTTPStatusError(
                    f"Policy service returned {resp.status_code}",
                    request=resp.request,
                    response=resp,
                )

            result = dict(resp.json())
            self._opa_cb.record_success()

            # Extract inner data (ACP envelope wraps in "data")
            return result.get("data", result)

        except Exception as exc:
            self._opa_cb.record_failure()
            logger.error("opa_call_failed", error=str(exc), agent_id=str(agent_id))
            return {
                "allowed": False,
                "reason": f"Policy evaluation failed: {type(exc).__name__}",
                "risk_adjustment": 0.0
            }

    async def analyze_behavior(self, tenant_id: str, agent_id: str, tool: str, tokens: int) -> dict[str, Any] | None:
        client = await self.get_client()
        url = f"{settings.BEHAVIOR_SERVICE_URL.rstrip('/')}/analyze"
        try:
            resp = await client.post(url, json={
                "tenant_id": str(tenant_id),
                "agent_id": str(agent_id),
                "tool": tool,
                "tokens": tokens
            }, headers=self._get_headers())
            resp.raise_for_status()
            return resp.json().get("data")
        except Exception as exc:
            logger.error("behavior_analysis_failed", error=str(exc))
            return None

    async def evaluate_decision(self, req_data: dict[str, Any]) -> dict[str, Any]:
        """
        Final unified decision engine call.
        Enforces Rule 4: Must fail closed if the decision engine is unavailable.
        """
        client = await self.get_client()
        url = f"{settings.DECISION_SERVICE_URL.rstrip('/')}/evaluate"
        try:
            # P4-1 FIX: Standardizing serialization (ensure strings for IDs)
            payload = {k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in req_data.items()}

            headers = self._get_headers()
            headers["X-Internal-Secret"] = str(settings.INTERNAL_SECRET or "")

            # NOTE: timeout is handled by ResilientClient via SLA/deadline logic.
            # Passing it here leads to "multiple values for timeout" TypeError.
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.error("decision_evaluation_failed",
                         error=str(exc),
                         error_type=type(exc).__name__,
                         url=url)
            # RULE 4: Fail closed. Do NOT return None.
            return {
                "action": "deny",
                "risk": 1.0,
                "reasons": [f"Decision engine unavailable: {type(exc).__name__}"]
            }

    # ------------------------------------------------------------------
    # AUDIT LOGGING (async Redis Stream — non-blocking)
    # ------------------------------------------------------------------

    async def log_audit_stream(self, redis: Redis, log_data: dict[str, Any]) -> None:
        """
        Directly push audit event to Redis Stream (PRODUCER).
        Ensures zero-loss durability on the hot path.
        """
        try:
            # 1. Record Production SLO
            SLO_AUDIT_DURABILITY_TOTAL.labels(stage="produced").inc()

            payload = {
                k: ("" if v is None else (json.dumps(v) if not isinstance(v, str) else v))
                for k, v in log_data.items()
            }
            # maxlen=10_000 keeps the stream's steady-state below the
            # /system/health "Degraded Performance" threshold (12_000).
            # The old 50_000 cap meant the stream sat near full at sustained
            # load and tripped the 45_000 warning continuously even though
            # the consumer group's lag was 0 — every entry already XACK'd,
            # just retained by the cap. The audit DB write is the durability
            # path; the stream is a transient handoff buffer.
            await redis.xadd(
                "acp:audit_stream",
                payload,
                maxlen=10_000,
                approximate=True,
            )
        except Exception as exc:
            logger.error("audit_stream_write_failed", error=str(exc))
            SLO_AUDIT_DURABILITY_TOTAL.labels(stage="failed_at_producer").inc()
            raise

    # ------------------------------------------------------------------
    # API KEY VALIDATION
    # ------------------------------------------------------------------

    async def validate_api_key(self, api_key: str) -> dict[str, Any] | None:
        """Call API service to validate an API key."""
        url = f"{settings.API_SERVICE_URL.rstrip('/')}/api-keys/validate"
        client = await self.get_client()
        try:
            resp = await client.post(
                url, json={"api_key": api_key}, headers=self._get_headers()
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("data", data)
            return None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # AGENT METADATA (with cache)
    # ------------------------------------------------------------------

    async def get_agent_metadata(
        self,
        agent_id: uuid.UUID,
        tenant_id: uuid.UUID,
        jwt_claims: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """
        Return agent metadata, sourced in priority order:
          1. JWT claims (embedded at token issuance — zero network calls)
          2. Redis agent cache (300s TTL)
          3. Registry HTTP (last resort, sets cache entry)

        This eliminates Registry calls from the execute hot-path when agents
        use fresh tokens issued after the permissions-embedding change.
        """
        # JWT fast-path: claims contain status + permissions
        if jwt_claims and jwt_claims.get("agent_status") is not None:
            return {
                "id":          jwt_claims.get("agent_id", str(agent_id)),
                "tenant_id":   jwt_claims.get("tenant_id", str(tenant_id)),
                "status":      jwt_claims["agent_status"],
                "permissions": jwt_claims.get("permissions", []),
                "risk_level":  jwt_claims.get("risk_level", "low"),
                "name":        jwt_claims.get("agent_name", ""),
            }

        # P5-1 FIX: Handle Discovery/Management mode (Null Agent)
        if agent_id == uuid.UUID(int=0):
            return {
                "name": "Global Management Context",
                "status": "active",
                "permissions": [{"tool_name": "*", "action": "allow"}]
            }

        # P5-2 ADD: Dashboard Agent System Identity
        # Well-known UUID for dashboard-agent (fixed for policy consistency)
        DASHBOARD_AGENT_ID = uuid.UUID("00000000-0000-0000-0000-00000000d05b")
        if str(agent_id) == "dashboard-agent" or agent_id == DASHBOARD_AGENT_ID:
            return {
                "name": "Dashboard System Agent",
                "status": "active",
                "permissions": [
                    {"tool_name": "risk.summary", "action": "allow"},
                    {"tool_name": "insights.recent", "action": "allow"},
                    {"tool_name": "billing.summary", "action": "allow"},
                    {"tool_name": "agents", "action": "allow"},
                    {"tool_name": "agents.search", "action": "allow"},
                    {"tool_name": "decision.kill-switch", "action": "allow"},
                    {"tool_name": "forensics", "action": "allow"},
                    {"tool_name": "forensics.read", "action": "allow"},
                    {"tool_name": "forensics.recall", "action": "allow"},
                    {"tool_name": "unknown-tool", "action": "allow"} # Pass-through for discovery
                ]
            }

        if self._agent_cache:
            cached = await self._agent_cache.get(agent_id)
            if cached:
                return cached

        url = f"{settings.REGISTRY_SERVICE_URL.rstrip('/')}/agents/{agent_id}"
        client = await self.get_client()
        try:
            resp = await client.get(
                url, headers={**self._get_headers(), "X-Tenant-ID": str(tenant_id)}
            )
            if resp.status_code == 200:
                data = resp.json().get("data", resp.json())
                if self._agent_cache:
                    await self._agent_cache.set(agent_id, data)
                return data
            raise httpx.HTTPError(f"Registry returned {resp.status_code}")
        except Exception as exc:
            logger.warning("registry_service_down_using_cache_fallback", agent_id=str(agent_id), error=str(exc))
            # If we already checked cache at start, this is a retry or we can just double check
            if self._agent_cache:
                return await self._agent_cache.get(agent_id)
        return None

    async def get_tenant_metadata(self, tenant_id: uuid.UUID) -> dict[str, Any]:
        """
        Fetch tenant tier + rpm_limit from Identity service (Redis-cached, 10-min TTL).
        Returns real tier data so rate limiting is actually enforced per tier.
        """
        if self._tenant_cache:
            cached = await self._tenant_cache.get(tenant_id)
            if cached:
                return cached

        # Fetch from Identity service — it owns the Tenant table
        url = f"{settings.IDENTITY_SERVICE_URL.rstrip('/')}/auth/tenants/{tenant_id}"
        client = await self.get_client()
        data: dict[str, Any] = {
            "tenant_id": str(tenant_id),
            "org_id":    str(tenant_id),
            "tier":      "basic",
            "rpm_limit": 60,
            "status":    "active",
        }
        try:
            resp = await client.get(url, headers=self._get_headers())
            if resp.status_code == 200:
                data = resp.json()
        except Exception as exc:
            logger.warning("tenant_metadata_fetch_failed", error=str(exc), tenant_id=str(tenant_id))

        if self._tenant_cache:
            await self._tenant_cache.set(tenant_id, data)
        return data

    async def record_billing_event(
        self,
        tenant_id: str,
        action: str,
        agent_id: uuid.UUID | str | None,
        tokens: int = 0,
        audit_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, bool]:
        """
        Record a billing event with idempotent retry logic.

        GUARANTEE: Returns {"success": True} ONLY if billing event was recorded.
        Returns {"success": False} with critical error if it fails after retries.

        Args:
            idempotency_key: Request ID to prevent duplicate usage records.
                             MUST be the same for retries of the same execution.

        Returns:
            {"success": True} if billing was recorded
            {"success": False, "error": str} if billing failed after retries
        """
        from sdk.utils import (
            BILLING_EVENTS_FAILED,
            BILLING_EVENTS_TOTAL,
            BILLING_ZERO_TOKEN_CORRECTED,
        )

        # Use audit_id as idempotency key (ensures same execution always uses same key)
        idempotency_key = idempotency_key or audit_id
        if not idempotency_key:
            error_msg = "Missing idempotency_key (audit_id required for billing guarantee)"
            logger.critical("BILLING_MISSING_IDEMPOTENCY", error=error_msg)
            return {"success": False, "error": error_msg}

        safe_tokens = max(tokens, 1)
        safe_agent_id: str | None = None
        if agent_id is not None and str(agent_id) not in ("None", "unknown", str(uuid.UUID(int=0))):
            try:
                safe_agent_id = str(uuid.UUID(str(agent_id)))
            except ValueError:
                safe_agent_id = None

        payload = {
            "tenant_id": tenant_id,
            "agent_id": safe_agent_id,
            "tool": "unknown",
            "units": safe_tokens,
            "cost": safe_tokens * 0.001,
            "audit_id": audit_id
        }

        try:
            BILLING_EVENTS_TOTAL.inc()
            if tokens <= 0:
                logger.error("INVALID_TOKENS_FOR_BILLING", tokens=tokens)
                BILLING_ZERO_TOKEN_CORRECTED.inc()

            # Step 1: Record usage (idempotent via audit_id)
            client = await self.get_client()
            headers = self._get_headers()

            await self._record_usage_with_retry(
                client, headers, payload, idempotency_key
            )

            # Step 2: Record billing (idempotent via audit_id)
            await self._record_billing_with_retry(
                client, headers, tenant_id, action, agent_id, audit_id, idempotency_key
            )

            logger.info(
                "billing_event_success",
                idempotency_key=idempotency_key,
                audit_id=audit_id,
                tenant_id=tenant_id,
                action=action,
            )
            return {"success": True}

        except Exception as exc:
            BILLING_EVENTS_FAILED.inc()
            error_msg = f"Billing guarantee violation: {str(exc)}"
            logger.critical(
                "BILLING_GUARANTEE_VIOLATION",
                error=error_msg,
                idempotency_key=idempotency_key,
                audit_id=audit_id,
                tenant_id=tenant_id,
                action=action,
            )
            return {"success": False, "error": error_msg}

    async def _record_usage_with_retry(
        self, client: httpx.AsyncClient, headers: dict, payload: dict, idempotency_key: str
    ) -> None:
        """Record usage with idempotent retry (transient errors only)."""
        max_retries = 3
        backoff_delays = [0.1, 0.2, 0.4]

        for attempt in range(max_retries):
            try:
                # 2026-05-13 BUGFIX: do NOT pass timeout= here — ResilientClient
                # injects its own timeout=attempt_timeout inside _execute_request_loop;
                # passing one in kwargs causes httpx.AsyncClient.request() to fail with
                # "got multiple values for keyword argument 'timeout'".
                response = await client.post(
                    f"{settings.USAGE_SERVICE_URL.rstrip('/')}/usage/record",
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                logger.info("usage_recorded", idempotency_key=idempotency_key, attempt=attempt+1)
                return
            except (TimeoutError, httpx.TimeoutException, httpx.ConnectError):
                if attempt < max_retries - 1:
                    await asyncio.sleep(backoff_delays[attempt])
                    continue
                raise
            except httpx.HTTPStatusError as status_err:
                # Don't retry 4xx errors (client fault)
                if 400 <= status_err.status_code < 500:
                    raise
                # Retry 5xx (server fault)
                if attempt < max_retries - 1:
                    await asyncio.sleep(backoff_delays[attempt])
                    continue
                raise

    async def _record_billing_with_retry(
        self,
        client: httpx.AsyncClient,
        headers: dict,
        tenant_id: str,
        action: str,
        agent_id: uuid.UUID | str | None,
        audit_id: str | None,
        idempotency_key: str,
    ) -> None:
        """Record billing with idempotent retry (transient errors only)."""
        max_retries = 3
        backoff_delays = [0.1, 0.2, 0.4]

        # Parse UUIDs
        try:
            tenant_uuid = uuid.UUID(str(tenant_id)) if not isinstance(tenant_id, uuid.UUID) else tenant_id
        except ValueError as e:
            logger.error("invalid_tenant_uuid", error=str(e), tenant_id=tenant_id)
            raise

        agent_uuid = None
        if agent_id and str(agent_id) not in ("None", "unknown", str(uuid.UUID(int=0))):
            try:
                agent_uuid = uuid.UUID(str(agent_id)) if not isinstance(agent_id, uuid.UUID) else agent_id
            except ValueError:
                agent_uuid = None

        # Prepare billing request with idempotency key
        billing_headers = {**headers, "X-Tenant-ID": str(tenant_uuid)}
        billing_payload = {
            "tenant_id": str(tenant_uuid),
            "action": action,
            "agent_id": str(agent_uuid) if agent_uuid else None,
            "audit_id": audit_id,
            "idempotency_key": idempotency_key,  # Prevents duplicate usage records
        }

        for attempt in range(max_retries):
            try:
                # 2026-05-13 BUGFIX: ResilientClient owns timeout; do not pass it.
                response = await client.post(
                    f"{settings.USAGE_SERVICE_URL.rstrip('/')}/billing/events",
                    json=billing_payload,
                    headers=billing_headers,
                )
                response.raise_for_status()  # Raise on 4xx/5xx
                logger.info(
                    "billing_recorded",
                    idempotency_key=idempotency_key,
                    attempt=attempt+1,
                    audit_id=audit_id,
                )
                return
            except (TimeoutError, httpx.TimeoutException, httpx.ConnectError):
                if attempt < max_retries - 1:
                    logger.warning(
                        "billing_retry_transient",
                        idempotency_key=idempotency_key,
                        attempt=attempt+1,
                        error_type="network",
                    )
                    await asyncio.sleep(backoff_delays[attempt])
                    continue
                # Out of retries
                raise
            except httpx.HTTPStatusError as status_err:
                # Don't retry 4xx errors (client fault) — would fail again
                if 400 <= status_err.status_code < 500:
                    logger.error(
                        "billing_client_error",
                        idempotency_key=idempotency_key,
                        status=status_err.status_code,
                        response=status_err.response.text,
                    )
                    raise
                # Retry 5xx (server fault)
                if attempt < max_retries - 1:
                    logger.warning(
                        "billing_retry_server_error",
                        idempotency_key=idempotency_key,
                        attempt=attempt+1,
                        status=status_err.status_code,
                    )
                    await asyncio.sleep(backoff_delays[attempt])
                    continue
                raise

    async def publish_incident_event(
        self,
        *,
        tenant_id:  str,
        agent_id:   str,
        severity:   str,
        trigger:    str,
        title:      str,
        risk_score: float,
        tool:       str | None,
        request_id: str | None,
        reasons:    list[str],
    ) -> None:
        """
        Publish incident event to Redis Stream for durable, deduplicated processing.
        The API service's stream consumer handles creation with retry + backoff.
        Never raises — gateway request path must not be blocked by incident recording.
        """
        try:
            import json as _json
            payload = _json.dumps({
                "tenant_id":  tenant_id,
                "agent_id":   agent_id,
                "severity":   severity,
                "trigger":    trigger,
                "title":      title,
                "risk_score": risk_score,
                "tool":       tool,
                "request_id": request_id,
                "reasons":    reasons,
            }, default=str)
            await self._redis.xadd(
                "acp:incidents:queue",
                {"data": payload},
                maxlen=50_000,
                approximate=True,
            )
        except Exception as exc:
            logger.warning("incident_queue_publish_failed", error=str(exc))


service_client = ServiceClient()
