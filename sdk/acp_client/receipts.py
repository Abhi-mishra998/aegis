"""Offline receipt verifier for ACP execution receipts.

Pure-function module — never makes a network call. Customers fetch the public
key once (via Client.public_key()) and use this to verify any number of
receipts they've collected from /v1/receipts/{id} or from their SIEM archive.
"""
from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

_ALGORITHM = "ed25519"


def canonical_json(obj: dict[str, Any]) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def fingerprint_public_key(pub_pem: bytes) -> str:
    return hashlib.sha256(pub_pem).hexdigest()[:32]


def _b64d(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def verify_receipt(payload: dict[str, Any], public_key_pem: str) -> bool:
    """Verify a signed-receipt payload from /v1/receipts/{id}.

    Returns True iff signature, fingerprint, and canonical encoding all
    agree. Raises ValueError for malformed inputs so the caller can tell
    "bad payload" apart from "valid payload, bad signature."
    """
    for k in ("receipt", "signature", "algorithm", "public_key_fingerprint"):
        if k not in payload:
            raise ValueError(f"missing field: {k}")
    if payload["algorithm"] != _ALGORITHM:
        raise ValueError(f"unsupported algorithm: {payload['algorithm']}")

    pub_pem = public_key_pem.encode("ascii")
    if fingerprint_public_key(pub_pem) != payload["public_key_fingerprint"]:
        return False

    try:
        pub = serialization.load_pem_public_key(pub_pem)
    except ValueError as e:
        raise ValueError(f"invalid public key PEM: {e}") from e
    if not isinstance(pub, ed25519.Ed25519PublicKey):
        raise ValueError("public key is not ed25519")

    try:
        pub.verify(_b64d(payload["signature"]), canonical_json(payload["receipt"]))
        return True
    except Exception:
        return False
