"""
_AuthMixin — authentication and token-revocation helpers extracted from
SecurityMiddleware.  All methods use ``self.redis`` which is initialised by
SecurityMiddleware.__init__ at runtime.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid

import redis.exceptions
import structlog
from fastapi import HTTPException, Request
from starlette.responses import Response

from sdk.common.background import safe_bg as _safe_bg
from sdk.utils import IDEMPOTENCY_HITS_TOTAL
from services.gateway.auth import REDIS_REVOKE_PREFIX
from services.gateway.client import service_client

logger = structlog.get_logger(__name__)

_IDEMPOTENCY_PREFIX = "acp:idempotency:"
_API_KEY_CACHE_PREFIX = "acp:apikey:valid:"
_API_KEY_CACHE_TTL = 60  # seconds


class _AuthMixin:
    async def _validate_api_key_cached(self, raw_key: str) -> dict | None:
        """Validate an API key with Redis caching (60s TTL) to avoid per-request DB calls."""
        cache_key = f"{_API_KEY_CACHE_PREFIX}{hashlib.sha256(raw_key.encode()).hexdigest()}"
        try:
            cached = await self.redis.get(cache_key)
            if cached:
                return json.loads(cached)
        except Exception:
            pass  # cache miss is fine; fall through to live call

        key_data = await service_client.validate_api_key(raw_key)
        if key_data:
            try:
                await self.redis.setex(cache_key, _API_KEY_CACHE_TTL, json.dumps(key_data, default=str))
            except Exception:
                pass
        return key_data

    async def _authenticate(
        self, request: Request, is_execute_path: bool = False
    ) -> tuple[uuid.UUID, uuid.UUID, str, str, str | None]:
        """
        Authenticate the request and return
        (tenant_id, agent_id, tenant_id_str, agent_id_str, jti).

        Auth precedence:
          1. Authorization: Bearer acp_... → API key
          2. Authorization: Bearer <JWT>   → JWT
          3. X-API-Key header              → API key (legacy header)
          4. Cookie acp_token              → JWT from cookie
        """
        auth_header = request.headers.get("Authorization")
        x_cookie_token = request.cookies.get("acp_token")
        api_key_header = request.headers.get("X-API-Key")
        client_ip = request.client.host if request.client else "unknown"

        if not auth_header and x_cookie_token:
            auth_header = f"Bearer {x_cookie_token}"

        tenant_id: uuid.UUID | None = None
        agent_id: uuid.UUID | None = None
        tenant_id_str: str = ""
        agent_id_str: str = ""
        jti: str | None = None

        # Rate limit failed auth attempts per IP
        auth_fail_key = f"acp:auth_failures:{client_ip}"

        if auth_header and auth_header.lower().startswith("bearer "):
            parts = auth_header.split(" ", 1)
            if len(parts) == 2:
                token = parts[1].strip()

                # ── API key Bearer path (prefix: acp_) ────────────────────
                if token.startswith("acp_"):
                    key_data = await self._validate_api_key_cached(token)
                    if not key_data:
                        try:
                            await self.redis.incr(auth_fail_key)
                            await self.redis.expire(auth_fail_key, 300)
                        except Exception:
                            pass
                        raise HTTPException(status_code=401, detail="Invalid or expired API key")

                    tenant_id_str = str(key_data["tenant_id"])
                    tenant_id = uuid.UUID(tenant_id_str)

                    # Sprint 1.5 — when the API key is bound to a specific
                    # agent (key_data["agent_id"] is set), the inbound
                    # X-Agent-ID header MUST match. Without this check, an
                    # attacker holding a per-agent key could call /execute
                    # while claiming to be any other agent in the tenant — an
                    # API-key analog of the multi-tenancy header-trust
                    # vulnerability (S5 in the audit). Tenant-scoped keys
                    # (agent_id NULL) preserve the legacy informational
                    # X-Agent-ID behavior for back-compat.
                    bound_agent_raw = key_data.get("agent_id")
                    x_agent_hdr = request.headers.get("X-Agent-ID", "")

                    if bound_agent_raw:
                        try:
                            bound_agent = uuid.UUID(str(bound_agent_raw))
                        except ValueError as exc:
                            logger.error(
                                "api_key_corrupt_agent_binding",
                                key_prefix=key_data.get("key_prefix", "?"),
                                error=str(exc),
                            )
                            raise HTTPException(
                                status_code=500,
                                detail="API key has corrupt agent binding",
                            )
                        if not x_agent_hdr:
                            raise HTTPException(
                                status_code=400,
                                detail=(
                                    "X-Agent-ID header is required for "
                                    "per-agent API keys"
                                ),
                            )
                        try:
                            header_agent = uuid.UUID(x_agent_hdr)
                        except ValueError:
                            raise HTTPException(
                                status_code=400,
                                detail="X-Agent-ID must be a valid UUID",
                            )
                        if header_agent != bound_agent:
                            logger.critical(
                                "api_key_agent_binding_violation",
                                key_prefix=key_data.get("key_prefix", "?"),
                                bound_agent=str(bound_agent),
                                header_agent=str(header_agent),
                            )
                            raise HTTPException(
                                status_code=403,
                                detail="X-Agent-ID does not match the API key's bound agent",
                            )
                        agent_id = bound_agent
                    else:
                        try:
                            agent_id = (
                                uuid.UUID(x_agent_hdr) if x_agent_hdr else uuid.UUID(int=0)
                            )
                        except ValueError:
                            agent_id = uuid.UUID(int=0)
                    agent_id_str = str(agent_id)

                    request.state.permissions = ["execute_agent", "view_risk"]
                    request.state.role = "agent"
                    request.state.actor = f"apikey:{key_data.get('key_prefix', token[:8])}"
                    request.state.jwt_claims = {}
                    jti = None

                # ── JWT Bearer path ────────────────────────────────────────
                else:
                    token_hash = hashlib.sha256(token.encode()).hexdigest()

                    # Local Revocation Check
                    try:
                        if await self.redis.get(f"{REDIS_REVOKE_PREFIX}{token_hash}"):
                            await self.redis.incr(auth_fail_key)
                            await self.redis.expire(auth_fail_key, 300)
                            failures = await self.redis.get(auth_fail_key)
                            if failures and int(failures) > 1000:
                                raise HTTPException(status_code=429, detail="Too many authentication failures")
                            raise HTTPException(status_code=401, detail="Token revoked")
                    except HTTPException:
                        raise
                    except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as _re:
                        # Redis timeout on revocation check -> FAIL CLOSED (Security First)
                        logger.error("redis_timeout_revocation_check", error=str(_re))
                        raise HTTPException(status_code=503, detail="Security infrastructure timeout")

                    try:
                        from services.gateway.auth import token_validator as tv
                        if tv and hasattr(tv, 'validate'):
                            auth_data = await tv.validate(token)
                        else:
                            from services.gateway.auth import LocalTokenValidator
                            auth_data = LocalTokenValidator()._validate_signature(token)
                    except Exception:
                        try:
                            await self.redis.incr(auth_fail_key)
                            await self.redis.expire(auth_fail_key, 300)
                        except Exception as _re:
                            logger.debug("auth_fail_counter_error", error=str(_re))

                        raise HTTPException(status_code=401, detail="Invalid or expired token")

                    # Active RBAC Mapping
                    # Sprint 1 — Role enum extended (OWNER + SECURITY_ANALYST + DEVELOPER + READ_ONLY).
                    # OWNER subsumes ADMIN; SECURITY_ANALYST subsumes legacy SECURITY;
                    # DEVELOPER + READ_ONLY map to legacy AGENT/VIEWER for permission_map purposes.
                    role = auth_data.get("role", "READ_ONLY")
                    permissions_map = {
                        "OWNER":            ["*"],
                        "ADMIN":            ["*"],
                        "SECURITY_ANALYST": ["kill_switch", "view_risk", "execute_agent"],
                        "DEVELOPER":        ["execute_agent", "view_risk"],
                        "READ_ONLY":        ["view_risk"],
                        # Legacy role names kept so pre-Sprint-1 JWTs still resolve.
                        "SECURITY":         ["kill_switch", "view_risk", "execute_agent"],
                        "AUDITOR":          ["view_risk", "view_audit"],
                        "VIEWER":           ["view_risk"],
                        "agent":            ["execute_agent"],
                    }
                    request.state.permissions = permissions_map.get(role, [])
                    request.state.role = role

                    # Write-path enforcement: mutations require an admin-tier role.
                    # Sprint 1 added OWNER (top tier) + renamed SECURITY → SECURITY_ANALYST;
                    # both legacy + new names are accepted so existing JWTs keep working.
                    # agent-role tokens are allowed only on /execute (controlled by OPA + Decision Engine).
                    _write_roles = ("OWNER", "ADMIN", "SECURITY_ANALYST", "SECURITY")
                    if request.method not in ("GET", "HEAD", "OPTIONS"):
                        if role not in _write_roles:
                            if not (is_execute_path and role == "agent"):
                                raise HTTPException(
                                    status_code=403,
                                    detail="Write operations require OWNER, ADMIN, or SECURITY_ANALYST role",
                                )

                    # Enterprise JTI Atomic Burst Lock — tool executions only.
                    # Management CRUD paths are already protected by RBAC and rate limiting.
                    # Replay detection only applies to /execute (tool execution) where the
                    # same JTI reusing within 50ms would indicate a genuine replay attack.
                    jti = auth_data.get("jti")
                    if jti and is_execute_path and request.method not in ("GET", "HEAD", "OPTIONS"):
                        if await self.redis.get(f"{REDIS_REVOKE_PREFIX}jti:{jti}"):
                            raise HTTPException(status_code=401, detail="Token ID revoked")

                        replay_key = f"acp:jti_last_used:{jti}"
                        now_ts = time.time()

                        try:
                            if not await self.redis.setnx(replay_key, now_ts):
                                last = await self.redis.get(replay_key)
                                if last and (now_ts - float(last)) < 0.001:  # 1ms burst window (reduced for load tests)
                                    raise HTTPException(status_code=429, detail="Too many requests: burst replay detected")
                                await self.redis.set(replay_key, now_ts)

                            # Replay TTL aligned with Token Expiry
                            exp = auth_data.get("exp")
                            ttl = int(exp - now_ts) if exp else 900
                            await self.redis.expire(replay_key, max(1, ttl))
                        except HTTPException:
                            raise  # propagate real replay rejections
                        except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as _re:
                            # Sprint 1.5 — fail closed. The audit (S4) flagged
                            # the previous "log + allow" behavior as the wrong
                            # trade-off for a security gate: a replay attack
                            # arriving during a Redis outage would pass through
                            # untouched. The aligned policy with the revocation
                            # check above is to return 503 and let the caller
                            # retry once Redis recovers.
                            logger.error("replay_check_redis_unavailable_fail_closed", jti=jti, error=str(_re))
                            raise HTTPException(
                                status_code=503,
                                detail="Security infrastructure timeout: replay check unavailable",
                            )

                    agent_id_str = auth_data.get("agent_id", "")
                    request.state.actor = auth_data.get("sub", "unknown")
                    tenant_id_str = auth_data["tenant_id"]

                    try:
                        tenant_id = uuid.UUID(tenant_id_str)
                        agent_id = uuid.UUID(agent_id_str) if agent_id_str else uuid.UUID(int=0)
                    except ValueError:
                        raise HTTPException(status_code=401, detail="Invalid identity claims in token")

                    # Store full JWT claims so downstream code can use embedded permissions
                    # without making any Registry or Policy HTTP calls.
                    request.state.jwt_claims = auth_data

        elif api_key_header:
            # Legacy X-API-Key header support
            key_data = await self._validate_api_key_cached(api_key_header)
            if key_data:
                tenant_id_str = str(key_data["tenant_id"])
                tenant_id = uuid.UUID(tenant_id_str)
                x_agent_hdr = request.headers.get("X-Agent-ID", "")
                try:
                    agent_id = uuid.UUID(x_agent_hdr) if x_agent_hdr else uuid.UUID(int=0)
                except ValueError:
                    agent_id = uuid.UUID(int=0)
                agent_id_str = str(agent_id)
                request.state.permissions = ["execute_agent", "view_risk"]
                request.state.role = "agent"
                request.state.actor = f"apikey:{key_data.get('key_prefix', api_key_header[:8])}"
                request.state.jwt_claims = {}

        if not tenant_id:
            raise HTTPException(status_code=401, detail="Authentication required")

        x_tenant = request.headers.get("X-Tenant-ID") or tenant_id_str
        if not x_tenant:
            raise HTTPException(status_code=401, detail="Tenant ID required")

        if x_tenant != tenant_id_str:
            logger.critical("tenant_isolation_violation", token_tenant=tenant_id_str, header_tenant=x_tenant)
            raise HTTPException(status_code=403, detail="Tenant mismatch detected")

        # Sprint 6 — Auto-Remediation revoked-agents check. When an
        # incident transitions to quarantined the executor adds the
        # agent_id to a per-tenant set; subsequent requests bearing the
        # same agent_id are rejected at auth. Single SISMEMBER call;
        # ~0.2 ms on the warm pool. Skipped for the unknown-agent
        # sentinel (UUID(int=0)) so unauthenticated/anonymous paths
        # aren't affected.
        if agent_id_str and agent_id_str != str(uuid.UUID(int=0)):
            try:
                from services.security.remediation.executor import is_agent_revoked
                if await is_agent_revoked(self.redis, tenant_id_str, agent_id_str):
                    logger.warning(
                        "agent_revoked_by_remediation",
                        tenant_id=tenant_id_str, agent_id=agent_id_str,
                    )
                    raise HTTPException(
                        status_code=401,
                        detail="agent_revoked_by_remediation",
                    )
            except HTTPException:
                raise
            except Exception as _rex:
                # Fail open on the revoked-set check — if Redis blips we
                # shouldn't take down auth for every request.
                logger.warning("revoked_agents_check_failed", error=str(_rex))

        # Org-level isolation: if the client sends X-Org-ID it MUST match the token's org_id.
        # The header is optional — older clients without org_id support are still served.
        x_org_id = request.headers.get("X-Org-ID")
        if x_org_id and auth_header:
            token_org_id = (
                (request.state.jwt_claims if hasattr(request.state, "jwt_claims") else {})
                .get("org_id", tenant_id_str)
            )
            if x_org_id != token_org_id:
                logger.critical(
                    "org_isolation_violation",
                    token_org=token_org_id,
                    header_org=x_org_id,
                )
                raise HTTPException(status_code=403, detail="Org mismatch detected")

        # Enforce strict SaaS invariant: org_id == tenant_id on ALL write paths
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            org_to_check = x_org_id or ((request.state.jwt_claims if hasattr(request.state, "jwt_claims") else {}).get("org_id"))
            if org_to_check:
                from sdk.common.invariants import (
                    InvariantViolation,
                    assert_org_consistency,
                )
                try:
                    assert_org_consistency(uuid.UUID(org_to_check), tenant_id, "gateway write path")
                except InvariantViolation as e:
                    logger.critical("strict_invariant_violation", detail=str(e))
                    raise HTTPException(status_code=403, detail=str(e))

        return tenant_id, agent_id or uuid.UUID(int=0), tenant_id_str, agent_id_str, jti

    async def _check_idempotency(
        self, request: Request, tenant_id_str: str, body_hash: str
    ) -> Response | None:
        """Check for idempotency hit. Returns a Response if hit, else None."""
        idem_key = request.headers.get("Idempotency-Key")
        if not (idem_key and request.method in ("POST", "PUT", "PATCH")):
            return None

        full_key = f"{_IDEMPOTENCY_PREFIX}{tenant_id_str}:{idem_key}"
        cached = await self.redis.get(full_key)
        if not cached:
            return None

        cached_data = json.loads(cached)
        if cached_data.get("payload_hash") != body_hash:
            IDEMPOTENCY_HITS_TOTAL.labels(
                tenant_id=tenant_id_str, outcome="conflict"
            ).inc()
            return self._deny(
                "Idempotency conflict: key used with different payload", 400
            )

        logger.info("idempotency_hit", key=idem_key)
        IDEMPOTENCY_HITS_TOTAL.labels(tenant_id=tenant_id_str, outcome="hit").inc()
        return Response(
            content=cached_data["body"],
            status_code=cached_data["status"],
            headers={**cached_data["headers"], "X-Idempotency-Hit": "true"},
            media_type="application/json",
        )

    async def _kill_token(self, request: Request) -> None:
        """Revoke a token immediately."""
        auth_header = request.headers.get("Authorization", "")
        token = auth_header.split(" ", 1)[1].strip() if " " in auth_header else auth_header
        if not token:
            return
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        revoke_key = f"{REDIS_REVOKE_PREFIX}{token_hash}"
        await self.redis.setex(revoke_key, 86400, "killed")

        jti = getattr(request.state, "jti", None)
        if jti:
            await self.redis.setex(f"{REDIS_REVOKE_PREFIX}jti:{jti}", 86400, "killed")

        asyncio.create_task(_safe_bg(self.redis.incr("acp:metrics:token_failures")))
        asyncio.create_task(_safe_bg(self.redis.incr("acp:metrics:kill_switch_events")))
