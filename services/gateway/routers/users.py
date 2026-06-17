"""Gateway proxy routes for user account management and API keys.

9 routes lifted out of services/gateway/main.py in the sprint-5 audit
cleanup. Two related concerns share this module because they're both
admin-driven CRUD on identity-adjacent resources:

  /users + /auth/users    — user accounts (proxied to identity service)
  /api-keys/*             — programmatic API keys (proxied to api service)

The /auth/users POST route keeps its tag of "auth" (the first-user
self-service signup flow lives there) — the other user endpoints carry
the "users" tag.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request
from redis.asyncio import Redis

from sdk.common.config import settings
from sdk.common.redis import get_redis_client
from services.gateway._helpers import internal_headers, passthrough, publish_event

router = APIRouter()

_redis: Redis = get_redis_client(settings.REDIS_URL, decode_responses=False)


def _identity_base() -> str:
    return settings.IDENTITY_SERVICE_URL.rstrip("/")


def _api_base() -> str:
    return settings.API_SERVICE_URL.rstrip("/")


# ── User management (proxies to identity service) ────────────────────────

@router.post("/auth/users", tags=["auth"])
async def create_user(request: Request) -> Any:
    """Proxy → Identity: create a new user account.

    First user open; subsequent users require ADMIN role per the identity
    service's policy.
    """
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{_identity_base()}/auth/users",
        json=body,
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/users", tags=["users"])
async def list_users_proxy(request: Request) -> Any:
    """Proxy → Identity: list users for the tenant.

    Forwards ``?role=`` and ``?is_active=`` filters as-is.
    """
    resp = await request.app.state.client.get(
        f"{_identity_base()}/users",
        params=dict(request.query_params),
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.post("/users/invite", tags=["users"])
async def invite_user_proxy(request: Request) -> Any:
    """Proxy → Identity: invite a new user (creates account with random password)."""
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{_identity_base()}/users/invite",
        json=body,
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.patch("/users/{user_id}", tags=["users"])
async def update_user_proxy(user_id: str, request: Request) -> Any:
    """Proxy → Identity: update user role or active status."""
    body = await request.json()
    resp = await request.app.state.client.patch(
        f"{_identity_base()}/users/{user_id}",
        json=body,
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.delete("/users/{user_id}", tags=["users"])
async def deactivate_user_proxy(user_id: str, request: Request) -> Any:
    """Proxy → Identity: soft-delete (deactivate) a user."""
    resp = await request.app.state.client.delete(
        f"{_identity_base()}/users/{user_id}",
        headers=internal_headers(request),
    )
    return passthrough(resp)


# ── API key management (proxies to api service) ──────────────────────────
# /api-keys/validate must precede /api-keys/{key_id} so FastAPI doesn't
# greedily match the literal "validate" as a key_id (same shape as
# /playbooks/stats).

@router.get("/api-keys", tags=["API Keys"])
async def list_api_keys(request: Request) -> Any:
    """Proxy → API service list keys.

    Sprint 17 — query-string ``?subject_kind=employee`` filters to the
    employee virtual keys for the Team page. The legacy Developer panel
    omits the parameter and still sees all kinds.
    """
    params = {}
    sk = request.query_params.get("subject_kind")
    if sk:
        params["subject_kind"] = sk
    resp = await request.app.state.client.get(
        f"{_api_base()}/api-keys",
        params=params,
        headers=internal_headers(request),
    )
    return passthrough(resp)


# Sprint 17 — Aegis for Teams. Mint a virtual `acp_emp_…` key for one
# employee. Proxies to the API service's new endpoint; same auth shape
# as the other /api-keys/* proxies.
@router.post("/api-keys/employees", tags=["API Keys"])
async def create_employee_key(request: Request) -> Any:
    """Proxy → API service mint employee key."""
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{_api_base()}/api-keys/employees",
        json=body,
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.post("/api-keys", tags=["API Keys"])
async def create_api_key(request: Request) -> Any:
    """Proxy → API service create key."""
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{_api_base()}/api-keys",
        json=body,
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.post("/api-keys/validate", tags=["API Keys"])
async def validate_api_key(request: Request) -> Any:
    """Proxy → API service validate key."""
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{_api_base()}/api-keys/validate",
        json=body,
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.delete("/api-keys/{key_id}", tags=["API Keys"])
async def revoke_api_key(key_id: str, request: Request) -> Any:
    """Proxy → API service revoke key. Publishes key_revoked SSE event.

    Security operators need real-time visibility into key revocations
    because the tenant's threat surface just changed — a revoked
    virtual key may be in active use by an exiting employee or
    compromised agent.
    """
    resp = await request.app.state.client.delete(
        f"{_api_base()}/api-keys/{key_id}",
        headers=internal_headers(request),
    )
    if resp.status_code in (200, 204):
        try:
            tenant_id_str = (
                getattr(request.state, "tenant_id", None)
                or request.headers.get("X-Tenant-ID", "")
            )
            if tenant_id_str:
                revoker = getattr(request.state, "actor", "unknown")
                await publish_event(
                    _redis,
                    str(tenant_id_str),
                    "key_revoked",
                    {
                        "key_id": key_id,
                        "revoker_email": revoker,
                        "subject_kind": "employee",
                        "revoked_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
        except Exception:
            pass
    return passthrough(resp)
