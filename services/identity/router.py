from __future__ import annotations

import asyncio
import os
import secrets
import uuid
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
from sdk.common.config import settings
from sdk.common.db import get_db
from sdk.common.deadline import check_deadline
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
            if claims.get("role", "").upper() != UserRole.ADMIN.value:
                raise HTTPException(status_code=403, detail="Admin role required")
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
        if role not in ("ADMIN", "SECURITY"):
            raise HTTPException(status_code=403, detail="ADMIN or SECURITY role required")
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
    elif user_id:
        stmt = select(User).where(User.id == user_id, User.tenant_id == tenant_id)
        res = await db.execute(stmt)
        user = res.scalar_one_or_none()
        if not user or not user.is_active:
            raise HTTPException(status_code=401, detail="User account is no longer active")

    # 2. Revoke old token
    await token_svc.revoke(token)

    # Issue new token
    new_token, expires_in = await token_svc.issue(
        tenant_id=tenant_id,
        agent_id=agent_id,
        user_id=user_id,
        role=role
    )

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

_TIER_RPM_DEFAULTS: dict[str, int] = {
    "basic":      60,
    "pro":       300,
    "enterprise": 1000,
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
            except Exception:
                pass
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


@router.get("/auth/sso/{provider}", summary="Initiate SSO login")
async def sso_login(
    provider: str,
    request: Request,
    tenant_id: str | None = None,
) -> RedirectResponse:
    """
    Redirect the browser to the OIDC provider's authorization endpoint.
    `tenant_id` query param determines which tenant the SSO user belongs to.
    Defaults to the demo tenant when omitted.
    """
    from services.identity.oidc import _provider_cfg, enabled_providers, generate_state

    if provider not in enabled_providers():
        raise HTTPException(status_code=404, detail=f"SSO provider '{provider}' is not configured")

    tid = tenant_id or "00000000-0000-0000-0000-000000000001"
    state = generate_state(_SSO_STATE_SECRET, provider, tid)

    base = str(request.base_url).rstrip("/")
    redirect_uri = f"{base}/auth/sso/{provider}/callback"

    cfg = _provider_cfg(provider)
    from services.identity.oidc import _get_discovery
    doc = await _get_discovery(provider)

    from urllib.parse import urlencode
    params = {
        "client_id":     cfg["client_id"],
        "redirect_uri":  redirect_uri,
        "response_type": "code",
        "scope":         cfg["scope"],
        "state":         state,
        "nonce":         secrets.token_urlsafe(16),
    }
    auth_url = f"{doc['authorization_endpoint']}?{urlencode(params)}"
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

    try:
        tenant_uuid = uuid.UUID(tenant_id_str)
    except ValueError:
        tenant_uuid = uuid.UUID("00000000-0000-0000-0000-000000000001")

    base = str(request.base_url).rstrip("/")
    redirect_uri = f"{base}/auth/sso/{provider}/callback"

    try:
        claims = await exchange_code(provider, code, redirect_uri)
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

    return APIResponse(data={"status": "ok"})
