-- Create databases
CREATE DATABASE acp_registry;
CREATE DATABASE acp_identity;
CREATE DATABASE acp_audit;
CREATE DATABASE acp_api;
CREATE DATABASE acp_usage;
-- 2026-05-13: Runtime Trust Infrastructure
CREATE DATABASE acp_identity_graph;
CREATE DATABASE acp_flight_recorder;
CREATE DATABASE acp_autonomy;
-- 2026-05-16: Behavior/Learning service persistent profiles
CREATE DATABASE acp_behavior;

-- PgBouncer Auth Lookup Setup (2026-05-13)
-- This allows PgBouncer to authenticate all service users by querying pg_shadow.
CREATE SCHEMA IF NOT EXISTS pgbouncer;

CREATE OR REPLACE FUNCTION pgbouncer.user_lookup(in p_user text, out uname text, out phash text)
RETURNS record AS $$
BEGIN
    SELECT usename, passwd FROM pg_shadow WHERE usename = p_user INTO uname, phash;
    RETURN;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

REVOKE ALL ON FUNCTION pgbouncer.user_lookup(text) FROM PUBLIC;

-- Create PgBouncer Admin User (The bootstrap user for auth_query)
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'pgbouncer_admin') THEN
        CREATE USER pgbouncer_admin WITH PASSWORD 'postgres';
    END IF;
END
$$;

GRANT EXECUTE ON FUNCTION pgbouncer.user_lookup(text) TO pgbouncer_admin;

-- SECURITY: Passwords below are LOCAL DEV DEFAULTS ONLY.
-- For staging/production, rotate these via your secrets manager before deployment.
-- Each service user must have a distinct password. Generate with: openssl rand -hex 16
-- Update DATABASE_URL in docker-compose.yml/.env to match any changes here.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'registry_user') THEN
        CREATE USER registry_user WITH PASSWORD 'registry_prod_pwd';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'identity_user') THEN
        CREATE USER identity_user WITH PASSWORD 'identity_prod_pwd';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'audit_user') THEN
        CREATE USER audit_user WITH PASSWORD 'audit_prod_pwd';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'api_user') THEN
        CREATE USER api_user WITH PASSWORD 'api_prod_pwd';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'usage_user') THEN
        CREATE USER usage_user WITH PASSWORD 'usage_prod_pwd';
    END IF;
    -- 2026-05-13: Runtime Trust Infrastructure
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'identity_graph_user') THEN
        CREATE USER identity_graph_user WITH PASSWORD 'identity_graph_prod_pwd';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'flight_recorder_user') THEN
        CREATE USER flight_recorder_user WITH PASSWORD 'flight_recorder_prod_pwd';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'autonomy_user') THEN
        CREATE USER autonomy_user WITH PASSWORD 'autonomy_prod_pwd';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'behavior_user') THEN
        CREATE USER behavior_user WITH PASSWORD 'behavior_prod_pwd';
    END IF;
END
$$;

-- Grant Database-level privileges
GRANT ALL PRIVILEGES ON DATABASE acp_registry TO registry_user;
GRANT ALL PRIVILEGES ON DATABASE acp_identity TO identity_user;
GRANT ALL PRIVILEGES ON DATABASE acp_audit TO audit_user;
GRANT ALL PRIVILEGES ON DATABASE acp_api TO api_user;
GRANT ALL PRIVILEGES ON DATABASE acp_usage TO usage_user;
GRANT ALL PRIVILEGES ON DATABASE acp_identity_graph  TO identity_graph_user;
GRANT ALL PRIVILEGES ON DATABASE acp_flight_recorder TO flight_recorder_user;
GRANT ALL PRIVILEGES ON DATABASE acp_autonomy        TO autonomy_user;
GRANT ALL PRIVILEGES ON DATABASE acp_behavior        TO behavior_user;

-- Staff Engineer Fix: Grant Schema-level privileges (Required for Postgres 15+)
-- We must connect to each DB and grant these, but since this script runs on 'acp' or 'postgres' initial connection,
-- we'll use ALTER DEFAULT PRIVILEGES or ensure migrations can run.
-- The most reliable way in this init script is to ensure the users OWN the public schema in their respective DBs.

\c acp_registry
GRANT ALL ON SCHEMA public TO registry_user;
ALTER SCHEMA public OWNER TO registry_user;

\c acp_identity
GRANT ALL ON SCHEMA public TO identity_user;
ALTER SCHEMA public OWNER TO identity_user;

\c acp_audit
GRANT ALL ON SCHEMA public TO audit_user;
ALTER SCHEMA public OWNER TO audit_user;

\c acp_api
GRANT ALL ON SCHEMA public TO api_user;
ALTER SCHEMA public OWNER TO api_user;

\c acp_usage
GRANT ALL ON SCHEMA public TO usage_user;
ALTER SCHEMA public OWNER TO usage_user;

\c acp_identity_graph
GRANT ALL ON SCHEMA public TO identity_graph_user;
ALTER SCHEMA public OWNER TO identity_graph_user;
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

\c acp_flight_recorder
GRANT ALL ON SCHEMA public TO flight_recorder_user;
ALTER SCHEMA public OWNER TO flight_recorder_user;
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

\c acp_autonomy
GRANT ALL ON SCHEMA public TO autonomy_user;
ALTER SCHEMA public OWNER TO autonomy_user;
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

\c acp_behavior
GRANT ALL ON SCHEMA public TO behavior_user;
ALTER SCHEMA public OWNER TO behavior_user;
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Streaming replication user
\c postgres
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'replicator') THEN
    CREATE USER replicator WITH REPLICATION ENCRYPTED PASSWORD 'replicator_pass_2026';
  END IF;
END $$;
