"""ATF v3.2 §6.3 — verdict engine.

Pure function. Given a claim + expected evidence types + observed
events, produce exactly one verdict:

    CORROBORATED — every expected evidence type is present AND for
                   `api` observations any observed status codes are 2xx
                   (or the claim itself is an error claim)
    CONTRADICTED — an `api` observation shows non-2xx while the claim
                   didn't call for it; OR at least one probe fired an
                   event whose detail conflicts with the claim (kept
                   minimal at Phase 1b — expandable per action class)
    UNOBSERVED   — no observations at all, or the Witness has
                   flagged itself degraded upstream

Kept small deliberately. The refinement point when eBPF probes get
richer is inside `_matches_claim` and `_is_contradicted`.
"""
from __future__ import annotations

from services.witness.schemas import (
    Observation,
    ObservationType,
    VerdictRequest,
    WitnessVerdict,
)


def evaluate(
    req: VerdictRequest,
    observations: list[Observation],
    witness_degraded: bool = False,
) -> WitnessVerdict:
    if witness_degraded or not observations:
        return "UNOBSERVED"

    types_seen: set[ObservationType] = {o.type for o in observations}
    missing_expected = set(req.expected_evidence) - types_seen
    if missing_expected:
        # C1: any evidence type suffices; C2/C3: all expected required.
        if req.action_class == "C1" and types_seen:
            pass
        else:
            return "UNOBSERVED"

    for obs in observations:
        if _is_contradicted(obs, req):
            return "CONTRADICTED"

    return "CORROBORATED"


def _is_contradicted(obs: Observation, req: VerdictRequest) -> bool:
    """Minimal contradiction rules — extend per action class as probes mature."""
    if obs.type == "api":
        # An api tap that saw non-2xx while the claim expected success = contradicted.
        status = obs.extra.get("status_code")
        if isinstance(status, int) and (status < 200 or status >= 300):
            claim_lower = req.claim.lower()
            if not any(w in claim_lower for w in ("fail", "error", "reject", "abort")):
                return True
    return False
