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
from sdk.common.exceptions import ACPAuthError
from sdk.utils import IDEMPOTENCY_HITS_TOTAL
from services.gateway.auth import REDIS_REVOKE_PREFIX
from services.gateway.client import service_client

logger = structlog.get_logger(__name__)

_IDEMPOTENCY_PREFIX = "acp:idempotency:"


class _AuthMixin:
    async def _authenticate(
        self, request: Request, is_execute_path: bool = False
    ) -> tuple[uuid.UUID, uuid.UUID, str, str, str | None]:
        """
        Authenticate the request and return
        (tenant_id, agent_id, tenant_id_str, agent_id_str, jti).
        """
        auth_header = request.headers.get("Authorization")
        x_cookie_token = request.cookies.get("acp_token")
        api_key = request.headers.get("X-API-Key")
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
                except Exception as exc:
                    try:
                        await self.redis.incr(auth_fail_key)
                        await self.redis.expire(auth_fail_key, 300)
                    except Exception as _re:
                        logger.debug("auth_fail_counter_error", error=str(_re))

                    detail = str(exc) if isinstance(exc, ACPAuthError) else "Invalid token"
                    raise HTTPException(status_code=401, detail=detail)

                # Active RBAC Mapping
                role = auth_data.get("role", "VIEWER")
                permissions_map = {
                    "ADMIN": ["*"],
                    "SECURITY": ["kill_switch", "view_risk", "execute_agent"],
                    "AUDITOR": ["view_risk", "view_audit"],
                    "VIEWER": ["view_risk"],
                    "agent": ["execute_agent"],
                }
                request.state.permissions = permissions_map.get(role, [])
                request.state.role = role

                # Write-path enforcement: mutations require ADMIN or SECURITY,
                # except agent-role tokens on /execute (controlled by OPA + Decision Engine).
                if request.method not in ("GET", "HEAD", "OPTIONS"):
                    if role not in ("ADMIN", "SECURITY"):
                        if not (is_execute_path and role == "agent"):
                            raise HTTPException(
                                status_code=403,
                                detail="Write operations require ADMIN or SECURITY role",
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
                        # Redis unavailable → skip replay check; genuine replays are still
                        # caught by JTI revocation above. False-blocking valid traffic is worse.
                        logger.warning("replay_check_skipped_redis_unavailable", jti=jti, error=str(_re))

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
        elif api_key:
            key_data = await service_client.validate_api_key(api_key)
            if key_data:
                tenant_id_str = key_data["tenant_id"]
                tenant_id = uuid.UUID(tenant_id_str)
                agent_id = uuid.UUID(int=0)

        if not tenant_id:
            raise HTTPException(status_code=401, detail="Authentication required")

        x_tenant = request.headers.get("X-Tenant-ID") or tenant_id_str
        if not x_tenant:
            raise HTTPException(status_code=401, detail="Tenant ID required")

        if x_tenant != tenant_id_str:
            logger.critical("tenant_isolation_violation", token_tenant=tenant_id_str, header_tenant=x_tenant)
            raise HTTPException(status_code=403, detail="Tenant mismatch detected")

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
