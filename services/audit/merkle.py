"""Merkle tree for the daily transparency log.

The daily root commits to every signed receipt produced in a (tenant, date)
window. A customer who archives the root at end-of-day can later detect
retroactive deletion or reordering — the root would no longer match.

Conventions (must agree across language SDKs):

  - Hash: SHA-256 (32 bytes, hex-encoded for transport).
  - Leaves are bytes32, sorted by `(timestamp ASC, audit_id ASC)` at build time
    so two implementations always produce the same tree.
  - Inner nodes: H(left || right). 64 bytes input → 32 bytes output.
  - Odd level: duplicate the last node (Bitcoin-style). Documented so
    the verifier behaves identically.
  - Empty tree: root = sha256(b"") (zero-leaf sentinel). Recorded so callers
    can tell "no events today" apart from "events lost."

Inclusion proof shape:

    {
      "leaf":  "<hex32>",
      "index": <int>,            # 0-based position in the sorted leaves
      "siblings": [
        {"side": "L"|"R", "hash": "<hex32>"},  # bottom-up
        ...
      ],
      "root":  "<hex32>",
      "size":  <int>             # total leaf count
    }
"""
from __future__ import annotations

import hashlib
from typing import Any

EMPTY_ROOT = hashlib.sha256(b"").hexdigest()


def _hex(b: bytes) -> str:
    return b.hex()


def _unhex(s: str) -> bytes:
    return bytes.fromhex(s)


def _h(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(left + right).digest()


def leaf_hash(payload: bytes) -> str:
    """Hash a single leaf payload (e.g. canonical receipt JSON) to a hex digest."""
    return hashlib.sha256(payload).hexdigest()


def build_root(leaves_hex: list[str]) -> str:
    """Build the Merkle root over hex-encoded leaves. Caller must pre-sort."""
    if not leaves_hex:
        return EMPTY_ROOT
    level = [_unhex(h) for h in leaves_hex]
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        level = [_h(level[i], level[i + 1]) for i in range(0, len(level), 2)]
    return _hex(level[0])


def inclusion_proof(leaves_hex: list[str], index: int) -> dict[str, Any]:
    """Build an inclusion proof for the leaf at `index`.

    Returns the proof object described in the module docstring. Caller is
    responsible for the leaf list being the same one used to compute the root.
    """
    if not leaves_hex:
        raise ValueError("cannot build proof for empty leaf set")
    if index < 0 or index >= len(leaves_hex):
        raise IndexError(f"index {index} out of range for {len(leaves_hex)} leaves")

    level = [_unhex(h) for h in leaves_hex]
    siblings: list[dict[str, str]] = []
    idx = index
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        if idx % 2 == 0:
            sib = level[idx + 1]
            siblings.append({"side": "R", "hash": _hex(sib)})
        else:
            sib = level[idx - 1]
            siblings.append({"side": "L", "hash": _hex(sib)})
        level = [_h(level[i], level[i + 1]) for i in range(0, len(level), 2)]
        idx //= 2

    return {
        "leaf":     leaves_hex[index],
        "index":    index,
        "siblings": siblings,
        "root":     _hex(level[0]),
        "size":     len(leaves_hex),
    }


def verify_inclusion(leaf_hex: str, proof: dict[str, Any], expected_root: str) -> bool:
    """Verify that `leaf_hex` is included in the tree whose root is `expected_root`.

    Returns False on any mismatch. Raises ValueError only on malformed proof
    so callers can tell "bad input" from "valid input, bad signature."
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

    cur = _unhex(leaf_hex)
    for sib in proof["siblings"]:
        side = sib.get("side")
        h_hex = sib.get("hash")
        if side not in ("L", "R") or not isinstance(h_hex, str):
            raise ValueError("malformed sibling entry")
        sh = _unhex(h_hex)
        cur = _h(sh, cur) if side == "L" else _h(cur, sh)

    return _hex(cur) == expected_root
