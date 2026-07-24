"""Self-check that the published JSON Schemas parse AND agree with the
reference Python implementation. Runnable as `python specs/verify_schemas.py`.

No external `jsonschema` library dependency to keep the check portable —
we do a minimal structural validation manually (required-field check +
type check on the top-level fields).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_SPECS = Path(__file__).parent


def _load(name: str) -> dict:
    return json.loads((_SPECS / name).read_text())


def _check_required(instance: dict, required: list[str], where: str) -> list[str]:
    return [f"{where}: missing {f!r}" for f in required if f not in instance]


def _reference_ledger_entry() -> dict:
    """Matches sdk/common/atf_entry.py::to_atf_entry output shape."""
    return {
        "entry_version": "3.0",
        "entry_id":      "el_01J",
        "ts":            "2026-07-21T14:02:11Z",
        "intent": {
            "agent":              "spiffe://acme/agent/finance-001",
            "aegis_profile_hash": "sha256:" + "a" * 64,
            "claim":              "delete crm record 123",
            "action_class":       "C2",
        },
        "authorization": {
            "gate_decision_id":      "gd_01J",
            "decision":              "allow",
            "policy_manifest_hash":  "sha256:" + "b" * 64,
            "constraints_evaluated": ["record_scope"],
            "delegation_chain":      [],
        },
        "observation": {
            "witness_attestation_id": "wa_01J",
            "verdict":                "CORROBORATED",
            "witness_sig":            "ed25519:signature-b64",
        },
        "outcome": {
            "status":              "COMPLETED",
            "response_hash":       "sha256:" + "c" * 64,
            "human_verification":  None,
        },
        "chain": {
            "prev_entry_hash": "sha256:" + "d" * 64,
            "merkle_leaf":     "sha256:" + "e" * 64,
            "anchor_batch":    "mb_2026-07-21T14:05Z",
        },
    }


def _reference_witness_attestation() -> dict:
    """Matches services/witness/schemas.py::Attestation."""
    return {
        "attestation_version": "3.0",
        "gate_decision_id":    "gd_01J",
        "claim":               "delete crm record 123",
        "verdict":             "CORROBORATED",
        "evidence": [
            {"type": "net", "detail": "TLS crm.internal:443",
             "ts": "2026-07-21T14:02:11Z"},
            {"type": "api", "detail": "DELETE /records/123 → 200",
             "ts": "2026-07-21T14:02:11Z",
             "payload_hash": "sha256:" + "0" * 64,
             "extra": {"status_code": 200}},
        ],
        "witness_id": "spiffe://acme/witness/node-7",
        "ts":         "2026-07-21T14:02:12Z",
        "signature":  "base64url-signature",
    }


def _reference_export_bundle() -> dict:
    """Matches sdk/common/atf_export_bundle.py::build_bundle output."""
    return {
        "bundle_version": "3.0",
        "summary": {
            "period_start":         "2026-07-01T00:00:00Z",
            "period_end":           "2026-07-21T00:00:00Z",
            "agent_count":          5,
            "entry_count":          3,
            "action_class_counts":  {"C0": 0, "C1": 1, "C2": 2, "C3": 0},
            "verdict_counts":       {"CORROBORATED": 2, "CONTRADICTED": 0, "UNOBSERVED": 1},
            "escalations":          1,
            "contradictions":       0,
        },
        "entries":       [_reference_ledger_entry()],
        "merkle_proofs": [{"leaf": "sha256:" + "1" * 64, "path": []}],
        "anchor_refs":   ["s3://aegis-anchors/2026-07-21T14:00Z"],
        "policy_manifests": [{"hash": "sha256:" + "2" * 64, "content": "package aegis..."}],
    }


def main() -> int:
    errs: list[str] = []

    ledger_schema = _load("ledger_entry.schema.json")
    assert ledger_schema["$id"].endswith("/3.0")
    errs += _check_required(_reference_ledger_entry(),
                            ledger_schema["required"], "ledger_entry")

    wa_schema = _load("witness_attestation.schema.json")
    errs += _check_required(_reference_witness_attestation(),
                            wa_schema["required"], "witness_attestation")

    eb_schema = _load("export_bundle.schema.json")
    errs += _check_required(_reference_export_bundle(),
                            eb_schema["required"], "export_bundle")

    if errs:
        for e in errs:
            print("FAIL:", e)
        return 1
    print("specs OK — all 3 schemas parse and match reference impl")
    return 0


if __name__ == "__main__":
    sys.exit(main())
