import hmac
import json
import os
import time
from functools import lru_cache
from typing import Any

import structlog
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import APIKeyHeader
from jose import ExpiredSignatureError, JWTError, jwt
from prometheus_client import Counter

from sdk.common.config import settings

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

MESH_JWT_AUTH_TOTAL = Counter(
    "mesh_jwt_auth_total",
    "Service mesh authentication attempts by method",
    ["method"],  # jwt | legacy | failed
)

# N22 (2026-06-21): operational visibility into ACP_MESH_TRUSTED_KEYS misconfiguration.
# An unparseable trusted-keys env var fail-closes (all mesh JWTs fail to verify),
# but the failure was silent — only a single ERROR-level log line. Surface it as a
# Prometheus counter so monitoring/alerts fire immediately when this happens.
MESH_TRUSTED_KEYS_PARSE_FAILURES = Counter(
    "mesh_trusted_keys_parse_failures_total",
    "Times ACP_MESH_TRUSTED_KEYS env var was unparseable",
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
# Sprint 1.4 — Per-service asymmetric mesh JWT (closes audit C12).
# ---------------------------------------------------------------------------
# Before Sprint 1: every service signed and verified mesh tokens with the SAME
# HS256 secret (``MESH_JWT_SECRET`` → fallback to ``INTERNAL_SECRET``). Leak of
# one secret forged every other service's tokens, which contradicted the
# README claim "no single shared secret."
#
# After Sprint 1: each service owns an ES256 (ECDSA P-256) private key. Verifying
# services hold the *public* keys of every service whose tokens they accept.
# Leaking service A's private key cannot forge service B's tokens — the
# verifier's kid lookup binds the signature to a single trusted public key.
#
# Configuration (env vars):
#   ACP_MESH_SERVICE_NAME       — this process's identity (e.g. "gateway").
#   ACP_MESH_PRIVATE_KEY_PEM    — base64-encoded ES256 PKCS8 PEM (this service).
#                                 Optional if the service only verifies tokens.
#   ACP_MESH_TRUSTED_KEYS       — JSON object {service_name: PEM (base64)}
#                                 listing every service whose tokens we accept.
#
# Back-compat: when none of the above are set, mint_service_token + verify
# fall back to the legacy HS256/INTERNAL_SECRET path. ``verify_internal_secret``
# refuses the legacy ``X-Internal-Secret`` header only when mesh keys are
# configured — otherwise pre-Sprint-1 deployments keep working unchanged.

_MESH_AUDIENCE = "acp.mesh.internal"
_MESH_DEFAULT_TTL_SECONDS = 300  # 5-minute TTL for service mesh tokens
_MESH_LEGACY_ALGORITHM = "HS256"
_MESH_ES256_ALGORITHM = "ES256"


def _b64_or_raw_pem(raw: str) -> bytes:
    """Accept either a base64-encoded PEM or a raw PEM string."""
    import base64
    raw = raw.strip()
    if raw.startswith("-----BEGIN"):
        return raw.encode("ascii")
    try:
        return base64.b64decode(raw)
    except Exception:
        return raw.encode("ascii")


@lru_cache(maxsize=1)
def _mesh_service_identity() -> str | None:
    name = (os.environ.get("ACP_MESH_SERVICE_NAME") or "").strip()
    return name or None


@lru_cache(maxsize=1)
def _mesh_private_key_pem() -> bytes | None:
    raw = os.environ.get("ACP_MESH_PRIVATE_KEY_PEM", "").strip()
    if not raw:
        return None
    return _b64_or_raw_pem(raw)


@lru_cache(maxsize=1)
def _mesh_trusted_public_keys() -> dict[str, bytes]:
    """Map of service_name → PEM-bytes from ``ACP_MESH_TRUSTED_KEYS`` JSON.

    Empty when not configured — callers treat empty-registry as "fall back to
    the legacy HS256 path."

    N22 (2026-06-21): a malformed env var used to log ERROR and silently
    return ``{}``, which fails closed (every mesh JWT verification misses
    the trusted_keys lookup and is rejected) but is operationally invisible.
    Now we increment a Prometheus counter and log at CRITICAL so monitoring
    fires immediately. Individually bad entries (e.g. a single non-string
    PEM) are skipped instead of poisoning the whole dict — the rest of the
    services keep working.
    """
    raw = (os.environ.get("ACP_MESH_TRUSTED_KEYS") or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        MESH_TRUSTED_KEYS_PARSE_FAILURES.inc()
        logger.critical("mesh_trusted_keys_unparseable", error=str(exc))
        return {}
    if not isinstance(data, dict):
        MESH_TRUSTED_KEYS_PARSE_FAILURES.inc()
        logger.critical("mesh_trusted_keys_not_a_dict", type=type(data).__name__)
        return {}
    out: dict[str, bytes] = {}
    for name, pem_raw in data.items():
        if not isinstance(pem_raw, str):
            logger.warning(
                "mesh_trusted_keys_bad_entry",
                svc=name,
                type=type(pem_raw).__name__,
            )
            continue  # Skip bad entries — don't poison the whole dict
        out[str(name)] = _b64_or_raw_pem(pem_raw)
    return out


def _mesh_keys_configured() -> bool:
    """True when the asymmetric mesh-key path is active in this process."""
    return bool(_mesh_private_key_pem() or _mesh_trusted_public_keys())


def _reset_mesh_caches_for_tests() -> None:
    """Drop the lru caches so tests can monkeypatch env vars."""
    _mesh_service_identity.cache_clear()
    _mesh_private_key_pem.cache_clear()
    _mesh_trusted_public_keys.cache_clear()


def _mesh_legacy_signing_key() -> str:
    """HS256 path. Kept for back-compat with deployments that haven't rotated
    to asymmetric keys yet. Once ``ACP_MESH_TRUSTED_KEYS`` is set this path is
    not used for new tokens and is rejected by the verifier."""
    return settings.MESH_JWT_SECRET or settings.INTERNAL_SECRET


def mint_service_token(service_name: str, ttl_seconds: int = _MESH_DEFAULT_TTL_SECONDS) -> str:
    """Mint a short-lived mesh JWT.

    Algorithm selection:
      * ES256 + per-service private key when ``ACP_MESH_PRIVATE_KEY_PEM`` is
        configured (Sprint 1.4 path).
      * HS256 + ``_mesh_legacy_signing_key`` otherwise (back-compat).

    Claims: ``iss`` (caller name), ``aud`` (``acp.mesh.internal``), ``iat``,
    ``exp``, ``scope=internal``. The JOSE header carries ``kid`` set to the
    caller's service name so the verifier can pick the right public key.
    """
    now = int(time.time())
    payload: dict[str, Any] = {
        "iss":   service_name,
        "aud":   _MESH_AUDIENCE,
        "iat":   now,
        "exp":   now + max(5, int(ttl_seconds)),
        "scope": "internal",
    }
    priv_pem = _mesh_private_key_pem()
    if priv_pem:
        # ES256 path. Identity is the env-configured service name when
        # available; the explicit ``service_name`` arg is what gets stamped
        # into iss/kid so the verifier's lookup is unambiguous.
        return jwt.encode(
            payload,
            priv_pem.decode("ascii"),
            algorithm=_MESH_ES256_ALGORITHM,
            headers={"kid": service_name},
        )
    # Legacy HS256.
    return jwt.encode(payload, _mesh_legacy_signing_key(), algorithm=_MESH_LEGACY_ALGORITHM)


def _verify_mesh_jwt(token: str) -> dict[str, Any] | None:
    """Validate a mesh JWT.

    Order of checks:
      1. If the token's header ``alg`` is ES256, look up the signer's public
         key in ``_mesh_trusted_public_keys()`` by ``kid``. Verify and return
         claims. If the kid is unknown or signature fails, treat as hard
         failure (no fallback — the asymmetric path is the strict path).
      2. Otherwise, try HS256 against the legacy secret. This is the
         back-compat lane and is what keeps pre-Sprint-1 deployments working.

    Returns claims dict on success, ``None`` on hard failure, or a sentinel
    ``{"_expired": True, "iss": ...}`` so callers can distinguish expiry from
    other validation failures (mesh tokens rotate often; a 60-second skew
    during rotation must not 500 the request).
    """
    try:
        header = jwt.get_unverified_header(token)
    except JWTError:
        return None
    alg = header.get("alg", "")

    if alg == _MESH_ES256_ALGORITHM:
        kid = header.get("kid", "")
        trusted = _mesh_trusted_public_keys()
        pub_pem = trusted.get(kid)
        if pub_pem is None:
            logger.warning("mesh_jwt_unknown_kid", kid=kid)
            return None
        try:
            return dict(
                jwt.decode(
                    token,
                    pub_pem.decode("ascii"),
                    algorithms=[_MESH_ES256_ALGORITHM],
                    audience=_MESH_AUDIENCE,
                )
            )
        except ExpiredSignatureError:
            try:
                unverified = jwt.get_unverified_claims(token)
                return {"_expired": True, "iss": unverified.get("iss", kid)}
            except Exception:
                return {"_expired": True, "iss": kid}
        except JWTError as exc:
            logger.warning("mesh_jwt_es256_invalid", kid=kid, error=str(exc))
            return None

    # Back-compat HS256 lane.
    try:
        return dict(
            jwt.decode(
                token,
                _mesh_legacy_signing_key(),
                algorithms=[_MESH_LEGACY_ALGORITHM],
                audience=_MESH_AUDIENCE,
            )
        )
    except ExpiredSignatureError:
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

    # Legacy X-Internal-Secret lane. Sprint 1.4: when per-service mesh keys
    # are configured the legacy single-secret path is REJECTED — otherwise
    # an attacker who compromised the old INTERNAL_SECRET would still get a
    # free pass even after we ship asymmetric mesh keys.
    if _mesh_keys_configured():
        MESH_JWT_AUTH_TOTAL.labels(method="failed").inc()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Access forbidden: legacy X-Internal-Secret is disabled when "
                "ACP_MESH_TRUSTED_KEYS is configured; present a mesh JWT instead"
            ),
        )

    if secret and hmac.compare_digest(secret, settings.INTERNAL_SECRET):
        MESH_JWT_AUTH_TOTAL.labels(method="legacy").inc()
        return secret

    MESH_JWT_AUTH_TOTAL.labels(method="failed").inc()
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Access forbidden: missing or invalid internal secret",
    )
