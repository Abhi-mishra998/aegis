"""Sprint EI-3 (2026-06-20) — SCIM bearer-token resolver.

The Okta SCIM connector sends every provisioning call with
``Authorization: Bearer scim_<22 base32 chars>``. This helper validates
the bearer against the ``scim_tokens`` table, returns the resolved
``tenant_id``, and updates ``last_used_at`` as a side effect.

Callers (the SCIM protocol router) use this *before* hitting any
SCIM-shaped endpoint. The standard JWT middleware skips ``/scim/v2/``
paths so we get a single, clean auth path here.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

import structlog
from fastapi import HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


async def resolve_scim_bearer(request: Request, db: AsyncSession) -> uuid.UUID:
    """Validate the request's Bearer token and return the tenant_id.

    Raises HTTPException(401) on any failure, with a SCIM-shaped error
    body (RFC 7644 §3.12) so Okta surfaces the message intact.
    """
    auth = request.headers.get("Authorization", "")
    if not auth.lower().startswith("bearer "):
        raise _scim_unauthorized("Missing Bearer token")

    parts = auth.split(None, 1)
    if len(parts) < 2:
        raise _scim_unauthorized("Malformed Authorization header")
    token = parts[1].strip()
    if not token.startswith("scim_"):
        raise _scim_unauthorized("SCIM bearer tokens must begin with 'scim_'")

    sha = hashlib.sha256(token.encode()).hexdigest()

    from services.identity.models import ScimToken  # noqa: PLC0415

    # P0-1 fix (2026-06-22) — wrap the DB lookup in a narrow try/except so
    # infrastructure errors (table missing, pgbouncer down, DB unreachable)
    # surface as 503 with a SCIM-shaped body instead of bubbling raw
    # SQLAlchemy exceptions through to the generic 500 handler. Without
    # this, every malformed SCIM bearer request — including reachability
    # probes from Okta — paged ops with a 500 + leaked the SQL string in
    # error logs.
    try:
        res = await db.execute(
            select(ScimToken).where(ScimToken.token_hash == sha),
        )
        row = res.scalar_one_or_none()
    except Exception as db_exc:
        logger.error(
            "scim_db_unreachable",
            error_type=type(db_exc).__name__,
            # NOTE: error string deliberately not logged here — SQLAlchemy
            # ProgrammingError stringifies the full SQL + parameters which
            # is exactly the data we redact in the global exception
            # handler (sdk.common.exceptions). Type alone is enough to
            # alert on.
        )
        raise HTTPException(
            status_code=503,
            detail={
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                "status":  "503",
                "detail":  "SCIM authentication backend unavailable",
            },
            headers={"Retry-After": "30", "WWW-Authenticate": 'Bearer realm="SCIM"'},
        ) from None
    if row is None:
        logger.warning(
            "scim_bearer_unknown",
            prefix=token[:8] + "…" + token[-4:] if len(token) >= 12 else "<short>",
        )
        raise _scim_unauthorized("Invalid SCIM token")
    if row.revoked_at is not None:
        raise _scim_unauthorized("SCIM token has been revoked")

    # Best-effort last_used_at update — fire and forget; never blocks the request.
    try:
        await db.execute(
            update(ScimToken)
            .where(ScimToken.id == row.id)
            .values(last_used_at=datetime.now(UTC)),
        )
        await db.commit()
    except Exception as exc:
        logger.warning("scim_last_used_update_failed", error=str(exc))

    # Pin tenant context onto request.state so downstream tenant-scoped
    # queries on User / Team naturally filter to the right tenant.
    request.state.tenant_id = row.tenant_id
    request.state.actor = f"scim:{row.token_prefix}"
    return row.tenant_id


def _scim_unauthorized(detail: str) -> HTTPException:
    """Build an RFC 7644-shaped 401 the Okta connector understands."""
    return HTTPException(
        status_code=401,
        detail={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
            "status":  "401",
            "detail":  detail,
        },
        headers={"WWW-Authenticate": 'Bearer realm="SCIM"'},
    )
