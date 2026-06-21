-- P0-3 fix v2: separate audit_owner role + block DDL on audit_logs
-- Handles missing chain_sequence column gracefully (table recreated via
-- SQLAlchemy create_all does not include the DB-only column).
BEGIN;

-- 1. Create audit_owner role (NOLOGIN — ownership target only).
DO $blk$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'audit_owner') THEN
        CREATE ROLE audit_owner NOLOGIN;
    END IF;
END$blk$;

-- 2. Transfer ownership.
ALTER TABLE audit_logs OWNER TO audit_owner;

-- 3. Min runtime perms for audit_user.
REVOKE ALL ON audit_logs FROM audit_user;
GRANT INSERT, SELECT, REFERENCES ON audit_logs TO audit_user;

-- 4. Sequence grant — wrap in exception handler because chain_sequence
--    column only exists when migrations have run; SQLAlchemy create_all
--    doesn't add it.
DO $blk$
DECLARE
    seq text;
BEGIN
    BEGIN
        SELECT pg_get_serial_sequence('audit_logs', 'chain_sequence') INTO seq;
    EXCEPTION WHEN undefined_column THEN
        seq := NULL;
    END;
    IF seq IS NOT NULL THEN
        EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE %s TO audit_user', seq);
    END IF;
END$blk$;

-- 5. Re-create append-only DML trigger.
CREATE OR REPLACE FUNCTION deny_audit_log_mutation() RETURNS trigger AS $fn$
BEGIN
    RAISE EXCEPTION USING ERRCODE = 42501,
        MESSAGE = format('audit_logs is append-only; %s is forbidden', TG_OP);
END;
$fn$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS deny_audit_mutation ON audit_logs;
CREATE TRIGGER deny_audit_mutation
    BEFORE UPDATE OR DELETE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION deny_audit_log_mutation();

-- 6. EVENT TRIGGER: block ALTER TABLE on audit_logs (DDL_COMMAND_END).
CREATE OR REPLACE FUNCTION block_audit_logs_ddl() RETURNS event_trigger AS $fn$
DECLARE
    r record;
BEGIN
    FOR r IN SELECT * FROM pg_event_trigger_ddl_commands()
    LOOP
        IF r.object_identity = 'public.audit_logs' THEN
            RAISE EXCEPTION USING ERRCODE = 42501,
                MESSAGE = format(
                    'audit_logs is protected; %s blocked. Break-glass: ALTER EVENT TRIGGER protect_audit_logs DISABLE (superuser only)',
                    r.command_tag
                );
        END IF;
    END LOOP;
END;
$fn$ LANGUAGE plpgsql;

DROP EVENT TRIGGER IF EXISTS protect_audit_logs;
CREATE EVENT TRIGGER protect_audit_logs
    ON ddl_command_end
    EXECUTE FUNCTION block_audit_logs_ddl();

-- 7. EVENT TRIGGER: block DROP TABLE audit_logs (SQL_DROP).
CREATE OR REPLACE FUNCTION block_audit_logs_drop() RETURNS event_trigger AS $fn$
DECLARE
    r record;
BEGIN
    FOR r IN SELECT * FROM pg_event_trigger_dropped_objects()
    LOOP
        IF r.object_name = 'audit_logs' AND r.object_type = 'table' THEN
            RAISE EXCEPTION USING ERRCODE = 42501,
                MESSAGE = 'audit_logs DROP blocked by protect_audit_logs_drop event trigger';
        END IF;
    END LOOP;
END;
$fn$ LANGUAGE plpgsql;

DROP EVENT TRIGGER IF EXISTS protect_audit_logs_drop;
CREATE EVENT TRIGGER protect_audit_logs_drop
    ON sql_drop
    EXECUTE FUNCTION block_audit_logs_drop();

COMMIT;

SELECT 'P0-3_APPLIED' AS status;
