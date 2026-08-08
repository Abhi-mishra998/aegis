"""Merkle tree for the daily transparency log.

The daily root commits to every signed receipt produced in a (tenant, date)
window. A customer who archives the root at end-of-day can later detect
retroactive deletion or reordering — the root would no longer match.

Conventions (must agree across language SDKs):

  - Hash: SHA-256 (32 bytes, hex-encoded for transport).
  - Leaves are bytes32, sorted by ``(timestamp ASC, audit_id ASC)`` at
    build time so two implementations always produce the same tree.
  - The scheme is *versioned* — every signed root carries a
    ``merkle_version`` field so a verifier can select the correct
    hashing rules per row.

Version 1 (LEGACY — accepted on verify, no longer used for new writes):

    - leaf_hash(payload)  = sha256(payload)
    - inner(L, R)         = sha256(L || R)
    - odd count           = duplicate the last node

    The v1 tree is subject to two known second-preimage weaknesses
    (report findings C5, "Merkle tree lacks RFC 6962 domain separation"
    and "Bitcoin-style odd-leaf duplication"). It stays only for
    backward-compat with roots sealed before the 2026-07-31 fix.

Version 2 (current, RFC 6962 §2.1):

    - leaf_hash(payload)  = sha256(0x00 || payload)
    - inner(L, R)         = sha256(0x01 || L || R)
    - odd count           = promote the unpaired node up one level
                            unhashed (Certificate Transparency shape)

    Domain separation between leaves and inner nodes closes the
    64-byte second-preimage forgery: a receipt whose canonical JSON
    happens to be 64 bytes of two concatenated leaf hashes can no
    longer collide with an inner node, because leaves prefix ``0x00``
    and inner nodes prefix ``0x01``. Promoting the unpaired node fixes
    the Bitcoin CVE-2012-2459 duplicate-leaf ambiguity.

Inclusion proof shape (both versions):

    {
      "version": 1 | 2,
      "leaf":  "<hex32>",
      "index": <int>,
      "siblings": [
        {"side": "L"|"R", "hash": "<hex32>"},  # bottom-up
        ...
      ],
      "root":  "<hex32>",
      "size":  <int>
    }
"""
from __future__ import annotations

import hashlib
from typing import Any, Literal

# Current on-disk scheme for NEW writes.
CURRENT_VERSION: Literal[2] = 2

# Domain separation bytes per RFC 6962 §2.1 (only used by v2).
_LEAF_PREFIX = b"\x00"
_NODE_PREFIX = b"\x01"

# Legacy v1 empty root: sha256(b"").
_EMPTY_ROOT_V1 = hashlib.sha256(b"").hexdigest()
# v2 empty root: sha256(b"") per RFC 6962 §2.1 ("The hash of an empty
# list is the hash of an empty string"). Matches v1 by coincidence but
# named separately so a future scheme can drift.
_EMPTY_ROOT_V2 = hashlib.sha256(b"").hexdigest()

# Kept for source-compat with importers that used the old constant.
EMPTY_ROOT = _EMPTY_ROOT_V2


def _hex(b: bytes) -> str:
    return b.hex()


def _unhex(s: str) -> bytes:
    return bytes.fromhex(s)


def _leaf(payload: bytes, version: int) -> bytes:
    if version == 2:
        return hashlib.sha256(_LEAF_PREFIX + payload).digest()
    return hashlib.sha256(payload).digest()  # v1


def _inner(left: bytes, right: bytes, version: int) -> bytes:
    if version == 2:
        return hashlib.sha256(_NODE_PREFIX + left + right).digest()
    return hashlib.sha256(left + right).digest()  # v1


def leaf_hash(payload: bytes, *, version: int = CURRENT_VERSION) -> str:
    """Hash a single leaf payload (e.g. canonical receipt JSON) to a hex digest.

    Callers that need to verify a v1 root MUST pass ``version=1`` — the
    default is the current scheme.
    """
    return _leaf(payload, version).hex()


def build_root(leaves_hex: list[str], *, version: int = CURRENT_VERSION) -> str:
    """Build the Merkle root over hex-encoded leaves. Caller must pre-sort.

    v1: pair-and-duplicate-on-odd (kept for verify-only compatibility).
    v2: promote-on-odd (RFC 6962 §2.1) — the unpaired node rides up one
        level without being hashed against itself.
    """
    if not leaves_hex:
        return _EMPTY_ROOT_V2 if version == 2 else _EMPTY_ROOT_V1
    level = [_unhex(h) for h in leaves_hex]
    if version == 2:
        while len(level) > 1:
            new_level: list[bytes] = []
            i = 0
            while i + 1 < len(level):
                new_level.append(_inner(level[i], level[i + 1], version))
                i += 2
            if i < len(level):
                # Promote unpaired last node — RFC 6962 §2.1.
                new_level.append(level[i])
            level = new_level
        return _hex(level[0])
    # v1 legacy path.
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        level = [_inner(level[i], level[i + 1], version) for i in range(0, len(level), 2)]
    return _hex(level[0])


def inclusion_proof(
    leaves_hex: list[str], index: int, *, version: int = CURRENT_VERSION
) -> dict[str, Any]:
    """Build an inclusion proof for the leaf at ``index``.

    The proof carries its ``version`` so a verifier that doesn't
    remember which scheme sealed the root can still choose the right
    hashing rules.
    """
    if not leaves_hex:
        raise ValueError("cannot build proof for empty leaf set")
    if index < 0 or index >= len(leaves_hex):
        raise IndexError(f"index {index} out of range for {len(leaves_hex)} leaves")

    level = [_unhex(h) for h in leaves_hex]
    siblings: list[dict[str, str]] = []
    idx = index
    if version == 2:
        while len(level) > 1:
            new_level: list[bytes] = []
            paired_upper_bound = len(level) - (len(level) % 2)
            if idx < paired_upper_bound:
                if idx % 2 == 0:
                    siblings.append({"side": "R", "hash": _hex(level[idx + 1])})
                else:
                    siblings.append({"side": "L", "hash": _hex(level[idx - 1])})
            # (else: node is promoted — no sibling at this level)
            i = 0
            while i + 1 < len(level):
                new_level.append(_inner(level[i], level[i + 1], version))
                i += 2
            if i < len(level):
                new_level.append(level[i])
            level = new_level
            idx //= 2
        return {
            "version":  2,
            "leaf":     leaves_hex[index],
            "index":    index,
            "siblings": siblings,
            "root":     _hex(level[0]),
            "size":     len(leaves_hex),
        }
    # v1 legacy path.
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        if idx % 2 == 0:
            sib = level[idx + 1]
            siblings.append({"side": "R", "hash": _hex(sib)})
        else:
            sib = level[idx - 1]
            siblings.append({"side": "L", "hash": _hex(sib)})
        level = [_inner(level[i], level[i + 1], version) for i in range(0, len(level), 2)]
        idx //= 2
    return {
        "version":  1,
        "leaf":     leaves_hex[index],
        "index":    index,
        "siblings": siblings,
        "root":     _hex(level[0]),
        "size":     len(leaves_hex),
    }


def verify_inclusion(leaf_hex: str, proof: dict[str, Any], expected_root: str) -> bool:
    """Verify that ``leaf_hex`` is included in the tree whose root is
    ``expected_root``. The proof's ``version`` field selects the
    hashing rules; absent version defaults to v1 for legacy proofs.
    """
    if not isinstance(proof, dict):
        raise ValueError("proof must be a mapping")
    for k in ("leaf", "siblings", "root"):
        if k not in proof:
            raise ValueError(f"missing field: {k}")
    version = int(proof.get("version") or 1)
    if version not in (1, 2):
        raise ValueError(f"unknown merkle_version: {version!r}")
    if proof["leaf"] != leaf_hex:
        return False
    if proof["root"] != expected_root:
        return False

    cur = _unhex(leaf_hex)
    for sib in proof["siblings"]:
        side = sib.get("side")
        h_hex = sib.get("hash")
        if side not in ("L", "R") or not isinstance(h_hex, str):
            raise ValueError("malformed sibling entry")
        sh = _unhex(h_hex)
        cur = _inner(sh, cur, version) if side == "L" else _inner(cur, sh, version)

    return _hex(cur) == expected_root


def _demo_selfcheck() -> None:
    """SEC-2026-07-31 (ponytail assert-based test): the smallest thing
    that fails if v2 domain separation or odd-leaf promotion regresses.
    Runs on ``python -m services.audit.merkle`` (or during ad-hoc
    imports for the repl)."""
    # v2 leaf/inner domain separation.
    leaves = [leaf_hash(f"row-{i}".encode()) for i in range(5)]
    root = build_root(leaves)
    proof = inclusion_proof(leaves, 2)
    assert verify_inclusion(leaves[2], proof, root), "v2 inclusion proof round-trip failed"
    # v2 vs v1 divergence on the same leaf set.
    root_v1 = build_root(leaves, version=1)
    assert root != root_v1, "v2 root must differ from v1 for the same input (domain separation)"
    # Odd-count promote path (5 leaves → last one rides up).
    root_odd = build_root(leaves)
    root_odd_dup = build_root([*leaves, leaves[-1]])
    assert root_odd != root_odd_dup, "v2 must reject Bitcoin-style last-leaf duplication ambiguity"
    print("services/audit/merkle.py — v2 self-check OK")


if __name__ == "__main__":
    _demo_selfcheck()
