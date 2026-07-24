"""ATF v3.2 §14.5 — DESTROY destruction certificate.

Spec text (§14.5, DESTROY line):
    "ledger destruction only after retention floor passes (>= 6 months,
    §7.3); destruction produces a signed certificate referencing the
    final anchor — the customer can forever prove what existed and when
    it was destroyed"

The certificate is the artifact the customer keeps after the ledger
itself is gone. It commits to:
  1. WHO — the tenant identifier
  2. WHEN it existed — first/final ledger entry timestamps
  3. WHAT was anchored — the final Merkle root hash + its anchor ref
  4. THAT retention was honored — days retained vs the required floor
  5. WHO signed — root-key fingerprint (verifiable via historical keys
     table on any Aegis instance the customer or auditor spins up)

Pure module: no I/O, no DB, no signer instantiation. Callers pass in
the final anchor row + a callable that signs canonical bytes. This is
what keeps the certificate independently rebuildable + testable.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

# Spec §7.3: "Retained >= 6 months minimum, configurable upward".
# 180 days is the ATF-canonical floor. Customers may set higher via config.
DEFAULT_RETENTION_FLOOR_DAYS = 180

CERT_VERSION = "1.0"


class RetentionFloorNotMet(ValueError):
    """Raised when actual retention < required floor — DESTROY is refused.

    This is the primary invariant of the certificate: it must NEVER attest
    to a shorter retention than the floor the customer / regulator asked
    for. Wrong-shape certs are worse than none.
    """


@dataclass(frozen=True)
class FinalAnchor:
    """The last anchored transparency-root row before destruction.

    Fields mirror `services.audit.models.TransparencyRoot` — this
    dataclass is the data-transfer shape that keeps the pure module
    from importing SQLAlchemy models directly.
    """
    root_hash: str
    root_date_iso: str
    leaf_count: int
    signing_key_fingerprint: str | None
    prev_root_hash: str | None


def canonical_json_bytes(obj: dict[str, Any]) -> bytes:
    """Same canonicalization as services.audit.signer.canonical_json —
    duplicated here so this module has no upward import dependency."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")


def build_destruction_certificate(
    *,
    tenant_id: str,
    first_entry_ts: datetime,
    final_entry_ts: datetime,
    final_anchor: FinalAnchor,
    retention_floor_days: int,
    signer_fingerprint: str,
    sign: Callable[[bytes], bytes],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build + sign a destruction certificate.

    `sign` is a callable that takes canonical bytes and returns an
    Ed25519 signature — the caller supplies `ReceiptSigner._priv.sign`
    or an equivalent. This keeps the pure module free of key material.

    Raises RetentionFloorNotMet if the actual retention window is less
    than the floor. That refusal is the certificate's whole point:
    the artifact NEVER understates retention.
    """
    if retention_floor_days < 1:
        raise ValueError(f"retention_floor_days must be positive; got {retention_floor_days}")
    if final_entry_ts < first_entry_ts:
        raise ValueError("final_entry_ts must be >= first_entry_ts")

    actual_retention_days = (final_entry_ts - first_entry_ts).days
    if actual_retention_days < retention_floor_days:
        raise RetentionFloorNotMet(
            f"actual retention {actual_retention_days}d < floor "
            f"{retention_floor_days}d — DESTROY refused",
        )

    issued_at = (now or datetime.now(tz=UTC)).isoformat().replace("+00:00", "Z")

    body = {
        "cert_version":              CERT_VERSION,
        "tenant_id":                 str(tenant_id),
        "issued_at":                 issued_at,
        "first_entry_ts":            first_entry_ts.isoformat().replace("+00:00", "Z"),
        "final_entry_ts":            final_entry_ts.isoformat().replace("+00:00", "Z"),
        "retention_floor_days":      int(retention_floor_days),
        "actual_retention_days":     int(actual_retention_days),
        "final_anchor": {
            "root_hash":               final_anchor.root_hash,
            "root_date":               final_anchor.root_date_iso,
            "leaf_count":              int(final_anchor.leaf_count),
            "signing_key_fingerprint": final_anchor.signing_key_fingerprint,
            "prev_root_hash":          final_anchor.prev_root_hash,
        },
    }

    canonical = canonical_json_bytes(body)
    sig_bytes = sign(canonical)

    import base64
    return {
        **body,
        "signature":               base64.urlsafe_b64encode(sig_bytes).rstrip(b"=").decode("ascii"),
        "signature_algorithm":     "ed25519",
        "signing_key_fingerprint": signer_fingerprint,
        # Include a digest of the canonical body so an offline verifier
        # can double-check that the signature was computed over the
        # published body and nothing else.
        "canonical_body_sha256":   hashlib.sha256(canonical).hexdigest(),
    }


def verify_destruction_certificate(
    cert: dict[str, Any],
    public_key_pem: str,
) -> bool:
    """Offline verifier — takes a certificate + the signing public key
    (looked up by fingerprint in transparency_historical_keys) and
    returns True iff signature + canonical-body digest agree.

    Raises ValueError on missing fields — same distinction as
    verify_receipt: bad payload vs valid-but-not-verified.
    """
    import base64

    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    required = (
        "cert_version", "tenant_id", "issued_at",
        "first_entry_ts", "final_entry_ts",
        "retention_floor_days", "actual_retention_days",
        "final_anchor",
        "signature", "signature_algorithm", "signing_key_fingerprint",
        "canonical_body_sha256",
    )
    for k in required:
        if k not in cert:
            raise ValueError(f"missing field: {k}")
    if cert["signature_algorithm"] != "ed25519":
        raise ValueError(f"unsupported algorithm: {cert['signature_algorithm']}")

    body = {k: cert[k] for k in (
        "cert_version", "tenant_id", "issued_at",
        "first_entry_ts", "final_entry_ts",
        "retention_floor_days", "actual_retention_days",
        "final_anchor",
    )}
    canonical = canonical_json_bytes(body)
    if hashlib.sha256(canonical).hexdigest() != cert["canonical_body_sha256"]:
        # Canonical body hash disagrees — the cert was tampered with
        # between signing and verification.
        return False

    sig_pad = "=" * (-len(cert["signature"]) % 4)
    sig_bytes = base64.urlsafe_b64decode(cert["signature"] + sig_pad)
    pub = serialization.load_pem_public_key(public_key_pem.encode("ascii"))
    if not isinstance(pub, ed25519.Ed25519PublicKey):
        raise ValueError("public key is not ed25519")
    try:
        pub.verify(sig_bytes, canonical)
        return True
    except InvalidSignature:
        return False


if __name__ == "__main__":
    # Self-check — no external deps needed at runtime.
    from cryptography.hazmat.primitives import serialization as _ser
    from cryptography.hazmat.primitives.asymmetric import ed25519 as _ed

    priv = _ed.Ed25519PrivateKey.generate()
    pub_pem = priv.public_key().public_bytes(
        encoding=_ser.Encoding.PEM,
        format=_ser.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    fp = hashlib.sha256(pub_pem.encode("ascii")).hexdigest()

    anchor = FinalAnchor(
        root_hash="sha256:aa" * 16,
        root_date_iso="2026-07-24",
        leaf_count=12345,
        signing_key_fingerprint=fp,
        prev_root_hash="sha256:bb" * 16,
    )
    cert = build_destruction_certificate(
        tenant_id="acme-inc",
        first_entry_ts=datetime(2026, 1, 1, tzinfo=UTC),
        final_entry_ts=datetime(2026, 7, 24, tzinfo=UTC),
        final_anchor=anchor,
        retention_floor_days=180,
        signer_fingerprint=fp,
        sign=priv.sign,
    )
    assert verify_destruction_certificate(cert, pub_pem)

    # Tamper-detection sanity: mutate a byte, verification must fail.
    tampered = dict(cert)
    tampered["actual_retention_days"] = 999999
    assert not verify_destruction_certificate(tampered, pub_pem)

    # Retention-floor refusal: 100d actual < 180d floor.
    try:
        build_destruction_certificate(
            tenant_id="acme-inc",
            first_entry_ts=datetime(2026, 4, 1, tzinfo=UTC),
            final_entry_ts=datetime(2026, 7, 10, tzinfo=UTC),  # ~100 days
            final_anchor=anchor,
            retention_floor_days=180,
            signer_fingerprint=fp,
            sign=priv.sign,
        )
        raise AssertionError("expected RetentionFloorNotMet")
    except RetentionFloorNotMet:
        pass

    print("destruction_certificate OK")
