"""
OIDC provider integration — Google, Microsoft, Okta.

Flow:
  1. /auth/sso/{provider}           → redirect to provider's auth URL (PKCE-protected)
  2. Provider redirects back to     /auth/sso/{provider}/callback?code=...
  3. Exchange code + verifier       at provider's token endpoint
  4. Verify id_token signature      against provider's JWKS (cached, with rotation)
  5. Validate iss / aud / exp / iat from the id_token claims
  6. Extract email + name           from verified claims
  7. Upsert user in DB              (create if new, update last_login if existing)
  8. Issue ACP JWT                  same format as password auth
  9. Set httpOnly cookie + redirect → dashboard

Security note (Sprint 0): id_token signature verification against the IdP's JWKS is
mandatory. Decoding the payload without checking the signature is an SSO bypass:
HTTPS authenticates the IdP server identity, NOT the contents of the id_token. A
network attacker, a poisoned discovery cache, or a careless code update could
otherwise forge claims. JWKS keys are fetched on demand and cached with TTL; a
signing-key rotation at the IdP triggers a JWKS re-fetch on the first verification
failure for an unknown `kid`.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx
import structlog
from jose import jwt
from jose.exceptions import JWTError

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
        "expected_iss":  "https://accounts.google.com",
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
        # Microsoft's iss is tenant-scoped: `https://login.microsoftonline.com/{tid}/v2.0`.
        # Empty here means "trust the issuer present in the discovery doc" (resolved at runtime).
        "expected_iss":  "",
    },
    "okta": {
        "client_id":     os.environ.get("OKTA_CLIENT_ID", ""),
        "client_secret": os.environ.get("OKTA_CLIENT_SECRET", ""),
        "discovery_url": (
            f"https://{os.environ.get('OKTA_DOMAIN', 'example.okta.com')}"
            f"/.well-known/openid-configuration"
        ),
        "scope":         "openid email profile groups",
        "expected_iss":  "",  # resolved from discovery doc
    },
}

# In-process caches.
# Both are { provider: (issued_monotonic_ts, payload) } and refreshed on miss.
_discovery_cache: dict[str, tuple[float, dict]] = {}
_jwks_cache: dict[str, tuple[float, dict]] = {}
_DISCOVERY_TTL = 3600.0
_JWKS_TTL = 3600.0


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


async def _get_jwks(provider: str, force_refresh: bool = False) -> dict[str, Any]:
    """
    Fetch and cache the provider's JWKS (public signing keys).
    A `force_refresh=True` bypasses cache — call this when an `id_token` references
    a `kid` not present in the cache (covers IdP key rotation).
    """
    now = time.monotonic()
    if not force_refresh:
        cached = _jwks_cache.get(provider)
        if cached and (now - cached[0]) < _JWKS_TTL:
            return cached[1]

    doc = await _get_discovery(provider)
    jwks_uri = doc.get("jwks_uri")
    if not jwks_uri:
        raise ValueError(f"OIDC discovery for '{provider}' is missing jwks_uri")

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(jwks_uri)
        resp.raise_for_status()
        jwks = resp.json()

    _jwks_cache[provider] = (now, jwks)
    return jwks


def _find_key(jwks: dict[str, Any], kid: str) -> dict[str, Any] | None:
    """Locate the JWK whose `kid` matches the id_token header `kid`."""
    for key in jwks.get("keys", []):
        if key.get("kid") == kid:
            return key
    return None


async def verify_id_token(provider: str, id_token: str) -> dict[str, Any]:
    """
    Verify an id_token end-to-end against the IdP's JWKS and return the validated
    claims. Raises ValueError with a narrow reason on any verification failure.

    Checks:
      1. Header `alg` is RS256/RS384/RS512/ES256/ES384/ES512 (not 'none', not HSxx).
      2. Header `kid` resolves to a JWK in the JWKS (refreshes JWKS on miss).
      3. RSA/EC signature verifies.
      4. `iss` matches the provider's expected issuer (or the discovery doc's issuer).
      5. `aud` includes the configured client_id.
      6. `exp` in the future, `iat` not unreasonably in the future (clock skew 60s).

    This is the security boundary: anything past this function trusts the claims.
    """
    cfg = _provider_cfg(provider)
    discovery = await _get_discovery(provider)

    # 1. Parse the unverified header to extract `kid` (we still need the JWKS lookup
    #    before signature verification).
    try:
        unverified_header = jwt.get_unverified_header(id_token)
    except JWTError as exc:
        raise ValueError(f"id_token header malformed: {exc}") from exc

    alg = unverified_header.get("alg", "")
    if alg in ("none", "", "HS256", "HS384", "HS512"):
        # Symmetric-key algorithms have no place in OIDC — the IdP would have to
        # share a secret with every relying party. Refusing them blocks the
        # `alg: none` and HMAC-confusion attacks in one branch.
        raise ValueError(f"id_token uses disallowed alg: {alg or 'none'}")

    kid = unverified_header.get("kid")
    if not kid:
        raise ValueError("id_token header missing kid")

    # 2. Locate the signing key. If the kid is unknown, force a JWKS refresh once
    #    (covers IdP key rotation without waiting for the cache TTL).
    jwks = await _get_jwks(provider)
    key = _find_key(jwks, kid)
    if key is None:
        jwks = await _get_jwks(provider, force_refresh=True)
        key = _find_key(jwks, kid)
        if key is None:
            raise ValueError(f"id_token kid '{kid}' not found in JWKS")

    # 3+4+5+6. Hand the heavy lifting to python-jose, which does signature, iss,
    #          aud, and exp verification in one pass. We re-validate `iss` because
    #          some providers (Microsoft) have tenant-scoped issuers that don't
    #          appear in the static provider config.
    expected_iss = cfg["expected_iss"] or discovery.get("issuer", "")
    if not expected_iss:
        raise ValueError(f"no expected issuer for provider '{provider}'")

    try:
        claims = jwt.decode(
            id_token,
            key,
            algorithms=[alg],
            audience=cfg["client_id"],
            issuer=expected_iss,
            options={
                "verify_signature": True,
                "verify_aud":       True,
                "verify_iss":       True,
                "verify_exp":       True,
                "verify_iat":       True,
                "leeway":           60,  # 60s clock skew tolerance
            },
        )
    except JWTError as exc:
        raise ValueError(f"id_token verification failed: {exc}") from exc

    return claims


# ---------------------------------------------------------------------------
# PKCE (RFC 7636)
# ---------------------------------------------------------------------------

def build_pkce_challenge() -> tuple[str, str]:
    """
    Generate a PKCE (code_verifier, code_challenge) pair using S256 transform.

    - code_verifier:  43–128 char URL-safe base64 string, stored by the caller
                      (Redis under the `state` key) and presented to the token
                      endpoint during code exchange.
    - code_challenge: BASE64URL(SHA256(verifier)) — sent to the IdP in the
                      authorization request.

    Returns (verifier, challenge).
    """
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


# ---------------------------------------------------------------------------
# Auth URL + code exchange
# ---------------------------------------------------------------------------

async def build_auth_url(
    provider: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
) -> str:
    """Build the authorization URL the browser should be redirected to."""
    cfg = _provider_cfg(provider)
    doc = await _get_discovery(provider)

    params = {
        "client_id":             cfg["client_id"],
        "redirect_uri":          redirect_uri,
        "response_type":         "code",
        "scope":                 cfg["scope"],
        "state":                 state,
        "nonce":                 secrets.token_urlsafe(16),
        "code_challenge":        code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{doc['authorization_endpoint']}?{urlencode(params)}"


async def exchange_code(
    provider: str,
    code: str,
    redirect_uri: str,
    code_verifier: str,
) -> dict[str, Any]:
    """
    Exchange an authorization code for tokens, then verify the id_token.

    `code_verifier` is the PKCE secret stored under the state token at /sso/{provider}.
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
                "code_verifier": code_verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        token_resp = resp.json()

    id_token = token_resp.get("id_token", "")
    if not id_token:
        raise ValueError("no id_token in token response")

    # SECURITY: verify against IdP JWKS — never trust the payload directly.
    claims = await verify_id_token(provider, id_token)

    if not claims.get("email"):
        # Fallback for providers that put email in userinfo (rare but allowed).
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


# ---------------------------------------------------------------------------
# State token (CSRF defense)
# ---------------------------------------------------------------------------

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
