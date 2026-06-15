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
