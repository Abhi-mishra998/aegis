"""
Sprint 3 — Security Objectives.

Each module owns the detection logic for ONE MITRE ATT&CK tactic. The
orchestrator in services/policy/canonical.py calls every module's
detect() once per /execute and unions the resulting finding lists.

Contract for each module:

    def detect(canonical: dict) -> list[str]:
        '''Pure function. Reads only the fields on `canonical`. Emits
        registered signal-IDs from services.security.signal_registry.
        Returns deduped list (orchestrator unions anyway).'''

Adding a new detector:
    1. Register the signal in services/security/signal_registry.py.
    2. Implement the rule in the correct objective module here.
    3. Add a unit test in tests/security/test_objectives.py.

No cross-objective imports. No I/O. No mutation of `canonical`.
"""
from . import (
    collection,
    credential_access,
    defense_evasion,
    discovery,
    exfiltration,
    impact,
    initial_access,
    persistence,
    privilege_escalation,
)


# Public surface — the orchestrator iterates this tuple.
# Order does NOT matter for correctness (orchestrator dedupes) but is kept
# stable for log readability + deterministic test output.
DETECTORS = (
    initial_access,
    persistence,
    privilege_escalation,
    defense_evasion,
    credential_access,
    discovery,
    collection,
    exfiltration,
    impact,
)


__all__ = [
    "DETECTORS",
    "collection",
    "credential_access",
    "defense_evasion",
    "discovery",
    "exfiltration",
    "impact",
    "initial_access",
    "persistence",
    "privilege_escalation",
]
