"""Sprint 13 — Capability-based wizard.

Single source of truth for the mapping
    capability (what the agent does) → policies (Aegis rules that fire)

Replaces the abstract `risk_level: low|medium|high` knob the wizard
used to surface. A CISO can answer "what can this thing actually do?"
by ticking the seven boxes; Aegis auto-generates the matching policy
set without the operator writing any Rego.

Capabilities follow the founder's wording verbatim:
  - filesystem        — read/write files on disk
  - database          — query / mutate SQL data
  - infrastructure    — kubectl / terraform / cloud control plane
  - payments          — money movement, wire transfers, refunds
  - email             — send_email and equivalents
  - external_apis     — HTTP / webhook calls to non-tenant services
  - internal_apis     — RPC to other tenant-internal services

Policies referenced here are the canonical signal-registry IDs
(services/security/signal_registry.py). The wizard surfaces them
verbatim so the CISO sees the exact ATT&CK-mapped rules being enabled.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Capability = Literal[
    "filesystem",
    "database",
    "infrastructure",
    "payments",
    "email",
    "external_apis",
    "internal_apis",
]

KNOWN_CAPABILITIES: tuple[Capability, ...] = (
    "filesystem",
    "database",
    "infrastructure",
    "payments",
    "email",
    "external_apis",
    "internal_apis",
)


@dataclass(frozen=True)
class CapabilityDef:
    id:           Capability
    label:        str             # Human label for the wizard checkbox.
    blurb:        str             # One-liner explaining the capability.
    example_tool: str             # e.g. "kubectl delete", "transfer_money"
    risk_weight:  int             # 0–100; aggregated to derive risk_level.
    policies:     tuple[str, ...] # Signal-registry IDs auto-enabled.


# Single declaration block. Adding a new capability is one entry; no
# wizard/UI/Rego files need updating in parallel.
_DEFS: tuple[CapabilityDef, ...] = (
    CapabilityDef(
        id="filesystem",
        label="Filesystem (read/write)",
        blurb="Read or write files on the host disk.",
        example_tool="read_file / write_file",
        risk_weight=35,
        policies=(
            "path_traversal_detected",
            "credential_artifact_write",
            "credential_file_read",
        ),
    ),
    CapabilityDef(
        id="database",
        label="Database (SQL)",
        blurb="Run SQL queries or mutations against tenant databases.",
        example_tool="query_database",
        risk_weight=55,
        policies=(
            "sql_injection_detected",
            "mass_pii_export",
            "drop_table_detected",
            "no_limit_dump",
        ),
    ),
    CapabilityDef(
        id="infrastructure",
        label="Infrastructure (K8s / Terraform / Cloud)",
        blurb="Control-plane access — kubectl, terraform, cloud APIs.",
        example_tool="kubectl / terraform",
        risk_weight=80,
        policies=(
            "k8s_prod_destruction",
            "terraform_destroy_prod",
            "iam_grant_admin",
            "cloud_resource_delete_prod",
        ),
    ),
    CapabilityDef(
        id="payments",
        label="Payments (money movement)",
        blurb="Wire transfers, refunds, treasury operations.",
        example_tool="transfer_money / wire_transfer",
        risk_weight=95,
        policies=(
            "wire_above_hard_cap",
            "wire_external_escalate",
            "money_movement_above_threshold",
        ),
    ),
    CapabilityDef(
        id="email",
        label="Email (outbound)",
        blurb="Send email on behalf of users / the company.",
        example_tool="send_email",
        risk_weight=40,
        policies=(
            "external_email_pii_recipient_unverified",
            "phishing_pattern_detected",
        ),
    ),
    CapabilityDef(
        id="external_apis",
        label="External APIs (HTTP outbound)",
        blurb="HTTP calls / webhooks to non-tenant services.",
        example_tool="http_request",
        risk_weight=45,
        policies=(
            "external_post_pii_egress",
            "data_exfil_to_unknown_host",
            "transfer_sh_egress",
        ),
    ),
    CapabilityDef(
        id="internal_apis",
        label="Internal APIs (service mesh)",
        blurb="RPC to other tenant-internal services.",
        example_tool="internal_rpc / service_call",
        risk_weight=20,
        policies=(
            "identity_table_write",
        ),
    ),
)

_BY_ID: dict[str, CapabilityDef] = {d.id: d for d in _DEFS}


def all_definitions() -> tuple[CapabilityDef, ...]:
    """Return every capability — wizard UI reads this for the checkbox grid."""
    return _DEFS


def get(cap_id: str) -> CapabilityDef | None:
    return _BY_ID.get(cap_id)


def policies_for(capabilities: list[str]) -> list[str]:
    """Return the deduped, sorted policy set for a capability selection."""
    out: set[str] = set()
    for cap_id in capabilities:
        d = _BY_ID.get(cap_id)
        if d is None:
            continue
        out.update(d.policies)
    return sorted(out)


def derive_risk_level(capabilities: list[str]) -> str:
    """Map the picked capabilities to the legacy low/medium/high knob.

    The legacy /agents schema still requires this string; we derive it
    from the max risk_weight of the selected capabilities so a CISO who
    ticks 'payments' lands on 'high' without having to read a tooltip.

    Empty selection → low (no ambient capability is granted).
    """
    weights = [
        _BY_ID[c].risk_weight for c in capabilities if c in _BY_ID
    ]
    if not weights:
        return "low"
    top = max(weights)
    if top >= 70:
        return "high"
    if top >= 40:
        return "medium"
    return "low"


def derive_risk_score_pct(capabilities: list[str]) -> int:
    """Return 0–100 aggregate risk score so the wizard can show a
    progress bar above the checkbox grid as the operator ticks more
    capabilities. Capped at 100; uses max + half of the sum of the
    rest so two medium capabilities don't read as low-risk."""
    weights = sorted(
        (_BY_ID[c].risk_weight for c in capabilities if c in _BY_ID),
        reverse=True,
    )
    if not weights:
        return 0
    score = weights[0] + sum(w // 2 for w in weights[1:])
    return min(100, score)
