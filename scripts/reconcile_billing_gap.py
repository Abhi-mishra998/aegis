#!/usr/bin/env python3
"""
Reconcile Billing Integrity Gap (Run-3 Fix — 2026-05-13)
=========================================================
Finds execute_tool audit logs in acp_audit that have no matching usage_record
in acp_usage and re-queues them onto the `acp:billing_retry_queue` so the
gateway's existing `_process_billing_queue` worker heals them.

Why a queue replay (not a direct INSERT):
  - Same code path as the live retry worker — one canonical billing pipeline.
  - Forwards idempotency_key (= audit_id) so the value engine dedupes.
  - Works across two physical databases (audit + usage) without an ORM.

Usage:
  python scripts/reconcile_billing_gap.py --dry-run
  python scripts/reconcile_billing_gap.py --execute --hours 1
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

try:
    import psycopg2  # type: ignore
    import redis  # type: ignore
except ImportError as exc:
    print(f"missing dep ({exc}); install psycopg2-binary and redis-py", file=sys.stderr)
    sys.exit(2)


AUDIT_DB = os.environ.get("ACP_AUDIT_DB", "postgresql://postgres:postgres@localhost:5432/acp_audit")
USAGE_DB = os.environ.get("ACP_USAGE_DB", "postgresql://postgres:postgres@localhost:5432/acp_usage")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
RETRY_KEY = "acp:billing_retry_queue"


def _fetch_billable_audits(hours: int) -> list[dict[str, Any]]:
    conn = psycopg2.connect(AUDIT_DB)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, tenant_id, agent_id, tool, decision, metadata_json
                FROM audit_logs
                WHERE action = 'execute_tool'
                  AND decision <> 'reject'
                  AND tenant_id IS NOT NULL
                  AND created_at >= NOW() - (%s || ' hours')::interval
                """,
                (hours,),
            )
            cols = [c.name for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def _fetch_present_audit_ids(audit_ids: list[str]) -> set[str]:
    if not audit_ids:
        return set()
    conn = psycopg2.connect(USAGE_DB)
    try:
        with conn.cursor() as cur:
            # Chunk to keep the IN-list reasonable.
            present: set[str] = set()
            for i in range(0, len(audit_ids), 5000):
                chunk = audit_ids[i:i + 5000]
                cur.execute(
                    "SELECT audit_id FROM usage_records WHERE audit_id = ANY(%s)",
                    (chunk,),
                )
                present.update(str(r[0]) for r in cur.fetchall())
            return present
    finally:
        conn.close()


def _enqueue(rows: list[dict[str, Any]], r: "redis.Redis") -> int:
    pushed = 0
    for row in rows:
        audit_id = str(row["id"])
        tenant_id = str(row["tenant_id"])
        agent_id = str(row["agent_id"]) if row["agent_id"] else None
        meta = row.get("metadata_json") or {}
        tokens = 1
        if isinstance(meta, dict):
            tokens = int(meta.get("tokens") or meta.get("billing_units") or 1)

        payload = {
            "tenant_id": tenant_id,
            "agent_id": agent_id,
            "tool": row.get("tool") or "unknown",
            "units": max(tokens, 1),
            "cost": max(tokens, 1) * 0.001,
            "audit_id": audit_id,
            "idempotency_key": audit_id,
        }
        retry_payload = {
            "payload": payload,
            "action": row.get("decision") or "allow",
            "retry_count": 0,
            "reason": "reconcile_billing_gap",
        }
        r.lpush(RETRY_KEY, json.dumps(retry_payload))
        pushed += 1
    return pushed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--hours", type=int, default=2, help="Look back N hours (default: 2)")
    args = ap.parse_args()

    if not (args.dry_run or args.execute):
        ap.error("specify --dry-run or --execute")

    print(f"[reconcile] window=last {args.hours}h  audit_db={AUDIT_DB}  usage_db={USAGE_DB}")
    audits = _fetch_billable_audits(args.hours)
    print(f"[reconcile] billable audit rows scanned: {len(audits)}")
    if not audits:
        print("[reconcile] nothing to reconcile.")
        return 0

    audit_ids = [str(r["id"]) for r in audits]
    present = _fetch_present_audit_ids(audit_ids)
    missing = [r for r in audits if str(r["id"]) not in present]
    print(f"[reconcile] usage rows present: {len(present)}   missing: {len(missing)}")
    if not missing:
        print("[reconcile] ✅ no gap")
        return 0

    if args.dry_run:
        for r in missing[:5]:
            print(f"  - audit={r['id']} tenant={r['tenant_id']} tool={r.get('tool')}")
        print(f"[reconcile] dry-run: would enqueue {len(missing)} retries onto {RETRY_KEY}")
        return 0

    rclient = redis.Redis.from_url(REDIS_URL)
    pushed = _enqueue(missing, rclient)
    print(f"[reconcile] ✅ enqueued {pushed} retries onto {RETRY_KEY}")
    print("[reconcile] the gateway billing worker will drain them; monitor with:")
    print(f"           redis-cli LLEN {RETRY_KEY}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
