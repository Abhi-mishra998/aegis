"""Sprint EI-3 (2026-06-20) — SCIM bearer token issuance / revocation.

These endpoints are SEPARATE from the SCIM-protocol endpoints
(``/scim/v2/Users``, ``/scim/v2/Groups``) — they are the per-tenant
management surface that an OWNER uses to mint the token that Okta will
paste into its provisioning connector.

Surface:

  GET    /scim/v2/tokens               list (without plaintext)
  POST   /scim/v2/tokens               create — RETURNS PLAINTEXT ONCE
  DELETE /scim/v2/tokens/{token_id}    revoke

All three require OWNER role (enforced via ``services/gateway/_rbac_map.py``).
Plaintext is returned exactly once in the POST response body; subsequent
GETs surface only the prefix + last_used_at.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sdk.common.db import get_db

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/scim/v2/tokens", tags=["scim"])


class TokenCreate(BaseModel):
    label: str = Field(min_length=1, max_length=128)


def _tenant_id_from_request(request: Request) -> uuid.UUID:
    raw = getattr(request.state, "tenant_id", None)
    if not raw:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return uuid.UUID(str(raw)) if not isinstance(raw, uuid.UUID) else raw


def _user_id_from_request(request: Request) -> uuid.UUID | None:
    """Best-effort caller user_id for audit trail; None if not present."""
    claims = getattr(request.state, "jwt_claims", None) or {}
    raw = claims.get("user_id") or claims.get("sub")
    if not raw:
        return None
    try:
        return uuid.UUID(str(raw))
    except (ValueError, TypeError):
        return None


def _to_public_dict(row) -> dict[str, Any]:
    return {
        "id":            str(row.id),
        "label":         row.label,
        "token_prefix":  row.token_prefix,
        "active":        row.revoked_at is None,
        "last_used_at":  row.last_used_at.isoformat() if row.last_used_at else None,
        "revoked_at":    row.revoked_at.isoformat()   if row.revoked_at   else None,
        "created_at":    row.created_at.isoformat()   if row.created_at   else None,
    }


def _mint_token() -> tuple[str, str, str]:
    """Return (plaintext, prefix, sha256_hex). Format: ``scim_<23 base32 chars>``.

    Base32 gives us a URL-safe, case-insensitive secret (Okta UIs sometimes
    lowercase paste-buffer contents). 14 bytes encoded as base32 (no '=')
    is 23 chars × 5 bits ≈ 110 bits of usable entropy — comfortably above
    the 96-bit floor for long-lived bearer tokens.
    """
    raw = secrets.token_bytes(14)  # 14 bytes → 23 base32 chars (after strip='=')
    import base64
    body = base64.b32encode(raw).decode().rstrip("=").lower()
    plaintext = f"scim_{body}"
    prefix    = plaintext[:8] + "…" + plaintext[-4:]  # 'scim_abc…wxyz' for the UI
    sha       = hashlib.sha256(plaintext.encode()).hexdigest()
    return plaintext, prefix, sha


@router.get("")
async def list_tokens(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    from services.identity.models import ScimToken
    tenant_id = _tenant_id_from_request(request)
    res = await db.execute(
        select(ScimToken)
        .where(ScimToken.tenant_id == tenant_id)
        .order_by(ScimToken.created_at.desc()),
    )
    rows = [_to_public_dict(r) for r in res.scalars().all()]
    return {"data": rows}


@router.post("", status_code=201)
async def create_token(
    request: Request,
    payload: TokenCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Mint a new SCIM bearer. The PLAINTEXT is returned exactly once."""
    from services.identity.models import ScimToken
    tenant_id = _tenant_id_from_request(request)
    user_id   = _user_id_from_request(request)

    plaintext, prefix, sha = _mint_token()
    row = ScimToken(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        org_id=tenant_id,
        label=payload.label,
        token_hash=sha,
        token_prefix=prefix,
        created_by_user_id=user_id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    logger.info(
        "scim_token_issued",
        tenant_id=str(tenant_id),
        label=payload.label,
        token_prefix=prefix,
        actor=getattr(request.state, "actor", "unknown"),
    )
    body = _to_public_dict(row)
    body["plaintext"] = plaintext  # exposed exactly once
    body["plaintext_warning"] = (
        "Copy this token now. Aegis does not store it in plaintext and cannot "
        "show it again. Paste it into Okta → App → Provisioning → Authentication."
    )
    return {"data": body}


@router.delete("/{token_id}", status_code=204)
async def revoke_token(
    token_id: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    from services.identity.models import ScimToken
    tenant_id = _tenant_id_from_request(request)
    try:
        tid = uuid.UUID(token_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid token_id")

    res = await db.execute(
        select(ScimToken).where(
            ScimToken.id == tid,
            ScimToken.tenant_id == tenant_id,
        ),
    )
    row = res.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Token not found")
    if row.revoked_at is not None:
        return  # idempotent
    row.revoked_at = datetime.now(UTC)
    await db.commit()
    logger.info(
        "scim_token_revoked",
        tenant_id=str(tenant_id),
        token_id=token_id,
        actor=getattr(request.state, "actor", "unknown"),
    )
