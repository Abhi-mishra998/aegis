"""
Identity Graph Worker — Stream Consumer + Trust Score + Drift Loop
==================================================================
Three responsibilities, three independent coroutines run by main.py lifespan:

  1. _graph_event_consumer
       Reads acp:graph_events Redis stream (produced by the gateway middleware)
       and translates each runtime event into:
         • upsert(src node), upsert(dst node)
         • add_edge(src → dst)
       Idempotent: gateway is required to set a request_id so duplicate
       deliveries (XCLAIM after a worker crash) do not create duplicate edges.

  2. _trust_score_loop
       Every TRUST_SCORE_INTERVAL_S, recomputes trust scores for active nodes
       using GraphRepository.edge_stats(). Persists to TrustScoreHistory and
       denormalizes onto GraphNode.

  3. _drift_loop
       Compares each agent's last-hour edge mix against its trailing-24h
       baseline. When the L1 distance over (allow, deny, error, avg_risk)
       exceeds DRIFT_THRESHOLD, emits a DriftSignal.

All loops fail closed: on Redis or DB error the loop logs CRITICAL and
sleeps RETRY_SLEEP_S before retrying — never silently exits.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker

from services.identity_graph.repository import GraphRepository
from services.identity_graph.trust_engine import compute_trust

logger = structlog.get_logger(__name__)

GRAPH_STREAM_KEY        = "acp:graph_events"
GRAPH_CONSUMER_GROUP    = "acp:identity_graph"
GRAPH_CONSUMER_NAME     = os.getenv("GRAPH_CONSUMER_NAME", "identity-graph-1")
GRAPH_DLQ_KEY           = "acp:graph_events:dlq"

TRUST_SCORE_INTERVAL_S  = int(os.getenv("TRUST_SCORE_INTERVAL_S", "30"))
DRIFT_INTERVAL_S        = int(os.getenv("DRIFT_INTERVAL_S", "120"))
DRIFT_THRESHOLD         = float(os.getenv("DRIFT_THRESHOLD", "0.35"))
RETRY_SLEEP_S           = float(os.getenv("RETRY_SLEEP_S", "2.0"))
BLOCK_MS                = 2000
BATCH_SIZE              = 100

# ATF v3.2 §Phase 3 item 2 — collusion detector.
# Weight threshold: only edges with N+ interactions in the window count
# toward community assignment. Prevents incidental one-off calls from
# collapsing unrelated agents into one cluster.
COLLUSION_INTERVAL_S      = int(os.getenv("COLLUSION_INTERVAL_S", "300"))     # every 5 min
COLLUSION_WINDOW_HOURS    = int(os.getenv("COLLUSION_WINDOW_HOURS", "24"))
COLLUSION_MIN_EDGE_WEIGHT = float(os.getenv("COLLUSION_MIN_EDGE_WEIGHT", "3"))
COLLUSION_MIN_CLUSTER     = int(os.getenv("COLLUSION_MIN_CLUSTER", "3"))       # 3+ agents = worth alerting
COLLUSION_DRIFT_THRESHOLD = float(os.getenv("COLLUSION_DRIFT_THRESHOLD", "0.6"))


async def _ensure_group(redis: Redis) -> None:
    try:
        await redis.xgroup_create(GRAPH_STREAM_KEY, GRAPH_CONSUMER_GROUP, id="0", mkstream=True)
        logger.info("graph_consumer_group_created")
    except Exception as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def _parse(fields: dict) -> dict[str, Any] | None:
    """Stream payload is one JSON blob in field 'data'."""
    try:
        raw = fields.get("data") or fields.get(b"data")
        if isinstance(raw, bytes):
            raw = raw.decode()
        return json.loads(raw)
    except Exception as exc:
        logger.error("graph_event_parse_failed", error=str(exc))
        return None


async def _ingest_event(repo: GraphRepository, ev: dict[str, Any]) -> None:
    """
    Expected event shape (the gateway publishes this):
      {
        "tenant_id":   <uuid>,
        "src_type":    "agent" | "human" | "tool",
        "src_id":      "<external id>",
        "src_name":    "<display name>",
        "dst_type":    "tool" | "resource" | "agent",
        "dst_id":      "<external id>",
        "dst_name":    "<display name>",
        "edge_type":   "invokes" | "reads" | "writes" | "delegates" | "escalates",
        "action":      "execute_tool",
        "outcome":     "allow" | "deny" | "error",
        "risk_score":  0.0..1.0,
        "request_id":  "<uuid>",
        "attributes":  { ... }
      }
    """
    try:
        tenant_id = uuid.UUID(ev["tenant_id"])
    except Exception:
        logger.warning("graph_event_no_tenant", event=str(ev)[:200])
        return

    src = await repo.upsert_node(
        tenant_id=tenant_id,
        node_type=ev.get("src_type", "agent"),
        external_id=str(ev.get("src_id") or "unknown"),
        name=ev.get("src_name") or ev.get("src_id") or "unknown",
        attributes={"role": ev.get("src_role")} if ev.get("src_role") else None,
    )
    dst = await repo.upsert_node(
        tenant_id=tenant_id,
        node_type=ev.get("dst_type", "tool"),
        external_id=str(ev.get("dst_id") or "unknown"),
        name=ev.get("dst_name") or ev.get("dst_id") or "unknown",
        attributes=None,
    )
    await repo.add_edge(
        tenant_id=tenant_id,
        src_node_id=src.id,
        dst_node_id=dst.id,
        edge_type=ev.get("edge_type", "invokes"),
        action=ev.get("action", "execute_tool"),
        outcome=ev.get("outcome", "allow"),
        risk_score=float(ev.get("risk_score") or 0.0),
        request_id=ev.get("request_id"),
        attributes=ev.get("attributes") or {},
    )


async def _graph_event_consumer(redis: Redis, session_factory: async_sessionmaker) -> None:
    await _ensure_group(redis)
    while True:
        try:
            messages = await redis.xreadgroup(
                groupname=GRAPH_CONSUMER_GROUP,
                consumername=GRAPH_CONSUMER_NAME,
                streams={GRAPH_STREAM_KEY: ">"},
                count=BATCH_SIZE,
                block=BLOCK_MS,
            )
            if not messages:
                continue
            async with session_factory() as db:
                repo = GraphRepository(db)
                to_ack: list[Any] = []
                for _, batch in messages:
                    for msg_id, fields in batch:
                        # Redis returns bytes when decode_responses=False
                        decoded = {
                            (k.decode() if isinstance(k, bytes) else k):
                            (v.decode() if isinstance(v, bytes) else v)
                            for k, v in fields.items()
                        }
                        ev = _parse(decoded)
                        if ev is None:
                            await redis.xadd(GRAPH_DLQ_KEY, {"data": json.dumps(decoded)}, maxlen=10_000, approximate=True)
                            to_ack.append(msg_id)
                            continue
                        try:
                            await _ingest_event(repo, ev)
                            to_ack.append(msg_id)
                        except Exception as exc:
                            logger.error("graph_event_ingest_failed", error=str(exc), event_id=str(msg_id))
                            await redis.xadd(GRAPH_DLQ_KEY, {"data": json.dumps(ev), "error": str(exc)[:200]}, maxlen=10_000, approximate=True)
                            to_ack.append(msg_id)
                if to_ack:
                    await redis.xack(GRAPH_STREAM_KEY, GRAPH_CONSUMER_GROUP, *to_ack)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.critical("graph_consumer_loop_error", error=str(exc))
            await asyncio.sleep(RETRY_SLEEP_S)


async def _trust_score_loop(session_factory: async_sessionmaker) -> None:
    while True:
        try:
            async with session_factory() as db:
                repo = GraphRepository(db)
                # Score agents active in the last 24 h.
                from sqlalchemy import distinct, select

                from services.identity_graph.models import GraphEdge
                since = datetime.now(tz=UTC) - timedelta(hours=24)
                stmt = (
                    select(distinct(GraphEdge.src_node_id), GraphEdge.tenant_id)
                    .where(GraphEdge.occurred_at >= since)
                    .limit(1000)
                )
                rows = (await db.execute(stmt)).all()
                for src_node_id, tenant_id in rows:
                    node = await repo.get_node(tenant_id, src_node_id)
                    if node is None:
                        continue
                    stats = await repo.edge_stats(tenant_id, src_node_id, since_minutes=60)
                    score, components, reason = compute_trust(stats, drift_score=float(node.drift_score))
                    await repo.write_trust_score(tenant_id, src_node_id, score, components, reason)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.critical("trust_score_loop_error", error=str(exc))
        await asyncio.sleep(TRUST_SCORE_INTERVAL_S)


async def _drift_loop(session_factory: async_sessionmaker) -> None:
    while True:
        try:
            async with session_factory() as db:
                repo = GraphRepository(db)
                from sqlalchemy import distinct, select

                from services.identity_graph.models import GraphEdge
                since = datetime.now(tz=UTC) - timedelta(hours=24)
                rows = (await db.execute(
                    select(distinct(GraphEdge.src_node_id), GraphEdge.tenant_id)
                    .where(GraphEdge.occurred_at >= since)
                    .limit(1000)
                )).all()
                for src_node_id, tenant_id in rows:
                    baseline = await repo.edge_stats(tenant_id, src_node_id, since_minutes=1440)
                    recent   = await repo.edge_stats(tenant_id, src_node_id, since_minutes=60)
                    if baseline["total"] < 10 or recent["total"] < 3:
                        continue
                    # Normalize and compute L1 distance over the rate vector.
                    def _vec(s: dict) -> tuple[float, float, float, float]:
                        t = max(int(s.get("total", 0)), 1)
                        return (
                            s.get("allow", 0) / t,
                            s.get("deny", 0) / t,
                            s.get("error", 0) / t,
                            float(s.get("avg_risk", 0.0) or 0.0),
                        )
                    b = _vec(baseline)
                    r = _vec(recent)
                    delta = sum(abs(a - c) for a, c in zip(b, r, strict=False))
                    if delta >= DRIFT_THRESHOLD:
                        severity = "critical" if delta >= 1.0 else "warn" if delta >= 0.6 else "info"
                        await repo.add_drift(
                            tenant_id=tenant_id,
                            node_id=src_node_id,
                            signal_type="behavior_mix_shift",
                            severity=severity,
                            baseline={
                                "allow": b[0], "deny": b[1], "error": b[2], "avg_risk": b[3],
                                "samples": baseline["total"],
                            },
                            observed={
                                "allow": r[0], "deny": r[1], "error": r[2], "avg_risk": r[3],
                                "samples": recent["total"],
                            },
                            delta=delta,
                        )
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.critical("drift_loop_error", error=str(exc))
        await asyncio.sleep(DRIFT_INTERVAL_S)


async def _collusion_loop(session_factory: async_sessionmaker) -> None:
    """ATF v3.2 §Phase 3 item 2 — periodic collusion detection.

    Every COLLUSION_INTERVAL_S:
      * Read the last-24h edges per tenant.
      * Build undirected weighted graph (weight = interaction count).
      * Run label-propagation communities with a min-edge threshold so
        incidental interactions don't collapse unrelated agents.
      * A community with ≥ COLLUSION_MIN_CLUSTER agents where at least
        one has drift ≥ COLLUSION_DRIFT_THRESHOLD gets a
        `collusion_suspicion` DriftSignal recorded.

    Fail-closed on Redis/DB errors like the sibling loops.
    """
    from collections import Counter, defaultdict

    from sqlalchemy import distinct, select

    from services.identity_graph.collusion import (
        label_propagation_communities,
    )
    from services.identity_graph.models import GraphEdge

    while True:
        try:
            async with session_factory() as db:
                repo = GraphRepository(db)
                since = datetime.now(tz=UTC) - timedelta(hours=COLLUSION_WINDOW_HOURS)

                # Per-tenant edge rollup — build the graph in memory.
                tenants = (await db.execute(
                    select(distinct(GraphEdge.tenant_id))
                    .where(GraphEdge.occurred_at >= since)
                )).scalars().all()

                for tenant_id in tenants:
                    edge_rows = (await db.execute(
                        select(GraphEdge.src_node_id, GraphEdge.dst_node_id)
                        .where(GraphEdge.tenant_id == tenant_id)
                        .where(GraphEdge.occurred_at >= since)
                    )).all()
                    if not edge_rows:
                        continue

                    weights: Counter[tuple[str, str]] = Counter()
                    for src, dst in edge_rows:
                        if src == dst:
                            continue
                        key = (str(src), str(dst)) if str(src) < str(dst) else (str(dst), str(src))
                        weights[key] += 1

                    graph: dict[str, list[tuple[str, float]]] = defaultdict(list)
                    for (a, b), w in weights.items():
                        graph[a].append((b, float(w)))
                        graph[b].append((a, float(w)))

                    if not graph:
                        continue

                    communities = label_propagation_communities(
                        dict(graph),
                        min_edge_weight=COLLUSION_MIN_EDGE_WEIGHT,
                    )

                    # Group by community id → member list.
                    members_by_cluster: dict[int, list[str]] = defaultdict(list)
                    for node_id, cluster_id in communities.items():
                        members_by_cluster[cluster_id].append(node_id)

                    for cluster_id, members in members_by_cluster.items():
                        if len(members) < COLLUSION_MIN_CLUSTER:
                            continue

                        # Any high-drift member in the cluster?
                        elevated: list[dict[str, Any]] = []
                        for node_id_str in members:
                            try:
                                node = await repo.get_node(tenant_id, uuid.UUID(node_id_str))
                            except (ValueError, AttributeError):
                                continue
                            if node is None:
                                continue
                            if float(node.drift_score or 0.0) >= COLLUSION_DRIFT_THRESHOLD:
                                elevated.append({
                                    "node_id":     node_id_str,
                                    "drift_score": float(node.drift_score),
                                })

                        if not elevated:
                            continue

                        severity = "critical" if len(elevated) >= 2 else "warn"
                        # One suspicion signal per elevated node so triage
                        # queues rank the individual actors, not just the
                        # cluster.
                        for e in elevated:
                            try:
                                await repo.add_drift(
                                    tenant_id=tenant_id,
                                    node_id=uuid.UUID(e["node_id"]),
                                    signal_type="collusion_suspicion",
                                    severity=severity,
                                    baseline={},
                                    observed={
                                        "cluster_id":     cluster_id,
                                        "cluster_size":   len(members),
                                        "elevated_count": len(elevated),
                                        "cluster_sample": members[:20],
                                    },
                                    delta=float(len(elevated)),
                                )
                            except Exception as exc:
                                logger.warning(
                                    "collusion_signal_write_failed",
                                    tenant_id=str(tenant_id),
                                    node_id=e["node_id"],
                                    error=str(exc),
                                )
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.critical("collusion_loop_error", error=str(exc))
        await asyncio.sleep(COLLUSION_INTERVAL_S)
