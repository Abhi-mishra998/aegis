"""Wire schemas for the Execution Witness — matches ATF §6.4."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ObservationType = Literal["net", "api", "fs", "process", "resource"]
WitnessVerdict = Literal["CORROBORATED", "CONTRADICTED", "UNOBSERVED"]


class Observation(BaseModel):
    """One raw event emitted by a probe (eBPF sidecar or in-process observer)."""

    gate_decision_id: str
    type: ObservationType
    detail: str                          # e.g. "TLS crm.internal:443, 14:02:11Z"
    ts: str                              # ISO 8601 UTC
    payload_hash: str | None = None      # for `api` observations, response hash
    extra: dict[str, Any] = Field(default_factory=dict)


class VerdictRequest(BaseModel):
    """Ask the engine for a verdict on a specific gate_decision_id."""

    gate_decision_id: str
    claim: str
    action_class: Literal["C0", "C1", "C2", "C3"]
    expected_evidence: list[ObservationType] = Field(
        default_factory=lambda: ["net", "api"],
    )


class Attestation(BaseModel):
    """ATF §6.4 attestation record — signed with the Witness's Ed25519 key."""

    attestation_version: str = "3.0"
    gate_decision_id: str
    claim: str
    verdict: WitnessVerdict
    evidence: list[dict[str, Any]]       # projection of raw Observation
    witness_id: str                      # spiffe://tenant/witness/node-N
    ts: str                              # attestation time
    signature: str                       # base64url Ed25519 over canonical body
