# Pending Alembic Migration — audit_logs append-only trigger

## New head

- Service: `audit`
- Revision: `3a519b48a6f2`
- Down revision: `y0a1b2c3d4e5`
- File: `services/audit/alembic/versions/3a519b48a6f2_audit_log_append_only_trigger.py`

## Summary

Adds a PostgreSQL `BEFORE UPDATE OR DELETE` trigger on `audit_logs` that
raises `P0001` and aborts the transaction on any mutation attempt. The
audit log is the cryptographic source of truth — daily Merkle roots
chain over it — and application code never UPDATEs or DELETEs from this
table. This migration enforces the invariant at the database layer so a
compromised admin, an ORM bug, or a SQL-injection cannot silently mutate
chain rows.

## Apply on prod-ha

Run from the audit container (or any host with the audit service
DB credentials and the repo mounted):

```
alembic -c services/audit/alembic.ini upgrade head
```

Expected output ends with:

```
INFO  [alembic.runtime.migration] Running upgrade y0a1b2c3d4e5 -> 3a519b48a6f2, audit_logs append-only enforcement (database-level trigger)
```

## Verify after apply

Inside `psql` against the audit DB:

```sql
-- Trigger should be present.
SELECT tgname, tgenabled
  FROM pg_trigger
 WHERE tgname = 'deny_audit_log_mutation';

-- These should both raise: ERROR  audit_logs is append-only; UPDATE/DELETE is forbidden
BEGIN; UPDATE audit_logs SET reason = 'tamper' WHERE id = (SELECT id FROM audit_logs LIMIT 1); ROLLBACK;
BEGIN; DELETE FROM audit_logs WHERE id = (SELECT id FROM audit_logs LIMIT 1); ROLLBACK;

-- INSERT must still succeed (writer path).
-- (No standalone test — the gateway/audit-writer integration tests cover this.)
```

## Rollback

```
alembic -c services/audit/alembic.ini downgrade -1
```

The downgrade drops both the trigger and the function. After downgrade,
mutations are no longer blocked at the DB layer.
