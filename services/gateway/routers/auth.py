"""Gateway proxy routes for the identity service's auth surface.

9 routes lifted out of services/gateway/main.py in the sprint-5 audit
cleanup. SSO routes live in routers/sso.py; user-account CRUD lives in
routers/users.py — everything else (token mint, agent token, logout,
me, introspect, refresh, revoke, credentials, tenants/{id}) is here.

The /auth/token handler is the one with substantial inline logic: it
mints the token, sets the httpOnly ``acp_token`` cookie (gated on
``settings.ENVIRONMENT == "production"`` for the ``secure=`` flag), and
echoes the token in the body so SDK/Locust clients can use Bearer
auth. That whole sequence moved verbatim — no behavioural changes.
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, field_validator

from sdk.common.config import settings
from services.gateway._helpers import internal_headers, passthrough

router = APIRouter()

# RFC-5321-ish but permissive enough to allow .local / .test / .internal TLDs
# that the python-email-validator package rejects as "special-use or reserved".
# The identity service is the source of truth for credential validity.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _base() -> str:
    return settings.IDENTITY_SERVICE_URL.rstrip("/")


class AuthRequest(BaseModel):
    email:    str
    password: str

    @field_validator("email")
    @classmethod
    def _check_email(cls, v: str) -> str:
        if not _EMAIL_RE.match(v):
            raise ValueError("not a valid email address")
        return v


@router.post("/auth/token", tags=["auth"])
async def proxy_auth_token(
    request: Request, payload: AuthRequest, response: Response,
) -> dict[str, Any]:
    """Mint a user JWT.

    Returns ``access_token`` in BOTH the response body (SDK/Locust
    bearer-auth) AND as an httpOnly cookie (browser EventSource +
    same-origin fetches via ``credentials: include``). This eliminates
    the bearer-vs-cookie split that caused all post-restart auth
    failures in earlier sprints.

    ``secure=`` is gated on production so dev environments without HTTPS
    still work.
    """
    url = f"{_base()}/auth/login"
    client = request.app.state.client
    try:
        tenant_id = request.headers.get("X-Tenant-ID")
        headers = internal_headers(request)
        if tenant_id:
            headers["X-Tenant-ID"] = tenant_id

        resp = await client.post(
            url,
            json={"email": payload.email, "password": payload.password},
            headers=headers,
        )
        if resp.status_code != 200:
            try:
                err_body = resp.json()
                err_detail = err_body.get("error") or err_body.get("detail") or "Invalid email or password"
                if err_body.get("error") == "Validation failed":
                    for d in err_body.get("meta", {}).get("details", []):
                        if "x-tenant-id" in d.get("loc", []):
                            err_detail = "X-Tenant-ID required"
            except Exception:
                err_detail = "Invalid email or password"

            response.status_code = 400 if resp.status_code in (400, 422) else 401
            return {"success": False, "error": err_detail}

        data = resp.json() or {}
        info = data.get("data", {})
        token = info.get("access_token")
        if not token:
            return {"success": False, "error": "Token generation failed"}

        is_secure = settings.ENVIRONMENT == "production"

        response.set_cookie(
            key="acp_token",
            value=token,
            httponly=True,
            secure=is_secure,
            samesite="strict",
            max_age=86400,
        )

        return {
            "success": True,
            "data": {
                "access_token": token,
                "token_type": "bearer",
                "expires_in": info.get("expires_in"),
                "tenant_id":  str(info.get("tenant_id", "")),
                "role":       info.get("role"),
            },
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/auth/agent/token", tags=["auth"])
async def proxy_agent_token(request: Request, response: Response) -> Any:
    """Proxy → Identity: issue token for agents.

    Body: ``{agent_id, secret}``. Credentials must be provisioned first
    via POST /auth/credentials.
    """
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{_base()}/auth/token",
        json=body,
        headers={**internal_headers(request),
                 "X-Tenant-ID": request.headers.get("X-Tenant-ID", "")},
    )
    response.status_code = resp.status_code
    try:
        data = resp.json()
    except Exception:
        data = None
    if resp.status_code != 200 or data is None:
        detail = (data or {}).get("detail", "Agent authentication failed")
        return {"success": False, "error": detail, "data": None}
    return data


@router.post("/auth/logout", tags=["auth"])
async def logout(response: Response) -> dict[str, Any]:
    """Clear session cookies and terminate gateway session."""
    is_secure = settings.ENVIRONMENT == "production"
    response.delete_cookie("acp_token", secure=is_secure, httponly=True, samesite="strict")
    return {"success": True, "message": "Cleared session cookies."}


@router.get("/auth/me", tags=["auth"])
async def get_me(request: Request) -> Any:
    """Proxy → Identity: current user details from JWT."""
    resp = await request.app.state.client.get(
        f"{_base()}/auth/me",
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.post("/auth/introspect", tags=["auth"])
async def introspect_token(request: Request) -> Any:
    """Proxy → Identity: verify token validity and return claims."""
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{_base()}/auth/introspect",
        json=body,
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.post("/auth/refresh", tags=["auth"])
async def refresh_token(request: Request) -> Any:
    """Proxy → Identity: rotate access token (revokes old, issues new)."""
    resp = await request.app.state.client.post(
        f"{_base()}/auth/refresh",
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.post("/auth/revoke", tags=["auth"])
async def revoke_token(request: Request) -> Any:
    """Proxy → Identity: revoke all tokens for an agent (ADMIN/SECURITY only)."""
    resp = await request.app.state.client.post(
        f"{_base()}/auth/revoke",
        params=request.query_params,
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.post("/auth/credentials", tags=["auth"])
async def provision_credentials(request: Request, response: Response) -> Any:
    """Proxy → Identity: provision agent credentials.

    Requires the gateway's INTERNAL_SECRET, which ``internal_headers`` adds.
    """
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{_base()}/auth/credentials",
        json=body,
        headers={**internal_headers(request),
                 "X-Tenant-ID": request.headers.get("X-Tenant-ID", "")},
    )
    response.status_code = resp.status_code
    return passthrough(resp)


@router.get("/auth/tenants/{tenant_id}", tags=["auth"])
async def get_tenant_metadata(tenant_id: str, request: Request) -> Any:
    """Proxy → Identity: get tier and rate-limit metadata for a tenant (ADMIN only)."""
    resp = await request.app.state.client.get(
        f"{_base()}/auth/tenants/{tenant_id}",
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.post("/auth/tenants", tags=["auth"])
async def upsert_tenant(request: Request) -> Any:
    """Proxy → Identity: create or update a tenant's tier and rpm_limit (ADMIN only).

    On success busts the in-process Redis ``acp:tenant_meta:{id}`` cache so
    rpm_limit / tier changes take effect on the very next request rather
    than after the 10-minute TTL.
    """
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{_base()}/auth/tenants",
        json=body,
        headers=internal_headers(request),
    )
    if resp.status_code in (200, 201) and isinstance(body, dict) and body.get("tenant_id"):
        try:
            redis_client = request.app.state.redis
            await redis_client.delete(f"acp:tenant_meta:{body['tenant_id']}")
        except Exception:
            pass
    return passthrough(resp)
