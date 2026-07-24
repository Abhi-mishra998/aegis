"""ATF v3.2 §8.2 — Article 12 (EU AI Act) mapping.

Not a document. A live artifact the compliance service returns when the
customer's DPO asks "how do you satisfy each Article 12 requirement?"

Each row names the requirement, the kernel component that satisfies it,
and the pointer to the concrete evidence artifact the auditor can pull.
Rendered as a dict so the compliance router can return it as JSON.
"""
from __future__ import annotations

from typing import Any


def build_article_12_mapping() -> list[dict[str, Any]]:
    return [
        {
            "requirement": "Automatic recording of events over system lifetime; not toggleable by developers",
            "kernel_answer": "Capability Gate is in-line: no gate, no execution.",
            "artifact_kind": "gate_decision_record",
            "endpoint": "/audit/logs?action=policy_evaluation",
        },
        {
            "requirement": "Structured, complete records: timestamp, identity, action, input, output, context",
            "kernel_answer": "Ledger Entry §7.1 with intent/authorization/observation/outcome/chain slices.",
            "artifact_kind": "ledger_entry",
            "endpoint": "/audit/logs",
        },
        {
            "requirement": "Tamper-evident via cryptographic measures, not access controls",
            "kernel_answer": "Hash chain + Merkle tree + external anchor batches.",
            "artifact_kind": "anchor_batch",
            "endpoint": "/transparency/roots",
        },
        {
            "requirement": "Independently verifiable without relying on the provider's assertion",
            "kernel_answer": "Open-source `aegis-verify` CLI runs offline against an export bundle.",
            "artifact_kind": "verifier_cli",
            "endpoint": "package://aegis-aevf",
        },
        {
            "requirement": "Retained ≥ 6 months",
            "kernel_answer": "Retention floor enforced at the store level; retention config itself ledgered.",
            "artifact_kind": "retention_config",
            "endpoint": "/compliance/retention",
        },
        {
            "requirement": "Exportable, retrievable format for national authorities",
            "kernel_answer": "One-command export bundle (§7.3 shape, §7.4 semver).",
            "artifact_kind": "export_bundle",
            "endpoint": "/compliance/export",
        },
        {
            "requirement": "Traceability enabling output verification",
            "kernel_answer": "Witness verdict (CORROBORATED / CONTRADICTED / UNOBSERVED) binds claim ↔ observation.",
            "artifact_kind": "witness_attestation",
            "endpoint": "/witness/verdict",
        },
        {
            "requirement": "Human oversight interpretability (Article 14 adjacency)",
            "kernel_answer": "ESCALATE_HUMAN path + human receipts on C3 actions.",
            "artifact_kind": "human_verification",
            "endpoint": "/auto-response/pending",
        },
    ]


if __name__ == "__main__":
    m = build_article_12_mapping()
    assert len(m) == 8
    assert all(row["requirement"] and row["kernel_answer"] for row in m)
    assert all(row["endpoint"].startswith(("/", "package://")) for row in m)
    print(f"atf_article_12 OK — {len(m)} requirements mapped")
