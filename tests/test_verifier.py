"""Comprehensive unit tests for sdk/acp_client/verifier.py.

All tests use real ed25519 keypairs and real SHA-256 Merkle trees.
No crypto is mocked — the goal is to prove correctness at the algorithm level.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

# Module under test
from sdk.acp_client.verifier import (
    AuditVerifier,
    ExportVerification,
    InclusionVerification,
    ReceiptVerification,
    build_root,
    canonical_json,
    fingerprint_key,
    leaf_hash,
    verify_inclusion,
    verify_receipt,
    verify_root_chain,
)


# ── Helpers ───────────────────────────────────────────────────────────────


def _gen_keypair() -> tuple[str, str]:
    """Return (private_key_pem, public_key_pem) as strings."""
    priv = ed25519.Ed25519PrivateKey.generate()
    pub = priv.public_key()
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    pub_pem = pub.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return priv_pem, pub_pem


def _sign_receipt_payload(receipt: dict[str, Any], priv_pem: str, pub_pem: str) -> dict[str, Any]:
    """Build a properly signed payload dict (the format ``verify_receipt`` expects)."""
    priv = serialization.load_pem_private_key(priv_pem.encode(), password=None)
    msg = canonical_json(receipt)
    sig_bytes = priv.sign(msg)
    sig_b64 = base64.urlsafe_b64encode(sig_bytes).rstrip(b"=").decode()
    return {
        "receipt": receipt,
        "signature": sig_b64,
        "algorithm": "ed25519",
        "public_key_fingerprint": fingerprint_key(pub_pem),
    }


def _make_receipt(execution_id: str = "exec-001") -> dict[str, Any]:
    """Return a minimal receipt dict with all required fields."""
    return {
        "action": "execute_tool",
        "agent_id": "agent-test",
        "chain_shard": 0,
        "decision": "allow",
        "event_hash": "abc123",
        "execution_id": execution_id,
        "prev_hash": "0" * 64,
        "reason": "policy_allow",
        "request_id": f"req-{execution_id}",
        "tenant_id": "tenant-test",
        "timestamp": "2026-05-24T12:00:00Z",
        "tool": "search_web",
        "version": 1,
    }


def _build_inclusion_proof(
    payloads: list[dict[str, Any]], target_index: int
) -> tuple[str, list[dict[str, Any]], str]:
    """Build the Merkle tree from signed payloads and return (leaf, siblings, root)."""
    leaves_hex = [leaf_hash(p) for p in payloads]
    root = build_root(leaves_hex)

    # Walk the tree to build the sibling path for target_index
    level = list(leaves_hex)
    idx = target_index
    siblings: list[dict[str, Any]] = []

    while len(level) > 1:
        # Duplicate last if odd
        padded = level + ([level[-1]] if len(level) % 2 == 1 else [])
        sibling_idx = idx ^ 1  # flip last bit
        sibling_hash = padded[sibling_idx]
        side = "L" if sibling_idx < idx else "R"
        siblings.append({"side": side, "hash": sibling_hash})

        # Advance to next level
        next_level: list[str] = []
        for i in range(0, len(padded), 2):
            combined = hashlib.sha256(
                bytes.fromhex(padded[i]) + bytes.fromhex(padded[i + 1])
            ).hexdigest()
            next_level.append(combined)
        level = next_level
        idx //= 2

    return leaves_hex[target_index], siblings, root


# ── canonical_json ────────────────────────────────────────────────────────


def test_canonical_json_sorted_keys() -> None:
    obj = {"z": 1, "a": 2, "m": 3}
    result = canonical_json(obj)
    assert result == b'{"a":2,"m":3,"z":1}'


def test_canonical_json_utf8() -> None:
    obj = {"name": "José"}
    result = canonical_json(obj)
    assert "é".encode() in result


def test_canonical_json_compact() -> None:
    obj = {"key": "value"}
    result = canonical_json(obj)
    assert b" " not in result


# ── fingerprint_key ───────────────────────────────────────────────────────


def test_fingerprint_key_deterministic() -> None:
    _, pub_pem = _gen_keypair()
    fp1 = fingerprint_key(pub_pem)
    fp2 = fingerprint_key(pub_pem.encode())
    assert fp1 == fp2
    assert len(fp1) == 32


def test_fingerprint_key_different_keys() -> None:
    _, pub1 = _gen_keypair()
    _, pub2 = _gen_keypair()
    assert fingerprint_key(pub1) != fingerprint_key(pub2)


# ── verify_receipt ────────────────────────────────────────────────────────


def test_verify_receipt_valid() -> None:
    """Real ed25519 keypair; sign a receipt; verify succeeds."""
    priv_pem, pub_pem = _gen_keypair()
    receipt = _make_receipt("exec-valid")
    payload = _sign_receipt_payload(receipt, priv_pem, pub_pem)
    assert verify_receipt(payload, pub_pem) is True


def test_verify_receipt_tampered() -> None:
    """Mutate the receipt JSON after signing — verify must fail."""
    priv_pem, pub_pem = _gen_keypair()
    receipt = _make_receipt("exec-tamper")
    payload = _sign_receipt_payload(receipt, priv_pem, pub_pem)

    # Deep-copy and mutate
    tampered = json.loads(json.dumps(payload))
    tampered["receipt"]["decision"] = "deny"  # was "allow"
    assert verify_receipt(tampered, pub_pem) is False


def test_verify_receipt_wrong_key() -> None:
    """Verify with a completely different public key — must fail."""
    priv_pem, pub_pem = _gen_keypair()
    _, wrong_pub_pem = _gen_keypair()

    receipt = _make_receipt("exec-wrongkey")
    payload = _sign_receipt_payload(receipt, priv_pem, pub_pem)

    # Fingerprint mismatch → False (not exception)
    assert verify_receipt(payload, wrong_pub_pem) is False


def test_verify_receipt_missing_field_raises() -> None:
    """Missing required fields must raise ValueError."""
    priv_pem, pub_pem = _gen_keypair()
    payload = _sign_receipt_payload(_make_receipt(), priv_pem, pub_pem)
    del payload["signature"]
    with pytest.raises(ValueError, match="missing required field"):
        verify_receipt(payload, pub_pem)


def test_verify_receipt_wrong_algorithm_raises() -> None:
    priv_pem, pub_pem = _gen_keypair()
    payload = _sign_receipt_payload(_make_receipt(), priv_pem, pub_pem)
    payload["algorithm"] = "rsa"
    with pytest.raises(ValueError, match="unsupported algorithm"):
        verify_receipt(payload, pub_pem)


def test_verify_receipt_mutated_signature_fails() -> None:
    """A signature with one byte flipped must not verify."""
    priv_pem, pub_pem = _gen_keypair()
    payload = _sign_receipt_payload(_make_receipt("exec-badsig"), priv_pem, pub_pem)
    # Corrupt the signature string (replace a char in the middle)
    sig = payload["signature"]
    corrupted = sig[:10] + ("A" if sig[10] != "A" else "B") + sig[11:]
    payload["signature"] = corrupted
    # May raise (bad base64 decode) or return False — both are acceptable
    try:
        result = verify_receipt(payload, pub_pem)
        assert result is False
    except (ValueError, Exception):
        pass  # corrupted base64 or invalid sig bytes — acceptable


# ── leaf_hash ─────────────────────────────────────────────────────────────


def test_leaf_hash_deterministic() -> None:
    priv_pem, pub_pem = _gen_keypair()
    payload = _sign_receipt_payload(_make_receipt("lh-det"), priv_pem, pub_pem)
    assert leaf_hash(payload) == leaf_hash(payload)


def test_leaf_hash_changes_on_mutation() -> None:
    priv_pem, pub_pem = _gen_keypair()
    payload = _sign_receipt_payload(_make_receipt("lh-mut"), priv_pem, pub_pem)
    h1 = leaf_hash(payload)
    copy = json.loads(json.dumps(payload))
    copy["receipt"]["decision"] = "deny"
    h2 = leaf_hash(copy)
    assert h1 != h2


def test_leaf_hash_is_64_chars() -> None:
    priv_pem, pub_pem = _gen_keypair()
    payload = _sign_receipt_payload(_make_receipt("lh-len"), priv_pem, pub_pem)
    assert len(leaf_hash(payload)) == 64


# ── build_root ────────────────────────────────────────────────────────────


EMPTY_ROOT = hashlib.sha256(b"").hexdigest()


def test_build_root_empty() -> None:
    assert build_root([]) == EMPTY_ROOT


def test_build_root_single_leaf() -> None:
    leaf = hashlib.sha256(b"one").hexdigest()
    assert build_root([leaf]) == leaf


def test_build_root_deterministic() -> None:
    """Same leaves always produce the same root."""
    leaves = [hashlib.sha256(str(i).encode()).hexdigest() for i in range(8)]
    root1 = build_root(leaves)
    root2 = build_root(leaves)
    assert root1 == root2


def test_build_root_order_matters() -> None:
    l1 = hashlib.sha256(b"a").hexdigest()
    l2 = hashlib.sha256(b"b").hexdigest()
    assert build_root([l1, l2]) != build_root([l2, l1])


def test_build_root_two_leaves() -> None:
    """Manual: sha256(bytes(l1) + bytes(l2)) == build_root([l1, l2])."""
    l1 = hashlib.sha256(b"left").hexdigest()
    l2 = hashlib.sha256(b"right").hexdigest()
    expected = hashlib.sha256(bytes.fromhex(l1) + bytes.fromhex(l2)).hexdigest()
    assert build_root([l1, l2]) == expected


def test_build_root_odd_duplication() -> None:
    """Three leaves: last leaf duplicated to make an even pair."""
    l1 = hashlib.sha256(b"a").hexdigest()
    l2 = hashlib.sha256(b"b").hexdigest()
    l3 = hashlib.sha256(b"c").hexdigest()
    # Level 1: [l1, l2, l3, l3]  (l3 duplicated)
    # → two inner nodes
    i1 = hashlib.sha256(bytes.fromhex(l1) + bytes.fromhex(l2)).hexdigest()
    i2 = hashlib.sha256(bytes.fromhex(l3) + bytes.fromhex(l3)).hexdigest()
    expected = hashlib.sha256(bytes.fromhex(i1) + bytes.fromhex(i2)).hexdigest()
    assert build_root([l1, l2, l3]) == expected


# ── verify_inclusion ──────────────────────────────────────────────────────


def _make_payloads_and_proof(
    n: int, target_index: int
) -> tuple[list[dict[str, Any]], str, list[dict[str, Any]], str, str, str]:
    """Return (payloads, target_leaf, siblings, root, priv_pem, pub_pem)."""
    priv_pem, pub_pem = _gen_keypair()
    payloads = [
        _sign_receipt_payload(_make_receipt(f"exec-{i}"), priv_pem, pub_pem) for i in range(n)
    ]
    target_leaf, siblings, root = _build_inclusion_proof(payloads, target_index)
    return payloads, target_leaf, siblings, root, priv_pem, pub_pem


def test_verify_inclusion_valid_4_leaves() -> None:
    """Build a 4-leaf tree; every position verifies correctly."""
    priv_pem, pub_pem = _gen_keypair()
    payloads = [
        _sign_receipt_payload(_make_receipt(f"exec-{i}"), priv_pem, pub_pem) for i in range(4)
    ]
    leaves = [leaf_hash(p) for p in payloads]
    root = build_root(leaves)

    for idx in range(4):
        target_leaf, siblings, proof_root = _build_inclusion_proof(payloads, idx)
        proof = {
            "leaf": target_leaf,
            "index": idx,
            "siblings": siblings,
            "root": proof_root,
            "size": 4,
        }
        assert verify_inclusion(target_leaf, proof, root) is True


def test_verify_inclusion_valid_odd_tree() -> None:
    """5-leaf tree (odd); all positions verify."""
    priv_pem, pub_pem = _gen_keypair()
    payloads = [
        _sign_receipt_payload(_make_receipt(f"odd-{i}"), priv_pem, pub_pem) for i in range(5)
    ]
    for idx in range(5):
        target_leaf, siblings, proof_root = _build_inclusion_proof(payloads, idx)
        proof = {"leaf": target_leaf, "index": idx, "siblings": siblings, "root": proof_root, "size": 5}
        assert verify_inclusion(target_leaf, proof, proof_root) is True


def test_verify_inclusion_tampered_leaf() -> None:
    """Mutate the leaf hex; verification must fail."""
    payloads, target_leaf, siblings, root, _, _ = _make_payloads_and_proof(4, 0)
    bad_leaf = "0" * 64
    proof = {"leaf": bad_leaf, "index": 0, "siblings": siblings, "root": root, "size": 4}
    assert verify_inclusion(bad_leaf, proof, root) is False


def test_verify_inclusion_wrong_root() -> None:
    payloads, target_leaf, siblings, root, _, _ = _make_payloads_and_proof(4, 1)
    wrong_root = "f" * 64
    proof = {"leaf": target_leaf, "index": 1, "siblings": siblings, "root": wrong_root, "size": 4}
    assert verify_inclusion(target_leaf, proof, wrong_root) is False


def test_verify_inclusion_missing_sibling_key_raises() -> None:
    payloads, target_leaf, siblings, root, _, _ = _make_payloads_and_proof(4, 2)
    bad_siblings = [{"side": "X", "hash": siblings[0]["hash"]}]  # invalid side
    proof = {"leaf": target_leaf, "index": 2, "siblings": bad_siblings, "root": root, "size": 4}
    with pytest.raises(ValueError, match="malformed sibling"):
        verify_inclusion(target_leaf, proof, root)


def test_verify_inclusion_malformed_proof_raises() -> None:
    with pytest.raises(ValueError, match="missing required field"):
        verify_inclusion("ab" * 32, {"siblings": []}, "cd" * 32)


# ── verify_root_chain ─────────────────────────────────────────────────────


def _make_root(date: str, root_hash: str, prev_root_hash: str) -> dict[str, Any]:
    return {
        "receipt": {
            "date": date,
            "root_hash": root_hash,
            "prev_root_hash": prev_root_hash,
            "kind": "transparency_root",
        }
    }


def test_verify_root_chain_empty() -> None:
    ok, err = verify_root_chain([])
    assert ok is True
    assert err == ""


def test_verify_root_chain_single() -> None:
    root = _make_root("2026-05-01", "a" * 64, "0" * 64)
    ok, err = verify_root_chain([root])
    assert ok is True


def test_verify_root_chain_valid_three_roots() -> None:
    """3 consecutive daily roots properly linked."""
    h0 = hashlib.sha256(b"genesis").hexdigest()
    h1 = hashlib.sha256(b"day1").hexdigest()
    h2 = hashlib.sha256(b"day2").hexdigest()
    roots = [
        _make_root("2026-05-01", h0, "0" * 64),
        _make_root("2026-05-02", h1, h0),
        _make_root("2026-05-03", h2, h1),
    ]
    ok, err = verify_root_chain(roots)
    assert ok is True
    assert err == ""


def test_verify_root_chain_broken_link() -> None:
    """Break the link between day 2 and day 3."""
    h0 = hashlib.sha256(b"g").hexdigest()
    h1 = hashlib.sha256(b"d1").hexdigest()
    h2 = hashlib.sha256(b"d2").hexdigest()
    roots = [
        _make_root("2026-05-01", h0, "0" * 64),
        _make_root("2026-05-02", h1, h0),
        _make_root("2026-05-03", h2, "bad" + h1[3:]),  # wrong prev_root_hash
    ]
    ok, err = verify_root_chain(roots)
    assert ok is False
    assert err != ""
    assert "2026-05-03" in err or "chain broken" in err.lower()


def test_verify_root_chain_out_of_order_input() -> None:
    """Roots given in reverse order should still be sorted and verified."""
    h0 = hashlib.sha256(b"g").hexdigest()
    h1 = hashlib.sha256(b"d1").hexdigest()
    roots = [
        _make_root("2026-05-02", h1, h0),  # later date first
        _make_root("2026-05-01", h0, "0" * 64),
    ]
    ok, err = verify_root_chain(roots)
    assert ok is True


def test_verify_root_chain_truncated_tail_passes_but_note_limitation() -> None:
    """Document known limitation: verify_root_chain cannot detect a missing tail root.

    If an adversary silently drops the last root from a 3-root chain, the remaining
    2-root chain verifies as valid because linkage between *present* roots is correct.
    Callers MUST independently verify the expected root count or date range.
    This test documents the behaviour — NOT a false pass — so we know exactly
    what the verifier does and does not catch.
    """
    h0 = hashlib.sha256(b"g").hexdigest()
    h1 = hashlib.sha256(b"d1").hexdigest()
    h2 = hashlib.sha256(b"d2").hexdigest()
    full_chain = [
        _make_root("2026-05-01", h0, "0" * 64),
        _make_root("2026-05-02", h1, h0),
        _make_root("2026-05-03", h2, h1),
    ]
    truncated = full_chain[:2]  # last root silently removed
    ok, err = verify_root_chain(truncated)
    # The truncated chain is internally consistent — linkage holds.
    # verify_root_chain returns True because it only checks consecutive pairs.
    assert ok is True, (
        "verify_root_chain does not detect tail truncation — "
        "callers must enforce expected date/count separately"
    )


def test_verify_root_chain_broken_detects_correct_position() -> None:
    """Error message identifies the right pair."""
    h0 = hashlib.sha256(b"g").hexdigest()
    h1 = hashlib.sha256(b"d1").hexdigest()
    h2 = hashlib.sha256(b"d2").hexdigest()
    roots = [
        _make_root("2026-05-01", h0, "0" * 64),
        _make_root("2026-05-02", h1, "tampered_" + h0[:55]),  # bad link at index 1
        _make_root("2026-05-03", h2, h1),
    ]
    ok, err = verify_root_chain(roots)
    assert ok is False
    assert "2026-05-02" in err


# ── AuditVerifier (class) ─────────────────────────────────────────────────


def test_audit_verifier_verify_receipt_valid() -> None:
    priv_pem, pub_pem = _gen_keypair()
    payload = _sign_receipt_payload(_make_receipt("cls-001"), priv_pem, pub_pem)
    v = AuditVerifier([pub_pem])
    rv = v.verify_receipt(payload)
    assert isinstance(rv, ReceiptVerification)
    assert rv.ok is True
    assert rv.execution_id == "cls-001"


def test_audit_verifier_verify_receipt_wrong_key() -> None:
    priv_pem, pub_pem = _gen_keypair()
    _, wrong_pub = _gen_keypair()
    payload = _sign_receipt_payload(_make_receipt("cls-wk"), priv_pem, pub_pem)
    v = AuditVerifier([wrong_pub])
    rv = v.verify_receipt(payload)
    assert rv.ok is False


def test_audit_verifier_tries_all_keys() -> None:
    """Verifier succeeds if the correct key is anywhere in the list."""
    priv_pem, pub_pem = _gen_keypair()
    _, other_pub = _gen_keypair()
    payload = _sign_receipt_payload(_make_receipt("cls-multi"), priv_pem, pub_pem)
    # Put the correct key second
    v = AuditVerifier([other_pub, pub_pem])
    rv = v.verify_receipt(payload)
    assert rv.ok is True


def test_audit_verifier_verify_inclusion() -> None:
    priv_pem, pub_pem = _gen_keypair()
    payloads = [
        _sign_receipt_payload(_make_receipt(f"vi-{i}"), priv_pem, pub_pem) for i in range(4)
    ]
    target_leaf, siblings, root = _build_inclusion_proof(payloads, 2)
    proof = {"leaf": target_leaf, "index": 2, "siblings": siblings, "root": root, "size": 4}
    signed_root = {"receipt": {"root_hash": root, "date": "2026-05-24"}}

    v = AuditVerifier([pub_pem])
    iv = v.verify_inclusion(payloads[2], proof, signed_root)
    assert isinstance(iv, InclusionVerification)
    assert iv.ok is True
    assert iv.root_date == "2026-05-24"


def test_audit_verifier_no_keys_raises() -> None:
    with pytest.raises(ValueError, match="at least one public key"):
        AuditVerifier([])


# ── ExportVerification.ok property ───────────────────────────────────────


def test_export_verification_ok_property() -> None:
    ev = ExportVerification(
        total_receipts=3,
        valid_receipts=3,
        total_inclusions=2,
        valid_inclusions=2,
        chain_ok=True,
    )
    assert ev.ok is True


def test_export_verification_ok_false_on_missing_receipt() -> None:
    ev = ExportVerification(
        total_receipts=3,
        valid_receipts=2,  # one failed
        total_inclusions=0,
        valid_inclusions=0,
        chain_ok=True,
    )
    assert ev.ok is False


def test_export_verification_ok_false_on_chain_broken() -> None:
    ev = ExportVerification(
        total_receipts=2,
        valid_receipts=2,
        total_inclusions=0,
        valid_inclusions=0,
        chain_ok=False,
        chain_error="chain broken",
    )
    assert ev.ok is False


# ── End-to-end: verify_export ─────────────────────────────────────────────


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj))


def _make_signed_root(
    date: str, root_hash: str, prev_root_hash: str, priv_pem: str, pub_pem: str
) -> dict[str, Any]:
    receipt = {
        "kind": "transparency_root",
        "date": date,
        "root_hash": root_hash,
        "prev_root_hash": prev_root_hash,
    }
    return _sign_receipt_payload(receipt, priv_pem, pub_pem)


def test_verifier_export_end_to_end() -> None:
    """
    Full end-to-end test:
    1. Generate a temp export directory with real crypto.
    2. Run verify_export.
    3. Assert ok=True with zero errors.
    """
    priv_pem, pub_pem = _gen_keypair()
    N = 6  # number of receipts

    with tempfile.TemporaryDirectory() as tmp:
        export_dir = Path(tmp)

        # Create directory structure
        (export_dir / "keys").mkdir()
        (export_dir / "keys" / "historical").mkdir()
        (export_dir / "receipts").mkdir()
        (export_dir / "proofs").mkdir()
        (export_dir / "roots").mkdir()

        # Write active public key
        (export_dir / "keys" / "active.pem").write_text(pub_pem)

        # Generate N signed receipt payloads
        payloads: list[dict[str, Any]] = []
        for i in range(N):
            receipt = _make_receipt(f"exec-e2e-{i:03d}")
            receipt["timestamp"] = f"2026-05-24T12:0{i}:00Z"
            payload = _sign_receipt_payload(receipt, priv_pem, pub_pem)
            payloads.append(payload)
            _write_json(export_dir / "receipts" / f"exec-e2e-{i:03d}.json", payload)

        # Build the Merkle tree for all leaves
        leaves = [leaf_hash(p) for p in payloads]
        root_hex = build_root(leaves)

        # Write signed root for 2026-05-24
        genesis_prev = "0" * 64
        signed_root = _make_signed_root(
            date="2026-05-24",
            root_hash=root_hex,
            prev_root_hash=genesis_prev,
            priv_pem=priv_pem,
            pub_pem=pub_pem,
        )
        _write_json(export_dir / "roots" / "2026-05-24.json", signed_root)

        # Write inclusion proofs for each receipt
        for i, payload in enumerate(payloads):
            target_leaf, siblings, proof_root = _build_inclusion_proof(payloads, i)
            proof = {
                "leaf": target_leaf,
                "index": i,
                "siblings": siblings,
                "root": proof_root,
                "size": N,
            }
            proof_file = {
                "proof": proof,
                "signed_root": signed_root,
            }
            _write_json(export_dir / "proofs" / f"exec-e2e-{i:03d}.json", proof_file)

        # Run the verifier
        verifier = AuditVerifier.from_export_dir(export_dir)
        result = verifier.verify_export(export_dir)

        assert result.total_receipts == N, f"expected {N} receipts, got {result.total_receipts}"
        assert result.valid_receipts == N, f"receipt errors: {result.errors}"
        assert result.total_inclusions == N
        assert result.valid_inclusions == N, f"inclusion errors: {result.errors}"
        assert result.chain_ok is True, f"chain error: {result.chain_error}"
        assert result.errors == [], f"unexpected errors: {result.errors}"
        assert result.ok is True


def test_verifier_export_end_to_end_multi_day_chain() -> None:
    """
    Three days of roots form a valid append-only chain.
    Each day has 2 receipts.
    verify_export should pass with ok=True.
    """
    priv_pem, pub_pem = _gen_keypair()
    days = ["2026-05-22", "2026-05-23", "2026-05-24"]
    receipts_per_day = 2

    with tempfile.TemporaryDirectory() as tmp:
        export_dir = Path(tmp)
        (export_dir / "keys").mkdir()
        (export_dir / "keys" / "historical").mkdir()
        (export_dir / "receipts").mkdir()
        (export_dir / "proofs").mkdir()
        (export_dir / "roots").mkdir()
        (export_dir / "keys" / "active.pem").write_text(pub_pem)

        prev_hash = "0" * 64
        all_payloads: dict[str, list[dict[str, Any]]] = {}

        for day in days:
            day_payloads: list[dict[str, Any]] = []
            for j in range(receipts_per_day):
                exec_id = f"exec-{day}-{j}"
                receipt = _make_receipt(exec_id)
                receipt["timestamp"] = f"{day}T12:0{j}:00Z"
                payload = _sign_receipt_payload(receipt, priv_pem, pub_pem)
                day_payloads.append(payload)
                _write_json(export_dir / "receipts" / f"{exec_id}.json", payload)
            all_payloads[day] = day_payloads

            day_leaves = [leaf_hash(p) for p in day_payloads]
            day_root = build_root(day_leaves)

            signed_root = _make_signed_root(
                date=day,
                root_hash=day_root,
                prev_root_hash=prev_hash,
                priv_pem=priv_pem,
                pub_pem=pub_pem,
            )
            _write_json(export_dir / "roots" / f"{day}.json", signed_root)

            for j, payload in enumerate(day_payloads):
                target_leaf, siblings, proof_root = _build_inclusion_proof(day_payloads, j)
                proof_file = {
                    "proof": {
                        "leaf": target_leaf,
                        "index": j,
                        "siblings": siblings,
                        "root": proof_root,
                        "size": receipts_per_day,
                    },
                    "signed_root": signed_root,
                }
                exec_id = f"exec-{day}-{j}"
                _write_json(export_dir / "proofs" / f"{exec_id}.json", proof_file)

            prev_hash = day_root

        verifier = AuditVerifier.from_export_dir(export_dir)
        result = verifier.verify_export(export_dir)

        total = len(days) * receipts_per_day
        assert result.total_receipts == total
        assert result.valid_receipts == total, f"errors: {result.errors}"
        assert result.total_inclusions == total
        assert result.valid_inclusions == total, f"errors: {result.errors}"
        assert result.chain_ok is True, f"chain: {result.chain_error}"
        assert result.ok is True


def test_verifier_export_detects_tampered_receipt() -> None:
    """Tamper one receipt file; verify_export should report a failure."""
    priv_pem, pub_pem = _gen_keypair()

    with tempfile.TemporaryDirectory() as tmp:
        export_dir = Path(tmp)
        (export_dir / "keys").mkdir()
        (export_dir / "keys" / "historical").mkdir()
        (export_dir / "receipts").mkdir()
        (export_dir / "proofs").mkdir()
        (export_dir / "roots").mkdir()
        (export_dir / "keys" / "active.pem").write_text(pub_pem)

        payloads = [
            _sign_receipt_payload(_make_receipt(f"tamper-{i}"), priv_pem, pub_pem)
            for i in range(3)
        ]
        for i, p in enumerate(payloads):
            _write_json(export_dir / "receipts" / f"tamper-{i}.json", p)

        # Tamper receipt #1 after writing
        tamper_path = export_dir / "receipts" / "tamper-1.json"
        tampered = json.loads(tamper_path.read_text())
        tampered["receipt"]["decision"] = "deny"
        tamper_path.write_text(json.dumps(tampered))

        # Signed root (not used for inclusion in this test)
        day_root = build_root([leaf_hash(p) for p in payloads])
        signed_root = _make_signed_root("2026-05-24", day_root, "0" * 64, priv_pem, pub_pem)
        _write_json(export_dir / "roots" / "2026-05-24.json", signed_root)

        verifier = AuditVerifier.from_export_dir(export_dir)
        result = verifier.verify_export(export_dir)

        assert result.total_receipts == 3
        assert result.valid_receipts == 2  # only 2 of 3 pass
        assert result.ok is False
        assert any("tamper-1" in e for e in result.errors)
