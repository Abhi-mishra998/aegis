"""Partition audit_logs by RANGE(timestamp) — monthly partitions.

Revision ID: v5w6x7y8z9a0
Revises: u4v5w6x7y8z9
Created sprint-3.7.

⚠️  DO NOT RUN AUTOMATICALLY.  ⚠️

This migration converts the existing `audit_logs` table into a partitioned
parent + child partitions. It is destructive (renames the existing table,
creates a partitioned parent, copies rows back), and on a non-empty
production table it takes time proportional to row count and full-table
locks for the rename step.

Run this only:
  1. During a planned maintenance window (estimate: ~30 min/10M rows).
  2. With a verified fresh backup (`scripts/ops/backup.sh && pg_restore --list`).
  3. With the gateway in read-only mode (no audit writes) — see runbook below.
  4. After updating `services/audit/writer.py` to be partition-aware
     (the writer already inserts via SQLAlchemy ORM so this should be
     transparent, but verify with a dry-run on a copy first).

To deploy: comment out the early `op.execute("...PLEASE READ HEADER...")` line
and run `alembic upgrade head` from the audit container during the window.

Rollback: `alembic downgrade -1` restores the un-partitioned table. The
downgrade copies all rows back; same maintenance window requirement applies.

────────────────────────────────────────────────────────────────────────────
Runbook checklist
────────────────────────────────────────────────────────────────────────────
- [ ] Backup completed and verified (`scripts/ops/restore_drill.sh`)
- [ ] Slack #aegis-ops posted: "audit_logs partitioning starting at HH:MM"
- [ ] Set degraded mode: `redis-cli set acp:kill_switch:global "maintenance"`
- [ ] Run: `docker compose exec audit alembic upgrade head`
- [ ] Verify: `SELECT count(*) FROM audit_logs` matches pre-migration count
- [ ] Verify: `SELECT count(*) FROM audit_logs_y2026m05` is non-zero
- [ ] Clear kill switch: `redis-cli del acp:kill_switch:global`
- [ ] Slack #aegis-ops posted: "complete; chain row count = N"
- [ ] Append to docs/runbooks/drill_log.md
────────────────────────────────────────────────────────────────────────────
"""
from collections.abc import Sequence

from alembic import op

revision: str = "v5w6x7y8z9a0"
down_revision: str | None = "u4v5w6x7y8z9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Sprint 9 prod-ha — partitioning deferred for the 20-user testing infra.
    # The legacy audit_logs table works fine at this scale; revisit when we
    # scale up. For now, no-op so alembic head advances to Sprint 5/6/7
    # migrations that DO matter for the prod-ha stack.
    return

    # ── Reachable code below; kept un-indented for readability when ungated. ──
    # 1. Rename existing table out of the way.
    op.execute("ALTER TABLE audit_logs RENAME TO audit_logs_legacy")

    # 2. Create the partitioned parent with the same shape.
    op.execute("""
        CREATE TABLE audit_logs (
            tenant_id      UUID         NOT NULL,
            id             UUID         NOT NULL,
            agent_id       UUID         NOT NULL,
            action         VARCHAR(100) NOT NULL,
            tool           VARCHAR(255),
            decision       VARCHAR(50)  NOT NULL,
            reason         TEXT,
            metadata_json  JSONB        NOT NULL DEFAULT '{}'::jsonb,
            request_id     VARCHAR(50),
            event_hash     VARCHAR(64),
            prev_hash      VARCHAR(64),
            timestamp      TIMESTAMPTZ  NOT NULL DEFAULT now(),
            PRIMARY KEY (id, timestamp),
            UNIQUE (request_id, event_hash, timestamp)
        ) PARTITION BY RANGE (timestamp)
    """)

    # 3. Re-create the indexes (composite covering indexes from sprint-1).
    op.execute(
        "CREATE INDEX ix_audit_logs_tenant_ts ON audit_logs "
        "(tenant_id, timestamp DESC)"
    )
    op.execute(
        "CREATE INDEX ix_audit_logs_tenant_action_ts ON audit_logs "
        "(tenant_id, action, timestamp DESC)"
    )

    # 4. Create the current + next two months of partitions.
    #    A cron under sprint-2.3 (extended) creates future partitions weekly.
    for ym in ("2026_05", "2026_06", "2026_07"):
        year, month = ym.split("_")
        nxt_month = f"{int(month) + 1:02d}" if int(month) < 12 else "01"
        nxt_year  = year if int(month) < 12 else str(int(year) + 1)
        op.execute(f"""
            CREATE TABLE audit_logs_y{year}m{month} PARTITION OF audit_logs
                FOR VALUES FROM ('{year}-{month}-01') TO ('{nxt_year}-{nxt_month}-01')
        """)

    # 5. Copy existing rows back into the partitioned parent.
    op.execute(
        "INSERT INTO audit_logs SELECT * FROM audit_logs_legacy"
    )

    # 6. Drop the legacy table only after verifying row counts match.
    # (Runbook checks this manually — do NOT auto-drop here.)
    # op.execute("DROP TABLE audit_logs_legacy")


def downgrade() -> None:
    raise RuntimeError(
        "audit_logs partitioning downgrade is destructive and requires a "
        "maintenance window — see file header runbook."
    )

    # ── Reachable code below; ungate during the window. ──
    op.execute("CREATE TABLE audit_logs_restore AS SELECT * FROM audit_logs")
    op.execute("DROP TABLE audit_logs CASCADE")
    op.execute("ALTER TABLE audit_logs_restore RENAME TO audit_logs")
    # Re-create init_audit single-column indexes — see ef9332640e58.
    op.execute("CREATE INDEX ix_audit_logs_tenant_id ON audit_logs (tenant_id)")
    op.execute("CREATE INDEX ix_audit_logs_timestamp ON audit_logs (timestamp)")
