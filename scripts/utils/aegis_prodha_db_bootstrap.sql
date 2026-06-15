-- Sprint 9 — prod-ha database bootstrap.
--
-- Creates the 9 per-service databases the Aegis stack needs (each
-- service runs alembic migrations into its own schema). For the
-- 20-user testing infra every per-service user shares the master
-- password; rotate to distinct creds via Secrets Manager when scaling.
--
-- Invoke (psql variable substitution):
--
--   docker run --rm -e PGPASSWORD="${RDS_PASSWORD}" \
--       --network=infra_default -v $(pwd):/sql:ro postgres:15-alpine \
--       psql -h ${RDS_HOST} -U postgres -d postgres \
--            -v master_password="${RDS_PASSWORD}" \
--            -f /sql/aegis_prodha_db_bootstrap.sql
--
-- Idempotent — re-runs are no-ops, but CREATE DATABASE must run as
-- top-level statements (not inside DO blocks) per the Postgres
-- restriction.

\set ON_ERROR_STOP off
\echo === Aegis prod-ha DB bootstrap ===

-- ── 1. Create per-service databases ──────────────────────────────────
-- ON_ERROR_STOP is off here so a repeat run skips existing DBs
-- (CREATE DATABASE errors with "already exists", we ignore + continue).
CREATE DATABASE acp_registry        OWNER postgres;
CREATE DATABASE acp_identity        OWNER postgres;
CREATE DATABASE acp_audit           OWNER postgres;
CREATE DATABASE acp_api             OWNER postgres;
CREATE DATABASE acp_usage           OWNER postgres;
CREATE DATABASE acp_identity_graph  OWNER postgres;
CREATE DATABASE acp_flight_recorder OWNER postgres;
CREATE DATABASE acp_autonomy        OWNER postgres;
CREATE DATABASE acp_behavior        OWNER postgres;

\set ON_ERROR_STOP on

-- ── 2. Create per-service login roles ────────────────────────────────
-- CREATE ROLE supports IF NOT EXISTS-style guards via repeat idempotency:
-- we run CREATE ROLE inside ON_ERROR_STOP off and ignore the
-- "already exists" error on re-runs.
\set ON_ERROR_STOP off
CREATE ROLE registry_user        LOGIN PASSWORD :'master_password';
CREATE ROLE identity_user        LOGIN PASSWORD :'master_password';
CREATE ROLE audit_user           LOGIN PASSWORD :'master_password';
CREATE ROLE api_user             LOGIN PASSWORD :'master_password';
CREATE ROLE usage_user           LOGIN PASSWORD :'master_password';
CREATE ROLE identity_graph_user  LOGIN PASSWORD :'master_password';
CREATE ROLE flight_recorder_user LOGIN PASSWORD :'master_password';
CREATE ROLE autonomy_user        LOGIN PASSWORD :'master_password';
CREATE ROLE behavior_user        LOGIN PASSWORD :'master_password';
\set ON_ERROR_STOP on

-- Always update passwords (idempotent on re-run with rotated values).
ALTER ROLE registry_user        WITH PASSWORD :'master_password';
ALTER ROLE identity_user        WITH PASSWORD :'master_password';
ALTER ROLE audit_user           WITH PASSWORD :'master_password';
ALTER ROLE api_user             WITH PASSWORD :'master_password';
ALTER ROLE usage_user           WITH PASSWORD :'master_password';
ALTER ROLE identity_graph_user  WITH PASSWORD :'master_password';
ALTER ROLE flight_recorder_user WITH PASSWORD :'master_password';
ALTER ROLE autonomy_user        WITH PASSWORD :'master_password';
ALTER ROLE behavior_user        WITH PASSWORD :'master_password';

-- ── 3. Grant per-service users access to their database ──────────────
GRANT ALL PRIVILEGES ON DATABASE acp_registry        TO registry_user;
GRANT ALL PRIVILEGES ON DATABASE acp_identity        TO identity_user;
GRANT ALL PRIVILEGES ON DATABASE acp_audit           TO audit_user;
GRANT ALL PRIVILEGES ON DATABASE acp_api             TO api_user;
GRANT ALL PRIVILEGES ON DATABASE acp_usage           TO usage_user;
GRANT ALL PRIVILEGES ON DATABASE acp_identity_graph  TO identity_graph_user;
GRANT ALL PRIVILEGES ON DATABASE acp_flight_recorder TO flight_recorder_user;
GRANT ALL PRIVILEGES ON DATABASE acp_autonomy        TO autonomy_user;
GRANT ALL PRIVILEGES ON DATABASE acp_behavior        TO behavior_user;

-- ── 4. Per-DB schema grants ──────────────────────────────────────────
\connect acp_registry
GRANT ALL ON SCHEMA public TO registry_user;
\connect acp_identity
GRANT ALL ON SCHEMA public TO identity_user;
\connect acp_audit
GRANT ALL ON SCHEMA public TO audit_user;
\connect acp_api
GRANT ALL ON SCHEMA public TO api_user;
\connect acp_usage
GRANT ALL ON SCHEMA public TO usage_user;
\connect acp_identity_graph
GRANT ALL ON SCHEMA public TO identity_graph_user;
\connect acp_flight_recorder
GRANT ALL ON SCHEMA public TO flight_recorder_user;
\connect acp_autonomy
GRANT ALL ON SCHEMA public TO autonomy_user;
\connect acp_behavior
GRANT ALL ON SCHEMA public TO behavior_user;

\echo === Bootstrap complete ===
