"""ATF v3.2 §7.3 + §7.4 — export bundle format v3.

Contents (per §7.3):
  * JSON-lines ledger entries (already in §7.1 shape via atf_entry)
  * Merkle proofs
  * Anchor references
  * Policy manifests in force during the range
  * Human-readable summary (period, agents, action_class counts,
    escalations, contradictions)

Semver'd; verifier refuses unknown MAJORS (§7.4). Consumers see this
shape; producers use `build_bundle`.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

_BUNDLE_MAJOR = 3
_BUNDLE_MINOR = 0
BUNDLE_VERSION = f"{_BUNDLE_MAJOR}.{_BUNDLE_MINOR}"


class UnsupportedBundleVersion(ValueError):
    """Verifier refuses an unknown MAJOR."""


def is_supported_major(bundle_version: str) -> bool:
    try:
        major, _minor = bundle_version.split(".", 1)
        return int(major) == _BUNDLE_MAJOR
    except (ValueError, AttributeError):
        return False


@dataclass
class ExportSummary:
    period_start: str
    period_end: str
    agent_count: int
    entry_count: int
    action_class_counts: dict[str, int] = field(default_factory=dict)
    verdict_counts: dict[str, int] = field(default_factory=dict)
    escalations: int = 0
    contradictions: int = 0


def build_bundle(
    *,
    entries: list[dict[str, Any]],
    merkle_proofs: list[dict[str, Any]],
    anchor_refs: list[str],
    policy_manifests: list[dict[str, Any]],
    summary: ExportSummary,
) -> dict[str, Any]:
    """Assemble one export bundle. Callers stream `entries` in as JSON-lines
    when serializing; this function returns the *manifest* + summary +
    proofs, and the entries list is embedded so a single dict-to-file
    write is enough at Phase 1c scale.
    """
    return {
        "bundle_version": BUNDLE_VERSION,
        "summary": summary.__dict__,
        "entries": entries,
        "merkle_proofs": merkle_proofs,
        "anchor_refs": anchor_refs,
        "policy_manifests": policy_manifests,
    }


def bundle_digest(bundle: dict[str, Any]) -> str:
    """SHA-256 over the canonical bundle body — appears on export receipts."""
    body = json.dumps(bundle, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def parse_bundle(payload: dict[str, Any]) -> dict[str, Any]:
    """Verifier entry point — refuses unknown MAJOR; returns payload unchanged
    for supported MAJORs so existing verifiers can still integrity-check
    newer MINORs even if they can't semantically interpret every field.
    """
    version = payload.get("bundle_version", "")
    if not is_supported_major(version):
        raise UnsupportedBundleVersion(
            f"export bundle major {version} unsupported by this verifier"
        )
    return payload


if __name__ == "__main__":
    summary = ExportSummary(
        period_start="2026-07-01T00:00:00Z",
        period_end="2026-07-21T00:00:00Z",
        agent_count=5,
        entry_count=3,
        action_class_counts={"C0": 0, "C1": 1, "C2": 2, "C3": 0},
        verdict_counts={"CORROBORATED": 2, "CONTRADICTED": 0, "UNOBSERVED": 1},
        escalations=1,
        contradictions=0,
    )
    b = build_bundle(
        entries=[{"entry_id": "el_1"}, {"entry_id": "el_2"}, {"entry_id": "el_3"}],
        merkle_proofs=[{"leaf": "sha256:a", "path": []}],
        anchor_refs=["s3://aegis-anchors/2026-07-21T14:00Z"],
        policy_manifests=[{"hash": "sha256:policy-v17", "content": "..."}],
        summary=summary,
    )
    assert b["bundle_version"] == "3.0"
    assert b["summary"]["entry_count"] == 3

    d1 = bundle_digest(b)
    d2 = bundle_digest(b)
    assert d1 == d2, "digest must be deterministic"

    assert parse_bundle(b) == b

    # Verifier refuses unknown major
    try:
        parse_bundle({"bundle_version": "4.0"})
        raise AssertionError("expected UnsupportedBundleVersion")
    except UnsupportedBundleVersion:
        pass

    # Verifier accepts future minor (integrity still works)
    parse_bundle({"bundle_version": "3.99", "entries": []})

    print("atf_export_bundle OK")
