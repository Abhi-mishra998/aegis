-- 003_index_tenant_timestamp.sql (2026-05-14)
-- Composite index for the per-month billing rollup hot query.
-- Why: under single-tenant dev data PG correctly chose Seq Scan, but in
-- multi-tenant production the (tenant_id, timestamp) composite cuts the
-- per-tenant rollup from O(N) to O(log N + per-tenant rows).
--
-- CONCURRENTLY → does not block writes during the build.
-- IF NOT EXISTS → idempotent across redeploys.
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_usage_records_tenant_timestamp
    ON usage_records (tenant_id, timestamp);
