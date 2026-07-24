"""ATF v3.2 §7.1 — Ledger Entry adapter.

Translates an existing `audit_logs` row (or an in-memory decision result)
into the ATF §7.1 canonical shape:

    { intent, authorization, observation, outcome, chain }

Adapter, not a rewrite — the write path stays as-is; export bundles and
external consumers see the ATF-canonical view. The verifier refuses
unknown MAJORS via `_ENTRY_SCHEMA_MAJOR`.
"""
from __future__ import annotations

from typing import Any, Literal

_ENTRY_SCHEMA_MAJOR = 3
_ENTRY_SCHEMA_MINOR = 0
ENTRY_VERSION = f"{_ENTRY_SCHEMA_MAJOR}.{_ENTRY_SCHEMA_MINOR}"

WitnessVerdict = Literal["CORROBORATED", "CONTRADICTED", "UNOBSERVED"]


def is_supported_major(entry_version: str) -> bool:
    """Verifier gate: refuse unknown majors, accept future minors additively."""
    try:
        major, _minor = entry_version.split(".", 1)
        return int(major) == _ENTRY_SCHEMA_MAJOR
    except (ValueError, AttributeError):
        return False


def to_atf_entry(row: dict[str, Any]) -> dict[str, Any]:
    """Project an audit_logs row into the ATF §7.1 shape.

    Missing fields → JSON `null`. Verifier can prove integrity without
    interpreting new fields — semantic gates only apply at MAJOR bumps.
    """
    meta = row.get("metadata_json") or {}
    if not isinstance(meta, dict):
        meta = {}

    intent = {
        "agent": row.get("agent_id"),
        "aegis_profile_hash": meta.get("aegis_profile_hash"),
        "claim": row.get("action"),
        "action_class": meta.get("action_class"),  # populated post Phase 1a
    }

    authorization = {
        "gate_decision_id": row.get("id"),
        "decision": row.get("decision"),
        "policy_manifest_hash": meta.get("policy_manifest_hash") or meta.get("policy_id"),
        "constraints_evaluated": list(meta.get("findings") or []),
        "delegation_chain": list(meta.get("delegation_chain") or []),
    }

    # Observation slice becomes non-null once the Execution Witness ships
    # in Phase 1b; until then every entry is honestly UNOBSERVED.
    observation = {
        "witness_attestation_id": meta.get("witness_attestation_id"),
        "verdict": meta.get("witness_verdict", "UNOBSERVED"),
        "witness_sig": meta.get("witness_sig"),
    }

    outcome = {
        "status": meta.get("status") or ("COMPLETED" if row.get("decision") == "allow" else "BLOCKED"),
        "response_hash": meta.get("response_hash"),
        "human_verification": meta.get("human_verification"),
    }

    chain = {
        "prev_entry_hash": row.get("prev_hash"),
        "merkle_leaf": row.get("merkle_leaf"),
        "anchor_batch": row.get("anchor_batch"),
    }

    return {
        "entry_version": ENTRY_VERSION,
        "entry_id": row.get("id"),
        "ts": row.get("created_at") or row.get("ts"),
        "intent": intent,
        "authorization": authorization,
        "observation": observation,
        "outcome": outcome,
        "chain": chain,
    }


if __name__ == "__main__":
    assert is_supported_major("3.0")
    assert is_supported_major("3.99")
    assert not is_supported_major("2.9")
    assert not is_supported_major("4.0")
    assert not is_supported_major("garbage")

    row = {
        "id": "el_01",
        "agent_id": "spiffe://acme/agent/1",
        "action": "delete crm.record 123",
        "decision": "allow",
        "created_at": "2026-07-21T14:02:11Z",
        "prev_hash": "sha256:aa",
        "merkle_leaf": "sha256:bb",
        "anchor_batch": "mb_2026-07-21T14:05Z",
        "metadata_json": {
            "action_class": "C2",
            "policy_manifest_hash": "sha256:policy-v17",
            "findings": ["record_scope"],
            "response_hash": "sha256:resp",
        },
    }
    e = to_atf_entry(row)
    assert e["entry_version"] == "3.0"
    assert e["intent"]["action_class"] == "C2"
    assert e["authorization"]["policy_manifest_hash"] == "sha256:policy-v17"
    assert e["observation"]["verdict"] == "UNOBSERVED"  # pre-Witness
    assert e["outcome"]["response_hash"] == "sha256:resp"
    assert e["chain"]["prev_entry_hash"] == "sha256:aa"

    print("atf_entry OK")
