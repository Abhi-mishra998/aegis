"""ATF v3.2 §4.2 — Microsoft Entra Agent ID token acceptance.

Standard OIDC/OAuth JWT verification with Entra-specific issuer + JWKS
URL patterns. Ships as a thin, self-contained verifier so a customer
using Entra Agent ID + Conditional Access can wire ATF in without
building an adapter.

Verifier delegates crypto to `python-jose` (already vendored via the
audit signer). Fetches JWKS via a caller-supplied loader so unit tests
stay pure (no HTTP in `if __name__`).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from jose import JWTError, jwk, jwt


class EntraVerifyError(Exception):
    """Uniform failure — no auth-method oracle in the response body."""


@dataclass(frozen=True)
class EntraIdentity:
    subject: str                  # `sub` — the Entra Agent ID object id
    tenant_id: str                # `tid` — Entra tenant guid
    app_id: str                   # `appid` — the agent's app registration id
    audience: str
    issuer: str
    exp: int
    roles: list[str]


def _entra_issuer(tenant_id: str) -> str:
    return f"https://login.microsoftonline.com/{tenant_id}/v2.0"


def verify(
    token: str,
    *,
    expected_tenant_id: str,
    expected_audience: str,
    jwks_loader: Callable[[str], dict[str, Any]],
) -> EntraIdentity:
    """Verify an Entra-issued JWT.

    `jwks_loader(issuer_url)` → JWKS dict. The gateway wires this to a
    cached HTTPS fetch; tests wire it to a static dict.
    """
    try:
        header = jwt.get_unverified_header(token)
    except JWTError as exc:
        raise EntraVerifyError(f"malformed token: {exc}") from exc

    issuer = _entra_issuer(expected_tenant_id)
    jwks = jwks_loader(issuer)
    keys = jwks.get("keys") or []
    if not keys:
        raise EntraVerifyError("empty JWKS from Entra")

    kid = header.get("kid")
    matching = [k for k in keys if not kid or k.get("kid") == kid]
    if not matching:
        raise EntraVerifyError("no matching kid in JWKS")

    claims: dict[str, Any] | None = None
    last_err: Exception | None = None
    for candidate in matching:
        try:
            key = jwk.construct(candidate)
            claims = jwt.decode(
                token,
                key.to_pem().decode() if hasattr(key, "to_pem") else candidate,
                algorithms=[header.get("alg", "RS256")],
                issuer=issuer,
                audience=expected_audience,
                options={"verify_aud": True, "verify_exp": True, "verify_iss": True},
            )
            break
        except JWTError as exc:
            last_err = exc
    if claims is None:
        raise EntraVerifyError(f"signature verification failed: {last_err}")

    tid = str(claims.get("tid", ""))
    if tid != expected_tenant_id:
        raise EntraVerifyError(f"tid mismatch: {tid!r} != {expected_tenant_id!r}")

    roles = claims.get("roles") or []
    return EntraIdentity(
        subject=str(claims.get("sub", "")),
        tenant_id=tid,
        app_id=str(claims.get("appid", "") or claims.get("azp", "")),
        audience=str(claims.get("aud", "")),
        issuer=str(claims.get("iss", "")),
        exp=int(claims.get("exp", 0)),
        roles=list(roles) if isinstance(roles, list) else [],
    )


if __name__ == "__main__":
    # Pure-parsing self-check — crypto path exercised by `jose` upstream.
    assert _entra_issuer("aaaa-bbbb") == "https://login.microsoftonline.com/aaaa-bbbb/v2.0"

    # Empty JWKS raises the uniform error type
    def empty_jwks(_iss: str) -> dict[str, Any]:
        return {"keys": []}

    try:
        # Any well-formed but unverifiable token will trigger empty-JWKS
        # before the crypto path even runs; construct a header-only stub.
        import base64
        import json as _json
        header = base64.urlsafe_b64encode(
            _json.dumps({"alg": "RS256", "kid": "k1"}).encode()
        ).rstrip(b"=").decode()
        payload = base64.urlsafe_b64encode(
            _json.dumps({"tid": "t", "aud": "a"}).encode()
        ).rstrip(b"=").decode()
        fake = f"{header}.{payload}.sig"
        verify(fake, expected_tenant_id="t", expected_audience="a", jwks_loader=empty_jwks)
        raise AssertionError("expected EntraVerifyError")
    except EntraVerifyError:
        pass

    print("entra_auth OK")
