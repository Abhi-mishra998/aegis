-- ============================================================================
-- H-3 FIX (2026-05-13): usage_records partitioning roll-forward.
-- ============================================================================
-- Same pattern as audit_logs: monthly RANGE(timestamp). Critical for billion-
-- row scale because the ON CONFLICT (audit_id) index on usage_records is the
-- enforcement point of billing idempotency. A fragmented unique index there
-- becomes the hot path on every successful execution.
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS usage_records_partitioned (
    LIKE usage_records INCLUDING ALL
) PARTITION BY RANGE (timestamp);

CREATE TABLE IF NOT EXISTS usage_records_y2026m05 PARTITION OF usage_records_partitioned
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE IF NOT EXISTS usage_records_y2026m06 PARTITION OF usage_records_partitioned
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE IF NOT EXISTS usage_records_y2026m07 PARTITION OF usage_records_partitioned
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE IF NOT EXISTS usage_records_y2026m08 PARTITION OF usage_records_partitioned
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');

CREATE TABLE IF NOT EXISTS usage_records_default PARTITION OF usage_records_partitioned
    DEFAULT;

COMMIT;
