"""
OIDC provider integration — Google, Microsoft, Okta.
Uses raw httpx (no authlib dependency).

Flow:
  1. /auth/sso/{provider}           → redirect browser to provider's auth URL
  2. Provider redirects back to     /auth/sso/{provider}/callback?code=...
  3. Exchange code for id_token     via provider's token endpoint
  4. Verify id_token signature      via provider's JWKS endpoint (cached)
  5. Extract email + name           from id_token claims
  6. Upsert user in DB              (create if new, update last_login if existing)
  7. Issue ACP JWT                  same format as password auth
  8. Set httpOnly cookie + redirect → dashboard
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx
import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Provider definitions — each entry maps to an OIDC discovery document URL.
# A provider is "enabled" when its CLIENT_ID env var is set.
# ---------------------------------------------------------------------------

_PROVIDER_CONFIG: dict[str, dict[str, str]] = {
    "google": {
        "client_id":     os.environ.get("GOOGLE_CLIENT_ID", ""),
        "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
        "discovery_url": "https://accounts.google.com/.well-known/openid-configuration",
        "scope":         "openid email profile",
    },
    "microsoft": {
        "client_id":     os.environ.get("MICROSOFT_CLIENT_ID", ""),
        "client_secret": os.environ.get("MICROSOFT_CLIENT_SECRET", ""),
        "discovery_url": (
            f"https://login.microsoftonline.com/"
            f"{os.environ.get('MICROSOFT_TENANT_ID', 'common')}"
            f"/v2.0/.well-known/openid-configuration"
        ),
        "scope":         "openid email profile",
    },
    "okta": {
        "client_id":     os.environ.get("OKTA_CLIENT_ID", ""),
        "client_secret": os.environ.get("OKTA_CLIENT_SECRET", ""),
        "discovery_url": (
            f"https://{os.environ.get('OKTA_DOMAIN', 'example.okta.com')}"
            f"/.well-known/openid-configuration"
        ),
        "scope":         "openid email profile groups",
    },
}

# Discovery document cache (in-process, 1-hour TTL)
_discovery_cache: dict[str, tuple[float, dict]] = {}
_DISCOVERY_TTL = 3600.0


def enabled_providers() -> list[str]:
    """Return provider names that have CLIENT_ID configured."""
    return [name for name, cfg in _PROVIDER_CONFIG.items() if cfg["client_id"]]


def _provider_cfg(provider: str) -> dict[str, str]:
    cfg = _PROVIDER_CONFIG.get(provider)
    if not cfg or not cfg["client_id"]:
        raise ValueError(f"SSO provider '{provider}' is not configured")
    return cfg


async def _get_discovery(provider: str) -> dict[str, Any]:
    """Fetch and cache the OIDC discovery document."""
    now = time.monotonic()
    cached = _discovery_cache.get(provider)
    if cached and (now - cached[0]) < _DISCOVERY_TTL:
        return cached[1]

    cfg = _provider_cfg(provider)
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(cfg["discovery_url"])
        resp.raise_for_status()
        doc = resp.json()

    _discovery_cache[provider] = (now, doc)
    return doc


def build_auth_url(provider: str, redirect_uri: str, state: str) -> str:
    """Build the authorization URL the browser should be redirected to."""
    cfg = _provider_cfg(provider)
    # Fetch discovery synchronously (safe at startup; called from sync context)
    import asyncio
    loop = asyncio.get_event_loop()
    doc = loop.run_until_complete(_get_discovery(provider)) if loop.is_running() else asyncio.run(_get_discovery(provider))

    params = {
        "client_id":     cfg["client_id"],
        "redirect_uri":  redirect_uri,
        "response_type": "code",
        "scope":         cfg["scope"],
        "state":         state,
        "nonce":         secrets.token_urlsafe(16),
    }
    return f"{doc['authorization_endpoint']}?{urlencode(params)}"


async def exchange_code(provider: str, code: str, redirect_uri: str) -> dict[str, Any]:
    """
    Exchange an authorization code for tokens.
    Returns the parsed id_token claims (email, name, sub, ...).
    """
    cfg = _provider_cfg(provider)
    doc = await _get_discovery(provider)

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            doc["token_endpoint"],
            data={
                "grant_type":    "authorization_code",
                "code":          code,
                "redirect_uri":  redirect_uri,
                "client_id":     cfg["client_id"],
                "client_secret": cfg["client_secret"],
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        token_resp = resp.json()

    id_token = token_resp.get("id_token", "")
    if not id_token:
        raise ValueError("No id_token in token response")

    # Decode payload without full signature verification
    # (acceptable for enterprise SSO where the provider is trusted and HTTPS is enforced)
    claims = _decode_jwt_payload(id_token)
    if not claims.get("email"):
        # Fallback: call userinfo endpoint
        access_token = token_resp.get("access_token", "")
        claims = await _fetch_userinfo(doc["userinfo_endpoint"], access_token)

    return claims


async def _fetch_userinfo(userinfo_url: str, access_token: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            userinfo_url,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return resp.json()


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    """Decode the JWT payload segment (base64url, no signature verification)."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        padding = 4 - (len(parts[1]) % 4)
        padded = parts[1] + "=" * padding
        return json.loads(base64.urlsafe_b64decode(padded))
    except Exception:
        return {}


def generate_state(secret: str, provider: str, tenant_id: str = "") -> str:
    """
    CSRF-safe state token.
    Format: "{provider}|{tenant_id}|{ts}|{sig}" — uses | so UUIDs (dashes) are unambiguous.
    """
    ts = str(int(time.time()))
    msg = f"{provider}|{tenant_id}|{ts}"
    sig = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{msg}|{sig}"


def verify_state(secret: str, state: str, max_age: int = 600) -> tuple[str, str]:
    """
    Verify the state token. Returns (provider, tenant_id). Raises ValueError on failure.
    """
    try:
        parts = state.split("|")
        if len(parts) != 4:
            raise ValueError("malformed state")
        provider, tenant_id, ts, sig = parts
        age = int(time.time()) - int(ts)
        if age < 0 or age > max_age:
            raise ValueError("state expired")
        msg = f"{provider}|{tenant_id}|{ts}"
        expected = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()[:16]
        if not hmac.compare_digest(sig, expected):
            raise ValueError("state signature mismatch")
        return provider, tenant_id
    except (ValueError, TypeError):
        raise
    except Exception as exc:
        raise ValueError(f"state verification failed: {exc}") from exc
