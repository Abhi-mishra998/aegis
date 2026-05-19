"""Crypto Sprint — end-to-end cryptographic trust tests.

Covers the full trust chain so a regression in any layer fails CI loudly:

    receipt sign → receipt verify
    receipt sign → tamper body → verify fails
    Merkle build → inclusion proof → verify
    Merkle build → tamper leaf → verify fails
    root sign  → root verify
    root chain → break prev_hash → consistency fails
    canonical JSON is deterministic across orderings

The tests do NOT require Postgres / Redis — they exercise the pure
crypto + chaining logic directly. Integration with the live transparency
scheduler is covered separately in `test_transparency_scheduler.py`.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from services.audit.merkle import (
    EMPTY_ROOT,
    build_root,
    inclusion_proof,
    leaf_hash,
    verify_inclusion,
)
from services.audit.signer import (
    ReceiptSigner,
    canonical_json,
    reset_signer_for_tests,
    verify_receipt,
)
from sdk.acp_client.transparency import verify_root_chain, verify_root_signature


# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def signer():
    """Fresh in-memory Ed25519 signer for each test."""
    reset_signer_for_tests()
    from cryptography.hazmat.primitives.asymmetric import ed25519
    return ReceiptSigner(ed25519.Ed25519PrivateKey.generate(), source="test:in-memory")


def _fake_audit_row():
    """Plain dict that mimics the AuditLog attributes signer.build_receipt() reads."""
    return {
        "id":          uuid.UUID("00000000-0000-4000-8000-000000000001"),
        "tenant_id":   uuid.UUID("00000000-0000-4000-8000-aaaaaaaaaaaa"),
        "agent_id":    uuid.UUID("00000000-0000-4000-8000-bbbbbbbbbbbb"),
        "tool":        "db.query",
        "action":      "execute_tool",
        "decision":    "allow",
        "reason":      None,
        "request_id":  "req-abc-123",
        "timestamp":   datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC),
        "event_hash":  "a" * 64,
        "prev_hash":   "0" * 64,
        "chain_shard": 3,
    }


# ── canonical JSON is order-stable ─────────────────────────────────────────


def test_canonical_json_is_order_independent():
    a = canonical_json({"b": 2, "a": 1, "c": {"y": 4, "x": 3}})
    b = canonical_json({"a": 1, "c": {"x": 3, "y": 4}, "b": 2})
    assert a == b


def test_canonical_json_separators_are_compact():
    # No whitespace between key/value or array items — critical for
    # cross-language verifiers to agree byte-for-byte.
    s = canonical_json({"k": [1, 2, 3]}).decode()
    assert " " not in s
    assert s == '{"k":[1,2,3]}'


# ── Receipt: sign → verify round-trip + tamper detection ───────────────────


def test_receipt_round_trip_verifies(signer):
    payload = signer.sign(_fake_audit_row())
    assert verify_receipt(payload, signer.public_key_pem()) is True


def test_receipt_tamper_body_fails_verify(signer):
    payload = signer.sign(_fake_audit_row())
    # Mutate the body — any byte change MUST break the signature.
    payload["receipt"]["decision"] = "deny"
    assert verify_receipt(payload, signer.public_key_pem()) is False


def test_receipt_tamper_signature_fails_verify(signer):
    payload = signer.sign(_fake_audit_row())
    sig = list(payload["signature"])
    sig[0] = "A" if sig[0] != "A" else "B"
    payload["signature"] = "".join(sig)
    assert verify_receipt(payload, signer.public_key_pem()) is False


def test_receipt_wrong_pubkey_fails_verify(signer):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    other = ed25519.Ed25519PrivateKey.generate()
    other_pem = other.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    payload = signer.sign(_fake_audit_row())
    # Wrong fingerprint → False without raising.
    assert verify_receipt(payload, other_pem) is False


def test_receipt_missing_field_raises(signer):
    payload = signer.sign(_fake_audit_row())
    del payload["signature"]
    with pytest.raises(ValueError):
        verify_receipt(payload, signer.public_key_pem())


# ── Merkle: inclusion proof + tamper detection ─────────────────────────────


def test_merkle_inclusion_round_trip():
    leaves = [leaf_hash(f"leaf-{i}".encode()) for i in range(5)]
    root = build_root(leaves)
    proof = inclusion_proof(leaves, 2)
    assert verify_inclusion(leaves[2], proof, root) is True


def test_merkle_tampered_leaf_fails_verify():
    leaves = [leaf_hash(f"leaf-{i}".encode()) for i in range(5)]
    root = build_root(leaves)
    proof = inclusion_proof(leaves, 2)
    # Pretend the leaf was edited at rest — proof against same root must fail.
    tampered_leaf = leaf_hash(b"different-content")
    assert verify_inclusion(tampered_leaf, proof, root) is False


def test_merkle_tampered_root_fails_verify():
    leaves = [leaf_hash(f"leaf-{i}".encode()) for i in range(5)]
    root = build_root(leaves)
    proof = inclusion_proof(leaves, 2)
    flipped_root = "0" * len(root)
    assert verify_inclusion(leaves[2], proof, flipped_root) is False


def test_merkle_empty_root_is_sha256_of_empty():
    import hashlib
    assert build_root([]) == EMPTY_ROOT == hashlib.sha256(b"").hexdigest()


def test_merkle_singleton_root_equals_leaf():
    only = leaf_hash(b"sole")
    assert build_root([only]) == only


# ── Transparency root chain (Sprint 1: Merkle-of-Merkles) ──────────────────


def _signed_root(signer, *, tenant, day, root_hash, leaf_count, prev_hash=None):
    """Replicates what transparency._sign_root produces, without importing
    the full transparency module (which pulls in FastAPI + DB). Keeps these
    tests fast and dependency-free."""
    import base64
    payload = {
        "version":        2,
        "kind":           "transparency_root",
        "tenant_id":      str(tenant),
        "root_date":      day.isoformat(),
        "root_hash":      root_hash,
        "prev_root_hash": prev_hash,
        "leaf_count":     leaf_count,
    }
    sig = signer._priv.sign(canonical_json(payload))  # noqa: SLF001
    return {
        "receipt":                payload,
        "signature":              base64.urlsafe_b64encode(sig).rstrip(b"=").decode("ascii"),
        "algorithm":              "ed25519",
        "public_key_fingerprint": signer._fingerprint,  # noqa: SLF001
    }


def test_root_signature_round_trip(signer):
    tenant = uuid.uuid4()
    today = datetime.now(UTC).date()
    payload = _signed_root(signer, tenant=tenant, day=today, root_hash="ab" * 32, leaf_count=42)
    assert verify_root_signature(payload, signer.public_key_pem()) is True


def test_root_chain_three_day_consistent_passes(signer):
    tenant = uuid.uuid4()
    d0 = datetime.now(UTC).date()
    chain = []
    prev = None
    for i, h in enumerate(["aa" * 32, "bb" * 32, "cc" * 32]):
        payload = _signed_root(
            signer, tenant=tenant, day=d0 + timedelta(days=i),
            root_hash=h, leaf_count=10 + i, prev_hash=prev,
        )
        chain.append({"root_hash": h, "prev_root_hash": prev, "signed": payload})
        prev = h
    assert verify_root_chain(chain) is True


def test_root_chain_broken_pointer_fails(signer):
    tenant = uuid.uuid4()
    d0 = datetime.now(UTC).date()
    chain = []
    prev = None
    for i, h in enumerate(["aa" * 32, "bb" * 32, "cc" * 32]):
        chain.append({"root_hash": h, "prev_root_hash": prev})
        prev = h
    # Adversary edits the middle root's stored content but forgets to
    # re-sign + update the next day's prev pointer.
    chain[1]["prev_root_hash"] = "ff" * 32
    assert verify_root_chain(chain) is False


def test_root_payload_tamper_breaks_signature(signer):
    tenant = uuid.uuid4()
    today = datetime.now(UTC).date()
    payload = _signed_root(signer, tenant=tenant, day=today, root_hash="ab" * 32, leaf_count=42)
    # Adversary rewrites prev_root_hash to hide a history rewrite — the
    # signature breaks because it commits to the original prev pointer.
    payload["receipt"]["prev_root_hash"] = "ff" * 32
    assert verify_root_signature(payload, signer.public_key_pem()) is False


def test_empty_chain_is_consistent_by_definition():
    assert verify_root_chain([]) is True
    assert verify_root_chain([{"root_hash": "aa" * 32, "prev_root_hash": None}]) is True
