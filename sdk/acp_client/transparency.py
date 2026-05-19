"""Merkle inclusion proof verifier for the daily transparency log.

Pure-function module. The customer's flow:

    1. Fetch /v1/transparency/roots/{date}  → daily root + signed commitment.
    2. Verify the signed commitment with verify_receipt() (re-using the
       receipts module — the root is signed with the same ed25519 key).
    3. Fetch /v1/transparency/inclusion/{execution_id} → inclusion proof.
    4. Call verify_inclusion(leaf_hash, proof, root) — boolean.

The leaf hash is sha256(canonical_json(signed_receipt_payload)). Use
`leaf_hash_for_receipt(payload)` to compute it the same way the server does.
"""
from __future__ import annotations

import hashlib
from typing import Any

from .receipts import canonical_json, verify_receipt


def verify_root_signature(signed_root_payload: dict[str, Any], pem: str) -> bool:
    """Verify a signed transparency-root payload against the root-signing
    public key PEM. Thin wrapper over `verify_receipt` so callers have a
    semantically-clear function name in the transparency module.

    `signed_root_payload` is the `signed` sub-object returned by
    `/v1/transparency/roots/{date}` — i.e. `{receipt, signature, algorithm,
    public_key_fingerprint}` where `receipt.kind == "transparency_root"`.
    """
    return verify_receipt(signed_root_payload, pem)


def verify_root_chain(chain: list[dict[str, Any]]) -> bool:
    """Verify that `chain` is an append-only consistency proof.

    Each element is `{root_date, root_hash, prev_root_hash, ...}`. Returns
    True iff every consecutive pair (i, i+1) satisfies
    `chain[i+1].prev_root_hash == chain[i].root_hash`. An empty or single-
    element chain is consistent by definition.

    Designed to consume what `/v1/transparency/consistency` returns —
    paranoid callers run this client-side even when the server reports
    `consistent: true`, so trust still flows from the math.
    """
    prev_hash: str | None = None
    for i, link in enumerate(chain):
        if i > 0:
            if link.get("prev_root_hash") != prev_hash:
                return False
        prev_hash = link.get("root_hash")
    return True


def leaf_hash_for_receipt(signed_receipt_payload: dict[str, Any]) -> str:
    """Compute the Merkle leaf hash for one signed receipt payload.

    The argument is exactly what `/v1/receipts/{id}` returns: the full
    `{receipt, signature, algorithm, public_key_fingerprint}` object.
    """
    return hashlib.sha256(canonical_json(signed_receipt_payload)).hexdigest()


def verify_inclusion(leaf_hex: str, proof: dict[str, Any], expected_root: str) -> bool:
    """Verify that `leaf_hex` is included in the tree whose root is
    `expected_root`. Returns False on any mismatch. Raises ValueError only
    when the proof object itself is malformed.
    """
    if not isinstance(proof, dict):
        raise ValueError("proof must be a mapping")
    for k in ("leaf", "siblings", "root"):
        if k not in proof:
            raise ValueError(f"missing field: {k}")
    if proof["leaf"] != leaf_hex:
        return False
    if proof["root"] != expected_root:
        return False

    cur = bytes.fromhex(leaf_hex)
    for sib in proof["siblings"]:
        side = sib.get("side")
        h_hex = sib.get("hash")
        if side not in ("L", "R") or not isinstance(h_hex, str):
            raise ValueError("malformed sibling entry")
        sh = bytes.fromhex(h_hex)
        cur = hashlib.sha256(sh + cur).digest() if side == "L" else hashlib.sha256(cur + sh).digest()

    return cur.hex() == expected_root
