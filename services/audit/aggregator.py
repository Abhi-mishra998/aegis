from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.audit.models import AuditLog


class AuditAggregator:
    """
    Computes time-series insights and behavioral trends from the audit logs.
    Powers the 'Risk Dashboard' data layer.
    """

    @staticmethod
    async def get_top_risky_agents(db: AsyncSession, tenant_id: uuid.UUID, limit: int = 10) -> list[dict[str, Any]]:
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
    async def get_anomaly_trends(db: AsyncSession, tenant_id: uuid.UUID, days: int = 7) -> list[dict[str, Any]]:
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
