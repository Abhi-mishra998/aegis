"""Gateway SSO proxy routes.

All /auth/sso/* routes consolidated here in the sprint-5 audit cleanup
(previously split between this file and main.py with three duplicate
config routes that the late app.include_router silently shadowed).

  /auth/sso/providers              — public providers list (Login page
                                     calls it before any token exists;
                                     skip-listed in middleware)
  /auth/sso/config (GET + POST)    — tenant SSO provider config
                                     (secrets masked on read, persisted
                                     on write)
  /auth/sso/config/test            — probe configured SSO provider
                                     for reachability
  /auth/sso/{provider}             — kick off the OIDC redirect
  /auth/sso/{provider}/callback    — OIDC callback; forwards the
                                     upstream redirect-with-cookie
                                     back to the browser

The literal ``/auth/sso/config`` + ``/auth/sso/config/test`` paths MUST
sit BEFORE ``/auth/sso/{provider}`` so FastAPI doesn't match "config"
as a provider name. The order in this file preserves that.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from starlette.responses import RedirectResponse

from sdk.common.config import settings
from services.gateway._helpers import internal_headers, passthrough

router = APIRouter(tags=["sso"])


def _base() -> str:
    return settings.IDENTITY_SERVICE_URL.rstrip("/")


@router.get("/auth/sso/providers")
async def sso_providers(request: Request) -> Any:
    """Return the list of configured SSO providers for the login UI.

    Public (unauthenticated) — the Login page calls this before any
    token exists, so the gateway's middleware skip-list includes it.
    """
    resp = await request.app.state.client.get(f"{_base()}/auth/sso/providers")
    return passthrough(resp)


@router.get("/auth/sso/config")
async def get_sso_config(request: Request) -> Any:
    """Proxy → Identity: read tenant SSO provider config (secrets masked)."""
    resp = await request.app.state.client.get(
        f"{_base()}/auth/sso/config",
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.post("/auth/sso/config")
async def save_sso_config(request: Request) -> Any:
    """Proxy → Identity: persist tenant SSO provider config."""
    headers = internal_headers(request)
    ctype = request.headers.get("content-type")
    if ctype:
        headers["Content-Type"] = ctype
    resp = await request.app.state.client.post(
        f"{_base()}/auth/sso/config",
        content=await request.body(),
        headers=headers,
    )
    return passthrough(resp)


@router.post("/auth/sso/config/test")
async def test_sso_config(request: Request) -> Any:
    """Proxy → Identity: probe configured SSO provider for reachability."""
    headers = internal_headers(request)
    ctype = request.headers.get("content-type")
    if ctype:
        headers["Content-Type"] = ctype
    resp = await request.app.state.client.post(
        f"{_base()}/auth/sso/config/test",
        content=await request.body(),
        headers=headers,
    )
    return passthrough(resp)


@router.get("/auth/sso/{provider}")
async def sso_login_redirect(provider: str, request: Request) -> Any:
    """Initiate SSO — proxies redirect to OIDC provider."""
    url = f"{_base()}/auth/sso/{provider}"
    resp = await request.app.state.client.get(url, params=dict(request.query_params))
    if resp.status_code in (301, 302, 303, 307, 308):
        return RedirectResponse(resp.headers["location"], status_code=resp.status_code)
    return passthrough(resp)


@router.get("/auth/sso/{provider}/callback")
async def sso_callback_proxy(provider: str, request: Request) -> Any:
    """Handle the OIDC callback and proxy the redirect-with-cookie back to the browser."""
    url = f"{_base()}/auth/sso/{provider}/callback"
    resp = await request.app.state.client.get(url, params=dict(request.query_params))
    if resp.status_code in (301, 302, 303, 307, 308):
        rr = RedirectResponse(resp.headers.get("location", "/"), status_code=resp.status_code)
        if "set-cookie" in resp.headers:
            rr.headers["set-cookie"] = resp.headers["set-cookie"]
        return rr
    return passthrough(resp)
