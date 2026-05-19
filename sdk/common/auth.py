import hmac
import os
import time
from typing import Any

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import APIKeyHeader
from jose import JWTError, jwt

from sdk.common.config import settings

# auto_error=False so we control the error message and status code (403 not 422)
internal_secret_header = APIKeyHeader(name="X-Internal-Secret", auto_error=False)


def extract_bearer_token(authorization: str) -> str | None:
    """
    Extract the raw JWT from an Authorization header value.

    Returns the bare token string (without "Bearer " prefix), or None if the
    header is absent or malformed. All token hashing across the codebase MUST
    use this function so the hash input is always consistent.
    """
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip() or None
    return None


# ---------------------------------------------------------------------------
# H-1 FIX (2026-05-13): Per-service short-lived mesh JWT.
# ---------------------------------------------------------------------------
# Coexists with INTERNAL_SECRET for backwards compatibility. Services that mint
# a mesh JWT (mint_service_token) get caller-identity tied to a service name;
# leak of one service's mesh token does NOT impersonate any other service.
# Validation accepts either the legacy shared secret OR a valid mesh JWT.
# Roadmap: once all callers mint mesh JWTs, drop the legacy fallback.

_MESH_AUDIENCE = "acp.mesh.internal"
_MESH_DEFAULT_TTL_SECONDS = 60
_MESH_ALGORITHM = "HS256"


def _mesh_signing_key() -> str:
    """Per-mesh signing key — falls back to INTERNAL_SECRET so a single env upgrade rolls out."""
    return os.getenv("MESH_JWT_SECRET", settings.INTERNAL_SECRET)


def mint_service_token(service_name: str, ttl_seconds: int = _MESH_DEFAULT_TTL_SECONDS) -> str:
    """
    Mint a short-lived mesh JWT for one caller service.
    Use in service-to-service calls instead of raw INTERNAL_SECRET when possible.
    """
    now = int(time.time())
    payload: dict[str, Any] = {
        "iss": service_name,
        "aud": _MESH_AUDIENCE,
        "iat": now,
        "exp": now + max(5, int(ttl_seconds)),
    }
    return jwt.encode(payload, _mesh_signing_key(), algorithm=_MESH_ALGORITHM)


def _verify_mesh_jwt(token: str) -> dict[str, Any] | None:
    """Validate a mesh JWT. Returns claims dict on success, None on failure."""
    try:
        return dict(
            jwt.decode(
                token,
                _mesh_signing_key(),
                algorithms=[_MESH_ALGORITHM],
                audience=_MESH_AUDIENCE,
            )
        )
    except JWTError:
        return None


def verify_internal_secret(
    secret: str | None = Depends(internal_secret_header),
    mesh_token: str | None = Header(default=None, alias="X-Mesh-Token"),
) -> str:
    """
    Zero-trust service mesh auth.

    Accepts EITHER:
      • X-Internal-Secret matching the shared INTERNAL_SECRET (legacy), OR
      • X-Mesh-Token: a short-lived mesh JWT issued by a trusted service.

    Returns 403 (authorization) not 401 (authentication) — the caller is
    identified but not permitted to reach internal services.

    H-1: Constant-time comparison is used for the legacy secret path so leak
    cannot be probed via timing.
    """
    if mesh_token:
        claims = _verify_mesh_jwt(mesh_token)
        if claims is not None:
            return f"mesh:{claims.get('iss', 'unknown')}"

    if secret and hmac.compare_digest(secret, settings.INTERNAL_SECRET):
        return secret

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Access forbidden: missing or invalid internal secret",
    )
