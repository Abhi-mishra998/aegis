"""FastAPI router for the Execution Witness."""
from __future__ import annotations

import os
import time
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from sdk.common.auth import verify_internal_secret
from sdk.common.response import APIResponse
from services.witness import store
from services.witness.analytics import (
    AnalyticsInputTooLarge,
    VerdictRecord,
    aggregate,
    top_offending_tools,
)
from services.witness.auto_lockout import VerdictSummary, apply_verdict
from services.witness.reconciliation import (
    GateDecisionRow,
    WitnessVerdictRow,
    reconcile,
    synthesize_unobserved_verdicts,
)
from services.witness.schemas import Attestation, Observation, VerdictRequest
from services.witness.signer import get_signer
from services.witness.verdict import evaluate

router = APIRouter(prefix="/witness", tags=["witness"], dependencies=[Depends(verify_internal_secret)])

# Health endpoint sits outside the mesh-auth gate so the Docker HEALTHCHECK
# (which cannot mint a mesh JWT) can probe it. Container was flapping
# unhealthy every 30s (2026-07-25) with `403 Forbidden` on /witness/health.
health_router = APIRouter(prefix="/witness", tags=["witness"])

# Missing-heartbeat threshold — beyond this, all verdicts flip to
# UNOBSERVED and the agent state falls to RESTRICTED (per §11).
_HEARTBEAT_STALE_SECONDS = 30

# ATF Appendix D.1 — serverless agents have no co-located Witness, so
# every verdict is UNOBSERVED regardless of what observations happen
# to be in the store. Enforcing this at the router level prevents a
# misconfigured serverless deployment from silently reporting
# CORROBORATED verdicts based on stale/leaked observations.
#
# Accepted values: "sidecar" (default) | "serverless".
_DEPLOYMENT_MODE = (os.getenv("WITNESS_DEPLOYMENT_MODE", "sidecar") or "sidecar").lower()
if _DEPLOYMENT_MODE not in ("sidecar", "serverless"):
    _DEPLOYMENT_MODE = "sidecar"


@router.post("/observations")
async def record_observation(obs: Observation) -> APIResponse[dict]:
    await store.record(obs)
    return APIResponse(data={"recorded": obs.gate_decision_id})


@router.post("/heartbeat/{witness_id}")
async def heartbeat(witness_id: str) -> APIResponse[dict]:
    await store.heartbeat(witness_id, time.time())
    return APIResponse(data={"witness_id": witness_id, "ts": time.time()})


@router.post("/verdict", response_model=None)
async def render_verdict(req: VerdictRequest) -> APIResponse[Attestation]:
    signer = get_signer()
    # ATF D.1: serverless deployments have no co-located Witness; every
    # verdict is UNOBSERVED regardless of what's in the store. Sidecar
    # deployments use the standard heartbeat-staleness check.
    if _DEPLOYMENT_MODE == "serverless":
        degraded = True
        obs: list[Observation] = []
    else:
        last = await store.last_heartbeat(signer.witness_id)
        degraded = last is None or (time.time() - last) > _HEARTBEAT_STALE_SECONDS
        obs = await store.fetch(req.gate_decision_id)
    verdict = evaluate(req, obs, witness_degraded=degraded)

    evidence: list[dict[str, Any]] = [
        {"type": o.type, "detail": o.detail, "ts": o.ts,
         **({"payload_hash": o.payload_hash} if o.payload_hash else {}),
         **({"extra": o.extra} if o.extra else {})}
        for o in obs
    ]

    attestation = signer.sign(
        gate_decision_id=req.gate_decision_id,
        claim=req.claim,
        verdict=verdict,
        evidence=evidence,
    )
    return APIResponse(data=attestation)


@health_router.get("/health")
async def health() -> APIResponse[dict]:
    signer = get_signer()
    last = await store.last_heartbeat(signer.witness_id)
    stale = last is None or (time.time() - last) > _HEARTBEAT_STALE_SECONDS
    return APIResponse(data={
        "witness_id":         signer.witness_id,
        "fingerprint":        signer.fingerprint,
        "last_heartbeat_ts":  last,
        "stale":              stale,
        # ATF §11 — surface degradation honestly. Ops sees whether the
        # deployment is running shared-Redis or degraded single-process.
        "store_backend":      store.get_backend_kind(),
        # ATF Appendix D.1 — serverless deployments emit UNOBSERVED for
        # every verdict. Surfacing the mode on /health prevents a
        # misconfigured deploy from silently claiming shared coverage.
        "deployment_mode":    _DEPLOYMENT_MODE,
    })


@router.get("/public-key")
async def public_key() -> APIResponse[dict]:
    signer = get_signer()
    return APIResponse(data={"pem": signer.public_key_pem, "fingerprint": signer.fingerprint})


# ─────────────────────────────────────────────────────────────
# ATF §Phase 3 analytics + §6.3 auto-lockout + §12.3 I1 reconciliation.
# All three consume Witness verdicts + surface aggregated results.
# ─────────────────────────────────────────────────────────────


class _AnalyticsRequest(BaseModel):
    records: list[VerdictRecord] = Field(default_factory=list)


@router.post("/analytics")
async def analytics(req: _AnalyticsRequest) -> APIResponse[dict]:
    """§Phase 3 item 1 — per-tenant/agent/tool contradiction analytics +
    SOC triage queue. Caller passes the verdict window they want rolled
    up; SOC dashboards call this every N seconds."""
    from fastapi import HTTPException
    try:
        snap = aggregate(req.records)
    except AnalyticsInputTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    return APIResponse(data={
        "per_tenant": {t: s.__dict__ for t, s in snap.per_tenant.items()},
        "triage": [
            {
                "tenant_id": e.tenant_id,
                "agent_id":  e.agent_id,
                "tool":      e.tool,
                "score":     e.triage_score,
                "stats":     e.stats.__dict__,
            }
            for e in snap.triage
        ],
        "top_tools": top_offending_tools(snap),
    })


class _LockoutRequest(BaseModel):
    agent_id: str
    current_state: str
    verdict: str
    gate_decision_id: str
    summary: VerdictSummary


@router.post("/lockout")
async def lockout(req: _LockoutRequest) -> APIResponse[dict]:
    """§6.3 + §9.1 — given the Witness view of one agent, decide if the
    registry should transition its state. Registry-side subscriber calls
    this on every verdict and persists the returned change (or None)."""
    change = apply_verdict(
        agent_id=req.agent_id,
        current_state=req.current_state,  # type: ignore[arg-type]
        verdict=req.verdict,               # type: ignore[arg-type]
        gate_decision_id=req.gate_decision_id,
        summary=req.summary,
    )
    return APIResponse(data={
        "state_change": None if change is None else change.__dict__,
    })


class _ReconcileRequest(BaseModel):
    gate_decisions: list[GateDecisionRow] = Field(default_factory=list)
    verdicts: list[WitnessVerdictRow] = Field(default_factory=list)


@router.post("/reconcile")
async def reconcile_endpoint(req: _ReconcileRequest) -> APIResponse[dict]:
    """§12.3 I1 — gap between Gate decisions and Witness verdicts. Ops
    scheduler calls this on a cron; a `c2_c3_match_ratio < 1.0` result
    IS the SLO breach and should page."""
    report = reconcile(req.gate_decisions, req.verdicts)
    return APIResponse(data={
        "total_gate_decisions":  report.total_gate_decisions,
        "total_c2_c3":           report.total_c2_c3,
        "matched":               report.matched,
        "unmatched_ids":         report.unmatched_ids,
        "unmatched_c2_c3_ids":   report.unmatched_c2_c3_ids,
        "match_ratio":           report.match_ratio,
        "c2_c3_match_ratio":     report.c2_c3_match_ratio,
        "synthesized_unobserved": [
            v.__dict__ for v in synthesize_unobserved_verdicts(report)
        ],
    })
