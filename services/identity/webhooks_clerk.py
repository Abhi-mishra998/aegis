"""
Clerk → Aegis webhook receiver.

Receives Clerk's user / organization / organizationMembership webhooks
(Svix-signed) and provisions the matching Aegis Organization, Tenant, and
User rows so subsequent /execute calls find their workspace.

Trust model:
  - Every request is HMAC-SHA256 verified against CLERK_WEBHOOK_SECRET
    using Svix's signing scheme. Tampered bodies are rejected before any
    DB write.
  - Replay protection: svix-id is recorded in Redis for 1 hour after the
    first successful receive. Duplicate ids are accepted with a 200 but
    have no side effect (idempotent ack).
  - Clock-skew tolerance: 5 minutes either side of svix-timestamp.

Event handling:
  - organization.created  → upsert Organization + Tenant. Tenant gets a
    fresh tenant_id UUID and shadow_mode_until = now + 14 days.
  - organization.updated  → rename Organization.
  - organization.deleted  → mark Organization.is_active = False and the
    bound Tenant.is_active = False. No row deletion (audit safety).
  - user.created          → no-op. A user without an org membership has
    no tenant context yet; we wait for organizationMembership.created.
  - organizationMembership.created → upsert User row inside the org's
    Tenant with the canonical Aegis role projected from membership.role.
  - organizationMembership.updated → patch User.role.
  - organizationMembership.deleted → mark User.is_active = False.

Side effects beyond DB:
  - Writes the clerk_org_id → aegis_tenant_id mapping to Redis so the
    gateway's Clerk JWKS validator can resolve the canonical tenant_id
    without a DB round-trip when the JWT claim is empty.

The endpoint is mounted at POST /webhooks/clerk on the identity service,
which the gateway proxies to /webhooks/clerk publicly. CLERK_WEBHOOK_SECRET
must be set or every request 503s — the receiver fail-closes on missing
config so an unconfigured deployment cannot silently accept arbitrary
events.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
import uuid
from datetime import datetime, timedelta
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sdk.common.config import settings
from sdk.common.db import get_db
from sdk.common.redis import get_redis_client
from sdk.common.roles import canonical_role
from services.identity.clerk_backend_api import (
    ClerkBackendAPIError,
    update_organization_public_metadata,
)
from services.identity.models import (
    SHADOW_MODE_DEFAULT_DAYS,
    DegradedModePolicy,
    Organization,
    Tenant,
    TenantTier,
    User,
    UserRole,
)

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["clerk-webhooks"])

# Svix accepts a 5-minute clock-skew window in either direction (matches their
# SDK default). Replay-id TTL of 1 hour is much longer than the skew so a
# webhook redelivered after the first ack is a no-op rather than a duplicate
# DB write.
_SVIX_TIMESTAMP_TOLERANCE_SECONDS = 5 * 60
_SVIX_REPLAY_TTL_SECONDS = 60 * 60

# Redis key shapes — keep in sync with services.gateway.auth_clerk (which reads
# the org→tenant mapping when the JWT's aegis_tenant_id claim is empty).
_REPLAY_KEY_PREFIX = "acp:clerk:webhook:svix:"
_ORG_TO_TENANT_KEY_PREFIX = "acp:clerk:org-tenant:"


# =========================================================================
# Redis dependency — mirrors the pattern in router.py
# =========================================================================


_redis_client: Any = None


def _get_redis_client() -> Any:
    global _redis_client
    if _redis_client is None:
        _redis_client = get_redis_client(settings.REDIS_URL, decode_responses=False)
    return _redis_client


async def get_redis() -> Any:
    yield _get_redis_client()


# =========================================================================
# Svix signature verification — Clerk uses the Svix scheme.
# =========================================================================


def _decode_signing_secret(secret: str) -> bytes:
    """
    Svix encodes the signing key as ``whsec_<base64>``. Tolerate either form
    so a user pasting just the base64 part still works.
    """
    if not secret:
        raise ValueError("CLERK_WEBHOOK_SECRET is empty")
    prefix = "whsec_"
    body = secret[len(prefix):] if secret.startswith(prefix) else secret
    try:
        return base64.b64decode(body)
    except Exception as exc:
        raise ValueError(f"CLERK_WEBHOOK_SECRET is not base64-encoded: {exc}")


def _verify_svix_signature(
    *,
    svix_id: str,
    svix_timestamp: str,
    svix_signature: str,
    body: bytes,
    secret: str,
) -> None:
    """
    Verify the Svix signature on a webhook body. Raises HTTPException 401 on
    any failure — never logs the body so a debug log can't leak event payloads.
    """
    try:
        ts = int(svix_timestamp)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed svix-timestamp header",
        ) from exc

    now = int(time.time())
    if abs(now - ts) > _SVIX_TIMESTAMP_TOLERANCE_SECONDS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "svix-timestamp outside tolerance window "
                f"({_SVIX_TIMESTAMP_TOLERANCE_SECONDS}s)"
            ),
        )

    try:
        key_bytes = _decode_signing_secret(secret)
    except ValueError as exc:
        # Fail closed — operator misconfiguration must not become an
        # arbitrary-event-acceptance bug.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    signed_payload = f"{svix_id}.{svix_timestamp}.".encode("utf-8") + body
    expected_sig_bytes = hmac.new(
        key_bytes, signed_payload, hashlib.sha256,
    ).digest()
    expected_sig_b64 = base64.b64encode(expected_sig_bytes).decode("ascii")

    # Svix sends "v1,<b64> v1,<b64>" — multiple sigs space-separated for
    # rotation. ANY valid one passes.
    candidates = svix_signature.split(" ") if svix_signature else []
    for candidate in candidates:
        if "," not in candidate:
            continue
        version, value = candidate.split(",", 1)
        if version != "v1":
            continue
        if hmac.compare_digest(value, expected_sig_b64):
            return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="svix-signature did not match any v1 candidate",
    )


# =========================================================================
# Event handlers — each one is idempotent so a redelivery is harmless.
# =========================================================================


async def _handle_organization_created(
    db: AsyncSession, redis: Redis, data: dict[str, Any],
) -> dict[str, Any]:
    clerk_org_id = str(data.get("id") or "")
    name = str(data.get("name") or "Untitled Workspace")[:255]
    slug = str(data.get("slug") or clerk_org_id)[:100] or clerk_org_id

    if not clerk_org_id:
        raise HTTPException(
            status_code=400, detail="organization.created missing id field",
        )

    # Idempotent: if the org already exists, return its identifiers.
    existing = await db.execute(
        select(Organization).where(Organization.clerk_org_id == clerk_org_id),
    )
    org = existing.scalar_one_or_none()

    if org is None:
        org = Organization(name=name, slug=slug, clerk_org_id=clerk_org_id)
        db.add(org)
        try:
            await db.flush()
        except IntegrityError as exc:
            await db.rollback()
            # Re-fetch; race with another concurrent webhook delivery.
            existing = await db.execute(
                select(Organization).where(Organization.clerk_org_id == clerk_org_id),
            )
            org = existing.scalar_one_or_none()
            if org is None:
                raise HTTPException(
                    status_code=500,
                    detail=f"Organization create raced and could not recover: {exc}",
                ) from exc

    # One Tenant per Clerk org (Sprint 1 model — multi-tenant per org is a v2 concern).
    tenant_q = await db.execute(
        select(Tenant).where(Tenant.org_id == org.id),
    )
    tenant = tenant_q.scalar_one_or_none()

    if tenant is None:
        tenant = Tenant(
            org_id=org.id,
            tenant_id=uuid.uuid4(),
            name=name,
            tier=TenantTier.BASIC,
            rpm_limit=0,
            shadow_mode_until=datetime.utcnow()
                + timedelta(days=SHADOW_MODE_DEFAULT_DAYS),
            degraded_mode_policy=DegradedModePolicy.BLOCK_HIGH_RISK,
        )
        db.add(tenant)
        await db.flush()

    await db.commit()

    # Cache the org→tenant mapping so the gateway's Clerk JWKS validator
    # can resolve aegis_tenant_id from the JWT's org_id claim without a
    # DB round-trip. TTL is wide (7 days) — webhook on organization.deleted
    # invalidates it, and a stale lookup is cheap to refresh on miss.
    try:
        await redis.setex(
            f"{_ORG_TO_TENANT_KEY_PREFIX}{clerk_org_id}",
            7 * 24 * 60 * 60,
            str(tenant.tenant_id),
        )
    except Exception as exc:
        logger.warning("clerk_org_tenant_redis_cache_failed", error=str(exc))

    # Write `aegis_tenant_id` + `aegis_org_id` into the Clerk org's
    # public_metadata so the `aegis` JWT template surfaces them as JWT
    # claims. Failures here are NON-FATAL — the Redis mapping above is
    # the gateway's fallback path, so a Clerk API outage cannot block
    # provisioning.
    #
    # `aegis_org_id` MUST equal `aegis_tenant_id` to satisfy the SaaS
    # strict invariant (`ck_users_org_tenant_match`). The Organization PK
    # (`org.id`) is a separate UUID used only as an internal FK target;
    # it must NEVER leak into a JWT claim, or the gateway's write-path
    # invariant check 403s every Clerk user with
    # "Org consistency violation during gateway write path".
    await _write_aegis_metadata(
        clerk_org_id=clerk_org_id,
        aegis_org_id=str(tenant.tenant_id),
        aegis_tenant_id=str(tenant.tenant_id),
    )

    return {
        "organization_id": str(org.id),
        "tenant_id": str(tenant.tenant_id),
        "shadow_mode_until": tenant.shadow_mode_until.isoformat()
            if tenant.shadow_mode_until else None,
    }


async def _write_aegis_metadata(
    *, clerk_org_id: str, aegis_org_id: str, aegis_tenant_id: str,
) -> None:
    """Best-effort write of Aegis identifiers into Clerk org public_metadata."""
    if not settings.CLERK_SECRET_KEY:
        logger.info(
            "clerk_metadata_writeback_skipped_no_secret",
            clerk_org_id=clerk_org_id,
        )
        return
    try:
        await update_organization_public_metadata(
            clerk_org_id,
            {
                "aegis_org_id": aegis_org_id,
                "aegis_tenant_id": aegis_tenant_id,
            },
        )
        logger.info(
            "clerk_metadata_writeback_ok",
            clerk_org_id=clerk_org_id,
            aegis_tenant_id=aegis_tenant_id,
        )
    except ClerkBackendAPIError as exc:
        logger.warning(
            "clerk_metadata_writeback_failed",
            clerk_org_id=clerk_org_id,
            status=exc.status_code,
            body=str(exc),
        )


async def _handle_organization_updated(
    db: AsyncSession, redis: Redis, data: dict[str, Any],
) -> dict[str, Any]:
    clerk_org_id = str(data.get("id") or "")
    if not clerk_org_id:
        raise HTTPException(status_code=400, detail="organization.updated missing id")

    existing = await db.execute(
        select(Organization).where(Organization.clerk_org_id == clerk_org_id),
    )
    org = existing.scalar_one_or_none()
    if org is None:
        # We were not subscribed when the org was created; treat update as a
        # late-arriving created.
        return await _handle_organization_created(db, redis, data)

    new_name = data.get("name")
    if new_name:
        org.name = str(new_name)[:255]
    await db.commit()
    return {"organization_id": str(org.id)}


async def _handle_organization_deleted(
    db: AsyncSession, redis: Redis, data: dict[str, Any],
) -> dict[str, Any]:
    clerk_org_id = str(data.get("id") or "")
    existing = await db.execute(
        select(Organization).where(Organization.clerk_org_id == clerk_org_id),
    )
    org = existing.scalar_one_or_none()
    if org is None:
        return {"organization_id": None, "deleted": False}

    org.is_active = False
    tenant_q = await db.execute(select(Tenant).where(Tenant.org_id == org.id))
    for tenant in tenant_q.scalars():
        tenant.is_active = False
    await db.commit()

    try:
        await redis.delete(f"{_ORG_TO_TENANT_KEY_PREFIX}{clerk_org_id}")
    except Exception as exc:
        logger.warning("clerk_org_tenant_redis_invalidate_failed", error=str(exc))

    return {"organization_id": str(org.id), "deleted": True}


def _extract_primary_email(data: dict[str, Any]) -> str:
    """Pull the primary email address out of a Clerk user payload."""
    primary_id = data.get("primary_email_address_id")
    for addr in data.get("email_addresses", []) or []:
        if not isinstance(addr, dict):
            continue
        if not primary_id or addr.get("id") == primary_id:
            email = addr.get("email_address")
            if email:
                return str(email).lower()
    # Fallback — first address regardless of primary marker.
    addresses = data.get("email_addresses", []) or []
    if addresses and isinstance(addresses[0], dict):
        first = addresses[0].get("email_address")
        if first:
            return str(first).lower()
    return ""


async def _handle_membership_created_or_updated(
    db: AsyncSession, redis: Redis, data: dict[str, Any],
) -> dict[str, Any]:
    """
    Bind a Clerk user to an Aegis Tenant with the projected role.

    Payload shape (per Clerk's organizationMembership event):
        {
          "id": <clerk membership id>,
          "role": "org:owner" | "org:admin" | ...,
          "organization": {"id": <clerk_org_id>, ...},
          "public_user_data": {
              "user_id": <clerk_user_id>,
              "identifier": <email>,
              ...
          }
        }
    """
    org_payload = data.get("organization") or {}
    user_payload = data.get("public_user_data") or {}
    clerk_org_id = str(org_payload.get("id") or "")
    clerk_user_id = str(user_payload.get("user_id") or "")
    raw_role = str(data.get("role") or "org:read_only")

    if not clerk_org_id or not clerk_user_id:
        raise HTTPException(
            status_code=400,
            detail="organizationMembership event missing organization.id or public_user_data.user_id",
        )

    org_q = await db.execute(
        select(Organization).where(Organization.clerk_org_id == clerk_org_id),
    )
    org = org_q.scalar_one_or_none()
    if org is None:
        # Late-arriving membership for an org we haven't seen yet —
        # provision the org first so the downstream user create succeeds.
        await _handle_organization_created(db, redis, {"id": clerk_org_id, "name": "Workspace", "slug": clerk_org_id})
        org_q = await db.execute(
            select(Organization).where(Organization.clerk_org_id == clerk_org_id),
        )
        org = org_q.scalar_one()

    tenant_q = await db.execute(select(Tenant).where(Tenant.org_id == org.id))
    tenant = tenant_q.scalar_one_or_none()
    if tenant is None:
        raise HTTPException(
            status_code=500,
            detail=f"Organization {org.id} exists without a bound Tenant — provisioning corruption",
        )

    # Project the Clerk role onto the Aegis vocabulary.
    canonical_value = canonical_role(raw_role.replace("org:", "").upper().replace("-", "_"))
    try:
        role_enum = UserRole(canonical_value)
    except ValueError:
        role_enum = UserRole.VIEWER  # extreme fallback; canonical_role already defaults to READ_ONLY

    email = _extract_primary_email(user_payload)

    # N8 — Concurrent /auth/clerk/provision race. The pre-fix code did a
    # SELECT WHERE clerk_user_id=X, then an INSERT if not found. Two
    # concurrent provision calls for the same Clerk user could both pass
    # the SELECT and both attempt INSERT; the second hit the UNIQUE
    # constraint on clerk_user_id and surfaced as a 500 to the caller.
    #
    # Resolution is in two parts:
    #
    #   (1) Legacy-email-link path. If a pre-Clerk row exists with the
    #       same email and a NULL clerk_user_id, we update *that* row to
    #       link it to the Clerk identity. This is wrapped in a
    #       try/except IntegrityError so that if two concurrent linkers
    #       race on the same email row (or two requests linking
    #       DIFFERENT legacy rows to the SAME clerk_user_id), the loser
    #       falls through to the atomic UPSERT below instead of crashing.
    #
    #   (2) Atomic UPSERT keyed on clerk_user_id. Replaces the
    #       SELECT-then-INSERT with INSERT ... ON CONFLICT
    #       (clerk_user_id) DO UPDATE. The DO UPDATE branch idempotently
    #       refreshes role/tenant_id/is_active so a concurrent loser
    #       still sees the canonical post-write state.
    if email:
        legacy_q = await db.execute(
            select(User).where(
                User.email == email,
                User.clerk_user_id.is_(None),
            ),
        )
        legacy_user = legacy_q.scalar_one_or_none()
        if legacy_user is not None:
            legacy_user.clerk_user_id = clerk_user_id
            legacy_user.role = role_enum
            legacy_user.tenant_id = tenant.tenant_id
            legacy_user.org_id = tenant.tenant_id
            legacy_user.is_active = True
            try:
                await db.commit()
                return {
                    "user_id": str(legacy_user.id),
                    "tenant_id": str(tenant.tenant_id),
                    "role": role_enum.value,
                }
            except IntegrityError:
                # Another concurrent request already linked a different
                # row to this clerk_user_id (or this same legacy row was
                # re-linked under us). Fall through to UPSERT — the
                # winning side has the canonical row already.
                await db.rollback()

    # Clerk owns the password; we store a placeholder hash so the
    # NOT-NULL constraint is satisfied. The user cannot log in via the
    # legacy /auth/login path with this row.
    placeholder_hash = "$2b$12$ClerkOwnsThisPasswordPlaceholderHashXXXX"
    # `users.org_id == users.tenant_id` is enforced by the
    # ck_users_org_tenant_match check constraint (SaaS strict
    # invariant). The legacy login path always sets both columns to
    # the same UUID. For Clerk users, set both to tenant.tenant_id —
    # the Organization PK (org.id) is a SEPARATE UUID and was the
    # source of every Clerk-signup IntegrityError that prevented
    # /auth/clerk/provision from ever completing.
    insert_values = {
        "email": email or f"{clerk_user_id}@clerk.invalid",
        "hashed_password": placeholder_hash,
        "tenant_id": tenant.tenant_id,
        "org_id": tenant.tenant_id,
        "role": role_enum,
        "is_active": True,
        "clerk_user_id": clerk_user_id,
    }
    upsert_stmt = (
        pg_insert(User)
        .values(**insert_values)
        .on_conflict_do_update(
            index_elements=["clerk_user_id"],
            set_={
                "role": role_enum,
                "tenant_id": tenant.tenant_id,
                "org_id": tenant.tenant_id,
                "is_active": True,
                "updated_at": func.now(),
            },
        )
        .returning(User.id)
    )
    result = await db.execute(upsert_stmt)
    user_id = result.scalar_one()
    await db.commit()

    return {
        "user_id": str(user_id),
        "tenant_id": str(tenant.tenant_id),
        "role": role_enum.value,
    }


async def _handle_membership_deleted(
    db: AsyncSession, redis: Redis, data: dict[str, Any],
) -> dict[str, Any]:
    user_payload = data.get("public_user_data") or {}
    clerk_user_id = str(user_payload.get("user_id") or "")
    if not clerk_user_id:
        return {"deactivated": False}

    user_q = await db.execute(
        select(User).where(User.clerk_user_id == clerk_user_id),
    )
    user = user_q.scalar_one_or_none()
    if user is None:
        return {"deactivated": False}

    user.is_active = False
    await db.commit()
    return {"deactivated": True, "user_id": str(user.id)}


# Map of event type → handler. Unknown events return 200 with action="ignored"
# so Clerk does not redeliver them indefinitely.
_HANDLERS: dict[str, Any] = {
    "organization.created": _handle_organization_created,
    "organization.updated": _handle_organization_updated,
    "organization.deleted": _handle_organization_deleted,
    "organizationMembership.created": _handle_membership_created_or_updated,
    "organizationMembership.updated": _handle_membership_created_or_updated,
    "organizationMembership.deleted": _handle_membership_deleted,
}


# =========================================================================
# Route
# =========================================================================


@router.post(
    "/webhooks/clerk",
    summary="Clerk webhook receiver (Svix-signed)",
    status_code=status.HTTP_200_OK,
)
async def receive_clerk_webhook(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
    svix_id: Annotated[str | None, Header(alias="svix-id")] = None,
    svix_timestamp: Annotated[str | None, Header(alias="svix-timestamp")] = None,
    svix_signature: Annotated[str | None, Header(alias="svix-signature")] = None,
) -> dict[str, Any]:
    """
    Verify the incoming Svix signature, dedupe by svix-id, and dispatch to
    the per-event handler. Returns ``{event, action, result}`` so the
    operator can read deliveries in the Clerk dashboard log without an
    external sink.
    """
    if not settings.CLERK_WEBHOOK_SECRET:
        # Fail-closed when the operator hasn't configured the secret.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="CLERK_WEBHOOK_SECRET is not configured; refusing to accept webhooks",
        )

    if not (svix_id and svix_timestamp and svix_signature):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing svix-id / svix-timestamp / svix-signature headers",
        )

    body = await request.body()

    _verify_svix_signature(
        svix_id=svix_id,
        svix_timestamp=svix_timestamp,
        svix_signature=svix_signature,
        body=body,
        secret=settings.CLERK_WEBHOOK_SECRET,
    )

    # Idempotent ack: if we've already seen this svix-id, return 200 without
    # re-running the handler. Race-safe via Redis SET NX.
    replay_key = f"{_REPLAY_KEY_PREFIX}{svix_id}"
    try:
        first_time = await redis.set(
            replay_key, "1", ex=_SVIX_REPLAY_TTL_SECONDS, nx=True,
        )
    except Exception as exc:
        # Sprint 25 A7 — fail-CLOSED with Retry-After so Svix retries this
        # event later when Redis is back. The previous fail-open behavior
        # treated every Svix retry as first-time, triggering duplicate user
        # provisioning on every retry while Redis was down.
        logger.warning("clerk_webhook_replay_check_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Idempotency check temporarily unavailable; retry",
            headers={"Retry-After": "30"},
        )

    if not first_time:
        logger.info("clerk_webhook_replay_ignored", svix_id=svix_id)
        return {"event": None, "action": "replay_ignored", "svix_id": svix_id}

    import json
    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Body is not valid UTF-8 JSON: {exc}",
        )

    event_type = str(payload.get("type") or "")
    data = payload.get("data") or {}

    handler = _HANDLERS.get(event_type)
    if handler is None:
        logger.info("clerk_webhook_unknown_event", event=event_type)
        return {"event": event_type, "action": "ignored"}

    try:
        result = await handler(db, redis, data)
    except HTTPException:
        raise
    except Exception as exc:
        # Log at error but return 500 so Clerk will retry — the operator can
        # inspect why the handler died and replay after the fix.
        logger.error(
            "clerk_webhook_handler_failed",
            event=event_type,
            svix_id=svix_id,
            error=str(exc),
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Handler for {event_type!r} failed; Clerk will retry",
        ) from exc

    logger.info(
        "clerk_webhook_handled",
        event=event_type,
        svix_id=svix_id,
        result=result,
    )
    return {"event": event_type, "action": "applied", "result": result}
