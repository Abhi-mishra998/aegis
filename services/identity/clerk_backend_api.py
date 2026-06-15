"""
Thin async client for the Clerk Backend API.

Only covers the calls Aegis Sprint 1 needs:
  - `update_organization_public_metadata` — set the `aegis_tenant_id` and
    `aegis_org_id` fields after the webhook receiver provisions the matching
    Aegis Org + Tenant. With these in place, the `aegis` JWT template in
    Clerk dashboard surfaces them on every issued JWT, so the gateway's
    Clerk JWKS validator can resolve the canonical tenant_id from claims
    alone (skipping the Redis fallback round trip).

  - `get_organization` — diagnostic / read-back probe used by the
    synchronous provision endpoint to confirm metadata persisted.

This is deliberately NOT a generic Clerk client. Every call here is
auth-bearing (`Bearer CLERK_SECRET_KEY`) and the secret must never reach
a browser. Any future addition (e.g. listing users, deleting orgs)
should be similarly scoped and reviewed.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from sdk.common.config import settings

logger = structlog.get_logger(__name__)

_CLERK_API_BASE = "https://api.clerk.com/v1"
_DEFAULT_TIMEOUT_SECONDS = 8.0


class ClerkBackendAPIError(RuntimeError):
    """Raised when Clerk's Backend API responds with a non-2xx status."""

    def __init__(self, *, status_code: int, body: str) -> None:
        super().__init__(f"Clerk API {status_code}: {body[:200]}")
        self.status_code = status_code
        self.body = body


def _auth_headers() -> dict[str, str]:
    if not settings.CLERK_SECRET_KEY:
        raise ClerkBackendAPIError(
            status_code=503,
            body="CLERK_SECRET_KEY is not configured",
        )
    return {
        "Authorization": f"Bearer {settings.CLERK_SECRET_KEY}",
        "Content-Type": "application/json",
    }


async def update_organization_public_metadata(
    clerk_org_id: str,
    metadata: dict[str, Any],
    *,
    merge: bool = True,
) -> dict[str, Any]:
    """
    Set fields on an organization's `public_metadata` blob.

    By default the call MERGES — Clerk's PATCH semantics on this object
    only replace the keys we send. With ``merge=False`` we send the
    metadata dict verbatim, which Clerk treats as a full replacement.

    Returns the updated organization payload from Clerk.
    """
    if not clerk_org_id:
        raise ValueError("clerk_org_id is required")

    url = f"{_CLERK_API_BASE}/organizations/{clerk_org_id}/metadata"
    payload = {"public_metadata": metadata}

    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_SECONDS) as client:
            resp = await client.patch(url, headers=_auth_headers(), json=payload)
    except httpx.HTTPError as exc:
        logger.error(
            "clerk_metadata_patch_transport_error",
            clerk_org_id=clerk_org_id,
            error=str(exc),
        )
        raise ClerkBackendAPIError(status_code=502, body=str(exc)) from exc

    if resp.status_code >= 400:
        logger.error(
            "clerk_metadata_patch_failed",
            clerk_org_id=clerk_org_id,
            status=resp.status_code,
            body=resp.text[:200],
        )
        raise ClerkBackendAPIError(
            status_code=resp.status_code, body=resp.text,
        )

    try:
        return resp.json()
    except ValueError:
        return {"raw": resp.text}


async def get_organization(clerk_org_id: str) -> dict[str, Any]:
    """Read the Clerk organization object — used to verify metadata writes."""
    if not clerk_org_id:
        raise ValueError("clerk_org_id is required")
    url = f"{_CLERK_API_BASE}/organizations/{clerk_org_id}"
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_SECONDS) as client:
            resp = await client.get(url, headers=_auth_headers())
    except httpx.HTTPError as exc:
        raise ClerkBackendAPIError(status_code=502, body=str(exc)) from exc
    if resp.status_code >= 400:
        raise ClerkBackendAPIError(status_code=resp.status_code, body=resp.text)
    return resp.json()


async def get_user(clerk_user_id: str) -> dict[str, Any]:
    """Read the Clerk user object — used by /auth/clerk/provision."""
    if not clerk_user_id:
        raise ValueError("clerk_user_id is required")
    url = f"{_CLERK_API_BASE}/users/{clerk_user_id}"
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_SECONDS) as client:
            resp = await client.get(url, headers=_auth_headers())
    except httpx.HTTPError as exc:
        raise ClerkBackendAPIError(status_code=502, body=str(exc)) from exc
    if resp.status_code >= 400:
        raise ClerkBackendAPIError(status_code=resp.status_code, body=resp.text)
    return resp.json()


async def list_user_organizations(clerk_user_id: str) -> list[dict[str, Any]]:
    """List orgs the user belongs to — used to find the active org during signup."""
    if not clerk_user_id:
        raise ValueError("clerk_user_id is required")
    url = f"{_CLERK_API_BASE}/users/{clerk_user_id}/organization_memberships"
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_SECONDS) as client:
            resp = await client.get(url, headers=_auth_headers())
    except httpx.HTTPError as exc:
        raise ClerkBackendAPIError(status_code=502, body=str(exc)) from exc
    if resp.status_code >= 400:
        raise ClerkBackendAPIError(status_code=resp.status_code, body=resp.text)
    try:
        body = resp.json()
    except ValueError:
        return []
    # Clerk wraps list responses in {data: [...], total_count: N}
    if isinstance(body, dict) and "data" in body:
        return list(body["data"])
    if isinstance(body, list):
        return body
    return []


__all__ = [
    "ClerkBackendAPIError",
    "update_organization_public_metadata",
    "get_organization",
    "get_user",
    "list_user_organizations",
]
