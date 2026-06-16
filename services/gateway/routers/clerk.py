"""
Gateway proxies for the Clerk integration surface.

Two routes:

  POST /webhooks/clerk         — receives Svix-signed Clerk webhooks and
                                 forwards them to the identity service.
                                 Raw body + svix-* headers preserved.
                                 No Aegis auth (Svix-signed only).

  POST /auth/clerk/provision   — synchronous fallback for the
                                 signup → first-request race. Forwards
                                 a Clerk Bearer JWT to identity, which
                                 validates it via JWKS and upserts
                                 Org+Tenant+User.

Both routes MUST be present in `_SKIP_PATHS` in middleware.py — neither
carries an Aegis-issued bearer token at the gateway boundary, and the
middleware would otherwise 401 every request before our proxy runs.
"""

from __future__ import annotations

import json as _json
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, Request, Response

from sdk.common.config import settings

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["clerk"])


def _identity_base() -> str:
    return settings.IDENTITY_SERVICE_URL.rstrip("/")


@router.post("/webhooks/clerk")
async def proxy_clerk_webhook(request: Request) -> Response:
    """
    Forward a Clerk webhook to identity:8002/webhooks/clerk with the raw
    body preserved (Svix HMAC is computed over the byte-exact body — any
    re-serialization corrupts it).

    The svix-id / svix-timestamp / svix-signature headers are forwarded
    explicitly so the upstream verifier sees identical bytes to what
    Clerk signed.
    """
    raw_body = await request.body()

    forward_headers: dict[str, str] = {}
    # Svix-signed headers — case-insensitive on the wire; httpx normalizes.
    for h in ("svix-id", "svix-timestamp", "svix-signature"):
        val = request.headers.get(h)
        if val:
            forward_headers[h] = val
    # Preserve Content-Type so the upstream FastAPI parser doesn't re-guess.
    ct = request.headers.get("content-type")
    if ct:
        forward_headers["content-type"] = ct

    url = f"{_identity_base()}/webhooks/clerk"
    client = request.app.state.client
    try:
        resp = await client.post(
            url,
            headers=forward_headers,
            content=raw_body,
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        logger.error("clerk_webhook_proxy_error", error=str(exc))
        return Response(
            content=_json.dumps(
                {"error": f"Upstream identity unreachable: {type(exc).__name__}"},
            ),
            status_code=502,
            media_type="application/json",
        )

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/json"),
    )


@router.post("/auth/clerk/provision")
async def proxy_clerk_provision(request: Request) -> Response:
    """
    Forward the Clerk JWT to identity:8002/auth/clerk/provision.

    Unlike most /auth/* proxies, we do NOT add `internal_headers` — the
    upstream handler authenticates against the Clerk JWT itself, and any
    Aegis-mesh secret would only obscure the actual authenticator.

    Sprint-1 follow-up: ALSO set the Clerk JWT as the `acp_token`
    httpOnly cookie. The browser EventSource API can't attach custom
    headers, so the SSE endpoint /events/stream can only authenticate
    Clerk users via the cookie (the gateway's token_validator routes
    Clerk RS256 tokens through Clerk's JWKS regardless of whether
    they arrive via the Authorization header or the cookie). Without
    this set-cookie, Clerk users see the topbar "Syncing" indicator
    forever because the SSE handshake gets 401 every time.
    """
    raw_body = await request.body()

    forward_headers: dict[str, str] = {}
    auth = request.headers.get("authorization")
    if auth:
        forward_headers["authorization"] = auth
    ct = request.headers.get("content-type")
    if ct:
        forward_headers["content-type"] = ct

    url = f"{_identity_base()}/auth/clerk/provision"
    client = request.app.state.client
    try:
        resp = await client.post(
            url,
            headers=forward_headers,
            content=raw_body if raw_body else None,
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        logger.error("clerk_provision_proxy_error", error=str(exc))
        return Response(
            content=_json.dumps(
                {"error": f"Upstream identity unreachable: {type(exc).__name__}"},
            ),
            status_code=502,
            media_type="application/json",
        )

    out = Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/json"),
    )

    # On successful provision, mirror the Clerk JWT into the acp_token
    # cookie. Mirrors the legacy /auth/token cookie convention: httpOnly,
    # samesite=strict, 1-day TTL, secure-flag gated on production.
    if 200 <= resp.status_code < 300 and auth and auth.lower().startswith("bearer "):
        clerk_jwt = auth[7:].strip()
        if clerk_jwt:
            is_secure = settings.ENVIRONMENT == "production"
            out.set_cookie(
                key="acp_token",
                value=clerk_jwt,
                httponly=True,
                secure=is_secure,
                samesite="strict",
                max_age=86400,
            )

    return out
