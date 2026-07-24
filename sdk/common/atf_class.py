"""ATF v3.2 §3.3 — action classification C0/C1/C2/C3.

INPUT dimension, distinct from the existing 5-tier DECISION output
(allow/monitor/escalate/deny/quarantine in `services/policy/canonical.py`).

The class tells Gate/Witness/Ledger *how much scrutiny* the action needs;
the decision tells the caller *what happened*. Two orthogonal axes.

Deterministic predicate — two implementations classify identically:

    C3 if irreversible ∨ financial_value ≥ high ∨ legal_commitment
    C2 if resource_classification ≥ CONFIDENTIAL ∨ financial_value > 0
        ∨ external_communication ∨ pii_touched ∨ reversibility == HARD
    C1 if mutation ∧ reversible ∧ internal
    C0 otherwise

Tie-break: highest wins. Missing attribute → treated as the more restrictive
value (fail-toward-scrutiny), so the class only ever goes UP, never down.
"""
from __future__ import annotations

from typing import Literal, TypedDict

ActionClass = Literal["C0", "C1", "C2", "C3"]


class ActionAttrs(TypedDict, total=False):
    """Attributes drawn from tool manifest + request inspection."""

    mutation: bool
    external_communication: bool
    pii_touched: bool
    reversibility: str  # "REVERSIBLE" | "HARD" | "IRREVERSIBLE"
    financial_value: float
    resource_classification: str  # "PUBLIC" | "INTERNAL" | "CONFIDENTIAL" | "RESTRICTED"
    legal_commitment: bool


# Tunable per tenant via policy file — these are the defaults.
_HIGH_VALUE_USD = 10_000.0


def classify(attrs: ActionAttrs, high_value_usd: float = _HIGH_VALUE_USD) -> ActionClass:
    # Missing → fail toward scrutiny.
    reversibility = attrs.get("reversibility", "HARD")
    financial = float(attrs.get("financial_value", 0.0) or 0.0)
    resource = attrs.get("resource_classification", "CONFIDENTIAL")

    if (
        reversibility == "IRREVERSIBLE"
        or financial >= high_value_usd
        or attrs.get("legal_commitment", False)
    ):
        return "C3"

    if (
        resource in ("CONFIDENTIAL", "RESTRICTED")
        or financial > 0
        or attrs.get("external_communication", False)
        or attrs.get("pii_touched", False)
        or reversibility == "HARD"
    ):
        return "C2"

    if attrs.get("mutation", False):
        return "C1"

    return "C0"


if __name__ == "__main__":
    # C0 — pure read, internal
    assert classify({"mutation": False, "reversibility": "REVERSIBLE",
                     "resource_classification": "INTERNAL"}) == "C0"
    # C1 — write, reversible, internal
    assert classify({"mutation": True, "reversibility": "REVERSIBLE",
                     "resource_classification": "INTERNAL"}) == "C1"
    # C2 — external comm
    assert classify({"mutation": True, "reversibility": "REVERSIBLE",
                     "external_communication": True,
                     "resource_classification": "INTERNAL"}) == "C2"
    # C2 — PII touch
    assert classify({"pii_touched": True, "reversibility": "REVERSIBLE",
                     "resource_classification": "INTERNAL"}) == "C2"
    # C3 — irreversible
    assert classify({"reversibility": "IRREVERSIBLE"}) == "C3"
    # C3 — high-value financial
    assert classify({"financial_value": 25_000.0,
                     "reversibility": "REVERSIBLE",
                     "resource_classification": "INTERNAL"}) == "C3"
    # Missing attrs → C2 (fail toward scrutiny: default reversibility=HARD)
    assert classify({}) == "C2"
    print("atf_class OK")
