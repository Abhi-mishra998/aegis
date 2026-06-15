"""
Sprint 4 — Fleet aggregation for the in-product dashboards.

This module is the data-layer behind ``GET /audit/fleet/*``. The audit
log already carries every decision Aegis makes; the Fleet dashboard's
job is to surface that data as the KPI cards + time-series + agent
health rankings + recent-events list the operator needs to act on.

The new aggregations are kept in their own module rather than tacked
onto ``services/audit/aggregator.py`` so the Sprint 4 surface is easy
to find, easy to extend, and easy to delete if the strategy shifts.
The existing ``AuditAggregator`` continues to power the older Audit
Trail page; this module powers the new Fleet / Agent Health pages.

All queries are tenant-scoped via the ``tenant_id`` parameter (no
cross-tenant leakage — the gateway extracts ``tenant_id`` from the
verified JWT before invoking these methods, never the request header).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import sqlalchemy as sa
from sqlalchemy import asc, case, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.audit.models import AuditLog


# The actions we count as "decision events" for the Fleet KPIs. The audit
# log also stores meta-events (user_login, behavior_firewall_decision,
# etc.) that we don't want polluting the Decisions counter — those land
# in dedicated counters.
_DECISION_ACTIONS = ("execute_tool", "decision_evaluate")
_DENY_DECISIONS   = ("deny", "block")
_ERROR_DECISIONS  = ("error",)


# Mapping the time-series ``metric`` name to the SQL expression that
# computes that metric's per-bucket value. Centralised so adding a new
# metric is one entry, not a switch-case sprawl.
_METRIC_EXPRESSIONS = {
    "decisions":  lambda: func.count(AuditLog.id),
    "denied":     lambda: func.sum(case((AuditLog.decision.in_(_DENY_DECISIONS), 1), else_=0)),
    "errors":     lambda: func.sum(case((AuditLog.decision.in_(_ERROR_DECISIONS), 1), else_=0)),
    # Latency lives in metadata_json.latency_ms; we average per-bucket and
    # the UI overlays p95 from a separate query if it needs that resolution.
    "latency_ms": lambda: func.avg(
        sa.cast(AuditLog.metadata_json["latency_ms"].as_string(), sa.Float)
    ),
}


class FleetAggregator:
    """Sprint 4 — Fleet dashboard data layer."""

    # ------------------------------------------------------------------
    # KPIs
    # ------------------------------------------------------------------

    @staticmethod
    async def kpis(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        window_minutes: int = 60,
    ) -> dict[str, Any]:
        """Return the KPI card payload for the Fleet Home dashboard.

        Returns:
          ``decisions``, ``denied``, ``errors``, ``deny_rate``,
          ``error_rate``, ``active_agents``, ``distinct_tools`` for the
          last ``window_minutes`` minutes. The UI overlays per-stage
          latency (from ``/flight/fleet/p95``) and tokens / USD (from
          ``/usage/fleet/burn-down``) — those live on the side-services
          where the data is canonical.
        """
        since = datetime.now(tz=UTC) - timedelta(minutes=window_minutes)

        # One COUNT() pass for decisions + denied + errored using SUM(CASE...)
        # so we don't issue four round-trips for a 4-card render.
        row = (await db.execute(
            select(
                func.count(AuditLog.id).label("decisions"),
                func.sum(case((AuditLog.decision.in_(_DENY_DECISIONS), 1), else_=0)).label("denied"),
                func.sum(case((AuditLog.decision.in_(_ERROR_DECISIONS), 1), else_=0)).label("errors"),
                func.count(func.distinct(AuditLog.agent_id)).label("active_agents"),
                func.count(func.distinct(AuditLog.tool)).label("distinct_tools"),
            )
            .where(AuditLog.tenant_id == tenant_id)
            .where(AuditLog.timestamp >= since)
            .where(AuditLog.action.in_(_DECISION_ACTIONS))
        )).one()

        decisions     = int(row.decisions or 0)
        denied        = int(row.denied or 0)
        errors        = int(row.errors or 0)
        active_agents = int(row.active_agents or 0)
        tools         = int(row.distinct_tools or 0)
        deny_rate     = round(denied / decisions, 4) if decisions else 0.0
        error_rate    = round(errors / decisions, 4) if decisions else 0.0

        return {
            "window_minutes":   window_minutes,
            "decisions":        decisions,
            "denied":           denied,
            "errors":           errors,
            "deny_rate":        deny_rate,
            "error_rate":       error_rate,
            "active_agents":    active_agents,
            "distinct_tools":   tools,
        }

    # ------------------------------------------------------------------
    # Time-series
    # ------------------------------------------------------------------

    @staticmethod
    async def timeseries(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        *,
        metric: Literal["decisions", "denied", "errors", "latency_ms"],
        window_minutes: int = 180,
        bucket_minutes: int = 5,
        agent_id: uuid.UUID | None = None,
    ) -> list[dict[str, Any]]:
        """Return the metric as time-bucketed series.

        The buckets are floor-aligned to ``bucket_minutes`` so two calls
        with the same window produce the same x-axis (the UI animates the
        series; misaligned buckets cause visual jitter).
        """
        if metric not in _METRIC_EXPRESSIONS:
            raise ValueError(
                f"metric must be one of {sorted(_METRIC_EXPRESSIONS)}; got {metric!r}"
            )
        if bucket_minutes <= 0:
            raise ValueError("bucket_minutes must be positive")

        since = datetime.now(tz=UTC) - timedelta(minutes=window_minutes)
        bucket_sec = bucket_minutes * 60
        # Truncate timestamp to bucket boundary. ``to_timestamp`` keeps
        # the value as a real datetime so the response is JSON-serializable
        # without an extra coerce on our side.
        bucket = func.to_timestamp(
            (func.extract("epoch", AuditLog.timestamp) / bucket_sec).cast(sa.Integer) * bucket_sec
        ).label("bucket")
        value = _METRIC_EXPRESSIONS[metric]().label("value")

        stmt = (
            select(bucket, value)
            .where(AuditLog.tenant_id == tenant_id)
            .where(AuditLog.timestamp >= since)
            .where(AuditLog.action.in_(_DECISION_ACTIONS))
            .group_by(bucket)
            .order_by(asc(bucket))
        )
        if agent_id is not None:
            stmt = stmt.where(AuditLog.agent_id == agent_id)

        return [
            {"t": row.bucket.isoformat(), "v": float(row.value or 0.0)}
            for row in (await db.execute(stmt)).all()
        ]

    # ------------------------------------------------------------------
    # Agent Health ranking
    # ------------------------------------------------------------------

    @staticmethod
    async def agent_health(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        *,
        rank_by: Literal["deny_rate", "error_rate", "volume", "avg_risk"] = "deny_rate",
        window_minutes: int = 60,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        """Return up to ``limit`` agents ranked by the chosen metric.

        Each row carries every metric the UI displays so the table can
        re-sort client-side without a round-trip per column.
        """
        since = datetime.now(tz=UTC) - timedelta(minutes=window_minutes)
        risk_val = sa.cast(AuditLog.metadata_json["risk_score"].as_string(), sa.Float)

        stmt = (
            select(
                AuditLog.agent_id,
                func.count(AuditLog.id).label("volume"),
                func.sum(case((AuditLog.decision.in_(_DENY_DECISIONS), 1), else_=0)).label("denied"),
                func.sum(case((AuditLog.decision.in_(_ERROR_DECISIONS), 1), else_=0)).label("errors"),
                func.avg(risk_val).label("avg_risk"),
                func.max(AuditLog.timestamp).label("last_seen"),
            )
            .where(AuditLog.tenant_id == tenant_id)
            .where(AuditLog.timestamp >= since)
            .where(AuditLog.action.in_(_DECISION_ACTIONS))
            .group_by(AuditLog.agent_id)
        )

        rows = list((await db.execute(stmt)).all())

        # Compute derived metrics in Python; rate calculations are cheap
        # and Postgres' CASE-based deny_rate would force a sub-select to
        # share the volume between ORDER BY and SELECT.
        out: list[dict[str, Any]] = []
        for r in rows:
            volume = int(r.volume or 0)
            denied = int(r.denied or 0)
            errors = int(r.errors or 0)
            out.append({
                "agent_id":   str(r.agent_id) if r.agent_id else None,
                "volume":     volume,
                "denied":     denied,
                "errors":     errors,
                "deny_rate":  round(denied / volume, 4) if volume else 0.0,
                "error_rate": round(errors / volume, 4) if volume else 0.0,
                "avg_risk":   round(float(r.avg_risk or 0.0), 4),
                "last_seen":  r.last_seen.isoformat() if r.last_seen else None,
            })

        # rank_by maps to a sort key; volume sorts descending, the rest
        # sort by the rate metric descending too. We tie-break by volume
        # so a low-volume noisy agent doesn't beat a high-volume offender.
        rank_keys: dict[str, Any] = {
            "deny_rate":  lambda d: (d["deny_rate"],  d["volume"]),
            "error_rate": lambda d: (d["error_rate"], d["volume"]),
            "avg_risk":   lambda d: (d["avg_risk"],   d["volume"]),
            "volume":     lambda d: (d["volume"],     d["deny_rate"]),
        }
        if rank_by not in rank_keys:
            raise ValueError(f"rank_by must be one of {sorted(rank_keys)}; got {rank_by!r}")
        out.sort(key=rank_keys[rank_by], reverse=True)
        return out[:limit]

    # ------------------------------------------------------------------
    # Recent denied / errored events for the activity table
    # ------------------------------------------------------------------

    @staticmethod
    async def recent_events(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        *,
        kind: Literal["denied", "errors", "any"] = "denied",
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        """Return the most recent denied / errored decisions for the
        Recent Events panel on the Fleet dashboard."""
        stmt = (
            select(
                AuditLog.id,
                AuditLog.timestamp,
                AuditLog.agent_id,
                AuditLog.tool,
                AuditLog.action,
                AuditLog.decision,
                AuditLog.reason,
                AuditLog.request_id,
                AuditLog.metadata_json,
            )
            .where(AuditLog.tenant_id == tenant_id)
            .where(AuditLog.action.in_(_DECISION_ACTIONS))
            .order_by(desc(AuditLog.timestamp))
            .limit(limit)
        )
        if kind == "denied":
            stmt = stmt.where(AuditLog.decision.in_(_DENY_DECISIONS))
        elif kind == "errors":
            stmt = stmt.where(AuditLog.decision.in_(_ERROR_DECISIONS))
        elif kind != "any":
            raise ValueError(f"kind must be denied|errors|any; got {kind!r}")

        rows = list((await db.execute(stmt)).all())
        return [
            {
                "audit_id":   str(r.id),
                "timestamp":  r.timestamp.isoformat() if r.timestamp else None,
                "agent_id":   str(r.agent_id) if r.agent_id else None,
                "tool":       r.tool,
                "action":     r.action,
                "decision":   r.decision,
                "reason":     r.reason,
                "request_id": r.request_id,
                "risk_score": (r.metadata_json or {}).get("risk_score"),
            }
            for r in rows
        ]
