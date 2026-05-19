"""
Identity Graph + Trust + Compromise + Drift — REST API
======================================================
GET    /graph/agents                — graph nodes (filtered by tenant)
GET    /graph/agent/{id}            — single node + neighbors
GET    /graph/blast-radius/{id}     — reachable nodes, bounded BFS
GET    /graph/risky-paths           — top-N high-risk edges
GET    /graph/trust-boundaries      — tenant + org boundary view
GET    /graph/runtime-relationships — recent edges (last 1 h default)
GET    /graph/trust/{id}            — trust score + history
GET    /graph/drift                 — recent drift signals
POST   /graph/compromise/simulate   — Feature 5: blast simulation
GET    /graph/compromise/history    — past simulations
"""
from __future__ import annotations

import uuid
from datetime import UTC
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from sdk.common.auth import verify_internal_secret
from sdk.common.db import get_db, get_tenant_id
from sdk.common.response import APIResponse
from services.identity_graph.repository import GraphRepository
from services.identity_graph.schemas import (
    BlastRadiusOut,
    CompromiseOut,
    CompromiseRequest,
    DriftOut,
    EdgeCreate,
    EdgeOut,
    GraphOut,
    NodeCreate,
    NodeOut,
    TrustScoreOut,
)
from services.identity_graph.trust_engine import compute_tenant_trust

router = APIRouter(
    prefix="/graph",
    tags=["identity_graph"],
    dependencies=[Depends(verify_internal_secret)],
)


@router.get("/agents", response_model=APIResponse[GraphOut])
async def list_agents(
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(500, ge=1, le=2000),
) -> APIResponse[GraphOut]:
    repo = GraphRepository(db)
    nodes = await repo.list_nodes(tenant_id, limit=limit)
    edges = await repo.list_edges(tenant_id, limit=limit * 2)
    return APIResponse(data=GraphOut(
        nodes=[NodeOut.model_validate(n) for n in nodes],
        edges=[EdgeOut.model_validate(e) for e in edges],
    ))


@router.post("/nodes", response_model=APIResponse[NodeOut], status_code=201)
async def create_node(
    payload: NodeCreate,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> APIResponse[NodeOut]:
    repo = GraphRepository(db)
    node = await repo.upsert_node(
        tenant_id=tenant_id,
        node_type=payload.node_type,
        external_id=payload.external_id,
        name=payload.name,
        attributes=payload.attributes,
    )
    return APIResponse(data=NodeOut.model_validate(node))


@router.post("/edges", response_model=APIResponse[EdgeOut], status_code=201)
async def create_edge(
    payload: EdgeCreate,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> APIResponse[EdgeOut]:
    repo = GraphRepository(db)
    edge = await repo.add_edge(
        tenant_id=tenant_id,
        src_node_id=payload.src_node_id,
        dst_node_id=payload.dst_node_id,
        edge_type=payload.edge_type,
        action=payload.action,
        outcome=payload.outcome,
        risk_score=payload.risk_score,
        request_id=payload.request_id,
    )
    return APIResponse(data=EdgeOut.model_validate(edge))


@router.get("/agent/{node_id}", response_model=APIResponse[GraphOut])
async def get_agent(
    node_id: uuid.UUID,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> APIResponse[GraphOut]:
    repo = GraphRepository(db)
    node = await repo.get_node(tenant_id, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found")
    out_edges, in_edges = await repo.neighbors(tenant_id, node_id)
    edges = out_edges + in_edges
    neighbor_ids = {node.id, *{e.dst_node_id for e in out_edges}, *{e.src_node_id for e in in_edges}}
    nodes = []
    for nid in neighbor_ids:
        n = await repo.get_node(tenant_id, nid)
        if n is not None:
            nodes.append(n)
    return APIResponse(data=GraphOut(
        nodes=[NodeOut.model_validate(n) for n in nodes],
        edges=[EdgeOut.model_validate(e) for e in edges],
    ))


@router.get("/blast-radius/{node_id}", response_model=APIResponse[BlastRadiusOut])
async def get_blast_radius(
    node_id: uuid.UUID,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    depth: int = Query(3, ge=1, le=6),
) -> APIResponse[BlastRadiusOut]:
    repo = GraphRepository(db)
    actor = await repo.get_node(tenant_id, node_id)
    if actor is None:
        raise HTTPException(status_code=404, detail="Actor node not found")
    visited, edges = await repo.blast_radius(tenant_id, node_id, depth)
    nodes = []
    risk_total = 0.0
    for nid in visited:
        n = await repo.get_node(tenant_id, nid)
        if n is not None:
            nodes.append(n)
            risk_total += (1.0 - float(n.trust_score))
    risk_score = round(min(1.0, risk_total / max(1, len(nodes))), 4)
    # Any reachable CRITICAL node means attacker can reach production assets —
    # floor the score at MEDIUM (0.4) so the classification is never misleadingly LOW.
    has_critical = any((n.attributes or {}).get("critical", False) for n in nodes)
    if has_critical and risk_score < 0.4:
        risk_score = 0.4
    affected_resources = sum(1 for n in nodes if n.node_type in ("resource", "tool"))
    return APIResponse(data=BlastRadiusOut(
        actor=NodeOut.model_validate(actor),
        depth=depth,
        reachable_nodes=[NodeOut.model_validate(n) for n in nodes],
        edges_traversed=[EdgeOut.model_validate(e) for e in edges],
        affected_resources=affected_resources,
        risk_score=risk_score,
    ))


@router.get("/risky-paths", response_model=APIResponse[list[EdgeOut]])
async def risky_paths(
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(50, ge=1, le=500),
) -> APIResponse[list[EdgeOut]]:
    from sqlalchemy import desc, select

    from services.identity_graph.models import GraphEdge
    stmt = (
        select(GraphEdge)
        .where(GraphEdge.tenant_id == tenant_id)
        .order_by(desc(GraphEdge.risk_score), desc(GraphEdge.occurred_at))
        .limit(limit)
    )
    edges = list((await db.execute(stmt)).scalars().all())
    return APIResponse(data=[EdgeOut.model_validate(e) for e in edges])


@router.get("/trust-boundaries", response_model=APIResponse[dict])
async def trust_boundaries(
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> APIResponse[dict]:
    repo = GraphRepository(db)
    nodes = await repo.list_nodes(tenant_id, limit=2000)
    scores = [float(n.trust_score) for n in nodes]
    tenant_score = compute_tenant_trust(scores)
    by_type: dict[str, dict] = {}
    for n in nodes:
        bucket = by_type.setdefault(n.node_type, {"count": 0, "trust_sum": 0.0})
        bucket["count"] += 1
        bucket["trust_sum"] += float(n.trust_score)
    for bucket in by_type.values():
        bucket["avg_trust"] = round(bucket["trust_sum"] / max(1, bucket["count"]), 4)
        del bucket["trust_sum"]
    return APIResponse(data={
        "tenant_id": str(tenant_id),
        "tenant_trust_score": tenant_score,
        "by_node_type": by_type,
    })


@router.get("/runtime-relationships", response_model=APIResponse[list[EdgeOut]])
async def runtime_relationships(
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    minutes: int = Query(60, ge=1, le=2880),
    limit: int = Query(500, ge=1, le=5000),
) -> APIResponse[list[EdgeOut]]:
    from datetime import datetime, timedelta

    from sqlalchemy import desc, select

    from services.identity_graph.models import GraphEdge
    since = datetime.now(tz=UTC) - timedelta(minutes=minutes)
    stmt = (
        select(GraphEdge)
        .where(GraphEdge.tenant_id == tenant_id, GraphEdge.occurred_at >= since)
        .order_by(desc(GraphEdge.occurred_at))
        .limit(limit)
    )
    edges = list((await db.execute(stmt)).scalars().all())
    return APIResponse(data=[EdgeOut.model_validate(e) for e in edges])


@router.get("/trust/{node_id}", response_model=APIResponse[dict])
async def get_trust(
    node_id: uuid.UUID,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(100, ge=1, le=500),
) -> APIResponse[dict]:
    repo = GraphRepository(db)
    node = await repo.get_node(tenant_id, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found")
    hist = await repo.trust_history(tenant_id, node_id, limit=limit)
    return APIResponse(data={
        "node": NodeOut.model_validate(node).model_dump(),
        "history": [TrustScoreOut.model_validate(h).model_dump() for h in hist],
    })


@router.get("/drift", response_model=APIResponse[list[DriftOut]])
async def list_drift(
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    minutes: int = Query(1440, ge=1, le=43200),
    limit: int = Query(200, ge=1, le=1000),
) -> APIResponse[list[DriftOut]]:
    repo = GraphRepository(db)
    signals = await repo.list_drift(tenant_id, since_minutes=minutes, limit=limit)
    return APIResponse(data=[DriftOut.model_validate(s) for s in signals])


@router.post("/compromise/simulate", response_model=APIResponse[CompromiseOut])
async def simulate_compromise(
    payload: CompromiseRequest,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> APIResponse[CompromiseOut]:
    repo = GraphRepository(db)
    actor = await repo.get_node(tenant_id, payload.actor_node_id)
    if actor is None:
        raise HTTPException(status_code=404, detail="Actor node not found")
    visited, edges = await repo.blast_radius(tenant_id, payload.actor_node_id, payload.depth)
    reachable: list[dict] = []
    affected_tenants_set: set[str] = set()
    risk_acc = 0.0
    for nid in visited:
        n = await repo.get_node(tenant_id, nid)
        if not n:
            continue
        reachable.append({
            "id": str(n.id),
            "type": n.node_type,
            "name": n.name,
            "trust_score": float(n.trust_score),
        })
        affected_tenants_set.add(str(n.tenant_id))
        risk_acc += (1.0 - float(n.trust_score))
    blast = len(reachable)
    risk_score = round(min(1.0, risk_acc / max(1, blast)), 4)

    # Scenario-specific risk multipliers
    scenario_weights = {
        "stolen_token":         1.0,
        "rogue_agent":          1.2,
        "prompt_injection":     0.9,
        "malicious_tool":       1.1,
        "lateral_movement":     1.3,
        "runaway_autonomy":     1.5,
    }
    risk_score = round(min(1.0, risk_score * scenario_weights.get(payload.scenario, 1.0)), 4)

    summary = {
        "edges_traversed": len(edges),
        "scenario": payload.scenario,
        "actor_type": actor.node_type,
        "actor_name": actor.name,
        "risk_classification": (
            "CRITICAL" if risk_score >= 0.8 else
            "HIGH"     if risk_score >= 0.6 else
            "MEDIUM"   if risk_score >= 0.4 else
            "LOW"
        ),
    }

    sim = await repo.record_simulation(
        tenant_id=tenant_id,
        actor_node_id=payload.actor_node_id,
        scenario=payload.scenario,
        depth=payload.depth,
        reachable_nodes=reachable,
        affected_tenants=list(affected_tenants_set),
        blast_radius=blast,
        risk_score=risk_score,
        summary=summary,
        started_by=None,
    )
    return APIResponse(data=CompromiseOut.model_validate(sim))


@router.get("/compromise/history", response_model=APIResponse[list[CompromiseOut]])
async def compromise_history(
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(50, ge=1, le=500),
) -> APIResponse[list[CompromiseOut]]:
    repo = GraphRepository(db)
    sims = await repo.list_simulations(tenant_id, limit=limit)
    return APIResponse(data=[CompromiseOut.model_validate(s) for s in sims])
