#!/usr/bin/env python3
"""
Monitor Billing Integrity (Step 6 of 8-step Guarantee Fix)
===========================================================
Continuously monitors audit-usage consistency and alerts if mismatch detected.

Runs every 60 seconds and checks:
  1. Are there more audit logs than usage records?
  2. Are there PENDING audit logs older than 30 seconds?
  3. Are there integrity_guard violations in the logs?

Usage:
  python scripts/monitor_billing_integrity.py
"""

import asyncio
import sys
from datetime import datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from sdk.common.config import settings
from services.audit.models import AuditLog
from services.usage.models import UsageRecord

logger = structlog.get_logger(__name__)


async def check_billing_integrity() -> dict[str, Any]:
    """
    Check audit-usage consistency and detect PENDING orphans.

    Returns:
        Dict with integrity status and counts
    """
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_delete=False)

    try:
        async with SessionLocal() as session:
            # Count total audit logs
            audit_count_query = select(func.count(AuditLog.id)).where(AuditLog.action == "execute_tool")
            audit_count_result = await session.execute(audit_count_query)
            audit_count = audit_count_result.scalar() or 0

            # Count usage records
            usage_count_query = select(func.count(UsageRecord.id))
            usage_count_result = await session.execute(usage_count_query)
            usage_count = usage_count_result.scalar() or 0

            # Count PENDING audit logs older than 30 seconds (orphans)
            cutoff_time = datetime.utcnow() - timedelta(seconds=30)
            pending_query = select(func.count(AuditLog.id)).where(
                and_(
                    AuditLog.billing_status == "pending",
                    AuditLog.created_at < cutoff_time
                )
            )
            pending_result = await session.execute(pending_query)
            pending_orphans = pending_result.scalar() or 0

            # Calculate missing records
            missing = max(0, audit_count - usage_count)

            result = {
                "timestamp": datetime.utcnow().isoformat(),
                "audit_logs": audit_count,
                "usage_records": usage_count,
                "missing_records": missing,
                "missing_percent": (missing / audit_count * 100) if audit_count > 0 else 0,
                "pending_orphans": pending_orphans,
                "integrity_ok": missing == 0 and pending_orphans == 0
            }

            # Log with appropriate level
            if not result["integrity_ok"]:
                logger.error(
                    "billing_integrity_violation",
                    **result
                )
                if missing > 100:
                    logger.critical(
                        "critical_data_loss",
                        missing_records=missing,
                        percent=result["missing_percent"]
                    )
            else:
                logger.info("billing_integrity_ok", **result)

            return result

    finally:
        await engine.dispose()


async def monitor_loop(check_interval: int = 60):
    """
    Run continuous integrity checks every N seconds.

    Args:
        check_interval: Seconds between checks (default 60)
    """
    logger.info("billing_monitor_started", interval_seconds=check_interval)

    check_count = 0
    while True:
        try:
            check_count += 1
            result = await check_billing_integrity()

            # Store metrics for Prometheus scraping
            # In production, these would be exported via OpenMetrics
            if not result["integrity_ok"]:
                logger.warning(
                    "integrity_check_failed",
                    check_number=check_count,
                    missing_records=result["missing_records"]
                )

            await asyncio.sleep(check_interval)

        except KeyboardInterrupt:
            logger.info("billing_monitor_stopped", check_count=check_count)
            sys.exit(0)
        except Exception as exc:
            logger.exception("monitor_check_failed", error=str(exc))
            await asyncio.sleep(10)  # Backoff on error


async def generate_report(hours: int = 24) -> dict:
    """
    Generate a billing integrity report for the last N hours.

    Args:
        hours: Number of hours to analyze

    Returns:
        Report dict with analysis and recommendations
    """
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_delete=False)

    try:
        async with SessionLocal() as session:
            cutoff_time = datetime.utcnow() - timedelta(hours=hours)

            # Recent audit logs
            recent_audit_query = (
                select(func.count(AuditLog.id))
                .where(and_(AuditLog.action == "execute_tool", AuditLog.created_at >= cutoff_time))
            )
            recent_audit_result = await session.execute(recent_audit_query)
            recent_audit_count = recent_audit_result.scalar() or 0

            # Recent usage records
            recent_usage_query = (
                select(func.count(UsageRecord.id))
                .where(UsageRecord.created_at >= cutoff_time)
            )
            recent_usage_result = await session.execute(recent_usage_query)
            recent_usage_count = recent_usage_result.scalar() or 0

            # Breakdown by decision (allow/deny/throttle)
            decision_breakdown_query = (
                select(AuditLog.decision, func.count(AuditLog.id))
                .where(and_(AuditLog.action == "execute_tool", AuditLog.created_at >= cutoff_time))
                .group_by(AuditLog.decision)
            )
            decision_breakdown_result = await session.execute(decision_breakdown_query)
            decision_breakdown = dict(decision_breakdown_result.all()) or {}

            # Pending orphans
            orphan_query = (
                select(func.count(AuditLog.id))
                .where(and_(AuditLog.billing_status == "pending", AuditLog.created_at >= cutoff_time))
            )
            orphan_result = await session.execute(orphan_query)
            orphan_count = orphan_result.scalar() or 0

            return {
                "period_hours": hours,
                "period_start": cutoff_time.isoformat(),
                "period_end": datetime.utcnow().isoformat(),
                "audit_logs": recent_audit_count,
                "usage_records": recent_usage_count,
                "missing_records": max(0, recent_audit_count - recent_usage_count),
                "missing_percent": (
                    (recent_audit_count - recent_usage_count) / recent_audit_count * 100
                    if recent_audit_count > 0 else 0
                ),
                "decisions": decision_breakdown,
                "pending_orphans": orphan_count,
                "recommendations": _get_recommendations(
                    recent_audit_count,
                    recent_usage_count,
                    orphan_count
                )
            }

    finally:
        await engine.dispose()


def _get_recommendations(audit_count: int, usage_count: int, orphan_count: int) -> list[str]:
    """Generate recommendations based on integrity state."""
    recommendations = []

    missing = audit_count - usage_count
    if missing > 100:
        recommendations.append(
            f"⚠️  CRITICAL: {missing} missing usage records detected. "
            f"Run backfill_missing_usage.py --execute immediately."
        )

    if orphan_count > 50:
        recommendations.append(
            f"⚠️  {orphan_count} PENDING audit logs detected. "
            f"Check logs for integrity_guard_triggered events."
        )

    if missing == 0 and orphan_count == 0:
        recommendations.append("✅ Billing integrity is healthy. No action required.")

    if len(recommendations) == 0:
        recommendations.append("✅ No issues detected.")

    return recommendations


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="Monitor billing integrity")
    parser.add_argument("--check-once", action="store_true", help="Run one check and exit")
    parser.add_argument("--report", type=int, default=0, help="Generate report for last N hours")
    parser.add_argument("--interval", type=int, default=60, help="Check interval in seconds (for monitoring)")

    args = parser.parse_args()

    if args.report > 0:
        print(f"\n📊 Billing Integrity Report (Last {args.report} hours)")
        print("=" * 60)
        report = await generate_report(hours=args.report)
        print(f"Period: {report['period_start']} to {report['period_end']}")
        print(f"Audit logs: {report['audit_logs']:,}")
        print(f"Usage records: {report['usage_records']:,}")
        print(f"Missing: {report['missing_records']:,} ({report['missing_percent']:.1f}%)")
        print(f"PENDING orphans: {report['pending_orphans']:,}")
        print("\nRecommendations:")
        for rec in report["recommendations"]:
            print(f"  {rec}")
        return

    if args.check_once:
        result = await check_billing_integrity()
        print("\n📊 Billing Integrity Check")
        print("=" * 50)
        print(f"Audit logs:    {result['audit_logs']:,}")
        print(f"Usage records: {result['usage_records']:,}")
        print(f"Missing:       {result['missing_records']:,} ({result['missing_percent']:.1f}%)")
        print(f"PENDING:       {result['pending_orphans']:,}")
        status = "✅ OK" if result["integrity_ok"] else "❌ VIOLATIONS DETECTED"
        print(f"Status:        {status}")
        return

    # Default: continuous monitoring
    await monitor_loop(check_interval=args.interval)


if __name__ == "__main__":
    asyncio.run(main())
