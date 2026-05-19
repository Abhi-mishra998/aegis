#!/usr/bin/env python3
"""
Backfill Missing Usage Records (Step 5 of 8-step Guarantee Fix)
================================================================
Identifies audit logs that have no corresponding usage record and creates them.
This fixes the data loss from the first 100-user load test (3,741 missing records).

Usage:
  python scripts/backfill_missing_usage.py --dry-run  # Preview what would be backfilled
  python scripts/backfill_missing_usage.py --execute  # Actually backfill records
"""

import asyncio
import sys
import uuid
from datetime import datetime, timedelta

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from sdk.common.config import settings
from services.audit.models import AuditLog
from services.usage.models import UsageRecord

logger = structlog.get_logger(__name__)


async def backfill_missing_usage(dry_run: bool = True, hours_back: int = 24) -> dict[str, int]:
    """
    Find audit logs without corresponding usage records and create them.

    Args:
        dry_run: If True, report what would be backfilled without modifying DB
        hours_back: Only backfill records from the last N hours (safety: don't backfill old data)

    Returns:
        Dict with counts: {"scanned": int, "missing": int, "backfilled": int}
    """
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_delete=False)

    async with SessionLocal() as session:
        cutoff_time = datetime.utcnow() - timedelta(hours=hours_back)

        # Find audit logs created in the last N hours
        audit_query = (
            select(AuditLog)
            .where(AuditLog.created_at >= cutoff_time)
            .where(AuditLog.action == "execute_tool")
            .order_by(AuditLog.created_at.desc())
        )

        result = await session.execute(audit_query)
        audit_logs = result.scalars().all()

        scanned = len(audit_logs)
        logger.info("audit_logs_scanned", count=scanned, cutoff_time=cutoff_time)

        # Find which ones are missing usage records
        missing_count = 0
        backfilled_count = 0

        for audit_log in audit_logs:
            # Check if usage record exists for this audit_id
            usage_query = select(UsageRecord).where(UsageRecord.audit_id == audit_log.id)
            usage_result = await session.execute(usage_query)
            existing_usage = usage_result.scalar()

            if not existing_usage:
                missing_count += 1

                if not dry_run:
                    # Create usage record from audit log metadata
                    tokens = audit_log.metadata_json.get("tokens", 1) if audit_log.metadata_json else 1

                    usage_record = UsageRecord(
                        id=uuid.uuid4(),
                        tenant_id=audit_log.tenant_id,
                        agent_id=audit_log.agent_id,
                        audit_id=audit_log.id,
                        action=audit_log.decision,
                        tool=audit_log.tool,
                        tokens=max(tokens, 1),
                        cost=max(tokens, 1) * 0.001,
                        created_at=audit_log.created_at,
                        reason="backfill_missing_from_load_test_1"
                    )
                    session.add(usage_record)
                    backfilled_count += 1

                    if backfilled_count % 500 == 0:
                        logger.info("backfill_progress", count=backfilled_count)

        # Commit if executing
        if not dry_run and backfilled_count > 0:
            await session.commit()
            logger.info("backfill_complete", backfilled_count=backfilled_count)

        result_dict = {
            "scanned": scanned,
            "missing": missing_count,
            "backfilled": backfilled_count if not dry_run else 0,
            "dry_run": dry_run
        }

        logger.info("backfill_summary", **result_dict)
        return result_dict

    await engine.dispose()
    return None


async def verify_audit_usage_consistency() -> dict[str, int]:
    """
    Verify that all execute_tool audit logs have corresponding usage records.

    Returns:
        Dict with audit_count, usage_count, and missing_count
    """
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_delete=False)

    async with SessionLocal() as session:
        # Count audit logs
        audit_count_query = select(func.count(AuditLog.id)).where(AuditLog.action == "execute_tool")
        audit_count_result = await session.execute(audit_count_query)
        audit_count = audit_count_result.scalar() or 0

        # Count usage records
        usage_count_query = select(func.count(UsageRecord.id))
        usage_count_result = await session.execute(usage_count_query)
        usage_count = usage_count_result.scalar() or 0

        result_dict = {
            "audit_logs": audit_count,
            "usage_records": usage_count,
            "missing": max(0, audit_count - usage_count)
        }

        logger.info("audit_usage_consistency", **result_dict)
        return result_dict

    await engine.dispose()
    return None


async def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Backfill missing usage records")
    parser.add_argument("--dry-run", action="store_true", help="Preview without modifying DB")
    parser.add_argument("--execute", action="store_true", help="Actually backfill records")
    parser.add_argument("--hours", type=int, default=24, help="Only backfill records from last N hours")

    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        print("ERROR: Specify --dry-run or --execute")
        sys.exit(1)

    print("\n📊 Audit-Usage Consistency Check")
    print("=" * 50)
    consistency = await verify_audit_usage_consistency()
    print(f"  Audit logs:     {consistency['audit_logs']:,}")
    print(f"  Usage records:  {consistency['usage_records']:,}")
    print(f"  Missing:        {consistency['missing']:,}")

    if consistency['missing'] == 0:
        print("\n✅ No missing usage records detected!")
        return

    print(f"\n🔧 Backfilling Missing Records (last {args.hours} hours)")
    print("=" * 50)

    dry_run = args.dry_run and not args.execute
    result = await backfill_missing_usage(dry_run=dry_run, hours_back=args.hours)

    print(f"  Scanned:        {result['scanned']:,}")
    print(f"  Missing:        {result['missing']:,}")
    print(f"  Backfilled:     {result['backfilled']:,}")

    if dry_run:
        print(f"\n💡 This is a DRY RUN. Use --execute to actually backfill {result['missing']:,} records")
    else:
        print(f"\n✅ Successfully backfilled {result['backfilled']:,} missing usage records!")


if __name__ == "__main__":
    asyncio.run(main())
