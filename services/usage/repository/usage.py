from __future__ import annotations

import uuid
from collections.abc import Sequence

import httpx
import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sdk.common.config import settings
from services.usage.models.usage import UsageRecord
from services.usage.schemas.usage import UsageCreate, UsageSummary

logger = structlog.get_logger(__name__)

_INTERNAL_HEADERS = {
    "X-Internal-Secret": settings.INTERNAL_SECRET,
    "Content-Type": "application/json",
}


class UsageRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def record(self, payload: UsageCreate) -> UsageRecord:
        """
        Idempotent usage record insert.
        NOTE: billing_status on audit_logs is updated via the Audit Service HTTP API,
        NOT via a cross-database SQL call. See record_usage() in the router.
        """
        from sqlalchemy.dialects.postgresql import insert

        stmt = insert(UsageRecord).values(**payload.model_dump())

        if payload.audit_id:
            stmt = stmt.on_conflict_do_nothing(index_elements=["audit_id"])

        stmt = stmt.returning(UsageRecord)
        result = await self.db.execute(stmt)
        record = result.scalar_one_or_none()

        if record is None and payload.audit_id:
            # Conflict: fetch existing record
            sel = select(UsageRecord).where(UsageRecord.audit_id == payload.audit_id)
            res = await self.db.execute(sel)
            record = res.scalar_one()

        await self.db.commit()
        return record

    async def mark_audit_billing_complete(self, audit_id: uuid.UUID) -> None:
        """
        Calls Audit Service to mark a single audit log as billing_status='completed'.
        Fire-and-forget; logs on failure but never raises.
        """
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.patch(
                    f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/billing-status/complete",
                    json={"audit_ids": [str(audit_id)]},
                    headers=_INTERNAL_HEADERS,
                )
        except Exception as exc:
            logger.warning("audit_billing_status_update_failed", audit_id=str(audit_id), error=str(exc))

    async def get_summary(self, tenant_id: uuid.UUID) -> UsageSummary:
        stmt = select(
            func.sum(UsageRecord.units).label("total_units"),
            func.sum(UsageRecord.cost).label("total_cost"),
            func.count(UsageRecord.id).label("record_count"),
        ).where(UsageRecord.tenant_id == tenant_id)
        result = await self.db.execute(stmt)
        row = result.first()

        total_units = row[0] or 0
        total_cost = row[1] or 0.0

        return UsageSummary(
            tenant_id=tenant_id,
            total_units=total_units,
            total_cost=total_cost,
            record_count=row[2] or 0,
            money_saved=round(total_cost * 0.25, 2),
            cost_prevented=round(total_units * 0.005, 2),
        )

    async def get_revenue_dashboard(self, tenant_id: uuid.UUID) -> dict:
        # ── usage_records queries (correct DB: acp_usage) ──────────────────
        agent_stmt = select(
            UsageRecord.agent_id,
            func.sum(UsageRecord.cost).label("cost"),
        ).where(UsageRecord.tenant_id == tenant_id).group_by(UsageRecord.agent_id)
        agent_res = await self.db.execute(agent_stmt)
        cost_per_agent = [
            {"agent_id": str(r[0]), "cost": round(float(r[1]), 4)}
            for r in agent_res.all()
        ]

        tool_stmt = select(
            UsageRecord.tool,
            func.sum(UsageRecord.cost).label("cost"),
        ).where(UsageRecord.tenant_id == tenant_id).group_by(UsageRecord.tool)
        tool_res = await self.db.execute(tool_stmt)
        cost_per_tool = [
            {"tool": r[0], "cost": round(float(r[1]), 4)}
            for r in tool_res.all()
        ]

        risk_weighted = sum(c["cost"] for c in cost_per_agent) * 1.15

        # ── billing SLA stats — fetched from Audit Service HTTP API ─────────
        unbilled_sla = 0
        pending_count = 0
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/billing-stats",
                    headers={**_INTERNAL_HEADERS, "X-Tenant-ID": str(tenant_id)},
                )
                if resp.status_code == 200:
                    stats = resp.json().get("data", {})
                    unbilled_sla = stats.get("unbilled_events_sla", 0)
                    pending_count = stats.get("pending_events", 0)
        except Exception as exc:
            logger.warning("audit_billing_stats_fetch_failed", error=str(exc))

        # ── usage record count for integrity score (acp_usage DB is fine here) ─
        u_count_res = await self.db.execute(
            select(func.count(UsageRecord.id)).where(UsageRecord.tenant_id == tenant_id)
        )
        u_count = u_count_res.scalar() or 0
        # Integrity score: ratio of usage records to audit logs (≈1.0 is perfect)
        # a_count comes from audit service — if we can't get it, use u_count
        a_count = max(u_count + unbilled_sla + pending_count, 1)
        integrity_score = round(u_count / a_count, 4)

        return {
            "cost_per_agent": cost_per_agent,
            "cost_per_tool": cost_per_tool,
            "risk_weighted_billing": round(risk_weighted, 2),
            "unbilled_events_sla": unbilled_sla,
            "pending_events": pending_count,
            "p95_latency_sec": 0,   # Requires cross-service telemetry; reserved for Prometheus
            "p99_latency_sec": 0,   # Reserved
            "billing_integrity_score": integrity_score,
        }

    async def get_anomalies(self, tenant_id: uuid.UUID) -> list[dict]:
        from sqlalchemy import text

        stmt = text("""
            WITH stats AS (
                SELECT agent_id,
                       AVG(units)    AS mean_units,
                       STDDEV(units) AS std_units,
                       COUNT(units)  AS sample_count
                FROM usage_records
                WHERE tenant_id = :tid
                GROUP BY agent_id
            )
            SELECT u.id, u.units, u.cost, u.tool, u.agent_id, u.timestamp,
                   s.mean_units, s.std_units
            FROM usage_records u
            JOIN stats s ON u.agent_id = s.agent_id
            WHERE u.tenant_id = :tid
              AND s.sample_count >= 20
              AND s.std_units > 0
              AND u.units > s.mean_units + (3 * s.std_units)
              AND u.units > 100
            ORDER BY u.timestamp DESC
            LIMIT 10
        """)
        res = await self.db.execute(stmt, {"tid": tenant_id})
        anomalies = []
        for r in res.fetchall():
            anomalies.append({
                "id": str(r.id),
                "type": "spike_in_tokens",
                "agent_id": str(r.agent_id),
                "tool": r.tool,
                "cost": float(r.cost),
                "units": int(r.units),
                "timestamp": r.timestamp.isoformat(),
            })
        return anomalies

    async def list_for_tenant(
        self, tenant_id: uuid.UUID, limit: int = 100
    ) -> Sequence[UsageRecord]:
        stmt = (
            select(UsageRecord)
            .where(UsageRecord.tenant_id == tenant_id)
            .order_by(UsageRecord.timestamp.desc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return result.scalars().all()
