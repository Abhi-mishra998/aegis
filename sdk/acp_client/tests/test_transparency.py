"""End-to-end: build a tree on the server side, verify inclusion in the SDK."""
import uuid
from datetime import UTC, datetime

import pytest

from sdk.acp_client import (
    leaf_hash_for_receipt,
    verify_inclusion,
)
from services.audit.merkle import build_root, inclusion_proof
from services.audit.signer import canonical_json, get_signer, reset_signer_for_tests


@pytest.fixture(autouse=True)
def _isolate_signer(tmp_path, monkeypatch):
    monkeypatch.setenv("RECEIPT_SIGNING_KEY_PATH", str(tmp_path / "test-key.pem"))
    monkeypatch.delenv("RECEIPT_SIGNING_PRIVATE_KEY", raising=False)
    reset_signer_for_tests()
    yield
    reset_signer_for_tests()


def _row(i: int):
    return {
        "id":          uuid.UUID(int=i),
        "tenant_id":   uuid.UUID(int=999),
        "agent_id":    uuid.UUID(int=42),
        "tool":        "db.query",
        "action":      "execute",
        "decision":    "allow",
        "reason":      None,
        "request_id":  f"req_{i}",
        "timestamp":   datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC),
        "event_hash":  f"{i:064d}",
        "prev_hash":   f"{i-1:064d}" if i else None,
        "chain_shard": 0,
    }


def test_sdk_verifies_inclusion_in_server_built_tree() -> None:
    """The most important test in this sprint: customer-side verification works."""
    signer = get_signer()
    n = 7  # forces multiple odd-level duplications
    rows = [_row(i) for i in range(n)]

    leaves = []
    payloads = []
    for r in rows:
        p = signer.sign(r)
        payloads.append(p)
        leaves.append(leaf_hash_for_receipt(p))

    # Verify the SDK helper matches what the server would compute
    import hashlib
    for p, leaf in zip(payloads, leaves, strict=False):
        assert leaf == hashlib.sha256(canonical_json(p)).hexdigest()

    root = build_root(leaves)

    # Every leaf must have a verifiable proof
    for i, leaf in enumerate(leaves):
        proof = inclusion_proof(leaves, i)
        assert verify_inclusion(leaf, proof, root) is True


def test_sdk_rejects_tampered_proof() -> None:
    leaves = [f"{i:064x}" for i in range(5)]
    root = build_root(leaves)
    proof = inclusion_proof(leaves, 2)
    proof["siblings"][0]["hash"] = "f" * 64
    assert verify_inclusion(leaves[2], proof, root) is False


def test_sdk_rejects_swapped_leaf() -> None:
    leaves = [f"{i:064x}" for i in range(5)]
    root = build_root(leaves)
    proof = inclusion_proof(leaves, 2)
    # try to claim leaf 3 is at index 2 — proof should reject
    assert verify_inclusion(leaves[3], proof, root) is False


def test_sdk_raises_on_malformed_proof() -> None:
    with pytest.raises(ValueError, match="missing field"):
        verify_inclusion("a" * 64, {"leaf": "a" * 64}, "b" * 64)
