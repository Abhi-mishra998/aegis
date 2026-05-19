-- ============================================================================
-- H-3 FIX (2026-05-13): Audit logs partitioning roll-forward.
-- ============================================================================
-- Splits audit_logs into monthly RANGE(timestamp) partitions to keep the unique
-- index (request_id, event_hash) tractable at billion-row scale. Apply this
-- as a CONTROLLED MIGRATION during a low-traffic window. Steps are designed to
-- be online (no exclusive lock on the original table) when run sequentially.
--
-- Pre-requisites:
--   - PostgreSQL 11+ (declarative partitioning + DEFAULT partition)
--   - Snapshot/backup taken
--   - Application can tolerate a brief read-lock during the final rename swap
--
-- ============================================================================

BEGIN;

-- 1. Create the partitioned shadow table with the same schema as audit_logs.
CREATE TABLE IF NOT EXISTS audit_logs_partitioned (
    LIKE audit_logs INCLUDING ALL
) PARTITION BY RANGE (timestamp);

-- 2. Pre-create monthly partitions for the rolling 13-month window.
--    Operators MUST extend this list (pg_partman recommended) before swap.
CREATE TABLE IF NOT EXISTS audit_logs_y2026m05 PARTITION OF audit_logs_partitioned
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE IF NOT EXISTS audit_logs_y2026m06 PARTITION OF audit_logs_partitioned
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE IF NOT EXISTS audit_logs_y2026m07 PARTITION OF audit_logs_partitioned
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE IF NOT EXISTS audit_logs_y2026m08 PARTITION OF audit_logs_partitioned
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');

-- 3. Catch-all DEFAULT partition for late writes and out-of-range timestamps.
CREATE TABLE IF NOT EXISTS audit_logs_default PARTITION OF audit_logs_partitioned
    DEFAULT;

COMMIT;

-- 4. Migrate existing data in batches (run OUTSIDE the transaction).
--    Repeat until row count of audit_logs matches sum of partitions.
-- INSERT INTO audit_logs_partitioned
-- SELECT * FROM audit_logs
-- WHERE id > $LAST_ID
-- ORDER BY id
-- LIMIT 10000;

-- 5. Final swap (taken under brief exclusive lock):
-- BEGIN;
-- ALTER TABLE audit_logs RENAME TO audit_logs_legacy;
-- ALTER TABLE audit_logs_partitioned RENAME TO audit_logs;
-- ALTER INDEX audit_logs_partitioned_pkey RENAME TO audit_logs_pkey;
-- COMMIT;

-- 6. After verification (>= 7 days), DROP audit_logs_legacy.

-- ============================================================================
-- usage_records mirror migration. Same pattern; partition on timestamp.
-- ============================================================================
