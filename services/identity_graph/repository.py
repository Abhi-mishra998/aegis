"""
Repository for identity_graph. All graph reads/writes go through here so
the caller never touches SQL directly. Tenant scoping is enforced at this
layer — no method takes a `tenant_id=None` shortcut.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from services.identity_graph.models import (
    CompromiseSimulation,
    DriftSignal,
    GraphEdge,
    GraphNode,
    TrustScoreHistory,
)


class GraphRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # NODES
    # ------------------------------------------------------------------
    async def upsert_node(
        self,
        tenant_id: uuid.UUID,
        node_type: str,
        external_id: str,
        name: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> GraphNode:
        """Idempotent insert keyed on (tenant_id, node_type, external_id)."""
        stmt = insert(GraphNode).values(
            tenant_id=tenant_id,
            org_id=tenant_id,
            node_type=node_type,
            external_id=external_id,
            name=name or external_id,
            attributes=attributes or {},
        ).on_conflict_do_update(
            index_elements=["tenant_id", "node_type", "external_id"],
            set_={"name": name or external_id, "attributes": attributes or {}},
        ).returning(GraphNode)
        result = await self.db.execute(stmt)
        await self.db.commit()
        return result.scalar_one()

    async def get_node(self, tenant_id: uuid.UUID, node_id: uuid.UUID) -> GraphNode | None:
        stmt = select(GraphNode).where(
            GraphNode.id == node_id,
            GraphNode.tenant_id == tenant_id,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list_nodes(
        self, tenant_id: uuid.UUID, node_type: str | None = None, limit: int = 500
    ) -> list[GraphNode]:
        stmt = select(GraphNode).where(GraphNode.tenant_id == tenant_id)
        if node_type:
            stmt = stmt.where(GraphNode.node_type == node_type)
        stmt = stmt.order_by(GraphNode.trust_score.asc()).limit(limit)
        return list((await self.db.execute(stmt)).scalars().all())

    # ------------------------------------------------------------------
    # EDGES
    # ------------------------------------------------------------------
    async def add_edge(
        self,
        tenant_id: uuid.UUID,
        src_node_id: uuid.UUID,
        dst_node_id: uuid.UUID,
        edge_type: str,
        action: str,
        outcome: str,
        risk_score: float = 0.0,
        request_id: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> GraphEdge:
        edge = GraphEdge(
            tenant_id=tenant_id,
            org_id=tenant_id,
            src_node_id=src_node_id,
            dst_node_id=dst_node_id,
            edge_type=edge_type,
            action=action,
            outcome=outcome,
            risk_score=risk_score,
            request_id=request_id,
            attributes=attributes or {},
        )
        self.db.add(edge)
        await self.db.commit()
        return edge

    async def list_edges(
        self, tenant_id: uuid.UUID, limit: int = 1000
    ) -> list[GraphEdge]:
        stmt = (
            select(GraphEdge)
            .where(GraphEdge.tenant_id == tenant_id)
            .order_by(desc(GraphEdge.occurred_at))
            .limit(limit)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def neighbors(
        self, tenant_id: uuid.UUID, node_id: uuid.UUID, limit: int = 200
    ) -> tuple[list[GraphEdge], list[GraphEdge]]:
        out_stmt = (
            select(GraphEdge)
            .where(GraphEdge.tenant_id == tenant_id, GraphEdge.src_node_id == node_id)
            .order_by(desc(GraphEdge.occurred_at))
            .limit(limit)
        )
        in_stmt = (
            select(GraphEdge)
            .where(GraphEdge.tenant_id == tenant_id, GraphEdge.dst_node_id == node_id)
            .order_by(desc(GraphEdge.occurred_at))
            .limit(limit)
        )
        out_edges = list((await self.db.execute(out_stmt)).scalars().all())
        in_edges = list((await self.db.execute(in_stmt)).scalars().all())
        return out_edges, in_edges

    # ------------------------------------------------------------------
    # BLAST RADIUS — iterative BFS bounded by depth
    # ------------------------------------------------------------------
    async def blast_radius(
        self, tenant_id: uuid.UUID, root_id: uuid.UUID, depth: int = 3
    ) -> tuple[set[uuid.UUID], list[GraphEdge]]:
        """
        Return all nodes reachable from `root_id` within `depth` hops, plus
        the edges traversed. Uses bounded BFS — no recursive SQL — so it
        terminates in O(depth × fan-out) and is bounded by SQL LIMIT 200.
        """
        frontier: set[uuid.UUID] = {root_id}
        visited: set[uuid.UUID] = {root_id}
        all_edges: list[GraphEdge] = []
        for _ in range(max(1, min(depth, 6))):
            if not frontier:
                break
            stmt = (
                select(GraphEdge)
                .where(
                    GraphEdge.tenant_id == tenant_id,
                    GraphEdge.src_node_id.in_(list(frontier)),
                )
                .limit(2000)
            )
            edges = list((await self.db.execute(stmt)).scalars().all())
            next_frontier: set[uuid.UUID] = set()
            for e in edges:
                all_edges.append(e)
                if e.dst_node_id not in visited:
                    next_frontier.add(e.dst_node_id)
                    visited.add(e.dst_node_id)
            frontier = next_frontier
        return visited, all_edges

    # ------------------------------------------------------------------
    # TRUST + DRIFT
    # ------------------------------------------------------------------
    async def write_trust_score(
        self,
        tenant_id: uuid.UUID,
        node_id: uuid.UUID,
        score: float,
        components: dict[str, float],
        reason: str | None = None,
    ) -> TrustScoreHistory:
        # Denormalize on node
        node = await self.get_node(tenant_id, node_id)
        if node is not None:
            node.trust_score = score
            node.last_scored_at = func.now()
        hist = TrustScoreHistory(
            tenant_id=tenant_id, org_id=tenant_id,
            node_id=node_id, score=score, components=components, reason=reason,
        )
        self.db.add(hist)
        await self.db.commit()
        return hist

    async def trust_history(
        self, tenant_id: uuid.UUID, node_id: uuid.UUID, limit: int = 100
    ) -> list[TrustScoreHistory]:
        stmt = (
            select(TrustScoreHistory)
            .where(
                TrustScoreHistory.tenant_id == tenant_id,
                TrustScoreHistory.node_id == node_id,
            )
            .order_by(desc(TrustScoreHistory.captured_at))
            .limit(limit)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def add_drift(
        self,
        tenant_id: uuid.UUID,
        node_id: uuid.UUID,
        signal_type: str,
        severity: str,
        baseline: dict[str, Any],
        observed: dict[str, Any],
        delta: float,
    ) -> DriftSignal:
        d = DriftSignal(
            tenant_id=tenant_id, org_id=tenant_id, node_id=node_id,
            signal_type=signal_type, severity=severity,
            baseline=baseline, observed=observed, delta=delta,
        )
        self.db.add(d)
        # Denormalize on node
        node = await self.get_node(tenant_id, node_id)
        if node is not None:
            node.drift_score = max(node.drift_score, delta)
        await self.db.commit()
        return d

    async def list_drift(
        self, tenant_id: uuid.UUID, since_minutes: int = 1440, limit: int = 200
    ) -> list[DriftSignal]:
        from datetime import datetime, timedelta, timezone
        since = datetime.now(tz=timezone.utc) - timedelta(minutes=since_minutes)
        stmt = (
            select(DriftSignal)
            .where(
                DriftSignal.tenant_id == tenant_id,
                DriftSignal.detected_at >= since,
            )
            .order_by(desc(DriftSignal.detected_at))
            .limit(limit)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    # ------------------------------------------------------------------
    # COMPROMISE SIMULATION
    # ------------------------------------------------------------------
    async def record_simulation(
        self,
        tenant_id: uuid.UUID,
        actor_node_id: uuid.UUID,
        scenario: str,
        depth: int,
        reachable_nodes: list[dict[str, Any]],
        affected_tenants: list[str],
        blast_radius: int,
        risk_score: float,
        summary: dict[str, Any],
        started_by: str | None,
    ) -> CompromiseSimulation:
        sim = CompromiseSimulation(
            tenant_id=tenant_id, org_id=tenant_id,
            actor_node_id=actor_node_id, scenario=scenario, depth=depth,
            reachable_nodes=reachable_nodes,
            affected_tenants=affected_tenants,
            blast_radius=blast_radius, risk_score=risk_score,
            summary=summary, started_by=started_by,
        )
        self.db.add(sim)
        await self.db.commit()
        return sim

    async def list_simulations(
        self, tenant_id: uuid.UUID, limit: int = 50
    ) -> list[CompromiseSimulation]:
        stmt = (
            select(CompromiseSimulation)
            .where(CompromiseSimulation.tenant_id == tenant_id)
            .order_by(desc(CompromiseSimulation.completed_at))
            .limit(limit)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    # ------------------------------------------------------------------
    # AGGREGATES — used by trust-score worker
    # ------------------------------------------------------------------
    async def edge_stats(
        self, tenant_id: uuid.UUID, node_id: uuid.UUID, since_minutes: int = 60
    ) -> dict[str, Any]:
        from datetime import datetime, timedelta, timezone
        since = datetime.now(tz=timezone.utc) - timedelta(minutes=since_minutes)
        stmt = (
            select(
                GraphEdge.outcome,
                func.count().label("c"),
                func.avg(GraphEdge.risk_score).label("avg_risk"),
                func.max(GraphEdge.risk_score).label("max_risk"),
            )
            .where(
                GraphEdge.tenant_id == tenant_id,
                GraphEdge.src_node_id == node_id,
                GraphEdge.occurred_at >= since,
            )
            .group_by(GraphEdge.outcome)
        )
        rows = (await self.db.execute(stmt)).all()
        result = {"total": 0, "deny": 0, "error": 0, "allow": 0, "avg_risk": 0.0, "max_risk": 0.0}
        weighted_sum = 0.0
        for r in rows:
            outcome = (r.outcome or "").lower()
            cnt = int(r.c)
            result["total"] += cnt
            if outcome in result:
                result[outcome] += cnt
            result["max_risk"] = max(result["max_risk"], float(r.max_risk or 0.0))
            weighted_sum += float(r.avg_risk or 0.0) * cnt
        if result["total"] > 0:
            result["avg_risk"] = weighted_sum / result["total"]
        return result
