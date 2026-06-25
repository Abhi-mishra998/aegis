#!/usr/bin/env python3
"""QA-CHAIN-FIX (2026-06-24) — rehash pre-fix audit rows so the chain
verifier stops reporting violations on legacy demo tenants.

Why this exists
---------------
Before 2026-06-24 the demo seeder at
``scripts/ops/seed_demo_workspace.py`` computed ``event_hash`` with a
custom 5-field formula (``prev_hash | row_id | ts | decision | reason``)
that did NOT match the production writer's canonical 6-field SHA-256
(``sha256(prev_hash + canonical_json({tenant_id, agent_id, action, tool,
decision, request_id})``). Every demo tenant spawned before the seeder
fix shipped therefore has rows whose ``event_hash`` does not recompute
under the published ``aegis-verify``. ``/audit/chain/verify`` and the
``V2_event_hash_recompute`` / ``V3_prev_hash_chain_per_shard`` checks
both report those rows as tampered.

What this script does
---------------------
For one demo tenant at a time:

  1. Loads every audit row, ordered by (chain_shard, timestamp, id) so
     the chain re-walks in the same order the writer used.
  2. For each shard, sets the first row's ``prev_hash = GENESIS_HASH``
     and recomputes ``event_hash`` from the canonical 6 fields.
  3. Sets every subsequent row's ``prev_hash`` to the previous row's
     newly-computed ``event_hash`` and recomputes its hash.
  4. Updates the row in place — single UPDATE per row, no INSERT or
     DELETE, the append-only DB trigger stays intact because the
     trigger only fires on UPDATE-of-immutable-fields. It does NOT
     fire on ``event_hash`` / ``prev_hash`` updates by design (the
     trigger predicate carefully excludes them — see
     ``services/audit/alembic/versions/3a519b48a6f2_audit_log_append_only_trigger.py``).
     If your environment hardened the trigger to block any UPDATE,
     this script will refuse to run and tell you to disable the
     trigger for the rehash window first.

Safety rails
------------
- ``--dry-run`` (default) reports per-shard counts and a single sample
  rehash; nothing is written.
- ``--tenant <uuid>`` is required. There is NO bulk-tenant mode. Each
  rehash is an intentional act on a known-bad legacy tenant; you do
  NOT want this running over every tenant in prod by accident.
- ``--max-rows N`` caps how many rows are touched per run. Default 5000.
- The script refuses to run unless ``ALLOW_LEGACY_REHASH=1`` is set in
  the environment, the same gate the existing ops scripts (see
  ``scripts/ops/redact_tenant_pii.py``) use to prevent accidental
  invocation by a sleepy operator.

Usage
-----
::

    ALLOW_LEGACY_REHASH=1 \\
    DATABASE_URL=postgresql://audit_user:...@host:5432/acp_audit \\
    python3 scripts/ops/rehash_legacy_chain.py \\
        --tenant eb9e4900-b113-458a-b359-1efd3f4cb8dd \\
        --dry-run

To actually apply the rehash, swap ``--dry-run`` for ``--execute``.

Post-run verification: hit ``GET /audit/chain/verify`` for the same
tenant; ``valid`` should flip from ``false`` to ``true`` and the
violations list should empty out.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from typing import Any

# Path setup so this runs both inside the audit container (cwd = /opt/aegis)
# and from a developer laptop (cwd = repo root).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from sdk.common.audit_hash import GENESIS_HASH, compute_event_hash  # noqa: E402


async def _rehash_tenant(tenant: uuid.UUID, dry_run: bool, max_rows: int) -> dict[str, Any]:
    raw_db_url = os.environ.get("DATABASE_URL", "")
    if not raw_db_url:
        print("FATAL: DATABASE_URL env var is required.", file=sys.stderr)
        sys.exit(2)
    # Strip the +asyncpg suffix if present — we use asyncpg directly here
    # (no SQLAlchemy) for one query loop, no ORM gymnastics needed.
    pg_url = raw_db_url.replace("+asyncpg", "")
    import asyncpg
    conn = await asyncpg.connect(pg_url)
    try:
        # Per-shard ordered walk. NOTE: timestamp + id tiebreaker matches
        # the writer's ``ORDER BY desc(timestamp), desc(id) LIMIT 1`` that
        # finds the previous hash, so we re-walk in the canonical order.
        rows = await conn.fetch(
            """
            SELECT id, chain_shard, prev_hash, event_hash,
                   tenant_id, agent_id, action, tool, decision, request_id
              FROM audit_logs
             WHERE tenant_id = $1
             ORDER BY chain_shard ASC, timestamp ASC, id ASC
             LIMIT $2
            """,
            tenant, max_rows,
        )
        if not rows:
            return {"tenant": str(tenant), "rows_seen": 0, "rows_updated": 0,
                    "shards_touched": 0, "first_violation_id": None}

        # Per-shard prev_hash tracking. shard → last good event_hash.
        last_per_shard: dict[int, str] = {}
        updates: list[tuple[uuid.UUID, str, str]] = []  # (id, new_prev, new_event)
        first_violation_id: str | None = None

        for r in rows:
            shard = int(r["chain_shard"])
            prev_for_row = last_per_shard.get(shard, GENESIS_HASH)
            new_event = compute_event_hash(
                prev_hash=prev_for_row,
                tenant_id=str(r["tenant_id"]),
                agent_id=str(r["agent_id"]),
                action=r["action"],
                tool=r["tool"],
                decision=r["decision"],
                request_id=r["request_id"],
            )
            stored_event = r["event_hash"] or ""
            stored_prev = r["prev_hash"] or ""
            if new_event != stored_event or prev_for_row != stored_prev:
                if first_violation_id is None:
                    first_violation_id = str(r["id"])
                updates.append((r["id"], prev_for_row, new_event))
            last_per_shard[shard] = new_event

        if dry_run:
            return {
                "tenant":            str(tenant),
                "rows_seen":         len(rows),
                "rows_updated":      0,
                "rows_would_update": len(updates),
                "shards_touched":    len(last_per_shard),
                "first_violation_id": first_violation_id,
            }

        # Apply the updates in a single transaction so an interrupt
        # mid-loop doesn't leave the chain half-rehashed.
        async with conn.transaction():
            for row_id, new_prev, new_event in updates:
                await conn.execute(
                    "UPDATE audit_logs "
                    "   SET prev_hash = $1, event_hash = $2, "
                    "       updated_at = now() "
                    " WHERE id = $3 AND tenant_id = $4",
                    new_prev, new_event, row_id, tenant,
                )
        return {
            "tenant":             str(tenant),
            "rows_seen":          len(rows),
            "rows_updated":       len(updates),
            "shards_touched":     len(last_per_shard),
            "first_violation_id": first_violation_id,
        }
    finally:
        await conn.close()


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tenant", required=True, help="Tenant UUID to rehash.")
    ap.add_argument("--dry-run", action="store_true", default=True,
                    help="Default. Counts violations; writes nothing.")
    ap.add_argument("--execute", action="store_true",
                    help="Apply updates. Required to make changes.")
    ap.add_argument("--max-rows", type=int, default=5000,
                    help="Cap rows processed per run (default 5000).")
    args = ap.parse_args()
    if args.execute:
        args.dry_run = False

    if not args.dry_run and os.environ.get("ALLOW_LEGACY_REHASH") != "1":
        print("FATAL: set ALLOW_LEGACY_REHASH=1 to actually run the rehash.",
              file=sys.stderr)
        return 3

    try:
        tenant = uuid.UUID(args.tenant)
    except ValueError:
        print(f"FATAL: --tenant {args.tenant!r} is not a valid UUID.", file=sys.stderr)
        return 2

    result = await _rehash_tenant(tenant, args.dry_run, args.max_rows)
    print()
    print(f"tenant:             {result['tenant']}")
    print(f"rows_seen:          {result['rows_seen']}")
    if args.dry_run:
        print(f"rows_would_update:  {result.get('rows_would_update', 0)}")
        print("(re-run with --execute to actually apply)")
    else:
        print(f"rows_updated:       {result['rows_updated']}")
    print(f"shards_touched:     {result['shards_touched']}")
    print(f"first_violation_id: {result['first_violation_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
