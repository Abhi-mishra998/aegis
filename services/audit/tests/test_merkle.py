import hashlib

import pytest

from services.audit.merkle import (
    EMPTY_ROOT,
    build_root,
    inclusion_proof,
    leaf_hash,
    verify_inclusion,
)


def _leaves(n):
    return [leaf_hash(f"leaf-{i}".encode()) for i in range(n)]


def test_empty_tree_has_sentinel_root():
    assert build_root([]) == EMPTY_ROOT


def test_single_leaf_root_is_leaf():
    leaves = _leaves(1)
    assert build_root(leaves) == leaves[0]


def test_two_leaves_root_is_pair_hash():
    leaves = _leaves(2)
    expected = hashlib.sha256(bytes.fromhex(leaves[0]) + bytes.fromhex(leaves[1])).hexdigest()
    assert build_root(leaves) == expected


def test_root_stable_under_repeated_build():
    leaves = _leaves(13)  # forces odd-level duplication multiple times
    assert build_root(leaves) == build_root(leaves)


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 8, 13, 16, 100])
def test_every_leaf_has_a_valid_proof(n):
    leaves = _leaves(n)
    root = build_root(leaves)
    for i in range(n):
        proof = inclusion_proof(leaves, i)
        assert verify_inclusion(leaves[i], proof, root) is True


def test_proof_rejects_wrong_root():
    leaves = _leaves(5)
    proof = inclusion_proof(leaves, 2)
    assert verify_inclusion(leaves[2], proof, "0" * 64) is False


def test_proof_rejects_wrong_leaf():
    leaves = _leaves(5)
    proof = inclusion_proof(leaves, 2)
    root = build_root(leaves)
    assert verify_inclusion(leaves[3], proof, root) is False


def test_proof_rejects_tampered_sibling():
    leaves = _leaves(5)
    proof = inclusion_proof(leaves, 2)
    root = build_root(leaves)
    proof["siblings"][0]["hash"] = "f" * 64
    assert verify_inclusion(leaves[2], proof, root) is False


def test_proof_raises_on_malformed_input():
    with pytest.raises(ValueError, match="missing field"):
        verify_inclusion("a" * 64, {"leaf": "a" * 64}, "b" * 64)


def test_inclusion_proof_index_out_of_range():
    with pytest.raises(IndexError):
        inclusion_proof(_leaves(3), 10)


def test_inclusion_proof_empty_leaves():
    with pytest.raises(ValueError):
        inclusion_proof([], 0)
