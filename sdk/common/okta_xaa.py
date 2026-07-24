"""ATF v3.2 §4.2 — Okta for AI Agents + Cross-App Access (XAA)
via RFC 8693 OAuth 2.0 Token Exchange.

Two responsibilities:

  1. Accept an Okta-issued agent token.
  2. Perform an RFC 8693 token exchange to swap an XAA scope for a
     tenant-audience token the Gate can consume downstream.

Uses `python-jose` for signature verification (same pattern as the
Clerk auth path already in `sdk/common/clerk_auth.py`).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from jose import JWTError, jwk, jwt


class OktaVerifyError(Exception):
    """Uniform verification failure."""


class OktaExchangeError(Exception):
    """RFC 8693 token exchange failure."""


@dataclass(frozen=True)
class OktaAgentIdentity:
    subject: str                  # Okta agent user id
    issuer: str
    audience: str
    scopes: list[str]             # XAA scopes granted
    exp: int


def verify(
    token: str,
    *,
    expected_issuer: str,
    expected_audience: str,
    jwks_loader: Callable[[str], dict[str, Any]],
) -> OktaAgentIdentity:
    try:
        header = jwt.get_unverified_header(token)
    except JWTError as exc:
        raise OktaVerifyError(f"malformed token: {exc}") from exc

    jwks = jwks_loader(expected_issuer)
    keys = jwks.get("keys") or []
    if not keys:
        raise OktaVerifyError("empty JWKS")
    kid = header.get("kid")
    matching = [k for k in keys if not kid or k.get("kid") == kid]
    if not matching:
        raise OktaVerifyError("no matching kid")

    claims: dict[str, Any] | None = None
    last_err: Exception | None = None
    for candidate in matching:
        try:
            key = jwk.construct(candidate)
            claims = jwt.decode(
                token,
                key.to_pem().decode() if hasattr(key, "to_pem") else candidate,
                algorithms=[header.get("alg", "RS256")],
                issuer=expected_issuer,
                audience=expected_audience,
                options={"verify_aud": True, "verify_iss": True, "verify_exp": True},
            )
            break
        except JWTError as exc:
            last_err = exc
    if claims is None:
        raise OktaVerifyError(f"signature verification failed: {last_err}")

    scope_raw = claims.get("scp") or claims.get("scope") or []
    scopes = scope_raw.split() if isinstance(scope_raw, str) else list(scope_raw)

    return OktaAgentIdentity(
        subject=str(claims.get("sub", "")),
        issuer=str(claims.get("iss", "")),
        audience=str(claims.get("aud", "")),
        scopes=scopes,
        exp=int(claims.get("exp", 0)),
    )


# ─────────────────────────────────────────────────────────────
# RFC 8693 token exchange
# ─────────────────────────────────────────────────────────────

_GRANT_TYPE_TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
_TOKEN_TYPE_JWT = "urn:ietf:params:oauth:token-type:jwt"


def build_exchange_request(
    subject_token: str,
    *,
    audience: str,
    scope: str,
    client_id: str,
) -> dict[str, str]:
    """Compose the form-encoded body for a token exchange request per RFC 8693.

    The caller HTTP-posts this to Okta's `/oauth2/v1/token`; the response
    body carries an XAA-scoped access token bound to the requested aud.
    """
    return {
        "grant_type": _GRANT_TYPE_TOKEN_EXCHANGE,
        "subject_token": subject_token,
        "subject_token_type": _TOKEN_TYPE_JWT,
        "audience": audience,
        "scope": scope,
        "client_id": client_id,
    }


def parse_exchange_response(payload: dict[str, Any]) -> tuple[str, list[str], int]:
    """RFC 8693 §2.2.1 response shape → (access_token, scopes, expires_in)."""
    if payload.get("issued_token_type") != _TOKEN_TYPE_JWT:
        raise OktaExchangeError(
            f"unsupported issued_token_type: {payload.get('issued_token_type')!r}"
        )
    access_token = payload.get("access_token")
    if not access_token:
        raise OktaExchangeError("no access_token in response")
    scopes = (payload.get("scope") or "").split()
    return str(access_token), scopes, int(payload.get("expires_in") or 0)


if __name__ == "__main__":
    req = build_exchange_request(
        "eyJhbGciOi...",
        audience="https://gate.aegis/policy",
        scope="agent.read agent.execute",
        client_id="0oa8...",
    )
    assert req["grant_type"] == _GRANT_TYPE_TOKEN_EXCHANGE
    assert req["subject_token_type"] == _TOKEN_TYPE_JWT
    assert req["audience"] == "https://gate.aegis/policy"

    tok, scopes, exp = parse_exchange_response({
        "access_token":       "new.jwt.token",
        "issued_token_type":  _TOKEN_TYPE_JWT,
        "scope":              "agent.read agent.execute",
        "expires_in":         3600,
    })
    assert tok == "new.jwt.token"
    assert scopes == ["agent.read", "agent.execute"]
    assert exp == 3600

    try:
        parse_exchange_response({"issued_token_type": "opaque"})
        raise AssertionError("expected OktaExchangeError for wrong token type")
    except OktaExchangeError:
        pass

    try:
        parse_exchange_response({"issued_token_type": _TOKEN_TYPE_JWT})
        raise AssertionError("expected OktaExchangeError for missing token")
    except OktaExchangeError:
        pass

    print("okta_xaa OK")
