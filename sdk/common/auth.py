import hmac
import logging
import time
from typing import Any

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import APIKeyHeader
from jose import ExpiredSignatureError, JWTError, jwt
from prometheus_client import Counter

from sdk.common.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

MESH_JWT_AUTH_TOTAL = Counter(
    "mesh_jwt_auth_total",
    "Service mesh authentication attempts by method",
    ["method"],  # jwt | legacy | failed
)

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
_MESH_DEFAULT_TTL_SECONDS = 300  # 5-minute TTL for service mesh tokens
_MESH_ALGORITHM = "HS256"


def _mesh_signing_key() -> str:
    """Per-mesh signing key — falls back to INTERNAL_SECRET so a single env upgrade rolls out."""
    return settings.MESH_JWT_SECRET or settings.INTERNAL_SECRET


def mint_service_token(service_name: str, ttl_seconds: int = _MESH_DEFAULT_TTL_SECONDS) -> str:
    """
    Mint a short-lived mesh JWT for one caller service.
    Use in service-to-service calls instead of raw INTERNAL_SECRET when possible.

    Token claims:
      iss: caller service name (e.g. "gateway", "decision")
      aud: "acp.mesh.internal" (internal services only)
      iat: issuance time (UTC epoch seconds)
      exp: expiry time (iat + ttl_seconds, minimum 5s)
      scope: "internal" (machine-to-machine)
    """
    now = int(time.time())
    payload: dict[str, Any] = {
        "iss": service_name,
        "aud": _MESH_AUDIENCE,
        "iat": now,
        "exp": now + max(5, int(ttl_seconds)),
        "scope": "internal",
    }
    return jwt.encode(payload, _mesh_signing_key(), algorithm=_MESH_ALGORITHM)


def _verify_mesh_jwt(token: str) -> dict[str, Any] | None:
    """
    Validate a mesh JWT. Returns claims dict on success, None on hard failure.

    Distinguishes expired tokens (returns sentinel {"_expired": True, "iss": ...})
    so callers can fall through to the legacy secret rather than hard-failing.
    """
    try:
        return dict(
            jwt.decode(
                token,
                _mesh_signing_key(),
                algorithms=[_MESH_ALGORITHM],
                audience=_MESH_AUDIENCE,
            )
        )
    except ExpiredSignatureError:
        # Decode without verification to extract the issuer for logging purposes
        try:
            unverified = jwt.get_unverified_claims(token)
            return {"_expired": True, "iss": unverified.get("iss", "unknown")}
        except Exception:
            return {"_expired": True, "iss": "unknown"}
    except JWTError:
        return None


def verify_internal_secret(
    secret: str | None = Depends(internal_secret_header),
    mesh_token: str | None = Header(default=None, alias="X-Mesh-Token"),
) -> str:
    """
    Zero-trust service mesh auth.

    Accepts EITHER:
      • X-Mesh-Token: a short-lived mesh JWT issued by a trusted service (preferred), OR
      • X-Internal-Secret matching the shared INTERNAL_SECRET (legacy fallback).

    Returns 403 (authorization) not 401 (authentication) — the caller is
    identified but not permitted to reach internal services.

    H-1: Constant-time comparison is used for the legacy secret path so leak
    cannot be probed via timing.

    Migration behaviour:
      - Valid mesh JWT → accepted immediately, legacy secret ignored.
      - Expired mesh JWT → warning logged, fall through to legacy secret check
        (supports zero-downtime token rotation during gradual rollout).
      - Invalid/absent mesh JWT → fall through to legacy secret check.
      - Both absent → HTTP 403.
    """
    if mesh_token:
        claims = _verify_mesh_jwt(mesh_token)
        if claims is not None:
            if claims.get("_expired"):
                # Gradual migration: warn and fall through instead of hard-failing
                issuer = claims.get("iss", "unknown")
                logger.warning(
                    "mesh_jwt_expired_fallback_to_legacy",
                    issuer=issuer,
                )
                # Fall through to legacy check below
            else:
                MESH_JWT_AUTH_TOTAL.labels(method="jwt").inc()
                return f"mesh:{claims.get('iss', 'unknown')}"

    if secret and hmac.compare_digest(secret, settings.INTERNAL_SECRET):
        MESH_JWT_AUTH_TOTAL.labels(method="legacy").inc()
        return secret

    MESH_JWT_AUTH_TOTAL.labels(method="failed").inc()
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Access forbidden: missing or invalid internal secret",
    )
