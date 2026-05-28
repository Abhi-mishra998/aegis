from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy import asc, desc, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from services.audit.models import AuditLog


class AuditAggregator:
    """
    Computes time-series insights and behavioral trends from the audit logs.
    Powers the 'Risk Dashboard' data layer.
    """

    @staticmethod
    async def get_top_risky_agents(db: AsyncSession, tenant_id: uuid.UUID, limit: int = 10, agent_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
        """
        Identify agents with the highest density of security blocks/escalations.
        """
        # Decisions that indicate risk
        # M-12 fix: 'decision' column contains 'deny'/'allow'.
        # 'killed' and 'behavior_firewall_decision' are in the 'action' column.
        stmt = (
            select(
                AuditLog.agent_id,
                func.count(AuditLog.id).label("threat_count"),
                func.avg(sa.cast(AuditLog.metadata_json["risk_score"].as_string(), sa.Float)).label("avg_risk")
            )
            .where(AuditLog.tenant_id == tenant_id)
            .where(
                or_(
                    AuditLog.decision == "deny",
                    AuditLog.decision == "escalate",
                    AuditLog.action.in_(["killed", "behavior_firewall_decision"]),
                )
            )
            .group_by(AuditLog.agent_id)
            .order_by(desc("threat_count"))
            .limit(limit)
        )
        if agent_id is not None:
            stmt = stmt.where(AuditLog.agent_id == agent_id)

        result = await db.execute(stmt)
        return [
            {
                "agent_id": str(row.agent_id),
                "threat_count": row.threat_count,
                "avg_risk_score": round(row.avg_risk or 0.0, 2)
            }
            for row in result
        ]

    @staticmethod
    async def get_anomaly_trends(db: AsyncSession, tenant_id: uuid.UUID, days: int = 7, agent_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
        """
        2026-05-13 (Run-3): Returns ALL days in the window, zero-filling buckets
        with no traffic. The previous SQL only emitted days that had matching
        rows, so a tenant with <7 days of history rendered as "No timeline data"
        in the Risk Engine. We now build the calendar in Python and overlay
        whatever SQL returns.

        Shape per row:
          - date     ISO timestamp (day bucket, UTC)
          - count    ALL execution audits for the day
          - threats  subset that were denied / escalated / killed / blocked
          - avg_risk mean risk score from metadata_json across the day's executions
        """
        now = datetime.now(tz=UTC)
        # Anchor `since` to the start of the day so date_trunc buckets line up.
        today_bucket = datetime(now.year, now.month, now.day, tzinfo=UTC)
        since = today_bucket - timedelta(days=days - 1)

        stmt = (
            select(
                func.date_trunc('day', AuditLog.timestamp).label("day"),
                func.count(AuditLog.id).label("count"),
                func.sum(
                    sa.case(
                        (AuditLog.decision.in_(["deny", "escalate", "kill", "block"]), 1),
                        else_=0,
                    )
                ).label("threats"),
                func.avg(
                    sa.cast(AuditLog.metadata_json["risk_score"].as_string(), sa.Float)
                ).label("avg_risk"),
            )
            .where(AuditLog.tenant_id == tenant_id)
            .where(AuditLog.timestamp >= since)
            .where(AuditLog.action == "execute_tool")  # only billable executions
            .group_by("day")
            .order_by("day")
        )
        if agent_id is not None:
            stmt = stmt.where(AuditLog.agent_id == agent_id)

        result = await db.execute(stmt)
        by_day: dict[str, dict[str, Any]] = {}
        for row in result:
            day_iso = row.day.isoformat()
            by_day[day_iso] = {
                "date":     day_iso,
                "count":    int(row.count or 0),
                "threats":  int(row.threats or 0),
                "avg_risk": round(float(row.avg_risk or 0.0), 4),
            }

        # Zero-fill missing days so the chart always renders a continuous series.
        series: list[dict[str, Any]] = []
        for i in range(days):
            bucket = since + timedelta(days=i)
            iso = bucket.isoformat()
            series.append(by_day.get(iso, {
                "date": iso, "count": 0, "threats": 0, "avg_risk": 0.0,
            }))
        return series

    @staticmethod
    async def get_agent_drift_report(
        db: AsyncSession,
        agent_id: uuid.UUID,
        tenant_id: uuid.UUID,
        baseline_days: int = 7,
        comparison_hours: int = 24,
    ) -> dict[str, Any]:
        """
        Compare an agent's recent behaviour against its rolling baseline.

        Returns a drift_score in [0, 1] where 0 = identical to baseline and
        1 = completely different.  Metrics: avg_risk, deny_rate, call_volume,
        and unique_tools.
        """
        now = datetime.now(tz=UTC)
        baseline_since = now - timedelta(days=baseline_days)
        recent_since   = now - timedelta(hours=comparison_hours)

        async def _fetch(since: datetime, until: datetime) -> dict[str, Any]:
            stmt = (
                select(
                    func.count(AuditLog.id).label("total"),
                    func.sum(
                        sa.case((AuditLog.decision.in_(["deny", "escalate", "block"]), 1), else_=0)
                    ).label("denied"),
                    func.avg(
                        sa.cast(AuditLog.metadata_json["risk_score"].as_string(), sa.Float)
                    ).label("avg_risk"),
                    func.count(sa.distinct(AuditLog.tool_name)).label("unique_tools"),
                )
                .where(AuditLog.agent_id == agent_id)
                .where(AuditLog.tenant_id == tenant_id)
                .where(AuditLog.action == "execute_tool")
                .where(AuditLog.timestamp >= since)
                .where(AuditLog.timestamp < until)
            )
            row = (await db.execute(stmt)).one()
            total = int(row.total or 0)
            return {
                "total":        total,
                "deny_rate":    round((int(row.denied or 0) / total) if total else 0.0, 4),
                "avg_risk":     round(float(row.avg_risk or 0.0), 4),
                "unique_tools": int(row.unique_tools or 0),
            }

        baseline = await _fetch(baseline_since, recent_since)
        recent   = await _fetch(recent_since, now)

        # Per-metric drift: abs fractional change, capped at 1.0
        def _delta(base_val: float, recent_val: float) -> float:
            if base_val == 0:
                return min(1.0, recent_val)
            return min(1.0, abs(recent_val - base_val) / base_val)

        risk_drift   = _delta(baseline["avg_risk"],     recent["avg_risk"])
        deny_drift   = _delta(baseline["deny_rate"],    recent["deny_rate"])
        vol_drift    = _delta(
            baseline["total"] / baseline_days,
            recent["total"] / max(comparison_hours / 24, 1),
        )
        tool_drift   = _delta(baseline["unique_tools"], recent["unique_tools"])

        # Weighted composite drift score
        drift_score = round(
            0.35 * risk_drift
            + 0.30 * deny_drift
            + 0.20 * vol_drift
            + 0.15 * tool_drift,
            4,
        )

        level = (
            "critical" if drift_score >= 0.70
            else "high"    if drift_score >= 0.45
            else "medium"  if drift_score >= 0.20
            else "low"
        )

        return {
            "agent_id":         str(agent_id),
            "drift_score":      drift_score,
            "drift_level":      level,
            "baseline_days":    baseline_days,
            "comparison_hours": comparison_hours,
            "baseline":         baseline,
            "recent":           recent,
            "metrics": {
                "risk_drift":  round(risk_drift,  4),
                "deny_drift":  round(deny_drift,  4),
                "volume_drift": round(vol_drift,  4),
                "tool_drift":  round(tool_drift,  4),
            },
            "computed_at": now.isoformat(),
        }

    @staticmethod
    async def get_agent_risk_trend(
        db: AsyncSession,
        agent_id: uuid.UUID,
        tenant_id: uuid.UUID,
        days: int = 30,
    ) -> dict[str, Any]:
        """Daily risk score trend for a single agent over the past N days.

        Returns a zero-filled series so the chart always covers the full window.
        Each bucket: date, avg_risk, deny_count, allow_count, total_count.
        """
        now = datetime.now(tz=UTC)
        today_bucket = datetime(now.year, now.month, now.day, tzinfo=UTC)
        since = today_bucket - timedelta(days=days - 1)

        stmt = (
            select(
                func.date_trunc("day", AuditLog.timestamp).label("day"),
                func.count(AuditLog.id).label("total"),
                func.sum(
                    sa.case((AuditLog.decision.in_(["deny", "escalate", "block", "kill"]), 1), else_=0)
                ).label("deny_count"),
                func.sum(
                    sa.case((AuditLog.decision == "allow", 1), else_=0)
                ).label("allow_count"),
                func.avg(
                    sa.cast(AuditLog.metadata_json["risk_score"].as_string(), sa.Float)
                ).label("avg_risk"),
            )
            .where(AuditLog.agent_id == agent_id)
            .where(AuditLog.tenant_id == tenant_id)
            .where(AuditLog.timestamp >= since)
            .group_by("day")
            .order_by(asc("day"))
        )

        result = await db.execute(stmt)
        by_day: dict[str, dict[str, Any]] = {}
        for row in result:
            iso = row.day.isoformat()
            by_day[iso] = {
                "date":        iso,
                "avg_risk":    round(float(row.avg_risk or 0.0), 4),
                "deny_count":  int(row.deny_count or 0),
                "allow_count": int(row.allow_count or 0),
                "total_count": int(row.total or 0),
            }

        series: list[dict[str, Any]] = []
        for i in range(days):
            bucket = since + timedelta(days=i)
            iso = bucket.isoformat()
            series.append(by_day.get(iso, {
                "date": iso, "avg_risk": 0.0,
                "deny_count": 0, "allow_count": 0, "total_count": 0,
            }))

        max_risk = max((p["avg_risk"] for p in series), default=0.0)
        return {
            "agent_id": str(agent_id),
            "days":     days,
            "series":   series,
            "summary": {
                "max_risk":  round(max_risk, 4),
                "avg_risk":  round(
                    sum(p["avg_risk"] for p in series if p["total_count"]) /
                    max(sum(1 for p in series if p["total_count"]), 1),
                    4,
                ),
                "total_denials": sum(p["deny_count"] for p in series),
                "active_days":   sum(1 for p in series if p["total_count"] > 0),
            },
            "computed_at": now.isoformat(),
        }

    @staticmethod
    async def get_tool_risk_breakdown(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        limit: int = 20,
        days: int = 30,
        agent_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """Per-tool aggregation over the last N days.

        Returns up to ``limit`` tools ranked by deny_rate descending.
        Each row: tool, total_calls, denied_calls, deny_rate, avg_risk.
        """
        now = datetime.now(tz=UTC)
        since = now - timedelta(days=days)

        stmt = (
            select(
                AuditLog.tool.label("tool"),
                func.count(AuditLog.id).label("total_calls"),
                func.sum(
                    sa.case(
                        (AuditLog.decision.in_(["deny", "escalate", "block", "kill"]), 1),
                        else_=0,
                    )
                ).label("denied_calls"),
                func.avg(
                    sa.cast(AuditLog.metadata_json["risk_score"].as_string(), sa.Float)
                ).label("avg_risk"),
            )
            .where(AuditLog.tenant_id == tenant_id)
            .where(AuditLog.timestamp >= since)
            .where(AuditLog.tool.isnot(None))
            .group_by(AuditLog.tool)
            .order_by(desc("denied_calls"), desc("total_calls"))
            .limit(limit)
        )
        if agent_id is not None:
            stmt = stmt.where(AuditLog.agent_id == agent_id)

        result = await db.execute(stmt)
        tools = []
        for row in result:
            total = int(row.total_calls or 0)
            denied = int(row.denied_calls or 0)
            tools.append({
                "tool":         row.tool or "unknown",
                "total_calls":  total,
                "denied_calls": denied,
                "deny_rate":    round(denied / total if total else 0.0, 4),
                "avg_risk":     round(float(row.avg_risk or 0.0), 4),
            })

        return {
            "tools":        tools,
            "days":         days,
            "total_tools":  len(tools),
            "computed_at":  now.isoformat(),
        }

    @staticmethod
    async def get_agent_peer_benchmark(
        db: AsyncSession,
        agent_id: uuid.UUID,
        tenant_id: uuid.UUID,
        days: int = 30,
    ) -> dict[str, Any]:
        """Rank one agent against all other agents in the tenant.

        Returns percentile ranks (0-100) for deny_rate, avg_risk, and
        call_volume, plus the tenant-wide p50/p75/p95 reference values.
        """
        now = datetime.now(tz=UTC)
        since = now - timedelta(days=days)

        stmt = (
            select(
                AuditLog.agent_id.label("agent_id"),
                func.count(AuditLog.id).label("total"),
                func.sum(
                    sa.case(
                        (AuditLog.decision.in_(["deny", "escalate", "block", "kill"]), 1),
                        else_=0,
                    )
                ).label("denied"),
                func.avg(
                    sa.cast(AuditLog.metadata_json["risk_score"].as_string(), sa.Float)
                ).label("avg_risk"),
            )
            .where(AuditLog.tenant_id == tenant_id)
            .where(AuditLog.timestamp >= since)
            .group_by(AuditLog.agent_id)
        )

        result = await db.execute(stmt)
        rows = result.all()

        if not rows:
            return {
                "agent_id": str(agent_id),
                "days": days,
                "percentiles": {},
                "peer_count": 0,
                "agent_stats": {},
                "references": {},
                "computed_at": now.isoformat(),
            }

        def _pct_rank(values: list[float], target: float) -> int:
            """Return 0-100 percentile rank of target in values (higher = more extreme)."""
            if not values:
                return 50
            below = sum(1 for v in values if v < target)
            return round(below / len(values) * 100)

        def _percentile(values: list[float], p: float) -> float:
            if not values:
                return 0.0
            s = sorted(values)
            idx = (p / 100) * (len(s) - 1)
            lo, hi = int(idx), min(int(idx) + 1, len(s) - 1)
            return round(s[lo] + (s[hi] - s[lo]) * (idx - lo), 4)

        all_deny_rates: list[float] = []
        all_risks: list[float] = []
        all_volumes: list[float] = []
        agent_stats: dict[str, Any] = {}

        for row in rows:
            total = int(row.total or 0)
            denied = int(row.denied or 0)
            dr = denied / total if total else 0.0
            risk = float(row.avg_risk or 0.0)
            all_deny_rates.append(dr)
            all_risks.append(risk)
            all_volumes.append(float(total))
            if row.agent_id == agent_id:
                agent_stats = {
                    "total_calls": total,
                    "denied_calls": denied,
                    "deny_rate": round(dr, 4),
                    "avg_risk": round(risk, 4),
                }

        if not agent_stats:
            agent_stats = {"total_calls": 0, "denied_calls": 0, "deny_rate": 0.0, "avg_risk": 0.0}

        peer_count = len(rows)

        return {
            "agent_id":  str(agent_id),
            "days":      days,
            "peer_count": peer_count,
            "agent_stats": agent_stats,
            "percentiles": {
                "deny_rate":   _pct_rank(all_deny_rates, agent_stats["deny_rate"]),
                "avg_risk":    _pct_rank(all_risks,      agent_stats["avg_risk"]),
                "call_volume": _pct_rank(all_volumes,    float(agent_stats["total_calls"])),
            },
            "references": {
                "deny_rate": {
                    "p50": _percentile(all_deny_rates, 50),
                    "p75": _percentile(all_deny_rates, 75),
                    "p95": _percentile(all_deny_rates, 95),
                },
                "avg_risk": {
                    "p50": _percentile(all_risks, 50),
                    "p75": _percentile(all_risks, 75),
                    "p95": _percentile(all_risks, 95),
                },
                "call_volume": {
                    "p50": _percentile(all_volumes, 50),
                    "p75": _percentile(all_volumes, 75),
                    "p95": _percentile(all_volumes, 95),
                },
            },
            "computed_at": now.isoformat(),
        }

    @staticmethod
    async def get_top_findings(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        days: int = 30,
        limit: int = 15,
        agent_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """Frequency distribution of canonical security findings over the past N days.

        Unnests ``metadata_json->'findings'`` (a JSONB string array) and counts
        occurrences per finding code, returning the top ``limit`` entries ranked
        by count descending.
        """
        now = datetime.now(tz=UTC)
        since = now - timedelta(days=days)

        # Use a raw SQL CTE to unnest the JSONB array then aggregate.
        # SQLAlchemy core doesn't have a clean abstraction for jsonb_array_elements_text,
        # so we use text() for the subquery portion only.
        agent_clause = "AND agent_id = :agent_id" if agent_id is not None else ""
        params = {"tenant_id": str(tenant_id), "since": since, "limit": limit}
        if agent_id is not None:
            params["agent_id"] = str(agent_id)
        raw = await db.execute(
            text(f"""
                SELECT finding, COUNT(*) AS cnt
                FROM audit_logs,
                     jsonb_array_elements_text(
                         CASE jsonb_typeof(metadata_json->'findings')
                             WHEN 'array' THEN metadata_json->'findings'
                             ELSE '[]'::jsonb
                         END
                     ) AS finding
                WHERE tenant_id = :tenant_id
                  AND timestamp  >= :since
                  AND finding   <> ''
                  {agent_clause}
                GROUP BY finding
                ORDER BY cnt DESC
                LIMIT :limit
            """),
            params,
        )

        findings = [
            {"finding": row.finding, "count": int(row.cnt)}
            for row in raw
        ]

        total_events = sum(f["count"] for f in findings)
        return {
            "findings":     findings,
            "days":         days,
            "total_events": total_events,
            "computed_at":  now.isoformat(),
        }

    @staticmethod
    async def get_hourly_activity(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        days: int = 7,
        agent_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """
        Returns 24 hour-of-day buckets (0–23) with request count, deny count,
        and average risk score aggregated over the given day window.
        Missing hours are zero-filled so callers always receive a full 24-bucket series.
        """
        now = datetime.now(UTC)
        since = now - timedelta(days=days)

        stmt = (
            select(
                func.extract("hour", AuditLog.timestamp).label("hour"),
                func.count().label("count"),
                func.sum(
                    sa.case((AuditLog.decision.in_(["deny", "kill"]), 1), else_=0)
                ).label("deny_count"),
                func.avg(
                    func.coalesce(
                        sa.cast(AuditLog.metadata_json["risk_score"].astext, sa.Float),
                        0.0,
                    )
                ).label("avg_risk"),
            )
            .where(
                AuditLog.tenant_id == tenant_id,
                AuditLog.timestamp >= since,
            )
            .group_by(func.extract("hour", AuditLog.timestamp))
            .order_by(func.extract("hour", AuditLog.timestamp))
        )
        if agent_id is not None:
            stmt = stmt.where(AuditLog.agent_id == agent_id)

        rows = (await db.execute(stmt)).all()
        by_hour = {int(r.hour): r for r in rows}

        buckets = []
        for h in range(24):
            r = by_hour.get(h)
            buckets.append({
                "hour":       h,
                "count":      int(r.count) if r else 0,
                "deny_count": int(r.deny_count or 0) if r else 0,
                "avg_risk":   round(float(r.avg_risk or 0), 4) if r else 0.0,
            })

        return {
            "buckets":    buckets,
            "days":       days,
            "computed_at": now.isoformat(),
        }

    @staticmethod
    async def get_risk_histogram(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        days: int = 30,
        bins: int = 10,
        agent_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """
        Distributes audit log risk scores into ``bins`` equal-width buckets over [0, 1].
        Uses FLOOR(score * bins) / bins to assign each row a bucket.
        Always returns all ``bins`` buckets; missing ones get count=0.
        """
        now = datetime.now(UTC)
        since = now - timedelta(days=days)

        agent_clause = "AND agent_id = :agent_id" if agent_id is not None else ""
        params = {"tenant_id": str(tenant_id), "since": since, "bins": bins}
        if agent_id is not None:
            params["agent_id"] = str(agent_id)
        raw = await db.execute(
            text(f"""
                SELECT
                    FLOOR(CAST(metadata_json->>'risk_score' AS FLOAT) * :bins) / :bins AS bucket,
                    COUNT(*) AS cnt
                FROM audit_logs
                WHERE tenant_id = :tenant_id
                  AND timestamp  >= :since
                  AND metadata_json->>'risk_score' IS NOT NULL
                  AND metadata_json->>'risk_score' ~ '^[0-9]*\\.?[0-9]+$'
                  {agent_clause}
                GROUP BY bucket
                ORDER BY bucket
            """),
            params,
        )

        by_bucket: dict[float, int] = {}
        for r in raw:
            if r.bucket is not None:
                by_bucket[round(float(r.bucket), 3)] = int(r.cnt)

        step = 1.0 / bins
        buckets = []
        for i in range(bins):
            low  = round(i * step, 3)
            high = round((i + 1) * step, 3)
            buckets.append({
                "bin":   f"{low:.1f}–{high:.1f}",
                "low":   low,
                "high":  high,
                "count": by_bucket.get(low, 0),
            })

        total = sum(b["count"] for b in buckets)
        return {
            "buckets":    buckets,
            "total":      total,
            "days":       days,
            "bins":       bins,
            "computed_at": now.isoformat(),
        }

    @staticmethod
    async def get_weekly_heatmap(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        days: int = 28,
        agent_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """
        Returns a 7×24 grid of request counts by day-of-week × hour-of-day.
        PostgreSQL DOW 0=Sunday; remapped to Mon=0 … Sun=6.
        Missing cells get count=0; each cell carries a ``pct`` (0–100) relative
        to the maximum cell count so the UI can drive colour intensity directly.
        """
        now = datetime.now(UTC)
        since = now - timedelta(days=days)

        stmt = (
            select(
                func.extract("dow",  AuditLog.timestamp).label("dow"),
                func.extract("hour", AuditLog.timestamp).label("hour"),
                func.count().label("count"),
            )
            .where(
                AuditLog.tenant_id == tenant_id,
                AuditLog.timestamp >= since,
            )
            .group_by(
                func.extract("dow",  AuditLog.timestamp),
                func.extract("hour", AuditLog.timestamp),
            )
        )
        if agent_id is not None:
            stmt = stmt.where(AuditLog.agent_id == agent_id)

        rows = (await db.execute(stmt)).all()

        # Remap PG DOW (0=Sun … 6=Sat) → Mon=0 … Sun=6
        grid: dict[tuple[int, int], int] = {}
        for r in rows:
            dow      = int(r.dow)
            hour     = int(r.hour)
            day_idx  = (dow - 1) % 7  # Mon=0, Tue=1, …, Sun=6
            grid[(day_idx, hour)] = int(r.count)

        DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        max_count  = max(grid.values(), default=1)

        cells = [
            {
                "day":       d,
                "day_label": DAY_LABELS[d],
                "hour":      h,
                "count":     grid.get((d, h), 0),
                "pct":       round(grid.get((d, h), 0) / max_count * 100, 1),
            }
            for d in range(7)
            for h in range(24)
        ]

        return {
            "cells":      cells,
            "days":       days,
            "max_count":  max_count,
            "computed_at": now.isoformat(),
        }

    @staticmethod
    async def get_decision_trend(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        days: int = 30,
        agent_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """
        Returns a zero-filled ``days``-length daily series with counts split by
        decision outcome: allow, deny, escalate, monitor, kill.

        2026-05-28: bind the `date_trunc('day', ts)` expression to a CTE column
        so the parameterised expression isn't re-emitted at GROUP BY/ORDER BY
        time. The previous shape produced PG GroupingError because asyncpg
        emits a fresh `$N` placeholder per call site even when the literal
        argument is identical, and PG considers them distinct expressions.
        """
        now = datetime.now(UTC)
        since = now - timedelta(days=days)

        day_expr = func.date_trunc("day", AuditLog.timestamp)
        stmt = (
            select(
                day_expr.label("day"),
                AuditLog.decision,
                func.count().label("count"),
            )
            .where(
                AuditLog.tenant_id == tenant_id,
                AuditLog.timestamp >= since,
                AuditLog.decision.isnot(None),
            )
            .group_by(day_expr, AuditLog.decision)
            .order_by(day_expr)
        )
        if agent_id is not None:
            stmt = stmt.where(AuditLog.agent_id == agent_id)

        rows = (await db.execute(stmt)).all()

        by_day: dict[str, dict[str, int]] = {}
        for r in rows:
            day_str = r.day.strftime("%Y-%m-%d") if r.day else None
            if not day_str:
                continue
            decision = (r.decision or "unknown").lower()
            if day_str not in by_day:
                by_day[day_str] = {}
            by_day[day_str][decision] = by_day[day_str].get(decision, 0) + int(r.count)

        series = []
        for i in range(days):
            d = (since + timedelta(days=i)).strftime("%Y-%m-%d")
            counts = by_day.get(d, {})
            series.append({
                "date":     d,
                "allow":    counts.get("allow", 0),
                "deny":     counts.get("deny", 0),
                "escalate": counts.get("escalate", 0),
                "monitor":  counts.get("monitor", 0),
                "kill":     counts.get("kill", 0),
            })

        return {
            "series":     series,
            "days":       days,
            "computed_at": now.isoformat(),
        }

    @staticmethod
    async def get_agent_activity_summary(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        limit: int = 20,
    ) -> dict[str, Any]:
        """
        Returns per-agent activity summary: first_seen, last_seen, total_calls,
        deny_count, deny_rate, avg_risk — ordered by last_seen DESC.
        Useful for a quick "who's active and how risky?" registry view.
        """
        now = datetime.now(UTC)

        stmt = (
            select(
                AuditLog.agent_id,
                func.min(AuditLog.timestamp).label("first_seen"),
                func.max(AuditLog.timestamp).label("last_seen"),
                func.count().label("total_calls"),
                func.sum(
                    sa.case((AuditLog.decision.in_(["deny", "kill"]), 1), else_=0)
                ).label("deny_count"),
                func.avg(
                    func.coalesce(
                        sa.cast(AuditLog.metadata_json["risk_score"].astext, sa.Float),
                        0.0,
                    )
                ).label("avg_risk"),
            )
            .where(
                AuditLog.tenant_id == tenant_id,
                AuditLog.agent_id.isnot(None),
            )
            .group_by(AuditLog.agent_id)
            .order_by(desc(func.max(AuditLog.timestamp)))
            .limit(limit)
        )

        rows = (await db.execute(stmt)).all()

        agents = []
        for r in rows:
            total = int(r.total_calls)
            denies = int(r.deny_count or 0)
            agents.append({
                "agent_id":   str(r.agent_id),
                "first_seen": r.first_seen.isoformat() if r.first_seen else None,
                "last_seen":  r.last_seen.isoformat() if r.last_seen else None,
                "total_calls": total,
                "deny_count":  denies,
                "deny_rate":   round(denies / total * 100, 2) if total > 0 else 0.0,
                "avg_risk":    round(float(r.avg_risk or 0), 4),
            })

        return {
            "agents":     agents,
            "total":      len(agents),
            "computed_at": now.isoformat(),
        }

    @staticmethod
    async def get_high_risk_events(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        days: int = 7,
        limit: int = 20,
        threshold: float = 0.7,
        agent_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """
        Returns the most recent high-risk audit events where the stored risk_score
        is at or above ``threshold``, ordered by risk score descending.
        Each event includes findings tags extracted from metadata_json.
        """
        now = datetime.now(UTC)
        since = now - timedelta(days=days)

        stmt = (
            select(
                AuditLog.id,
                AuditLog.agent_id,
                AuditLog.tool,
                AuditLog.decision,
                AuditLog.timestamp,
                AuditLog.metadata_json,
            )
            .where(
                AuditLog.tenant_id == tenant_id,
                AuditLog.timestamp >= since,
                sa.cast(AuditLog.metadata_json["risk_score"].astext, sa.Float)
                >= threshold,
            )
            .order_by(
                desc(sa.cast(AuditLog.metadata_json["risk_score"].astext, sa.Float))
            )
            .limit(limit)
        )
        if agent_id is not None:
            stmt = stmt.where(AuditLog.agent_id == agent_id)

        rows = (await db.execute(stmt)).all()

        events = []
        for r in rows:
            meta = r.metadata_json or {}
            risk = float(meta.get("risk_score", 0))
            findings = meta.get("findings", [])
            if not isinstance(findings, list):
                findings = []
            events.append({
                "id":        str(r.id),
                "agent_id":  str(r.agent_id) if r.agent_id else None,
                "tool":      r.tool,
                "decision":  r.decision,
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                "risk_score": round(risk, 4),
                "findings":  findings[:5],
            })

        return {
            "events":    events,
            "threshold": threshold,
            "days":      days,
            "computed_at": now.isoformat(),
        }

    @staticmethod
    async def get_deny_reasons(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        days: int = 30,
        limit: int = 15,
        agent_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """
        Returns the most frequent ``reason`` strings from deny/kill decisions,
        with count and percentage of total denied events.
        Blank/null reasons are grouped under 'unspecified'.
        """
        now = datetime.now(UTC)
        since = now - timedelta(days=days)

        # Bind the COALESCE/NULLIF expression once so the asyncpg-parameterised
        # version is identical at SELECT and GROUP BY time (PG considers
        # `coalesce($1, ...)` and `coalesce($2, ...)` distinct expressions
        # even when both bind to the same literal).
        reason_expr = func.coalesce(
            func.nullif(func.trim(AuditLog.reason), ""),
            "unspecified",
        )
        stmt = (
            select(
                reason_expr.label("reason"),
                func.count().label("count"),
            )
            .where(
                AuditLog.tenant_id == tenant_id,
                AuditLog.timestamp >= since,
                AuditLog.decision.in_(["deny", "kill"]),
            )
            .group_by(reason_expr)
            .order_by(desc(func.count()))
            .limit(limit)
        )
        if agent_id is not None:
            stmt = stmt.where(AuditLog.agent_id == agent_id)

        rows = (await db.execute(stmt)).all()

        total_denied = sum(int(r.count) for r in rows)
        reasons = [
            {
                "reason":  r.reason,
                "count":   int(r.count),
                "pct":     round(int(r.count) / total_denied * 100, 1) if total_denied > 0 else 0.0,
            }
            for r in rows
        ]

        return {
            "reasons":      reasons,
            "total_denied": total_denied,
            "days":         days,
            "computed_at":  now.isoformat(),
        }

    @staticmethod
    async def get_agent_tool_usage(
        db: AsyncSession,
        agent_id: uuid.UUID,
        tenant_id: uuid.UUID,
        days: int = 30,
    ) -> dict[str, Any]:
        """Per-tool call stats for a single agent over the given window."""
        now = datetime.now(UTC)
        since = now - timedelta(days=days)

        total_stmt = (
            select(func.count())
            .where(
                AuditLog.agent_id == agent_id,
                AuditLog.tenant_id == tenant_id,
                AuditLog.timestamp >= since,
                AuditLog.tool.isnot(None),
            )
        )
        total_calls = (await db.execute(total_stmt)).scalar() or 0

        stmt = (
            select(
                AuditLog.tool.label("tool"),
                func.count().label("calls"),
                func.sum(
                    sa.case((AuditLog.decision.in_(["deny", "kill"]), 1), else_=0)
                ).label("deny_count"),
                func.avg(
                    sa.cast(
                        sa.func.nullif(
                            AuditLog.metadata_json["risk_score"].astext,
                            "",
                        ),
                        sa.Float,
                    )
                ).label("avg_risk"),
            )
            .where(
                AuditLog.agent_id == agent_id,
                AuditLog.tenant_id == tenant_id,
                AuditLog.timestamp >= since,
                AuditLog.tool.isnot(None),
            )
            .group_by(AuditLog.tool)
            .order_by(desc(func.count()))
        )

        rows = (await db.execute(stmt)).all()

        tools = [
            {
                "tool":       r.tool,
                "calls":      int(r.calls),
                "deny_count": int(r.deny_count or 0),
                "deny_rate":  round(int(r.deny_count or 0) / int(r.calls) * 100, 1) if int(r.calls) > 0 else 0.0,
                "avg_risk":   round(float(r.avg_risk), 3) if r.avg_risk is not None else 0.0,
            }
            for r in rows
        ]

        return {
            "agent_id":    str(agent_id),
            "tools":       tools,
            "total_calls": total_calls,
            "days":        days,
            "computed_at": now.isoformat(),
        }

    @staticmethod
    async def get_tool_risk_leaderboard(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        days: int = 30,
        limit: int = 20,
        agent_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """Cross-agent ranking of tools by deny rate over the given window."""
        now = datetime.now(UTC)
        since = now - timedelta(days=days)

        stmt = (
            select(
                AuditLog.tool.label("tool"),
                func.count().label("calls"),
                func.sum(
                    sa.case((AuditLog.decision.in_(["deny", "kill"]), 1), else_=0)
                ).label("deny_count"),
                func.count(AuditLog.agent_id.distinct()).label("agent_count"),
                # PG `metadata_json` is `jsonb`. The previous version called
                # `json_extract_path_text(jsonb, varchar)` which does NOT
                # exist (only the `json_*` family for the `json` type). Use
                # the JSONB `->>` accessor (`.astext`), null-out empty strings,
                # then cast to Float.
                func.avg(
                    sa.cast(
                        sa.func.nullif(
                            AuditLog.metadata_json["risk_score"].astext,
                            "",
                        ),
                        sa.Float,
                    )
                ).label("avg_risk"),
            )
            .where(
                AuditLog.tenant_id == tenant_id,
                AuditLog.timestamp >= since,
                AuditLog.tool.isnot(None),
            )
            .group_by(AuditLog.tool)
            .order_by(desc(func.sum(
                sa.case((AuditLog.decision.in_(["deny", "kill"]), 1), else_=0)
            )))
            .limit(limit)
        )
        if agent_id is not None:
            stmt = stmt.where(AuditLog.agent_id == agent_id)

        rows = (await db.execute(stmt)).all()

        tools = [
            {
                "tool":        r.tool,
                "calls":       int(r.calls),
                "deny_count":  int(r.deny_count or 0),
                "deny_rate":   round(int(r.deny_count or 0) / int(r.calls) * 100, 1) if int(r.calls) > 0 else 0.0,
                "avg_risk":    round(float(r.avg_risk), 3) if r.avg_risk is not None else 0.0,
                "agent_count": int(r.agent_count),
            }
            for r in rows
        ]

        return {
            "tools":       tools,
            "days":        days,
            "computed_at": now.isoformat(),
        }

    @staticmethod
    async def get_risk_percentile_trend(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        days: int = 30,
    ) -> dict[str, Any]:
        """Daily p50/p75/p95 risk scores over the given window."""
        now = datetime.now(UTC)
        since = now - timedelta(days=days)

        stmt = text("""
            SELECT
                date_trunc('day', timestamp AT TIME ZONE 'UTC') AS day,
                percentile_cont(0.50) WITHIN GROUP (
                    ORDER BY (metadata_json->>'risk_score')::float
                ) AS p50,
                percentile_cont(0.75) WITHIN GROUP (
                    ORDER BY (metadata_json->>'risk_score')::float
                ) AS p75,
                percentile_cont(0.95) WITHIN GROUP (
                    ORDER BY (metadata_json->>'risk_score')::float
                ) AS p95,
                count(*) AS scored_count
            FROM audit_logs
            WHERE tenant_id = :tenant_id
              AND timestamp >= :since
              AND metadata_json->>'risk_score' IS NOT NULL
              AND metadata_json->>'risk_score' != ''
              AND (metadata_json->>'risk_score') ~ '^[0-9]+(\\.[0-9]+)?$'
            GROUP BY 1
            ORDER BY 1
        """)

        rows = (await db.execute(stmt, {"tenant_id": str(tenant_id), "since": since})).all()

        by_day: dict[str, dict] = {
            r.day.strftime("%Y-%m-%d"): {
                "p50": round(float(r.p50), 3),
                "p75": round(float(r.p75), 3),
                "p95": round(float(r.p95), 3),
                "scored_count": int(r.scored_count),
            }
            for r in rows
        }

        series = []
        for i in range(days):
            d = (since + timedelta(days=i + 1)).strftime("%Y-%m-%d")
            entry = by_day.get(d, {"p50": None, "p75": None, "p95": None, "scored_count": 0})
            series.append({"date": d, **entry})

        return {
            "series":      series,
            "days":        days,
            "computed_at": now.isoformat(),
        }

    @staticmethod
    async def get_daily_active_agents(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        days: int = 30,
        agent_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """Count of distinct agents active each day over the given window."""
        now = datetime.now(UTC)
        since = now - timedelta(days=days)

        day_expr = func.date_trunc("day", AuditLog.timestamp)
        stmt = (
            select(
                day_expr.label("day"),
                func.count(AuditLog.agent_id.distinct()).label("active_agents"),
                func.count().label("total_calls"),
            )
            .where(
                AuditLog.tenant_id == tenant_id,
                AuditLog.timestamp >= since,
                AuditLog.agent_id.isnot(None),
            )
            .group_by(day_expr)
            .order_by(day_expr)
        )
        if agent_id is not None:
            stmt = stmt.where(AuditLog.agent_id == agent_id)

        rows = (await db.execute(stmt)).all()

        by_day: dict[str, dict] = {
            r.day.strftime("%Y-%m-%d"): {
                "active_agents": int(r.active_agents),
                "total_calls":   int(r.total_calls),
            }
            for r in rows
        }

        series = []
        for i in range(days):
            d = (since + timedelta(days=i + 1)).strftime("%Y-%m-%d")
            entry = by_day.get(d, {"active_agents": 0, "total_calls": 0})
            series.append({"date": d, **entry})

        peak = max((s["active_agents"] for s in series), default=0)

        return {
            "series":      series,
            "peak_agents": peak,
            "days":        days,
            "computed_at": now.isoformat(),
        }

    @staticmethod
    async def get_finding_breakdown(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        days: int = 30,
        limit: int = 20,
        agent_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """Ranked frequency of each canonical finding type over the given window."""
        now = datetime.now(UTC)
        since = now - timedelta(days=days)

        agent_clause = "AND agent_id = :agent_id" if agent_id is not None else ""
        stmt = text(f"""
            SELECT
                finding,
                count(*) AS count
            FROM audit_logs,
                 jsonb_array_elements_text(
                     CASE jsonb_typeof(metadata_json->'findings')
                         WHEN 'array' THEN metadata_json->'findings'
                         ELSE '[]'::jsonb
                     END
                 ) AS finding
            WHERE tenant_id = :tenant_id
              AND timestamp  >= :since
              {agent_clause}
            GROUP BY finding
            ORDER BY count DESC
            LIMIT :limit
        """)

        params = {"tenant_id": str(tenant_id), "since": since, "limit": limit}
        if agent_id is not None:
            params["agent_id"] = str(agent_id)
        rows = (await db.execute(
            stmt,
            params,
        )).all()

        total = sum(int(r.count) for r in rows)
        findings = [
            {
                "finding": r.finding,
                "count":   int(r.count),
                "pct":     round(int(r.count) / total * 100, 1) if total > 0 else 0.0,
            }
            for r in rows
        ]

        return {
            "findings":    findings,
            "total":       total,
            "days":        days,
            "computed_at": now.isoformat(),
        }

    @staticmethod
    async def get_agent_daily_decisions(
        db: AsyncSession,
        agent_id: uuid.UUID,
        tenant_id: uuid.UUID,
        days: int = 30,
    ) -> dict[str, Any]:
        """Daily allow / deny / total decision counts for a single agent."""
        now = datetime.now(UTC)
        since = now - timedelta(days=days)

        day_expr = func.date_trunc("day", AuditLog.timestamp)
        stmt = (
            select(
                day_expr.label("day"),
                func.count().label("total"),
                func.sum(
                    sa.case((AuditLog.decision == "allow", 1), else_=0)
                ).label("allow"),
                func.sum(
                    sa.case((AuditLog.decision.in_(["deny", "kill"]), 1), else_=0)
                ).label("deny"),
            )
            .where(
                AuditLog.agent_id == agent_id,
                AuditLog.tenant_id == tenant_id,
                AuditLog.timestamp >= since,
            )
            .group_by(day_expr)
            .order_by(day_expr)
        )

        rows = (await db.execute(stmt)).all()

        by_day: dict[str, dict] = {
            r.day.strftime("%Y-%m-%d"): {
                "total": int(r.total),
                "allow": int(r.allow or 0),
                "deny":  int(r.deny or 0),
            }
            for r in rows
        }

        series = []
        for i in range(days):
            d = (since + timedelta(days=i + 1)).strftime("%Y-%m-%d")
            entry = by_day.get(d, {"total": 0, "allow": 0, "deny": 0})
            series.append({"date": d, **entry})

        total_calls = sum(s["total"] for s in series)
        total_deny  = sum(s["deny"]  for s in series)

        return {
            "agent_id":    str(agent_id),
            "series":      series,
            "total_calls": total_calls,
            "total_deny":  total_deny,
            "days":        days,
            "computed_at": now.isoformat(),
        }

    @staticmethod
    async def get_agent_finding_breakdown(
        db: AsyncSession,
        agent_id: uuid.UUID,
        tenant_id: uuid.UUID,
        days: int = 30,
        limit: int = 15,
    ) -> dict[str, Any]:
        """Ranked finding type frequency for a single agent over the given window."""
        now = datetime.now(UTC)
        since = now - timedelta(days=days)

        stmt = text("""
            SELECT
                finding,
                count(*) AS count
            FROM audit_logs,
                 jsonb_array_elements_text(
                     CASE jsonb_typeof(metadata_json->'findings')
                         WHEN 'array' THEN metadata_json->'findings'
                         ELSE '[]'::jsonb
                     END
                 ) AS finding
            WHERE agent_id  = :agent_id
              AND tenant_id = :tenant_id
              AND timestamp >= :since
            GROUP BY finding
            ORDER BY count DESC
            LIMIT :limit
        """)

        rows = (await db.execute(
            stmt,
            {
                "agent_id":  str(agent_id),
                "tenant_id": str(tenant_id),
                "since":     since,
                "limit":     limit,
            },
        )).all()

        total = sum(int(r.count) for r in rows)
        findings = [
            {
                "finding": r.finding,
                "count":   int(r.count),
                "pct":     round(int(r.count) / total * 100, 1) if total > 0 else 0.0,
            }
            for r in rows
        ]

        return {
            "agent_id":    str(agent_id),
            "findings":    findings,
            "total":       total,
            "days":        days,
            "computed_at": now.isoformat(),
        }

    @staticmethod
    async def get_posture_score_trend(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        days: int = 30,
        agent_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """
        Daily tenant posture score: round((allow / total) * 100, 1).
        Zero-filled days get a score of None (no decisions).
        """
        now = datetime.now(UTC)
        since = now - timedelta(days=days)

        day_expr = func.date_trunc("day", AuditLog.timestamp)
        stmt = (
            select(
                day_expr.label("day"),
                func.count().label("total"),
                func.sum(
                    sa.case((AuditLog.decision == "allow", 1), else_=0)
                ).label("allow_count"),
                func.sum(
                    sa.case((AuditLog.decision.in_(["deny", "kill"]), 1), else_=0)
                ).label("deny_count"),
            )
            .where(
                AuditLog.tenant_id == tenant_id,
                AuditLog.timestamp >= since,
            )
            .group_by(day_expr)
            .order_by(day_expr)
        )
        if agent_id is not None:
            stmt = stmt.where(AuditLog.agent_id == agent_id)

        rows = (await db.execute(stmt)).all()

        by_day: dict[str, dict] = {}
        for r in rows:
            total = int(r.total)
            allow = int(r.allow_count or 0)
            by_day[r.day.strftime("%Y-%m-%d")] = {
                "total":         total,
                "allow_count":   allow,
                "deny_count":    int(r.deny_count or 0),
                "posture_score": round(allow / total * 100, 1) if total > 0 else None,
            }

        series = []
        for i in range(days):
            d = (since + timedelta(days=i + 1)).strftime("%Y-%m-%d")
            entry = by_day.get(d, {"total": 0, "allow_count": 0, "deny_count": 0, "posture_score": None})
            series.append({"date": d, **entry})

        scored = [s["posture_score"] for s in series if s["posture_score"] is not None]
        avg_score = round(sum(scored) / len(scored), 1) if scored else None

        return {
            "series":    series,
            "avg_score": avg_score,
            "days":      days,
            "computed_at": now.isoformat(),
        }

    @staticmethod
    async def get_escalation_rate_trend(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        days: int = 30,
        agent_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """Daily escalation rate: (escalate_count / total) * 100, zero-filled."""
        now = datetime.now(UTC)
        since = now - timedelta(days=days)

        day_expr = func.date_trunc("day", AuditLog.timestamp)
        stmt = (
            select(
                day_expr.label("day"),
                func.count().label("total"),
                func.sum(
                    sa.case((AuditLog.decision == "escalate", 1), else_=0)
                ).label("escalate_count"),
            )
            .where(
                AuditLog.tenant_id == tenant_id,
                AuditLog.timestamp >= since,
            )
            .group_by(day_expr)
            .order_by(day_expr)
        )
        if agent_id is not None:
            stmt = stmt.where(AuditLog.agent_id == agent_id)

        rows = (await db.execute(stmt)).all()

        by_day: dict[str, dict] = {}
        for r in rows:
            total = int(r.total)
            esc   = int(r.escalate_count or 0)
            by_day[r.day.strftime("%Y-%m-%d")] = {
                "total":          total,
                "escalate_count": esc,
                "escalation_rate": round(esc / total * 100, 2) if total > 0 else 0.0,
            }

        series = []
        for i in range(days):
            d = (since + timedelta(days=i + 1)).strftime("%Y-%m-%d")
            entry = by_day.get(d, {"total": 0, "escalate_count": 0, "escalation_rate": None})
            series.append({"date": d, **entry})

        rates = [s["escalation_rate"] for s in series if s["escalation_rate"] is not None]
        avg_rate = round(sum(rates) / len(rates), 2) if rates else None
        peak_rate = max(rates, default=None)

        return {
            "series":    series,
            "avg_rate":  avg_rate,
            "peak_rate": peak_rate,
            "days":      days,
            "computed_at": now.isoformat(),
        }
