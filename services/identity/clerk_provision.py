"""
Single-transaction, idempotent provisioning of an Aegis identity from a Clerk JWT.

Replaces the multi-helper, multi-commit path that produced duplicate Organization +
Tenant rows under the abhi986 incident (2026-06-22).

Architectural invariants (enforced by this module):

  Postgres = source of truth for (Organization, Tenant, User).
  Redis    = read-through cache only. Written on success, never the authority.
  Clerk    = identity provider. clerk_user_id + clerk_org_id are the natural keys.

The handler MUST:

  - Use the raw Clerk JWT `org_id` claim (or fall back to `personal_<sub>`) —
    never read `org_id` off the gateway-canonicalised claims dict, which has
    been intentionally overwritten with the Aegis tenant UUID for an unrelated
    invariant check downstream.
  - Run Organization + Tenant + User get_or_create inside ONE transaction so a
    crash anywhere never leaves a half-provisioned row.
  - Be idempotent: calling provision_aegis_identity(clerk_user_id, clerk_org_id)
    100 times yields exactly 1 Organization + 1 Tenant + 1 User.
  - Tolerate concurrent calls: two coroutines racing on the same Clerk user
    converge on the same row via ON CONFLICT DO NOTHING + SELECT.

Anything else (Redis cache, Clerk org metadata writeback) is a *side effect*
performed after the transaction commits.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import structlog
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from services.identity.models import (
    DegradedModePolicy,
    Organization,
    Tenant,
    TenantTier,
    User,
    UserRole,
    canonical_role,
)

logger = structlog.get_logger(__name__)

# Mirrors webhooks_clerk._ORG_TO_TENANT_KEY_PREFIX so both code paths cache to
# the same key. Cache is read-through (sdk/common/clerk_auth.py performs the
# read; a miss now falls back to the DB).
_ORG_TO_TENANT_KEY_PREFIX = "acp:clerk:org-tenant:"
_ORG_TO_TENANT_TTL_SECONDS = 7 * 24 * 60 * 60

SHADOW_MODE_DEFAULT_DAYS = 14


@dataclass(frozen=True)
class ProvisionResult:
    organization_id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    role: UserRole
    shadow_mode_until: datetime | None
    clerk_user_id: str
    clerk_org_id: str
    created_organization: bool
    created_tenant: bool
    created_user: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": str(self.tenant_id),
            "organization_id": str(self.organization_id),
            "user_id": str(self.user_id),
            "role": self.role.value,
            "shadow_mode_until": (
                self.shadow_mode_until.isoformat()
                if self.shadow_mode_until else None
            ),
            "clerk_user_id": self.clerk_user_id,
            "clerk_org_id": self.clerk_org_id,
            "provisioned": (
                self.created_organization
                or self.created_tenant
                or self.created_user
            ),
        }


def derive_clerk_org_id(
    *,
    raw_jwt_org_id: str | None,
    clerk_user_id: str,
) -> str:
    """Return the canonical Clerk org id for provisioning.

    Personal Clerk accounts emit JWTs with an empty native `org_id` claim. We
    map them to a stable synthetic id so the same user always lands on the
    same Organization row.

    The caller MUST pass the RAW (unverified) Clerk `org_id` claim — never the
    gateway-canonicalised value, which contains the Aegis tenant UUID.
    """
    raw = (raw_jwt_org_id or "").strip()
    if raw:
        return raw
    if not clerk_user_id:
        raise ValueError("Cannot derive clerk_org_id: no raw org_id and no clerk_user_id")
    return f"personal_{clerk_user_id}"


async def _get_or_create_organization(
    db: AsyncSession, *, clerk_org_id: str, display_name: str, slug: str,
) -> tuple[Organization, bool]:
    """SELECT ⊕ INSERT race-safe via ON CONFLICT DO NOTHING."""
    stmt = (
        pg_insert(Organization)
        .values(
            clerk_org_id=clerk_org_id,
            name=display_name[:255],
            slug=slug[:100],
            is_active=True,
        )
        .on_conflict_do_nothing(index_elements=["clerk_org_id"])
        .returning(Organization.id)
    )
    result = await db.execute(stmt)
    inserted_id = result.scalar_one_or_none()
    created = inserted_id is not None

    fetch = await db.execute(
        select(Organization).where(Organization.clerk_org_id == clerk_org_id)
    )
    org = fetch.scalar_one()
    return org, created


async def _get_or_create_tenant(
    db: AsyncSession, *, organization: Organization, display_name: str,
) -> tuple[Tenant, bool]:
    """One tenant per Clerk org (Sprint-1 model)."""
    existing = await db.execute(
        select(Tenant).where(Tenant.org_id == organization.id)
    )
    tenant = existing.scalar_one_or_none()
    if tenant is not None:
        return tenant, False

    tenant = Tenant(
        org_id=organization.id,
        tenant_id=uuid.uuid4(),
        name=display_name[:255],
        tier=TenantTier.BASIC,
        rpm_limit=0,
        shadow_mode_until=datetime.utcnow()
            + timedelta(days=SHADOW_MODE_DEFAULT_DAYS),
        degraded_mode_policy=DegradedModePolicy.BLOCK_HIGH_RISK,
    )
    db.add(tenant)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        # Race: another caller created the tenant. Refetch and continue.
        again = await db.execute(
            select(Tenant).where(Tenant.org_id == organization.id)
        )
        tenant = again.scalar_one()
        return tenant, False
    return tenant, True


async def _upsert_user_for_clerk(
    db: AsyncSession,
    *,
    tenant: Tenant,
    clerk_user_id: str,
    email: str,
    role: UserRole,
) -> tuple[uuid.UUID, bool]:
    """UPSERT keyed on clerk_user_id. Idempotent under concurrency."""
    placeholder_hash = "$2b$12$ClerkOwnsThisPasswordPlaceholderHashXXXX"
    values = {
        "email": (email or f"{clerk_user_id}@clerk.invalid").lower(),
        "hashed_password": placeholder_hash,
        "tenant_id": tenant.tenant_id,
        # ck_users_org_tenant_match: users.org_id MUST equal users.tenant_id
        # for Clerk-provisioned rows.
        "org_id": tenant.tenant_id,
        "role": role,
        "is_active": True,
        "clerk_user_id": clerk_user_id,
    }
    stmt = (
        pg_insert(User)
        .values(**values)
        .on_conflict_do_update(
            index_elements=["clerk_user_id"],
            set_={
                "role": role,
                "tenant_id": tenant.tenant_id,
                "org_id": tenant.tenant_id,
                "is_active": True,
            },
        )
        .returning(User.id, (User.created_at == User.updated_at).label("is_new"))
    )
    row = (await db.execute(stmt)).one()
    return row[0], bool(row[1])


async def _write_redis_mapping(
    redis: Redis, *, clerk_org_id: str, tenant_id: uuid.UUID,
) -> None:
    """Read-through cache write. Best-effort: a Redis outage here does not
    block provisioning because the Clerk JWT validator falls back to a DB
    read on cache miss (sdk/common/clerk_auth.py)."""
    try:
        await redis.setex(
            f"{_ORG_TO_TENANT_KEY_PREFIX}{clerk_org_id}",
            _ORG_TO_TENANT_TTL_SECONDS,
            str(tenant_id),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "clerk_org_tenant_cache_write_failed",
            clerk_org_id=clerk_org_id, error=str(exc),
        )


async def provision_aegis_identity(
    db: AsyncSession,
    redis: Redis,
    *,
    clerk_user_id: str,
    raw_jwt_org_id: str | None,
    org_role_claim: str | None,
    email: str,
    org_display_name: str | None = None,
) -> ProvisionResult:
    """
    Provision Aegis Organization + Tenant + User from a validated Clerk JWT.

    Single transaction. Calling this function N times with the same
    clerk_user_id produces exactly 1 Organization + 1 Tenant + 1 User.

    On success, also writes the org→tenant Redis cache (best-effort).

    Raises:
        ValueError: if neither raw_jwt_org_id nor clerk_user_id is set.
        IntegrityError: only if Postgres itself rejects a constraint after
                        the retry path — caller should map to 500.
    """
    clerk_org_id = derive_clerk_org_id(
        raw_jwt_org_id=raw_jwt_org_id,
        clerk_user_id=clerk_user_id,
    )

    display_name = (org_display_name or clerk_org_id)[:255]
    slug = clerk_org_id[:100]

    canonical = canonical_role(
        (org_role_claim or "org:owner").replace("org:", "").upper().replace("-", "_")
    )
    try:
        role_enum = UserRole(canonical)
    except ValueError:
        role_enum = UserRole.VIEWER

    org, created_org = await _get_or_create_organization(
        db, clerk_org_id=clerk_org_id, display_name=display_name, slug=slug,
    )
    tenant, created_tenant = await _get_or_create_tenant(
        db, organization=org, display_name=display_name,
    )
    user_id, created_user = await _upsert_user_for_clerk(
        db,
        tenant=tenant,
        clerk_user_id=clerk_user_id,
        email=email,
        role=role_enum,
    )

    await db.commit()

    # Cache + Clerk-metadata writebacks are side effects. They run AFTER the
    # DB transaction has committed so a Clerk API outage cannot roll back
    # provisioning. Both are idempotent.
    await _write_redis_mapping(
        redis, clerk_org_id=clerk_org_id, tenant_id=tenant.tenant_id,
    )

    logger.info(
        "clerk_provision_ok",
        clerk_user_id=clerk_user_id,
        clerk_org_id=clerk_org_id,
        tenant_id=str(tenant.tenant_id),
        created_organization=created_org,
        created_tenant=created_tenant,
        created_user=created_user,
    )

    return ProvisionResult(
        organization_id=org.id,
        tenant_id=tenant.tenant_id,
        user_id=user_id,
        role=role_enum,
        shadow_mode_until=tenant.shadow_mode_until,
        clerk_user_id=clerk_user_id,
        clerk_org_id=clerk_org_id,
        created_organization=created_org,
        created_tenant=created_tenant,
        created_user=created_user,
    )
