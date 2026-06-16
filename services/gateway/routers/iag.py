"""Sprint 5 — Identity & Access Graph + Blast Radius read API.

Routes:

  GET /iag/agents/{agent_id}
       accessible resources for one agent, with sensitivity-weighted
       criticality score. Useful for "what could this agent reach?"
       analysis even outside an incident.

  GET /iag/incidents/{incident_id}/blast-radius
       combines the Sprint 4 storyline (touched resources) with the IAG
       (accessible resources) to produce the BlastRadius dataclass —
       what was prevented vs. what was actually touched.

Both routes require the tenant JWT (the gateway auth middleware does this
before the request lands here). Resource sensitivity comes from the IAG
cache; if no ingestion has run yet, the response carries an empty graph
and a `last_ingest_ts=0` so the caller can spot the gap.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from redis.asyncio import Redis

from sdk.common.config import settings
from sdk.common.redis import get_redis_client
from services.security.iag import graph as iag_graph
from services.security.iag import store as iag_store
from services.security.incidents import store as incident_store

router = APIRouter()

_redis: Redis = get_redis_client(settings.REDIS_URL, decode_responses=False)


def _tenant_id(request: Request) -> str:
    tid = getattr(request.state, "tenant_id", "") or request.headers.get("X-Tenant-ID", "")
    if not tid:
        raise HTTPException(status_code=401, detail="tenant_id missing on request")
    return str(tid)


_TACTIC_NAMES: dict[str, str] = {
    "TA0001": "Initial Access",
    "TA0002": "Execution",
    "TA0003": "Persistence",
    "TA0004": "Privilege Escalation",
    "TA0005": "Defense Evasion",
    "TA0006": "Credential Access",
    "TA0007": "Discovery",
    "TA0008": "Lateral Movement",
    "TA0009": "Collection",
    "TA0010": "Exfiltration",
    "TA0011": "Command and Control",
    "TA0040": "Impact",
}


@router.get("/iag/mitre-coverage", tags=["IAG"])
async def get_mitre_coverage(request: Request) -> Any:
    """Sprint 7 — MITRE ATT&CK coverage matrix.

    Returns the 34 SignalDefinition entries grouped by tactic →
    technique → list of signals. The frontend's MitreCoverageGrid
    renders the result as a 12-tactic heatmap; cell colour comes from
    the max severity within that technique's signals.

    No DB call: the registry is module-level and immutable after
    import. Endpoint is JWT-gated by the gateway middleware, so we
    only enforce ``tenant_id`` is present (so the response can carry
    audit metadata if the caller is curious).
    """
    _ = _tenant_id(request)  # enforces JWT but result not used

    from services.security import signal_registry as _sr

    by_tactic: dict[str, dict[str, Any]] = {}
    for sig in _sr.all_signals():
        tactic_id = sig.mitre_tactic
        technique_id = sig.mitre_technique_id
        technique_name = sig.mitre_technique[len(technique_id) + 1:] if " " in sig.mitre_technique else sig.mitre_technique

        if tactic_id not in by_tactic:
            by_tactic[tactic_id] = {
                "tactic_id":   tactic_id,
                "tactic_name": _TACTIC_NAMES.get(tactic_id, tactic_id),
                "techniques":  {},
            }
        techniques = by_tactic[tactic_id]["techniques"]
        if technique_id not in techniques:
            techniques[technique_id] = {
                "technique_id":   technique_id,
                "technique_name": technique_name,
                "signals":        [],
                "max_severity":   "",
                "max_score":      0,
            }
        techniques[technique_id]["signals"].append(
            {
                "id":               sig.id,
                "severity":         sig.severity.value if hasattr(sig.severity, "value") else str(sig.severity),
                "default_score":    sig.default_score,
                "default_response": sig.default_response,
                "description":      sig.description,
            },
        )
        if sig.default_score > techniques[technique_id]["max_score"]:
            techniques[technique_id]["max_score"] = sig.default_score
            techniques[technique_id]["max_severity"] = (
                sig.severity.value if hasattr(sig.severity, "value") else str(sig.severity)
            )

    # Collapse technique dicts to lists for stable iteration order.
    tactics = []
    for tactic_id in sorted(by_tactic.keys()):
        block = by_tactic[tactic_id]
        techniques_list = sorted(block["techniques"].values(), key=lambda t: t["technique_id"])
        tactics.append(
            {
                "tactic_id":      tactic_id,
                "tactic_name":    block["tactic_name"],
                "technique_count": len(techniques_list),
                "signal_count":   sum(len(t["signals"]) for t in techniques_list),
                "techniques":     techniques_list,
            },
        )

    return {
        "tactics":     tactics,
        "signal_total": sum(t["signal_count"] for t in tactics),
        "tactic_total": len(tactics),
    }


@router.get("/iag/agents/{agent_id}", tags=["IAG"])
async def get_agent_iag(agent_id: str, request: Request) -> Any:
    """Accessible-resources view for one agent.

    Returns the BlastRadius shape with `touched_resources=[]` — i.e. every
    accessible resource is in the `untouched` slice. Useful for "what
    could this agent ever reach?" baselining outside an incident.
    """
    tenant_id = _tenant_id(request)
    if not agent_id:
        raise HTTPException(status_code=400, detail="agent_id required")
    agent_roles, role_perms, perm_resources, resource_meta = await iag_store.load_graph(
        _redis, tenant_id, agent_id,
    )
    br = iag_graph.compute_blast_radius(
        agent_id=agent_id,
        incident_id="",
        touched_resources=set(),
        agent_roles=agent_roles,
        role_perms=role_perms,
        perm_resources=perm_resources,
        resource_meta=resource_meta,
    )
    last_ingest = await iag_store.get_last_ingest_ts(_redis, tenant_id)
    out = br.to_dict()
    out["last_ingest_ts"] = last_ingest
    return out


@router.get("/iag/incidents/{incident_id}/blast-radius", tags=["IAG"])
async def get_blast_radius(incident_id: str, request: Request) -> Any:
    """Blast-radius view for one (incident, agent) pair.

    The agent is the *primary* agent on the storyline (the one that
    opened the incident — when a cross-agent kill chain spans multiple
    agents we union their touched-resources before computing).
    """
    tenant_id = _tenant_id(request)
    if not incident_id or not incident_id.startswith("INC-"):
        raise HTTPException(status_code=400, detail="incident_id must look like INC-…")
    s = await incident_store.get(_redis, tenant_id, incident_id)
    if s is None:
        raise HTTPException(status_code=404, detail=f"incident {incident_id} not found")
    agent_ids = list(s.participating_agents)
    if not agent_ids:
        raise HTTPException(
            status_code=409,
            detail=f"incident {incident_id} has no participating agents — IAG cannot compute blast radius",
        )
    primary_agent = agent_ids[0]
    # Touched resources = union of every step's target value (Sprint 4
    # Steps already capture this — the recorder writes it into the
    # storyline JSON as `step.target`).
    touched: set[str] = {step.target for step in s.steps if step.target}
    agent_roles, role_perms, perm_resources, resource_meta = await iag_store.load_graph(
        _redis, tenant_id, primary_agent,
    )
    # For cross-agent stories, union every participating agent's roles.
    for other in agent_ids[1:]:
        a_roles, a_role_perms, a_perm_resources, a_meta = await iag_store.load_graph(
            _redis, tenant_id, other,
        )
        agent_roles |= a_roles
        # Merge dicts — second-write-wins is fine, the sets are content-
        # addressable.
        role_perms.update(a_role_perms)
        perm_resources.update(a_perm_resources)
        resource_meta.update(a_meta)

    br = iag_graph.compute_blast_radius(
        agent_id=primary_agent,
        incident_id=incident_id,
        touched_resources=touched,
        agent_roles=agent_roles,
        role_perms=role_perms,
        perm_resources=perm_resources,
        resource_meta=resource_meta,
    )
    last_ingest = await iag_store.get_last_ingest_ts(_redis, tenant_id)
    out = br.to_dict()
    out["last_ingest_ts"] = last_ingest
    out["participating_agents"] = agent_ids
    return out
