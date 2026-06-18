"""aegis-verify — standalone offline verifier for Aegis evidence bundles.

Reads an Aegis evidence bundle (a JSON file containing the rows, the
public keys that signed them, the daily Merkle roots, and the per-row
Article mappings) and validates the entire chain without ever calling
back to Aegis.

Design constraint: zero dependency on a running Aegis instance. The only
runtime dependency is `cryptography` for ed25519 verification. Anything
that requires reaching the vendor is by definition not auditor-grade.

Usage:
    python -m aegis_verify --bundle evidence_bundle.json
    aegis-verify --bundle evidence_bundle.json --verbose
"""
__version__ = "1.1.0"

# AEVF specification this implementation conforms to. See `docs/AEVF/spec.md`
# for the canonical definition. The bundle format version (recorded as
# `format_version` in each bundle) is separate — a single spec version may
# support reading multiple bundle formats during a transition window.
SPEC_VERSION = "aevf/0.1.0"

__all__ = ["__version__", "SPEC_VERSION"]
