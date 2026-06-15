"""Sprint 6 — RemediationAction value type.

One row per fired action. Persisted into the per-incident ledger so the
SOC can replay the response chronologically and prove compliance with
DPDP §8(8) "breach detection & response".
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


# Action kinds — strings rather than an enum so they round-trip cleanly
# through JSON without a custom encoder, and so new adapters can land in
# Sprint 7 without breaking serialised history.
KIND_REVOKE_API_KEY    = "revoke_api_key"
KIND_KILL_ACTIVE_TOKENS = "kill_active_tokens"
KIND_PAGE_ONCALL       = "page_oncall"
KIND_AUDIT_LOG         = "audit_log"

# Status values
STATUS_DONE    = "done"
STATUS_FAILED  = "failed"
STATUS_SKIPPED = "skipped"   # policy disabled this action


@dataclass(frozen=True)
class RemediationAction:
    """One action fired (or attempted) on behalf of an incident."""
    incident_id: str
    tenant_id:   str
    agent_id:    str
    kind:        str       # one of KIND_* constants
    status:      str       # one of STATUS_* constants
    result:      str       # human-readable; on failure this is the error message
    ts:          float     # unix seconds

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
