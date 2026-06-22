from __future__ import annotations

import asyncio
import os
import secrets
import uuid
from datetime import datetime, timedelta
from functools import partial
from typing import Annotated, Any

import bcrypt
import httpx
import structlog
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sdk.common.audit_stream import push_audit_event
from sdk.common.auth import extract_bearer_token, verify_internal_secret
from sdk.common.clerk_auth import ClerkTokenValidator
from sdk.common.config import settings
from sdk.common.db import get_db
from sdk.common.deadline import check_deadline
from sdk.common.exceptions import ACPAuthError
from sdk.common.invariants import assert_org_consistency
from sdk.common.redis import get_redis_client
from sdk.common.response import APIResponse
from services.identity.exceptions import CredentialNotFoundError
from services.identity.models import AgentCredential, CredentialStatus, User, UserRole
from services.identity.registry_client import registry_client
from services.identity.schemas import (
    AgentLoginRequest,
    CredentialCreateRequest,
    CredentialResponse,
    RevokeResponse,
    TokenIntrospectRequest,
    TokenIntrospectResponse,
    TokenResponse,
    UserCreate,
    UserLogin,
    UserResponse,
)
from services.identity.token_service import TokenService

router = APIRouter(tags=["identity"])
logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Sprint S4 (2026-06-20) — per-visitor anonymous demo tenant
# ─────────────────────────────────────────────────────────────────────
# /auth/demo/spawn is called by the gateway's /demo/spawn-workspace
# handler. The identity service writes the new row inside acp_identity
# (which the gateway DB connection cannot see) and returns the ids;
# the gateway then mints the JWT.
#
# Auth: internal-secret only (X-Internal-Secret). Public callers reach
# the gateway, not this endpoint directly.
#
# Isolation invariants enforced:
#   - Every demo session gets its own Tenant + User row.
#   - Tenant carries is_demo=true + demo_expires_at = now() + 30 min so
#     scripts/ops/cleanup_expired_demos.py can hard-delete them.
#   - shadow_mode_until = NULL — demo tenants enforce blocks visibly
#     so prospects see the wire transfer actually denied.
#   - rps/burst/daily caps are halved vs production defaults so one
#     abusive visitor can't budget-DoS the shared NAT.
@router.post(
    "/auth/demo/spawn",
    dependencies=[Depends(verify_internal_secret)],
    tags=["auth"],
)
async def spawn_demo_tenant(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Provision an isolated demo tenant + read-only OWNER user.
    Returns ids the gateway needs to mint the demo JWT."""
    import uuid as _uuid
    from datetime import UTC, datetime as _dt, timedelta as _td

    from services.identity.models import Organization, Tenant, User
    from sdk.common.roles import Role

    tenant_id = _uuid.uuid4()
    org_id = _uuid.uuid4()
    user_id = _uuid.uuid4()
    suffix = tenant_id.hex[:8]
    owner_email = f"demo+{suffix}@aegisagent.in"
    expires = _dt.now(UTC) + _td(minutes=30)

    org = Organization(
        id=org_id,
        name=f"Demo Org {suffix}",
        slug=f"demo-{suffix}",
        clerk_org_id=f"demo_{suffix}",
        is_active=True,
    )
    db.add(org)

    tenant = Tenant(
        tenant_id=tenant_id,
        org_id=org_id,
        name=f"demo-{suffix}",
        is_demo=True,
        demo_expires_at=expires,
        shadow_mode_until=None,
        requests_per_second=25,
        burst=50,
        daily_request_cap=10_000,
        # EH-2: hard $5/day Anthropic ceiling on every demo tenant. Without
        # this a single prospect can burn ~$60k of inference inside a
        # 30-minute session (1000-token prompts × ~6 req/min × 30 min ×
        # ~$0.015/1k = ~$2.70 — but 100k-token prompts on Claude-Opus push
        # it to ~$10 each, and 10 concurrent sessions multiply). $5 is
        # generous enough for a real interactive demo (~30 turns of
        # /v1/messages) and bounds the abuse blast radius to manageable.
        daily_inference_cost_cap_usd=5.0,
    )
    db.add(tenant)
    await db.flush()
    # EH-2 follow-up: passing shadow_mode_until=None to the Tenant
    # constructor doesn't actually set NULL — SQLAlchemy omits the
    # column from INSERT (Python value matches the model's default of
    # None), and Postgres then applies the server_default
    # ("now() + interval '14 days'"). Force NULL with an explicit UPDATE
    # so demo tenants enforce blocks visibly from second 0.
    from sqlalchemy import update as _sql_update  # noqa: PLC0415
    await db.execute(
        _sql_update(Tenant)
        .where(Tenant.tenant_id == tenant_id)
        .values(shadow_mode_until=None)
    )

    # Demo users have no real password; bcrypt-hash a fresh random
    # token so the NOT NULL constraint is satisfied without enabling a
    # back-door login (the demo session uses the JWT, never /auth/login).
    dummy_pw = secrets.token_urlsafe(32).encode()
    hashed_pw = bcrypt.hashpw(dummy_pw, bcrypt.gensalt(rounds=4)).decode()
    user = User(
        id=user_id,
        tenant_id=tenant_id,
        org_id=tenant_id,
        email=owner_email,
        hashed_password=hashed_pw,
        role=Role.OWNER,
    )
    db.add(user)
    await db.commit()

    logger.info(
        "demo_tenant_spawned",
        tenant_id=str(tenant_id), user_id=str(user_id), expires_at=expires.isoformat(),
    )
    return {
        "success": True,
        "data": {
            "tenant_id":   str(tenant_id),
            "org_id":      str(org_id),
            "user_id":     str(user_id),
            "owner_email": owner_email,
            "expires_at":  int(expires.timestamp()),
        },
    }

# Concurrency cap on auth endpoints — prevents PgBouncer pool exhaustion
# under burst login load (C10). 40 slots: leaves headroom for non-auth ops
# on the 50-connection PgBouncer pool.
_AUTH_MAX_CONCURRENCY = 40
_auth_semaphore: asyncio.Semaphore | None = None


def _get_auth_semaphore() -> asyncio.Semaphore:
    global _auth_semaphore
    if _auth_semaphore is None:
        _auth_semaphore = asyncio.Semaphore(_AUTH_MAX_CONCURRENCY)
    return _auth_semaphore


# =========================
# REDIS DEPENDENCY
# =========================


_redis_client: Any = None


def _get_redis_client() -> Any:
    global _redis_client
    if _redis_client is None:
        _redis_client = get_redis_client(settings.REDIS_URL, decode_responses=False)
    return _redis_client


async def get_redis() -> Any:
    """Yield the shared Redis client (one pool per process)."""
    yield _get_redis_client()


# =========================
# PROVISION CREDENTIALS
# =========================


@router.post(
    "/auth/credentials",
    response_model=APIResponse[CredentialResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Provision credentials for an agent",
    dependencies=[Depends(verify_internal_secret)],
)
async def provision_credentials(
    db: Annotated[AsyncSession, Depends(get_db)],
    payload: CredentialCreateRequest,
    x_tenant_id: Annotated[str, Header()],
    _: Annotated[bool, Depends(check_deadline)] = True,
) -> APIResponse[CredentialResponse]:
    """Admin provisions secrets for an agent."""
    tenant_id = uuid.UUID(x_tenant_id)
    exists = await registry_client.agent_exists(payload.agent_id, tenant_id=tenant_id)
    if not exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found or not active in registry",
        )

    secret_str = payload.secret.get_secret_value()
    # Directly use bcrypt library, as passlib is broken here.
    secret_hash = bcrypt.hashpw(secret_str.encode("utf-8"), bcrypt.gensalt()).decode(
        "utf-8"
    )

    # Check for existing credential (active or inactive) to update instead of insert
    # This ensures IDEMPOTENCY as requested in audit-pre.md
    stmt_existing = select(AgentCredential).where(
        AgentCredential.agent_id == payload.agent_id,
        AgentCredential.tenant_id == tenant_id
    )
    res_existing = await db.execute(stmt_existing)
    existing_cred = res_existing.scalar_one_or_none()

    if existing_cred:
        # Update existing credentials (idempotent path)
        existing_cred.secret_hash = secret_hash
        existing_cred.status = CredentialStatus.ACTIVE
        existing_cred.is_active = True
        credential = existing_cred
    else:
        # 2b. Hardened creation: explicitly set org_id to match tenant_id
        credential = AgentCredential(
            agent_id=payload.agent_id,
            secret_hash=secret_hash,
            tenant_id=tenant_id,
            org_id=tenant_id,  # 🔥 Mandatory for production constraints
            status=CredentialStatus.ACTIVE,
            is_active=True
        )
        db.add(credential)

    try:
        await db.commit()
    except IntegrityError as err:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Credentials conflict detected (unique constraint)",
        ) from err
    await db.refresh(credential)
    return APIResponse(data=CredentialResponse.model_validate(credential))


@router.post(
    "/auth/users",
    response_model=APIResponse[UserResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create new user",
)
async def create_user(
    db: Annotated[AsyncSession, Depends(get_db)],
    payload: Annotated[UserCreate, Body()],
    authorization: Annotated[str | None, Header()] = None,
    redis: Annotated[Redis | None, Depends(get_redis)] = None,
    _: Annotated[bool, Depends(check_deadline)] = True,
) -> APIResponse[UserResponse]:
    count_res = await db.execute(select(func.count()).select_from(User))
    user_count = count_res.scalar_one()

    if user_count > 0:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Admin token required")
        if not redis:
            raise HTTPException(status_code=500, detail="Redis connection failed")
        token_svc = TokenService(redis)
        try:
            claims = await token_svc.verify(extract_bearer_token(authorization) or "")
            # Sprint 1 — OWNER subsumes ADMIN for super-tenant CRUD.
            if claims.get("role", "").upper() not in ("OWNER", "ADMIN"):
                raise HTTPException(status_code=403, detail="OWNER or ADMIN role required")
        except Exception as err:
            raise HTTPException(status_code=401, detail="Invalid token") from err

    try:
        tenant_id = uuid.UUID(payload.tenant_id)
        org_id = uuid.UUID(payload.org_id) if payload.org_id else tenant_id

        # HARDENED: Verify SaaS Strict Invariant before write
        assert_org_consistency(org_id, tenant_id, "user creation")

        hashed_password = bcrypt.hashpw(
            payload.password.encode("utf-8"), bcrypt.gensalt()
        ).decode("utf-8")
        user = User(
            email=payload.email,
            hashed_password=hashed_password,
            tenant_id=tenant_id,
            org_id=org_id,
            role=payload.role,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return APIResponse(data=UserResponse.model_validate(user))
    except ValueError as err:
        raise HTTPException(status_code=400, detail="Invalid UUID format") from err
    except IntegrityError as err:
        raise HTTPException(status_code=400, detail="User already exists") from err


@router.get(
    "/auth/me",
    response_model=APIResponse[UserResponse],
    summary="Get current user details",
)
async def get_me(
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    authorization: Annotated[str, Header()],
    _: Annotated[bool, Depends(check_deadline)] = True,
) -> APIResponse[UserResponse]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid or missing auth header")

    token = extract_bearer_token(authorization) or ""

    # Sprint 1 — dispatch by token shape. Legacy HS256 tokens carry our
    # local `typ: ACP_ACCESS` claim and validate against JWT_SECRET_KEY.
    # Clerk RS256 tokens carry our Clerk issuer URL and validate against
    # Clerk's JWKS. /auth/me was originally written assuming only legacy
    # tokens existed; with ACP_AUTH_PROVIDER=both every Clerk-signed-in
    # user hit `Invalid token` here, which propagated as 401 to every
    # page that did an initial getMe() probe.
    from sdk.common.clerk_auth import (
        ClerkTokenValidator,
        looks_like_clerk_token,
    )

    claims: dict
    if looks_like_clerk_token(token):
        try:
            validator = ClerkTokenValidator(redis_client=redis)
            claims = await validator.validate(token)
        except Exception as err:
            raise HTTPException(
                status_code=401, detail=f"Invalid Clerk token: {type(err).__name__}",
            ) from err
        # The Clerk claims map onto our canonical shape via
        # validator.validate; aegis_user_id lives on the `user_id`
        # claim. If the user row hasn't been provisioned yet, fall back
        # to the clerk_user_id lookup so the first /auth/me on signup
        # doesn't 404.
        user_id_str = claims.get("user_id") or ""
        clerk_user_id = claims.get("clerk_user_id") or claims.get("sub") or ""
        result = None
        if user_id_str:
            try:
                result = await db.execute(
                    select(User).where(User.id == uuid.UUID(user_id_str)),
                )
            except ValueError:
                result = None
        user = result.scalar_one_or_none() if result is not None else None
        if user is None and clerk_user_id:
            r2 = await db.execute(
                select(User).where(User.clerk_user_id == clerk_user_id),
            )
            user = r2.scalar_one_or_none()
        if user is None:
            raise HTTPException(
                status_code=404,
                detail="User not provisioned — sign out and sign in again",
            )
        return APIResponse(data=UserResponse.model_validate(user))

    # Legacy HS256 path
    token_svc = TokenService(redis)
    try:
        claims = await token_svc.verify(token)
    except Exception as err:
        raise HTTPException(status_code=401, detail="Invalid token") from err

    user_id_str = claims.get("user_id")
    if not user_id_str:
        raise HTTPException(
            status_code=403, detail="Token is not associated with a User account"
        )

    try:
        user_id = uuid.UUID(user_id_str)
    except ValueError as err:
        raise HTTPException(
            status_code=400, detail="Invalid user ID format in token"
        ) from err

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return APIResponse(data=UserResponse.model_validate(user))


@router.post(
    "/auth/token",
    response_model=APIResponse[TokenResponse],
    summary="Login agent and return JWT",
)
async def login_agent(
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    payload: AgentLoginRequest,
    _: Annotated[bool, Depends(check_deadline)] = True,
) -> APIResponse[TokenResponse]:
    """Agent logs in with agent_id and secret."""
    async with _get_auth_semaphore():
        result = await db.execute(
            select(AgentCredential).where(AgentCredential.agent_id == payload.agent_id)
        )
        credential = result.scalar_one_or_none()

        if not credential or not credential.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid agent credentials or inactive agent",
            )

        # Verify secret — run synchronous bcrypt in thread pool to avoid blocking event loop (PE-5)
        secret_bytes = payload.secret.get_secret_value().encode("utf-8")
        hash_bytes = credential.secret_hash.encode("utf-8")
        match = await asyncio.get_event_loop().run_in_executor(
            None, partial(bcrypt.checkpw, secret_bytes, hash_bytes)
        )
        if not match:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid agent credentials",
            )

        # Fetch agent metadata from Registry so we can embed permissions + status
        # in the JWT — this eliminates all Registry calls from the execute hot-path.
        agent_status = "active"
        permissions: list[dict] = []
        try:
            agent_data = await registry_client.get_agent(credential.agent_id, tenant_id=credential.tenant_id)
            inner = agent_data.get("data", agent_data)
            agent_status = inner.get("status", "active")
            permissions = [
                {"tool_name": p["tool_name"], "action": p.get("action", "ALLOW")}
                for p in inner.get("permissions", [])
            ]
        except Exception as exc:
            logger.warning("registry_metadata_fetch_failed", agent_id=str(credential.agent_id), error=str(exc))
            pass  # Registry unavailable — token still issued; gateway falls back to cache

        # Resolve org_id from the Tenant table (if migrated); default to tenant_id
        org_id = credential.org_id

        token_svc = TokenService(redis)
        token, expires_in = await token_svc.issue(
            tenant_id=credential.tenant_id,
            agent_id=credential.agent_id,
            role="agent",
            org_id=org_id,
            agent_status=agent_status,
            permissions=permissions,
        )

        await push_audit_event(
            redis=redis,
            tenant_id=credential.tenant_id,
            agent_id=credential.agent_id,
            action="token_issue",
            metadata={"role": "agent", "type": "access_token", "permissions_embedded": len(permissions)}
        )

    return APIResponse(
        data=TokenResponse(
            access_token=token,
            expires_in=expires_in,
            tenant_id=credential.tenant_id,
            agent_id=credential.agent_id,
            role="agent",
        )
    )


@router.post(
    "/auth/login",
    summary="Login user and return JWT",
)
async def login_user(
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    payload: UserLogin,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
    _: Annotated[bool, Depends(check_deadline)] = True,
) -> APIResponse[TokenResponse]:
    async with _get_auth_semaphore():
        if x_tenant_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="X-Tenant-ID header is required for multi-tenant authentication",
            )
        try:
            tenant_uuid = uuid.UUID(x_tenant_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid Tenant UUID"
            )

        email = payload.email.strip().lower()
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

        if not user or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials"
            )

        if user.tenant_id != tenant_uuid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials or tenant mismatch"
            )

        # PE-5: bcrypt is CPU-bound, run in thread pool
        pw_bytes = payload.password.encode("utf-8")
        stored_hash = user.hashed_password.encode("utf-8")
        pw_valid = await asyncio.get_event_loop().run_in_executor(
            None, partial(bcrypt.checkpw, pw_bytes, stored_hash)
        )
        if not pw_valid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials"
            )

        # HARDENED: We fail-fast on data corruption. If org_id is NULL, we refuse to issue a token.
        org_id = user.org_id
        if org_id is None:
            logger.error("SECURITY_CRITICAL: org_id is NULL for user", user_id=str(user.id), tenant_id=str(user.tenant_id))
            raise HTTPException(
                status_code=500,
                detail="System integrity error: inconsistent account metadata"
            )

        # HARDENED: Verify SaaS Strict Invariant on read/login
        assert_org_consistency(org_id, user.tenant_id, "user login")

        token_svc = TokenService(redis)
        token, expires_in = await token_svc.issue(
            tenant_id=user.tenant_id, user_id=user.id, role=user.role, org_id=org_id
        )

        await push_audit_event(
            redis=redis,
            tenant_id=user.tenant_id,
            agent_id=None,
            action="user_login",
            metadata={"role": user.role, "user_id": str(user.id)},
        )

    return APIResponse(
        data=TokenResponse(
            access_token=token,
            expires_in=expires_in,
            tenant_id=str(user.tenant_id),
            role=user.role,
        )
    )


# =========================
# INTROSPECT / VERIFY
# =========================


@router.post(
    "/auth/introspect",
    response_model=APIResponse[TokenIntrospectResponse],
    summary="Verify token validity",
)
async def introspect(
    request: Request,
    redis: Annotated[Redis, Depends(get_redis)],
    payload: TokenIntrospectRequest,
    _: Annotated[bool, Depends(check_deadline)] = True,
) -> APIResponse[TokenIntrospectResponse]:
    token_hash = TokenService._hash(payload.token)
    rate_key = f"acp:ratelimit:introspect:token:{token_hash}"
    count_raw = await redis.incr(rate_key)
    if count_raw == 1:
        await redis.expire(rate_key, 60)
    if count_raw > 30:  # 30 introspects per minute per IP
        raise HTTPException(status_code=429, detail="Introspect rate limit exceeded")

    token_svc = TokenService(redis)
    try:
        claims = await token_svc.verify(payload.token)
        return APIResponse(
            data=TokenIntrospectResponse(
                active=True,
                agent_id=uuid.UUID(claims["agent_id"])
                if "agent_id" in claims
                else None,
                user_id=uuid.UUID(claims["user_id"]) if "user_id" in claims else None,
                tenant_id=uuid.UUID(claims["tenant_id"]),
                role=claims.get("role"),
                exp=claims.get("exp"),
                iat=claims.get("iat"),
            )
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("token_introspect_failed", error=str(exc))
        return APIResponse(data=TokenIntrospectResponse(active=False))


# =========================
# REVOKE
# =========================


@router.post(
    "/auth/revoke",
    response_model=APIResponse[RevokeResponse],
    summary="Revoke all tokens for agent",
)
async def revoke_all(
    redis: Annotated[Redis, Depends(get_redis)],
    db: Annotated[AsyncSession, Depends(get_db)],
    agent_id: uuid.UUID,
    authorization: Annotated[str, Header()],
) -> APIResponse[RevokeResponse]:
    """AS-5: Authenticated revoke — requires ADMIN or SECURITY role."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    token_svc = TokenService(redis)
    try:
        claims = await token_svc.verify(extract_bearer_token(authorization) or "")
        role = claims.get("role", "").upper()
        # Sprint 1 — OWNER + SECURITY_ANALYST added; legacy ADMIN/SECURITY still accepted.
        if role not in ("OWNER", "ADMIN", "SECURITY_ANALYST", "SECURITY"):
            raise HTTPException(
                status_code=403,
                detail="OWNER, ADMIN, or SECURITY_ANALYST role required",
            )
    except HTTPException:
        raise
    except Exception as err:
        raise HTTPException(status_code=401, detail="Invalid token") from err
    result = await db.execute(
        select(AgentCredential).where(AgentCredential.agent_id == agent_id)
    )
    credential = result.scalar_one_or_none()
    if not credential:
        raise CredentialNotFoundError()

    # Ownership check: caller must belong to the same tenant as the agent
    try:
        claims_tenant_id = uuid.UUID(claims.get("tenant_id", ""))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=403, detail="Invalid tenant in token")
    if credential.tenant_id != claims_tenant_id:
        raise HTTPException(status_code=403, detail="Cannot revoke credentials for agent in another tenant")

    credential.status = CredentialStatus.REVOKED
    credential.is_active = False
    await db.commit()

    token_svc = TokenService(redis)
    count = await token_svc.revoke_all_for_agent(agent_id)

    # RULE 2: Every action is audited
    await push_audit_event(
        redis=redis,
        tenant_id=credential.tenant_id,
        agent_id=agent_id,
        action="token_revoke_all",
        metadata={"count": count}
    )

    return APIResponse(
        data=RevokeResponse(
            agent_id=agent_id, revoked=True, message=f"Revoked {count} tokens"
        )
    )


@router.post(
    "/auth/refresh",
    response_model=APIResponse[TokenResponse],
    summary="Refresh access token",
)
async def refresh_token(
    redis: Annotated[Redis, Depends(get_redis)],
    db: Annotated[AsyncSession, Depends(get_db)],
    authorization: Annotated[str, Header()],
    _: Annotated[bool, Depends(check_deadline)] = True,
) -> APIResponse[TokenResponse]:
    """Refresh an access token using current token."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid or missing auth header")

    token = extract_bearer_token(authorization) or ""
    token_svc = TokenService(redis)

    try:
        claims = await token_svc.verify(token)
    except Exception as err:
        raise HTTPException(status_code=401, detail="Invalid token") from err

    tenant_id = uuid.UUID(claims.get("tenant_id", ""))
    agent_id = uuid.UUID(claims["agent_id"]) if "agent_id" in claims else None
    user_id = uuid.UUID(claims["user_id"]) if "user_id" in claims else None
    role = claims.get("role", "viewer")
    # Forward org_id from the validated claim so the refreshed token
    # carries the same SaaS-invariant tuple (without this the new token
    # falls back to tenant_id and the next strict-invariant check fails
    # 403 for any account where org_id ≠ tenant_id, which becomes the
    # rule once multi-org tiers ship).
    org_id_claim = claims.get("org_id")
    try:
        org_id = uuid.UUID(org_id_claim) if org_id_claim else None
    except (ValueError, TypeError):
        org_id = None

    # 1. HARDENING: Re-validate status in DB
    if agent_id:
        stmt = select(AgentCredential).where(
            AgentCredential.agent_id == agent_id,
            AgentCredential.tenant_id == tenant_id
        )
        res = await db.execute(stmt)
        cred = res.scalar_one_or_none()
        if not cred or cred.status != CredentialStatus.ACTIVE:
            raise HTTPException(status_code=401, detail="Agent credentials are no longer active")
        if org_id is None:
            org_id = cred.org_id
    elif user_id:
        stmt = select(User).where(User.id == user_id, User.tenant_id == tenant_id)
        res = await db.execute(stmt)
        user = res.scalar_one_or_none()
        if not user or not user.is_active:
            raise HTTPException(status_code=401, detail="User account is no longer active")
        if org_id is None:
            org_id = user.org_id

    # 2. Issue NEW token first. If issuance fails the caller still holds a
    #    valid old token and can retry. The previous order revoked the old
    #    token before issuing the new one, which left the caller stranded
    #    on any Redis hiccup mid-refresh (no valid token, next request 401s
    #    and they get bounced to /login mid-session).
    new_token, expires_in = await token_svc.issue(
        tenant_id=tenant_id,
        agent_id=agent_id,
        user_id=user_id,
        role=role,
        org_id=org_id,
    )

    # 3. Only after a successful mint do we revoke the old token. A failure
    #    here is logged but non-fatal — the new token is already valid and
    #    the old one will expire naturally at its original exp.
    try:
        await token_svc.revoke(token)
    except Exception as exc:
        logger.warning("refresh_revoke_old_failed", error=str(exc))

    return APIResponse(
        data=TokenResponse(
            access_token=new_token,
            expires_in=expires_in,
            tenant_id=str(tenant_id),
            agent_id=str(agent_id) if agent_id else None,
            role=role,
        )
    )


# =========================
# TENANT MANAGEMENT
# =========================


from services.identity.models import (  # noqa: E402 — avoids circular at module top
    DegradedModePolicy,
    Tenant,
    TenantTier,
)

# Per-tier per-MINUTE rate-limit fallback used when tenant.rpm_limit
# is unset. These have to stay >= the Sprint-3.2 token-bucket
# capacity (requests_per_second × 60) or the legacy 60-second bucket
# becomes the bottleneck on bursty workloads — exactly the bug that
# bit the brutal-audit /v1/messages probe at 1 RPS sustained on
# 2026-06-17 (the basic default used to be 60 RPM = 1 RPS, completely
# at odds with the documented 50 RPS token bucket). Defaults below
# match (and slightly exceed) the per-tier token-bucket capacity:
#   basic         50 RPS → 3000 RPM
#   pro          100 RPS → 6000 RPM
#   enterprise   200 RPS → 12000 RPM
_TIER_RPM_DEFAULTS: dict[str, int] = {
    "basic":      3000,
    "pro":        6000,
    "enterprise": 12000,
}


@router.get(
    "/auth/tenants/{tenant_id}",
    summary="Get tier and rate-limit metadata for a tenant",
    dependencies=[Depends(verify_internal_secret)],
)
async def get_tenant_metadata(
    tenant_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """
    Called by the Gateway ServiceClient to resolve real tier + rpm_limit.
    Falls back to basic defaults if no row exists yet.
    """
    result = await db.execute(select(Tenant).where(Tenant.tenant_id == tenant_id))
    tenant = result.scalar_one_or_none()

    if not tenant:
        return {
            "tenant_id":            str(tenant_id),
            "org_id":               str(tenant_id),
            "tier":                 "basic",
            "rpm_limit":            _TIER_RPM_DEFAULTS["basic"],
            "status":               "active",
            "degraded_mode_policy": DegradedModePolicy.BLOCK_HIGH_RISK.value,
            "requests_per_second":  50,
            "burst":                100,
            "daily_request_cap":    1_000_000,
            "monthly_request_cap":  None,
            # Sprint 3 — Shadow mode. NULL = never in shadow mode (legacy
            # tenant), otherwise an ISO-8601 timestamp the gateway checks
            # against now() to decide whether to downgrade deny/escalate
            # to `would_have_blocked`.
            "shadow_mode_until":    None,
        }

    effective_rpm = tenant.rpm_limit or _TIER_RPM_DEFAULTS.get(tenant.tier.value, 60)
    return {
        "tenant_id":            str(tenant.tenant_id),
        "org_id":               str(tenant.org_id),
        "tier":                 tenant.tier.value,
        "rpm_limit":            effective_rpm,
        "status":               "active" if tenant.is_active else "suspended",
        "degraded_mode_policy": tenant.degraded_mode_policy.value,
        "requests_per_second":  tenant.requests_per_second or 50,
        "burst":                tenant.burst or 100,
        "daily_request_cap":    tenant.daily_request_cap or 1_000_000,
        "monthly_request_cap":  tenant.monthly_request_cap if tenant.monthly_request_cap else None,
        "daily_inference_cost_cap_usd": (
            tenant.daily_inference_cost_cap_usd
            if tenant.daily_inference_cost_cap_usd is not None else None
        ),
        "shadow_mode_until":    (
            tenant.shadow_mode_until.isoformat()
            if tenant.shadow_mode_until is not None else None
        ),
        # Sprint 8 — per-resource-kind dollar weights for the
        # Blast-Radius dollar formula. The gateway's TenantMetadataCache
        # forwards this verbatim; the IAG router reads it to compute
        # dollar_estimate on every blast-radius response.
        "system_values":        dict(tenant.system_values or {}),
    }


@router.post(
    "/auth/tenants",
    summary="Create or update tenant tier/rpm_limit",
    dependencies=[Depends(verify_internal_secret)],
    status_code=201,
)
async def upsert_tenant(
    db: Annotated[AsyncSession, Depends(get_db)],
    body: dict,
) -> dict:
    """
    Create or update a tenant record. Used by provisioning scripts.
    Body: {tenant_id, org_id, tier, rpm_limit, name}
    """

    tenant_id_val = uuid.UUID(body["tenant_id"])
    org_id_val    = uuid.UUID(body.get("org_id", body["tenant_id"]))
    tier_val      = body.get("tier", "basic")
    rpm_val       = int(body.get("rpm_limit", _TIER_RPM_DEFAULTS.get(tier_val, 60)))
    name_val      = body.get("name", "Default Tenant")
    degraded_val  = body.get("degraded_mode_policy", DegradedModePolicy.BLOCK_HIGH_RISK.value)
    # Validate enum membership eagerly so a typo gets a 4xx rather than a 500 deeper in.
    degraded_policy = DegradedModePolicy(degraded_val)

    result = await db.execute(select(Tenant).where(Tenant.tenant_id == tenant_id_val))
    existing = result.scalar_one_or_none()

    # Sprint 3.2 quota fields — optional in the request body so existing
    # callers don't break; defaults match the migration server_default.
    rps_val      = int(body.get("requests_per_second", 50))
    burst_val    = int(body.get("burst", 100))
    daily_val    = int(body.get("daily_request_cap", 1_000_000))
    monthly_raw  = body.get("monthly_request_cap")
    monthly_val: int | None = int(monthly_raw) if monthly_raw is not None else None
    cost_cap_raw = body.get("daily_inference_cost_cap_usd")
    cost_cap_val: float | None = float(cost_cap_raw) if cost_cap_raw is not None else None

    if existing:
        existing.tier                 = TenantTier(tier_val)
        existing.rpm_limit            = rpm_val
        existing.name                 = name_val
        existing.degraded_mode_policy = degraded_policy
        existing.requests_per_second  = rps_val
        existing.burst                = burst_val
        existing.daily_request_cap    = daily_val
        existing.monthly_request_cap  = monthly_val
        existing.daily_inference_cost_cap_usd = cost_cap_val
    else:
        db.add(Tenant(
            org_id=org_id_val,
            tenant_id=tenant_id_val,
            name=name_val,
            tier=TenantTier(tier_val),
            rpm_limit=rpm_val,
            degraded_mode_policy=degraded_policy,
            requests_per_second=rps_val,
            burst=burst_val,
            daily_request_cap=daily_val,
            monthly_request_cap=monthly_val,
            daily_inference_cost_cap_usd=cost_cap_val,
        ))

    await db.commit()
    return {
        "status":               "ok",
        "tenant_id":            str(tenant_id_val),
        "tier":                 tier_val,
        "rpm_limit":            rpm_val,
        "degraded_mode_policy": degraded_policy.value,
        "requests_per_second":  rps_val,
        "burst":                burst_val,
        "daily_request_cap":    daily_val,
        "monthly_request_cap":  monthly_val,
        "daily_inference_cost_cap_usd": cost_cap_val,
    }


# =============================================================================
# Workspace / Shadow Mode (Sprint 3)
# =============================================================================
#
# /workspace/me        — return signed-in workspace summary, including the
#                        shadow_mode_until timestamp.
# /workspace/exit-shadow-mode — OWNER-only. Clear the shadow window so the
#                        next deny/escalate from the policy engine actually
#                        blocks the customer's tool call.
#
# Both endpoints require X-Tenant-ID (set by gateway after JWT validation)
# and reach the Tenant row via the same SQLAlchemy session the rest of the
# router uses. The OWNER check reads the X-ACP-Role header the gateway
# injects from the validated JWT (see services/gateway/_helpers.py:120).


def _canonical_role(role: str | None) -> str:
    """Project an X-ACP-Role header value onto the canonical Aegis vocab."""
    from sdk.common.roles import canonical_role as _cr
    return _cr(role)


@router.get(
    "/workspace/me",
    summary="Return the signed-in workspace summary (including shadow mode)",
    dependencies=[Depends(verify_internal_secret)],
)
async def workspace_me(
    db: Annotated[AsyncSession, Depends(get_db)],
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
) -> dict:
    """Owner-friendly view: name, tier, shadow_mode_until, agent counts."""
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header required")
    try:
        tenant_uuid = uuid.UUID(x_tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid X-Tenant-ID")

    result = await db.execute(select(Tenant).where(Tenant.tenant_id == tenant_uuid))
    tenant = result.scalar_one_or_none()

    if tenant is None:
        # No row yet — webhook hasn't landed, or this is a pre-Sprint-1 tenant.
        return {
            "tenant_id":         x_tenant_id,
            "name":              "Workspace",
            "tier":              "basic",
            "is_active":         True,
            "shadow_mode_until": None,
            "shadow_mode_active": False,
            "shadow_mode_days_left": None,
        }

    now = datetime.utcnow()
    shadow_until = tenant.shadow_mode_until
    if shadow_until is not None and shadow_until.tzinfo is not None:
        # Compare in naive UTC.
        shadow_until_naive = shadow_until.replace(tzinfo=None)
    else:
        shadow_until_naive = shadow_until
    shadow_active = bool(shadow_until_naive and shadow_until_naive > now)
    days_left = (
        int((shadow_until_naive - now).total_seconds() / 86400)
        if shadow_active and shadow_until_naive else None
    )

    return {
        "tenant_id":              str(tenant.tenant_id),
        "name":                   tenant.name,
        "tier":                   tenant.tier.value,
        "is_active":              tenant.is_active,
        "shadow_mode_until":      shadow_until.isoformat() if shadow_until else None,
        "shadow_mode_active":     shadow_active,
        "shadow_mode_days_left":  days_left,
    }


@router.post(
    "/workspace/exit-shadow-mode",
    summary="OWNER-only — exit shadow mode immediately (deny/escalate now blocks)",
    dependencies=[Depends(verify_internal_secret)],
    status_code=200,
)
async def workspace_exit_shadow_mode(
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
    x_acp_role: Annotated[str | None, Header(alias="X-ACP-Role")] = None,
    x_acp_actor: Annotated[str | None, Header(alias="X-ACP-Actor")] = None,
) -> dict:
    """
    Flip ``tenants.shadow_mode_until`` to now()-1s so the next deny/escalate
    from the decision engine actually blocks the customer's tool call.

    Auth: relies on the gateway's verify_role(OWNER) dependency in the
    proxy router AND on the X-ACP-Role header for defense-in-depth here.
    """
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header required")
    if _canonical_role(x_acp_role) != "OWNER":
        raise HTTPException(
            status_code=403,
            detail="Only the workspace OWNER can exit shadow mode",
        )
    try:
        tenant_uuid = uuid.UUID(x_tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid X-Tenant-ID")

    result = await db.execute(select(Tenant).where(Tenant.tenant_id == tenant_uuid))
    tenant = result.scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status_code=404, detail="Workspace not found")

    previous = tenant.shadow_mode_until
    # Set to slightly-in-the-past so any in-flight downgrade check resolves
    # to "no shadow window" immediately on the next request.
    tenant.shadow_mode_until = datetime.utcnow() - timedelta(seconds=1)
    await db.commit()

    # Bust the gateway's TenantMetadataCache so the change is picked up on
    # the very next /execute (cache TTL is otherwise ~10 minutes).
    try:
        await redis.delete(f"acp:tenant:meta:{tenant_uuid}")
    except Exception as exc:
        logger.warning("tenant_meta_cache_bust_failed", error=str(exc))

    # Audit row — operator must always be able to prove who hit the switch.
    try:
        await push_audit_event(
            redis=redis,
            tenant_id=tenant_uuid,
            agent_id=None,
            action="workspace_exit_shadow_mode",
            metadata={
                "actor":             x_acp_actor or "",
                "previous_until":    previous.isoformat() if previous else None,
            },
        )
    except Exception as exc:
        logger.warning("exit_shadow_audit_failed", error=str(exc))

    return {
        "status":            "ok",
        "tenant_id":         str(tenant.tenant_id),
        "shadow_mode_until": tenant.shadow_mode_until.isoformat(),
        "previous_until":    previous.isoformat() if previous else None,
    }


@router.patch(
    "/workspace/system-values",
    summary="OWNER-only — set per-resource-kind dollar weights for blast-radius",
    dependencies=[Depends(verify_internal_secret)],
)
async def patch_system_values(
    body: dict,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
    x_acp_role: Annotated[str | None, Header(alias="X-ACP-Role")] = None,
    x_acp_actor: Annotated[str | None, Header(alias="X-ACP-Actor")] = None,
) -> dict:
    """
    Merge the provided dict into ``tenants.system_values``. Keys are
    resource kinds (e.g. ``table``, ``api``, ``secret``); values are
    integer dollar weights. Negative values are rejected; zero clears a
    kind from the rollup.

    Sample body::

        { "table": 50000, "api": 100000, "secret": 25000 }

    The gateway's TenantMetadataCache is busted so the next
    ``/iag/.../blast-radius`` response picks up the new weights.
    """
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header required")
    if _canonical_role(x_acp_role) != "OWNER":
        raise HTTPException(
            status_code=403,
            detail="Only the workspace OWNER can update system values",
        )
    try:
        tenant_uuid = uuid.UUID(x_tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid X-Tenant-ID")

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")

    normalized: dict[str, int] = {}
    for key, value in body.items():
        if not isinstance(key, str) or not key.strip():
            raise HTTPException(
                status_code=400, detail=f"Invalid kind name: {key!r}",
            )
        try:
            weight = int(value)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400,
                detail=f"Weight for {key!r} must be an integer (got {value!r})",
            )
        if weight < 0:
            raise HTTPException(
                status_code=400,
                detail=f"Weight for {key!r} must be ≥0 (got {weight})",
            )
        normalized[key.strip().lower()] = weight

    result = await db.execute(select(Tenant).where(Tenant.tenant_id == tenant_uuid))
    tenant = result.scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status_code=404, detail="Workspace not found")

    # Merge — caller can drop a kind by sending it as 0 (or omit it to
    # leave it alone). PATCH semantics, not PUT.
    current = dict(tenant.system_values or {})
    for k, v in normalized.items():
        if v == 0:
            current.pop(k, None)
        else:
            current[k] = v
    tenant.system_values = current
    await db.commit()

    # Bust the TenantMetadataCache so /iag picks up new weights immediately.
    try:
        await redis.delete(f"acp:tenant:meta:{tenant_uuid}")
    except Exception as exc:
        logger.warning("tenant_meta_cache_bust_failed_system_values", error=str(exc))

    try:
        await push_audit_event(
            redis=redis,
            tenant_id=tenant_uuid,
            agent_id=None,
            action="workspace_system_values_update",
            metadata={
                "actor":           x_acp_actor or "",
                "applied":         normalized,
                "new_state":       current,
            },
        )
    except Exception as exc:
        logger.warning("system_values_audit_failed", error=str(exc))

    return {
        "status":        "ok",
        "tenant_id":     str(tenant.tenant_id),
        "system_values": current,
        "applied":       normalized,
    }


# =============================================================================
# Sprint 21 — Slack approvals: per-tenant webhook + signing secret.
# =============================================================================


@router.get(
    "/workspace/slack-config",
    summary="Internal — return tenant's Slack approvals config (webhook + signing secret)",
    dependencies=[Depends(verify_internal_secret)],
)
async def get_slack_config(
    db: Annotated[AsyncSession, Depends(get_db)],
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
) -> dict:
    """Returns the Slack approvals config so the gateway can post
    cards + verify signed callbacks.

    The signing_secret is included in the response: this endpoint is
    behind ``verify_internal_secret`` (only reachable from the gateway
    over the service mesh, never from the browser).
    """
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header required")
    try:
        tenant_uuid = uuid.UUID(x_tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid X-Tenant-ID")

    result = await db.execute(select(Tenant).where(Tenant.tenant_id == tenant_uuid))
    tenant = result.scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return {
        "status": "ok",
        "data": {
            "webhook_url":     tenant.slack_webhook_url or "",
            "signing_secret":  tenant.slack_approval_secret or "",
            "configured":      bool(tenant.slack_webhook_url and tenant.slack_approval_secret),
        },
    }


@router.put(
    "/workspace/slack-config",
    summary="OWNER/ADMIN — set the Slack incoming-webhook URL for approval cards",
    dependencies=[Depends(verify_internal_secret)],
)
async def put_slack_config(
    body: dict,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
    x_acp_role: Annotated[str | None, Header(alias="X-ACP-Role")] = None,
    x_acp_actor: Annotated[str | None, Header(alias="X-ACP-Actor")] = None,
) -> dict:
    """Body: ``{"webhook_url": "https://hooks.slack.com/services/…"}``.

    On first set the signing secret is auto-generated (32 random
    bytes). To rotate the secret, send ``{"rotate_secret": true}``
    alongside (or instead of) a webhook_url change. To disable Slack
    approvals entirely, send ``{"webhook_url": ""}`` — the secret is
    cleared as well.
    """
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header required")
    if _canonical_role(x_acp_role) not in ("OWNER", "ADMIN"):
        raise HTTPException(
            status_code=403,
            detail="Only OWNER / ADMIN can change Slack approvals config",
        )
    try:
        tenant_uuid = uuid.UUID(x_tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid X-Tenant-ID")

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")

    result = await db.execute(select(Tenant).where(Tenant.tenant_id == tenant_uuid))
    tenant = result.scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status_code=404, detail="Workspace not found")

    # Apply edits.
    if "webhook_url" in body:
        url = (body.get("webhook_url") or "").strip()
        if url and not url.startswith("https://hooks.slack.com/"):
            raise HTTPException(
                status_code=400,
                detail="webhook_url must be a Slack incoming webhook (https://hooks.slack.com/…)",
            )
        tenant.slack_webhook_url = url or None
        if not url:
            # Disable also clears the secret.
            tenant.slack_approval_secret = None

    rotate = bool(body.get("rotate_secret"))
    if tenant.slack_webhook_url and (not tenant.slack_approval_secret or rotate):
        import secrets as _secrets
        tenant.slack_approval_secret = _secrets.token_urlsafe(32)

    await db.commit()

    try:
        await push_audit_event(
            redis=redis,
            tenant_id=tenant_uuid,
            agent_id=None,
            action="workspace_slack_config_update",
            metadata={
                "actor":       x_acp_actor or "",
                "configured":  bool(tenant.slack_webhook_url and tenant.slack_approval_secret),
                "rotated":     rotate,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("slack_config_audit_failed", error=str(exc))

    return {
        "status": "ok",
        "data": {
            "configured": bool(tenant.slack_webhook_url and tenant.slack_approval_secret),
            "webhook_url": tenant.slack_webhook_url or "",
        },
    }


# =============================================================================
# Sprint 23 — Compliance Policy Packs.
# =============================================================================


@router.get(
    "/policy-packs/catalog",
    summary="The 5 sales-grade compliance packs Aegis ships with",
    dependencies=[Depends(verify_internal_secret)],
)
async def list_policy_packs_catalog() -> dict:
    """Static catalog of packs. Same content the wizard + Settings tab
    render — single source of truth lives in services/policy/packs.py."""
    from services.policy import packs as _packs  # local import = no startup cost when unused
    out: list[dict] = []
    for p in _packs.all_packs():
        out.append({
            "id":                  p.id,
            "label":               p.label,
            "blurb":               p.blurb,
            "framework_controls":  list(p.framework_controls),
            "default_capabilities": list(p.default_capabilities),
            "extra_escalations": [
                {
                    "id":            ep.id,
                    "label":         ep.label,
                    "approver_role": ep.approver_role,
                }
                for ep in p.extra_escalation_patterns
            ],
        })
    return {"status": "ok", "data": out}


@router.get(
    "/workspace/policy-packs",
    summary="Internal — tenant's enabled policy-pack IDs (gateway reads this every escalation)",
    dependencies=[Depends(verify_internal_secret)],
)
async def get_policy_packs(
    db: Annotated[AsyncSession, Depends(get_db)],
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
) -> dict:
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header required")
    try:
        tenant_uuid = uuid.UUID(x_tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid X-Tenant-ID")
    result = await db.execute(select(Tenant).where(Tenant.tenant_id == tenant_uuid))
    tenant = result.scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return {
        "status": "ok",
        "data": {
            "enabled": list(tenant.enabled_policy_packs or []),
        },
    }


@router.put(
    "/workspace/policy-packs",
    summary="OWNER/ADMIN — set the tenant's enabled policy-pack IDs",
    dependencies=[Depends(verify_internal_secret)],
)
async def put_policy_packs(
    body: dict,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
    x_acp_role: Annotated[str | None, Header(alias="X-ACP-Role")] = None,
    x_acp_actor: Annotated[str | None, Header(alias="X-ACP-Actor")] = None,
) -> dict:
    """Body: ``{"enabled": ["SOC2", "HIPAA"]}``.

    Unknown IDs are ignored (warns in the audit row, doesn't 400). The
    JSONB column is overwritten as PUT-not-PATCH semantics: an empty
    list disables every pack.
    """
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header required")
    if _canonical_role(x_acp_role) not in ("OWNER", "ADMIN"):
        raise HTTPException(
            status_code=403,
            detail="Only OWNER / ADMIN can change Compliance Policy Packs",
        )
    try:
        tenant_uuid = uuid.UUID(x_tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid X-Tenant-ID")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")
    raw_enabled = body.get("enabled") or []
    if not isinstance(raw_enabled, list):
        raise HTTPException(status_code=400, detail="`enabled` must be a JSON array")

    from services.policy import packs as _packs
    known = set(_packs.KNOWN_PACK_IDS)
    seen: set[str] = set()
    cleaned: list[str] = []
    ignored: list[str] = []
    for pid in raw_enabled:
        if not isinstance(pid, str):
            continue
        if pid in seen:
            continue
        seen.add(pid)
        if pid in known:
            cleaned.append(pid)
        else:
            ignored.append(pid)

    result = await db.execute(select(Tenant).where(Tenant.tenant_id == tenant_uuid))
    tenant = result.scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    tenant.enabled_policy_packs = cleaned
    await db.commit()

    try:
        await push_audit_event(
            redis=redis,
            tenant_id=tenant_uuid,
            agent_id=None,
            action="workspace_policy_packs_update",
            metadata={
                "actor":   x_acp_actor or "",
                "enabled": cleaned,
                "ignored": ignored,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("policy_packs_audit_failed", error=str(exc))

    return {
        "status": "ok",
        "data":   {"enabled": cleaned, "ignored": ignored},
    }


# =============================================================================
# Clerk synchronous provision — closes the signup → first-request race.
# =============================================================================
#
# The Clerk webhook receiver (services/identity/webhooks_clerk.py) is the
# authoritative provisioning path: every user.created / organization.created
# event lands an Aegis Org + Tenant + User row. But webhook delivery has
# latency (Clerk → svix → us, typically <2s but no SLA). For the case where
# the customer just signed up via the Clerk SignUp component and the
# frontend immediately wants to call /agents (or any tenant-scoped route),
# we expose this synchronous endpoint:
#
#   POST /auth/clerk/provision
#   Authorization: Bearer <Clerk JWT>
#
# The handler:
#   1. Validates the Clerk JWT via the shared ClerkTokenValidator (RS256+JWKS).
#   2. Extracts clerk_user_id (sub) + clerk_org_id (org_id native claim).
#   3. Idempotently upserts Organization + Tenant + User (same logic the
#      webhook handlers run — actually delegates to them).
#   4. Writes aegis_tenant_id + aegis_org_id back to Clerk's
#      org.public_metadata so the next JWT carries them as claims.
#   5. Returns the provisioned identifiers.
#
# Idempotency: if the webhook already landed first, the upserts are no-ops
# and the endpoint returns the existing identifiers — calling it is always
# safe to retry.
#
# This endpoint MUST be in the gateway's auth skip-list because the caller
# is presenting a Clerk JWT, not an Aegis-issued one, and at signup time
# they don't yet have an aegis_tenant_id in any token claim.


from services.identity.clerk_backend_api import (  # noqa: E402
    ClerkBackendAPIError,
    get_organization,
    list_user_organizations,
)
from services.identity.webhooks_clerk import (  # noqa: E402
    _handle_membership_created_or_updated,
    _handle_organization_created,
)


@router.post(
    "/auth/clerk/provision",
    summary="Synchronously provision Aegis Org+Tenant+User from a Clerk JWT",
)
async def provision_from_clerk(
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    # Sprint-QA fix: Optional so a missing header surfaces as 401
    # (canonical auth failure) instead of 422 (FastAPI validation). The
    # rest of the handler treats empty as "missing bearer" and raises 401.
    authorization: Annotated[str | None, Header()] = None,
) -> APIResponse[dict]:
    """
    Auth: Clerk Bearer JWT (not an Aegis-issued token). The handler
    validates the JWT via JWKS, then provisions the Aegis rows that the
    webhook would otherwise create asynchronously.

    Body: none. All required identifiers come from the JWT.

    Returns:
        {
          "tenant_id": <uuid>,
          "organization_id": <uuid>,
          "user_id": <uuid>,
          "role": <canonical Aegis role>,
          "shadow_mode_until": <iso8601>,
          "provisioned": <bool, true if this call created new rows>
        }
    """
    raw = extract_bearer_token(authorization or "") or ""
    if not raw:
        raise HTTPException(
            status_code=401,
            detail="Missing Authorization: Bearer header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    validator = ClerkTokenValidator(redis_client=redis)
    try:
        claims = await validator.validate(raw)
    except ACPAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    clerk_user_id = claims.get("clerk_user_id") or claims.get("sub") or ""
    if not clerk_user_id:
        raise HTTPException(
            status_code=400,
            detail="Clerk JWT missing `sub` (user id) claim",
        )

    # Read the RAW Clerk JWT `org_id` claim. The canonicalised `claims["org_id"]`
    # is intentionally overwritten by the gateway validator with the Aegis
    # tenant UUID for an unrelated invariant check downstream — trusting it
    # here would feed a UUID into `Organization.clerk_org_id` (a string slot),
    # which caused the abhi986 incident 2026-06-22 (duplicate Org/Tenant rows
    # for one Clerk user).
    from jose import jwt as _jose_jwt  # local import keeps cold start lean
    try:
        unverified = _jose_jwt.get_unverified_claims(raw)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot read Clerk JWT claims: {exc}",
        ) from exc
    raw_jwt_org_id = unverified.get("org_id") or ""

    # Resolve a display name. Best-effort — Clerk API outage doesn't block.
    org_display_name: str | None = None
    if raw_jwt_org_id:
        try:
            org_obj = await get_organization(raw_jwt_org_id)
            org_display_name = str(org_obj.get("name") or "")[:255] or None
        except ClerkBackendAPIError as exc:
            logger.warning(
                "clerk_get_organization_failed",
                clerk_org_id=raw_jwt_org_id, status=exc.status_code,
            )

    # Resolve email. Prefer the JWT claim; fall back to Clerk Backend API.
    email = (claims.get("email") or "").strip()
    if not email:
        try:
            memberships = await list_user_organizations(clerk_user_id)
            for m in memberships:
                pud = m.get("public_user_data") or {}
                identifier = pud.get("identifier") or ""
                if identifier:
                    email = identifier
                    break
        except ClerkBackendAPIError as exc:
            logger.warning(
                "clerk_list_memberships_failed",
                clerk_user_id=clerk_user_id, status=exc.status_code,
            )

    org_role = str(unverified.get("org_role") or "org:owner")

    from services.identity.clerk_provision import provision_aegis_identity

    try:
        result = await provision_aegis_identity(
            db, redis,
            clerk_user_id=clerk_user_id,
            raw_jwt_org_id=raw_jwt_org_id,
            org_role_claim=org_role,
            email=email,
            org_display_name=org_display_name,
        )
    except IntegrityError as exc:
        await db.rollback()
        logger.error(
            "clerk_provision_integrity_error",
            clerk_user_id=clerk_user_id,
            raw_jwt_org_id=raw_jwt_org_id,
            error=str(exc),
        )
        raise HTTPException(
            status_code=500,
            detail="Provisioning failed due to DB constraint violation",
        ) from exc

    return APIResponse(data=result.as_dict())


# =============================================================================
# SSO / OIDC — Google, Microsoft, Okta
# =============================================================================

_SSO_STATE_SECRET = os.environ.get("SSO_STATE_SECRET", settings.JWT_SECRET_KEY)


def _sso_config_key(tenant_id: str) -> str:
    return f"acp:sso:config:{tenant_id}"


def _mask_sso_secret(value: str | None) -> str:
    """Show only the last 8 characters of a secret value."""
    if not value:
        return ""
    return f"***{value[-8:]}" if len(value) > 8 else "****"


def _decode_redis_hash(raw: dict) -> dict[str, str]:
    return {
        (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
        for k, v in raw.items()
    }


@router.get("/auth/sso/config", summary="Get current SSO provider configuration")
async def get_sso_config(
    redis: Annotated[Redis, Depends(get_redis)],
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
) -> dict:
    """Return current SSO provider config from Redis (secrets masked)."""
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header required")

    raw: dict[bytes, bytes] = await redis.hgetall(_sso_config_key(x_tenant_id))
    cfg = _decode_redis_hash(raw)

    return {
        "provider_type": cfg.get("provider_type", ""),
        "entity_id": cfg.get("entity_id", ""),
        "sso_url": cfg.get("sso_url", ""),
        "certificate": _mask_sso_secret(cfg.get("certificate", "")),
        "client_id": cfg.get("client_id", ""),
        "client_secret": _mask_sso_secret(cfg.get("client_secret", "")),
        "issuer": cfg.get("issuer", ""),
    }


@router.post("/auth/sso/config", summary="Save SSO provider configuration")
async def save_sso_config(
    body: dict,
    redis: Annotated[Redis, Depends(get_redis)],
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
) -> dict:
    """Persist SSO provider config to Redis hash acp:sso:config:{tenant_id}."""
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header required")

    allowed_fields = ("provider_type", "entity_id", "sso_url", "certificate", "client_id", "client_secret", "issuer")
    mapping: dict[str, str] = {}
    for field in allowed_fields:
        if field in body:
            mapping[field] = str(body[field])

    if not mapping:
        raise HTTPException(status_code=400, detail="No recognised SSO config fields provided")

    await redis.hset(_sso_config_key(x_tenant_id), mapping=mapping)
    logger.info("sso_config_saved", tenant_id=x_tenant_id, fields=list(mapping.keys()))
    return {"status": "ok", "saved": list(mapping.keys())}


@router.post("/auth/sso/config/test", summary="Test SSO provider configuration reachability")
async def test_sso_config(
    redis: Annotated[Redis, Depends(get_redis)],
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
) -> dict:
    """
    Validate SSO config by attempting a metadata fetch.
    - SAML: GET entity_id URL
    - OIDC: GET issuer/.well-known/openid-configuration
    Returns {reachable: bool, issuer: str, status: str}
    """
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header required")

    raw: dict[bytes, bytes] = await redis.hgetall(_sso_config_key(x_tenant_id))
    cfg = _decode_redis_hash(raw)

    provider_type = cfg.get("provider_type", "oidc").lower()
    issuer = cfg.get("issuer", "")
    entity_id = cfg.get("entity_id", "")

    if provider_type == "saml":
        test_url = entity_id
        if not test_url:
            return {"reachable": False, "issuer": issuer, "status": "no_entity_id_configured"}
    else:
        # OIDC: fetch well-known discovery document
        base = issuer.rstrip("/") if issuer else ""
        test_url = f"{base}/.well-known/openid-configuration" if base else ""
        if not test_url:
            return {"reachable": False, "issuer": issuer, "status": "no_issuer_configured"}

    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            resp = await client.get(test_url)
        reachable = resp.status_code < 400
        discovered_issuer = issuer
        if provider_type == "oidc" and reachable:
            try:
                doc = resp.json()
                discovered_issuer = doc.get("issuer", issuer)
            except Exception as exc:
                # OIDC discovery response wasn't JSON or the JSON shape
                # didn't expose an issuer. Fall back to the configured
                # issuer so the operator can still see the URL came back
                # 2xx — log so a future "we returned a wrong issuer" bug
                # is debuggable.
                logger.warning(
                    "oidc_discovery_parse_failed",
                    test_url=test_url, error=str(exc),
                )
        return {
            "reachable": reachable,
            "issuer": discovered_issuer,
            "status": f"http_{resp.status_code}",
        }
    except Exception as exc:
        logger.warning("sso_config_test_failed", url=test_url, error=str(exc))
        return {"reachable": False, "issuer": issuer, "status": f"unreachable: {type(exc).__name__}"}


@router.get("/auth/sso/providers", summary="List enabled SSO providers")
async def list_sso_providers() -> dict:
    """Returns provider names that have been configured via env vars."""
    from services.identity.oidc import enabled_providers
    return {"providers": enabled_providers()}


_PKCE_REDIS_PREFIX = "acp:sso_pkce:"
_PKCE_TTL_SECONDS = 600  # match the state token's max_age


@router.get("/auth/sso/{provider}", summary="Initiate SSO login")
async def sso_login(
    provider: str,
    request: Request,
    redis: Annotated[Redis, Depends(get_redis)],
    tenant_id: str | None = None,
) -> RedirectResponse:
    """
    Redirect the browser to the OIDC provider's authorization endpoint.
    The `tenant_id` query parameter is REQUIRED — it determines which tenant the
    SSO user will be associated with. There is no demo-tenant fallback.
    A PKCE verifier is generated, stored in Redis under the state token, and
    presented to the IdP token endpoint at callback (RFC 7636).
    """
    from services.identity.oidc import build_auth_url, build_pkce_challenge, enabled_providers, generate_state

    if provider not in enabled_providers():
        raise HTTPException(status_code=404, detail=f"SSO provider '{provider}' is not configured")

    if not tenant_id:
        raise HTTPException(
            status_code=400,
            detail="tenant_id query parameter is required for SSO login",
        )
    try:
        uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="tenant_id must be a valid UUID")

    code_verifier, code_challenge = build_pkce_challenge()
    state = generate_state(_SSO_STATE_SECRET, provider, tenant_id)

    # Store the PKCE verifier under the state token. TTL bounded so the same
    # state cannot be replayed beyond the auth-code lifetime.
    await redis.setex(f"{_PKCE_REDIS_PREFIX}{state}", _PKCE_TTL_SECONDS, code_verifier)

    base = str(request.base_url).rstrip("/")
    redirect_uri = f"{base}/auth/sso/{provider}/callback"
    auth_url = await build_auth_url(provider, redirect_uri, state, code_challenge)
    return RedirectResponse(auth_url, status_code=302)


@router.get("/auth/sso/{provider}/callback", summary="Handle SSO callback")
async def sso_callback(
    provider: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """
    Exchange the authorization code for an id_token, upsert the user,
    issue an ACP JWT, and redirect to the dashboard with an httpOnly cookie.
    """
    from services.identity.oidc import exchange_code, verify_state

    if error:
        logger.warning("sso_provider_error", provider=provider, error=error)
        return RedirectResponse("/?sso_error=provider_denied", status_code=302)

    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state")

    # CSRF verification — state encodes "{provider}|{tenant_id}|{ts}|{sig}"
    try:
        _, tenant_id_str = verify_state(_SSO_STATE_SECRET, state)
    except ValueError as exc:
        logger.warning("sso_state_invalid", error=str(exc))
        raise HTTPException(status_code=400, detail="Invalid state parameter")

    # The state carries the tenant_id the user kicked off SSO for; reject any
    # malformed value rather than silently land them in the demo tenant.
    try:
        tenant_uuid = uuid.UUID(tenant_id_str)
    except ValueError:
        logger.warning("sso_state_invalid_tenant", tenant_id=tenant_id_str)
        raise HTTPException(status_code=400, detail="Invalid tenant_id in state parameter")

    # PKCE — recover the verifier the /sso/{provider} handler stored under the
    # state token. SET-EX-then-GETDEL would be ideal, but redis-py exposes
    # GETDEL as get + delete; we issue both so the verifier cannot be replayed.
    pkce_key = f"{_PKCE_REDIS_PREFIX}{state}"
    code_verifier_raw = await redis.get(pkce_key)
    await redis.delete(pkce_key)
    if not code_verifier_raw:
        logger.warning("sso_pkce_verifier_missing", provider=provider)
        raise HTTPException(status_code=400, detail="PKCE verifier missing or expired")
    code_verifier = (
        code_verifier_raw.decode("ascii")
        if isinstance(code_verifier_raw, bytes)
        else code_verifier_raw
    )

    base = str(request.base_url).rstrip("/")
    redirect_uri = f"{base}/auth/sso/{provider}/callback"

    try:
        claims = await exchange_code(provider, code, redirect_uri, code_verifier)
    except Exception as exc:
        logger.error("sso_code_exchange_failed", provider=provider, error=str(exc))
        return RedirectResponse("/?sso_error=exchange_failed", status_code=302)

    email = (claims.get("email") or "").strip().lower()

    if not email:
        return RedirectResponse("/?sso_error=no_email", status_code=302)

    # Upsert user — find by email + tenant, create if not present
    result = await db.execute(
        select(User).where(User.email == email, User.tenant_id == tenant_uuid)
    )
    user = result.scalar_one_or_none()

    if not user:
        # SSO users get a random unusable password hash (they can only log in via SSO)
        dummy_pw = bcrypt.hashpw(secrets.token_bytes(32), bcrypt.gensalt()).decode()
        user = User(
            email=email,
            hashed_password=dummy_pw,
            tenant_id=tenant_uuid,
            org_id=tenant_uuid,
            role=UserRole.VIEWER,
            is_active=True,
        )
        db.add(user)
        try:
            await db.commit()
            await db.refresh(user)
        except IntegrityError:
            await db.rollback()
            result = await db.execute(
                select(User).where(User.email == email, User.tenant_id == tenant_uuid)
            )
            user = result.scalar_one_or_none()
            if not user:
                return RedirectResponse("/?sso_error=db_error", status_code=302)

    if not user.is_active:
        return RedirectResponse("/?sso_error=account_disabled", status_code=302)

    token_svc = TokenService(redis)
    token, expires_in = await token_svc.issue(
        tenant_id=user.tenant_id,
        user_id=user.id,
        role=user.role,
        org_id=user.org_id or user.tenant_id,
    )

    await push_audit_event(
        redis=redis,
        tenant_id=user.tenant_id,
        agent_id=None,
        action="user_login",
        metadata={"role": user.role, "user_id": str(user.id), "sso_provider": provider},
    )

    logger.info("sso_login_success", provider=provider, email=email, tenant_id=str(tenant_uuid))

    response = RedirectResponse("/", status_code=302)
    response.set_cookie(
        "acp_token",
        token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=expires_in,
        path="/",
    )
    return response


# =============================================================================
# USER MANAGEMENT — GET /users, POST /users/invite, PATCH /users/{user_id},
#                   DELETE /users/{user_id}
# =============================================================================


@router.get(
    "/users",
    response_model=APIResponse[list],
    summary="List users for the tenant",
    dependencies=[Depends(verify_internal_secret)],
)
async def list_users(
    db: Annotated[AsyncSession, Depends(get_db)],
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
    role: str | None = None,
    is_active: bool | None = None,
) -> APIResponse[list]:
    """List users for the authenticated tenant. Filter by role and/or is_active."""
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header required")
    try:
        tenant_uuid = uuid.UUID(x_tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid Tenant UUID")

    q = select(User).where(User.tenant_id == tenant_uuid)
    if role is not None:
        try:
            role_enum = UserRole(role.upper())
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid role '{role}'. Valid: {[r.value for r in UserRole]}",
            )
        q = q.where(User.role == role_enum)
    if is_active is not None:
        q = q.where(User.is_active == is_active)
    q = q.order_by(User.id)

    result = await db.execute(q)
    users = result.scalars().all()

    return APIResponse(
        data=[
            {
                "id":         str(u.id),
                "email":      u.email,
                "role":       u.role,
                "is_active":  u.is_active,
                "created_at": u.created_at.isoformat() if hasattr(u, "created_at") and u.created_at else None,
            }
            for u in users
        ]
    )


@router.post(
    "/users/invite",
    response_model=APIResponse[dict],
    status_code=status.HTTP_201_CREATED,
    summary="Invite a new user",
    dependencies=[Depends(verify_internal_secret)],
)
async def invite_user(
    body: dict,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
) -> APIResponse[dict]:
    """
    Create a new user via invitation.

    Body: {email, role}
    Role must be one of the UserRole enum values.
    Assigns a random secure password (user must reset via SSO or password reset).
    Writes an audit row with action="user_invited".
    """
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header required")
    try:
        tenant_uuid = uuid.UUID(x_tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid Tenant UUID")

    email = str(body.get("email", "")).strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="'email' is required")

    role_raw = str(body.get("role", "")).upper()
    try:
        role_enum = UserRole(role_raw)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid role '{role_raw}'. Valid: {[r.value for r in UserRole]}",
        )

    # Generate a random secure password — user must reset before use
    random_pw = secrets.token_urlsafe(24)
    hashed_password = bcrypt.hashpw(random_pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    user = User(
        email=email,
        hashed_password=hashed_password,
        tenant_id=tenant_uuid,
        org_id=tenant_uuid,
        role=role_enum,
        is_active=True,
    )
    db.add(user)
    try:
        await db.commit()
        await db.refresh(user)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="User with this email already exists")

    # Audit trail
    try:
        await push_audit_event(
            redis=redis,
            tenant_id=tenant_uuid,
            agent_id=None,
            action="user_invited",
            metadata={"email": email, "role": role_enum},
        )
    except Exception as _audit_exc:
        logger.warning("user_invite_audit_failed", error=str(_audit_exc))

    return APIResponse(
        data={
            "id":         str(user.id),
            "email":      user.email,
            "role":       user.role,
            "is_active":  user.is_active,
            "created_at": user.created_at.isoformat() if hasattr(user, "created_at") and user.created_at else None,
        }
    )


# sprint-6.1 — surgical PATCH for tenant quota / tier updates.
# The Stripe webhook (services/gateway/routers/stripe_webhook.py) calls
# this to apply tier changes on subscription.created/updated/deleted.
# Distinct from POST /auth/tenants (which is a full upsert used by
# provisioning); this endpoint only updates the columns the request
# explicitly sets, leaving everything else alone.
@router.patch(
    "/admin/tenants/{tenant_id}",
    response_model=APIResponse[dict],
    summary="Patch a tenant's tier / quota columns (subscription-driven)",
    dependencies=[Depends(verify_internal_secret)],
)
async def patch_admin_tenant(
    tenant_id: str,
    body: dict,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> APIResponse[dict]:
    """
    Partially update a tenant. Only the columns present in the body are
    written; absent columns keep their current values.

    Accepted body keys:
      - tier                            (TenantTier enum value)
      - rpm_limit                       (int)
      - requests_per_second             (int)
      - burst                           (int)
      - daily_request_cap               (int)
      - monthly_request_cap             (int | null)
      - daily_inference_cost_cap_usd    (float | null)
      - degraded_mode_policy            (DegradedModePolicy enum value)
      - is_active                       (bool)

    Returns the updated row's metadata. Logs every applied field so the
    SOC2 audit trail captures Stripe-driven changes.
    """
    try:
        tenant_uuid = uuid.UUID(tenant_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="tenant_id must be a UUID") from exc

    result = await db.execute(select(Tenant).where(Tenant.tenant_id == tenant_uuid))
    existing = result.scalar_one_or_none()
    if existing is None:
        raise HTTPException(status_code=404, detail="Tenant not found")

    applied: dict[str, Any] = {}

    if "tier" in body:
        try:
            existing.tier = TenantTier(body["tier"])
            applied["tier"] = body["tier"]
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid tier: {body['tier']}") from exc

    if "rpm_limit" in body:
        existing.rpm_limit = int(body["rpm_limit"])
        applied["rpm_limit"] = existing.rpm_limit

    if "requests_per_second" in body:
        existing.requests_per_second = int(body["requests_per_second"])
        applied["requests_per_second"] = existing.requests_per_second

    if "burst" in body:
        existing.burst = int(body["burst"])
        applied["burst"] = existing.burst

    if "daily_request_cap" in body:
        existing.daily_request_cap = int(body["daily_request_cap"])
        applied["daily_request_cap"] = existing.daily_request_cap

    if "monthly_request_cap" in body:
        v = body["monthly_request_cap"]
        existing.monthly_request_cap = int(v) if v is not None else None
        applied["monthly_request_cap"] = existing.monthly_request_cap

    if "daily_inference_cost_cap_usd" in body:
        v = body["daily_inference_cost_cap_usd"]
        existing.daily_inference_cost_cap_usd = float(v) if v is not None else None
        applied["daily_inference_cost_cap_usd"] = existing.daily_inference_cost_cap_usd

    if "degraded_mode_policy" in body:
        try:
            existing.degraded_mode_policy = DegradedModePolicy(body["degraded_mode_policy"])
            applied["degraded_mode_policy"] = body["degraded_mode_policy"]
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid degraded_mode_policy: {body['degraded_mode_policy']}",
            ) from exc

    if "is_active" in body:
        existing.is_active = bool(body["is_active"])
        applied["is_active"] = existing.is_active

    if not applied:
        raise HTTPException(status_code=400, detail="No updatable fields in body")

    await db.commit()
    logger.info(
        "tenant_patched",
        tenant_id=str(tenant_uuid),
        applied=applied,
    )
    return APIResponse(data={
        "status":    "updated",
        "tenant_id": str(tenant_uuid),
        "applied":   applied,
    })


@router.patch(
    "/users/{user_id}",
    response_model=APIResponse[dict],
    summary="Update user role or active status",
    dependencies=[Depends(verify_internal_secret)],
)
async def update_user(
    user_id: str,
    body: dict,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
) -> APIResponse[dict]:
    """
    Update a user's role or is_active status.

    Body: {role?: str, is_active?: bool}
    Writes an audit row with action="user_updated".
    """
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header required")
    try:
        tenant_uuid = uuid.UUID(x_tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid Tenant UUID")

    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user_id format")

    result = await db.execute(
        select(User).where(User.id == user_uuid, User.tenant_id == tenant_uuid)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    changed_fields: list[str] = []

    if "role" in body:
        role_raw = str(body["role"]).upper()
        try:
            role_enum = UserRole(role_raw)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid role '{role_raw}'. Valid: {[r.value for r in UserRole]}",
            )
        if user.role != role_enum:
            user.role = role_enum
            changed_fields.append("role")

    if "is_active" in body:
        new_active = bool(body["is_active"])
        if user.is_active != new_active:
            user.is_active = new_active
            changed_fields.append("is_active")

    if changed_fields:
        await db.commit()
        await db.refresh(user)

        try:
            await push_audit_event(
                redis=redis,
                tenant_id=tenant_uuid,
                agent_id=None,
                action="user_updated",
                metadata={"user_id": user_id, "changed_fields": changed_fields},
            )
        except Exception as _audit_exc:
            logger.warning("user_update_audit_failed", error=str(_audit_exc))

    return APIResponse(
        data={
            "id":         str(user.id),
            "email":      user.email,
            "role":       user.role,
            "is_active":  user.is_active,
            "created_at": user.created_at.isoformat() if hasattr(user, "created_at") and user.created_at else None,
        }
    )


@router.delete(
    "/users/{user_id}",
    response_model=APIResponse[dict],
    summary="Soft-delete (deactivate) a user",
    dependencies=[Depends(verify_internal_secret)],
)
async def deactivate_user(
    user_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
) -> APIResponse[dict]:
    """
    Soft-delete a user by setting is_active = False.

    Does NOT hard-delete the record.
    Writes an audit row with action="user_deactivated".
    """
    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header required")
    try:
        tenant_uuid = uuid.UUID(x_tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid Tenant UUID")

    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user_id format")

    result = await db.execute(
        select(User).where(User.id == user_uuid, User.tenant_id == tenant_uuid)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_active = False
    await db.commit()

    try:
        await push_audit_event(
            redis=redis,
            tenant_id=tenant_uuid,
            agent_id=None,
            action="user_deactivated",
            metadata={"user_id": user_id, "email": user.email},
        )
    except Exception as _audit_exc:
        logger.warning("user_deactivate_audit_failed", error=str(_audit_exc))


# =========================
# ADMIN TENANT LIST (Phase 9)
# =========================


@router.get(
    "/admin/tenants",
    summary="List all tenants (admin view)",
    dependencies=[Depends(verify_internal_secret)],
)
async def list_admin_tenants(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Return all tenant rows from acp_identity.tenants.

    Protected by internal-secret so only the gateway (or ops scripts) can call
    it — never exposed to end-user tokens directly.

    Returns:
        {"data": [...tenant objects...]}
    """
    result = await db.execute(select(Tenant).order_by(Tenant.created_at.desc()))
    tenants = result.scalars().all()

    rows = []
    for t in tenants:
        rows.append({
            "id":                   str(t.id),
            "name":                 t.name,
            "tenant_id":            str(t.tenant_id),
            "plan":                 t.tier.value,
            "is_active":            t.is_active,
            "created_at":           t.created_at.isoformat() if t.created_at else None,
            "requests_per_second":  t.requests_per_second,
            "burst":                t.burst,
            "daily_request_cap":    t.daily_request_cap,
            "monthly_request_cap":  t.monthly_request_cap,
        })

    return {"data": rows}


@router.get(
    "/admin/tenants/{tenant_id}",
    summary="Get a single tenant by tenant_id (admin view)",
    dependencies=[Depends(verify_internal_secret)],
)
async def get_admin_tenant(
    tenant_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Return a single tenant row by its tenant_id UUID.

    Protected by internal-secret so only the gateway (or ops scripts) can call
    it — never exposed to end-user tokens directly.

    Returns:
        {"data": {...tenant object...}}
    """
    try:
        tenant_uuid = uuid.UUID(tenant_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid tenant_id UUID format")

    result = await db.execute(select(Tenant).where(Tenant.tenant_id == tenant_uuid))
    tenant = result.scalar_one_or_none()

    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")

    return {
        "data": {
            "id":                   str(tenant.id),
            "name":                 tenant.name,
            "tenant_id":            str(tenant.tenant_id),
            "plan":                 tenant.tier.value,
            "is_active":            tenant.is_active,
            "created_at":           tenant.created_at.isoformat() if tenant.created_at else None,
            "requests_per_second":  tenant.requests_per_second,
            "burst":                tenant.burst,
            "daily_request_cap":    tenant.daily_request_cap,
            "monthly_request_cap":  tenant.monthly_request_cap,
        }
    }
