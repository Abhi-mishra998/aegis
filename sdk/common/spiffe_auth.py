"""ATF v3.2 §4.2 — SPIFFE SVID acceptance (K8s-native identity input).

Verifies a workload-native SPIFFE JWT-SVID from a customer's own SPIRE
deployment. The trust bundle (JWKS-shaped) is refreshed periodically
from the SPIRE server or delivered via an operator-mounted secret.

Pure verifier: caller passes token + trust bundle + expected trust
domain; module returns the parsed subject or raises. No HTTP.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jose import JWTError, jwk, jwt


class SpiffeVerifyError(Exception):
    """Raised on ANY validation failure — never distinguish so the caller
    doesn't accidentally build an oracle. Debug detail on the exception
    message stays inside logs, not response bodies."""


@dataclass(frozen=True)
class SpiffeIdentity:
    spiffe_id: str          # spiffe://tenant.example/workload/agent-1
    trust_domain: str       # tenant.example
    workload_path: str      # /workload/agent-1
    audience: str | None
    exp: int


def parse_spiffe_id(sid: str) -> tuple[str, str]:
    """Split spiffe://<trust_domain><path> — raises on malformed input."""
    if not sid.startswith("spiffe://"):
        raise SpiffeVerifyError("not a SPIFFE ID")
    rest = sid[len("spiffe://"):]
    if "/" not in rest:
        raise SpiffeVerifyError("SPIFFE ID has no path")
    td, path = rest.split("/", 1)
    if not td:
        raise SpiffeVerifyError("SPIFFE trust domain is empty")
    return td, "/" + path


def verify(
    token: str,
    trust_bundle: dict[str, Any],
    *,
    expected_trust_domain: str,
    expected_audience: str | None = None,
) -> SpiffeIdentity:
    """Verify an ES256/RS256 SVID against a JWKS-shaped trust bundle.

    `trust_bundle` = {"keys": [<JWK>, ...]} for the given trust domain,
    per SPIFFE Trust Bundle spec.
    """
    keys = trust_bundle.get("keys") or []
    if not keys:
        raise SpiffeVerifyError("trust bundle empty")

    try:
        header = jwt.get_unverified_header(token)
    except JWTError as exc:
        raise SpiffeVerifyError(f"malformed token: {exc}") from exc

    kid = header.get("kid")
    matching = [k for k in keys if not kid or k.get("kid") == kid]
    if not matching:
        raise SpiffeVerifyError("no matching kid in trust bundle")

    last_err: Exception | None = None
    for candidate in matching:
        try:
            key = jwk.construct(candidate)
            claims = jwt.decode(
                token,
                key.to_pem().decode() if hasattr(key, "to_pem") else candidate,
                algorithms=[header.get("alg", "RS256")],
                options={
                    "verify_aud": expected_audience is not None,
                    "verify_iss": False,     # SPIFFE uses `sub`, not `iss`
                    "verify_exp": True,
                },
                audience=expected_audience,
            )
            break
        except JWTError as exc:
            last_err = exc
            claims = None
    if claims is None:
        raise SpiffeVerifyError(f"no key verified the token: {last_err}")

    sub = str(claims.get("sub", ""))
    td, path = parse_spiffe_id(sub)
    if td != expected_trust_domain:
        raise SpiffeVerifyError(
            f"trust domain mismatch: {td!r} != {expected_trust_domain!r}"
        )
    aud = claims.get("aud")
    aud_str = None if aud is None else (aud[0] if isinstance(aud, list) and aud else str(aud))
    return SpiffeIdentity(
        spiffe_id=sub,
        trust_domain=td,
        workload_path=path,
        audience=aud_str,
        exp=int(claims.get("exp", 0)),
    )


if __name__ == "__main__":
    # Self-check limited to the pure parsing helper — full crypto path is
    # covered by the standard `jose` library and cannot be exercised here
    # without generating a real Ed25519/RS256 key pair.
    td, path = parse_spiffe_id("spiffe://acme.example/workload/agent-1")
    assert td == "acme.example"
    assert path == "/workload/agent-1"

    try:
        parse_spiffe_id("not-a-spiffe-id")
        raise AssertionError("expected SpiffeVerifyError")
    except SpiffeVerifyError:
        pass

    try:
        parse_spiffe_id("spiffe://acme.example")
        raise AssertionError("expected SpiffeVerifyError for missing path")
    except SpiffeVerifyError:
        pass

    print("spiffe_auth OK")
