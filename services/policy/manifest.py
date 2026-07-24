"""ATF v3.2 §7.1 — policy_manifest_hash.

Hash over every `.rego` file the Gate loads, computed once at module
import (rego is code-and-data — a hash-per-decision would be gratuitous
churn for zero added evidence). The hash is stamped into every decision's
metadata so every ledger entry names the exact policy bundle in force.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

_POLICY_DIR = Path(__file__).parent / "policies"


def _compute() -> str:
    h = hashlib.sha256()
    # Deterministic order — filename hash contribution must be stable so
    # two workers on the same bundle agree byte-for-byte.
    for rego in sorted(_POLICY_DIR.glob("*.rego")):
        h.update(rego.name.encode("utf-8"))
        h.update(b"\x00")
        h.update(rego.read_bytes())
        h.update(b"\x00")
    return "sha256:" + h.hexdigest()


POLICY_MANIFEST_HASH = _compute()


if __name__ == "__main__":
    a = _compute()
    b = _compute()
    assert a == b, "manifest hash must be deterministic"
    assert a.startswith("sha256:") and len(a) == 71
    print(f"policy_manifest OK — {POLICY_MANIFEST_HASH}")
