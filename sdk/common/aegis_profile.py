"""ATF v3.2 §4.3 — Aegis Profile document.

Signed overlay referencing standard identities (SPIFFE / Entra / Okta /
Clerk). Content stays close to the v2.0 Passport minus the parallel-universe
sovereignty story:

    { subject, human_responsible, provenance, gate_policy_ref,
      action_class_max, signatures }

The profile is minted at agent-creation time (side-effect of
`services/registry/router.py::create_agent`) and referenced by hash inside
every ledger entry's `intent.aegis_profile_hash` (see `atf_entry.py`).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

from sdk.common.atf_class import ActionClass

_PROFILE_VERSION = "3.0"


@dataclass(frozen=True)
class ProfileSubject:
    spiffe_id: str | None = None
    idp_ref: str | None = None       # "entra:appid:...|okta:agent:...|clerk:user:..."
    scim_ref: str | None = None


@dataclass(frozen=True)
class ProfileProvenance:
    model_ref: str | None = None
    prompt_template_hash: str | None = None
    tool_manifest_hash: str | None = None
    container_image_digest: str | None = None
    sbom_ref: str | None = None


@dataclass(frozen=True)
class AegisProfile:
    subject: ProfileSubject
    human_responsible: str
    gate_policy_ref: str
    action_class_max: ActionClass = "C3"
    provenance: ProfileProvenance = field(default_factory=ProfileProvenance)
    aegis_profile_version: str = _PROFILE_VERSION
    signatures: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def canonical_bytes(profile: AegisProfile) -> bytes:
    """RFC 8785 JCS-canonical serialization for signing / hashing.

    Ordering is stable, whitespace is minimal, Unicode escapes are avoided.
    Signatures dict is excluded from the signing payload so a signer can
    hash the same profile it is about to sign.
    """
    body = profile.as_dict()
    body.pop("signatures", None)
    return json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def fingerprint(profile: AegisProfile) -> str:
    """SHA-256 hex of the canonical body — goes into every ledger entry."""
    return hashlib.sha256(canonical_bytes(profile)).hexdigest()


if __name__ == "__main__":
    p = AegisProfile(
        subject=ProfileSubject(
            spiffe_id="spiffe://acme/agent/finance-001",
            idp_ref="clerk:user:usr_abc",
            scim_ref="scim://acme/Agents/finance-001",
        ),
        human_responsible="scim://acme/Users/security-lead",
        gate_policy_ref="policy://acme/finance/v17",
        action_class_max="C3",
        provenance=ProfileProvenance(
            model_ref="registry://acme/models/gpt-4o/2026-07-15",
            prompt_template_hash="sha256:8b2c",
            tool_manifest_hash="sha256:9d1e",
        ),
    )
    fp1 = fingerprint(p)
    fp2 = fingerprint(p)
    assert fp1 == fp2, "fingerprint must be deterministic"
    assert len(fp1) == 64

    # Reordering fields inside signatures dict must NOT change the fingerprint
    p_signed = AegisProfile(
        subject=p.subject,
        human_responsible=p.human_responsible,
        gate_policy_ref=p.gate_policy_ref,
        action_class_max=p.action_class_max,
        provenance=p.provenance,
        signatures={"tenant": "ed25519:...."},
    )
    assert fingerprint(p_signed) == fp1

    # Changing a real field DOES change the fingerprint
    p_changed = AegisProfile(
        subject=p.subject,
        human_responsible="scim://acme/Users/other",
        gate_policy_ref=p.gate_policy_ref,
        action_class_max=p.action_class_max,
        provenance=p.provenance,
    )
    assert fingerprint(p_changed) != fp1

    print("aegis_profile OK")
