import json
import logging
import os
import time
from functools import lru_cache
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
    """
    raw = (os.environ.get("ACP_MESH_TRUSTED_KEYS") or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("mesh_trusted_keys_malformed: %s", exc)
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, bytes] = {}
    for name, pem_raw in data.items():
        if not isinstance(pem_raw, str):
            continue
        out[str(name)] = _b64_or_raw_pem(pem_raw)
    return out


def _reset_mesh_caches_for_tests() -> None:
    """Drop the lru caches so tests can monkeypatch env vars."""
    _mesh_service_identity.cache_clear()
    _mesh_private_key_pem.cache_clear()
    _mesh_trusted_public_keys.cache_clear()


def mint_service_token(service_name: str, ttl_seconds: int = _MESH_DEFAULT_TTL_SECONDS) -> str:
    """Mint a short-lived ES256 mesh JWT.

    Phase 3 (2026-06-21): the HS256 back-compat path was removed. Every
    container in prod has ACP_MESH_PRIVATE_KEY_PEM rendered from SSM at
    boot (see infra/docker-compose.aws.yml per-service environment block).
    A missing private key now raises RuntimeError loudly instead of
    silently downgrading to a shared-secret signature — that downgrade was
    the foothold for the .env-exfiltration attack the brutal review found.

    Claims: ``iss`` (caller name), ``aud`` (``acp.mesh.internal``), ``iat``,
    ``exp``, ``scope=internal``. The JOSE header carries ``kid`` set to the
    caller's service name so the verifier can pick the right public key.
    """
    priv_pem = _mesh_private_key_pem()
    if not priv_pem:
        raise RuntimeError(
            "ACP_MESH_PRIVATE_KEY_PEM is not configured; cannot mint mesh JWT. "
            "Phase 2 wired this env var per service from SSM — check the "
            "compose overlay and the .env render."
        )
    now = int(time.time())
    payload: dict[str, Any] = {
        "iss":   service_name,
        "aud":   _MESH_AUDIENCE,
        "iat":   now,
        "exp":   now + max(5, int(ttl_seconds)),
        "scope": "internal",
    }
    return jwt.encode(
        payload,
        priv_pem.decode("ascii"),
        algorithm=_MESH_ES256_ALGORITHM,
        headers={"kid": service_name},
    )


def mesh_headers(my_service: str) -> dict[str, str]:
    """Return the mesh-auth headers every internal HTTP caller should send.

    Phase 3 (2026-06-21): only X-Mesh-Token is sent. The legacy
    X-Internal-Secret companion header was dropped because every receiver
    rejects it (mesh keys configured ⇒ legacy disabled). Sending it was
    dead weight that gave a false impression of dual-auth defense.

    Raises only when the mint helper raises (no private key configured).
    The decision to fail loudly vs. fall back to a shared secret lives in
    mint_service_token; this wrapper just returns the resulting header.
    """
    return {"X-Mesh-Token": mint_service_token(my_service)}


def _verify_mesh_jwt(token: str) -> dict[str, Any] | None:
    """Validate a mesh JWT — ES256 only.

    Phase 3 (2026-06-21): the HS256 back-compat lane was removed. Mesh
    tokens MUST be ES256 + signed by a service whose public key is in the
    trusted_keys map. Any other algorithm — including HS256 minted with a
    leaked INTERNAL_SECRET — fails verification.

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

    if alg != _MESH_ES256_ALGORITHM:
        logger.warning("mesh_jwt_rejected_non_es256 alg=%r", alg)
        return None

    kid = header.get("kid", "")
    trusted = _mesh_trusted_public_keys()
    pub_pem = trusted.get(kid)
    if pub_pem is None:
        logger.warning("mesh_jwt_unknown_kid kid=%r", kid)
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
        logger.warning("mesh_jwt_es256_invalid kid=%r error=%s", kid, exc)
        return None


def verify_internal_secret(
    secret: str | None = Depends(internal_secret_header),
    mesh_token: str | None = Header(default=None, alias="X-Mesh-Token"),
) -> str:
    """Zero-trust service mesh auth — ES256 mesh JWT required.

    Phase 3 (2026-06-21): the legacy X-Internal-Secret lane was removed.
    Every internal HTTP call must carry a valid X-Mesh-Token (ES256,
    signed by a service whose public key is in ACP_MESH_TRUSTED_KEYS).
    Returns 403 for any other shape.

    The ``secret`` parameter is kept so existing FastAPI deps that read
    ``X-Internal-Secret`` for legacy clients still parse the request,
    but the value is discarded — presenting it without a mesh token is
    no longer a path to authentication.
    """
    del secret  # legacy header retained in signature for backward sig compat
    if mesh_token:
        claims = _verify_mesh_jwt(mesh_token)
        if claims is not None and not claims.get("_expired"):
            MESH_JWT_AUTH_TOTAL.labels(method="jwt").inc()
            return f"mesh:{claims.get('iss', 'unknown')}"

    MESH_JWT_AUTH_TOTAL.labels(method="failed").inc()
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Access forbidden: missing or invalid mesh JWT",
    )
