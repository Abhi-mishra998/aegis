# Pending Alembic Migrations — audit S3 → S4

## Current heads (as of 2026-07-21)

Audit service now has TWO pending migrations, applied together via
`alembic upgrade head`. Deployment order MATTERS — do not run the trigger
migration alone against the current codebase.

## New head (S3 — 2026-07-21)

- Service: `audit`
- Revision: `s3_split_billing_status_2026_07_21`
- Down revision: `p2_11_scim_audit_2026_06_22`
- File: `services/audit/alembic/versions/s3_split_billing_status_2026_07_21.py`

### Summary

Splits `billing_status` off `audit_logs` into a sibling
`audit_billing_status(audit_id, tenant_id, status, updated_at)` table so
the append-only trigger `3a519b48a6f2` can hold `audit_logs` absolutely.
Adds an AFTER INSERT trigger on `audit_logs` that keeps the invariant
"every audit_logs row has a matching audit_billing_status row" without
touching the 5+ INSERT call sites. Backfills existing rows.

The `audit_logs.billing_status` column is preserved for one release to
keep any pinned SIEM verifier working; a follow-up migration will drop
it once callers are proven off.

## Existing pending head (still owed — audit S4)

- Service: `audit`
- Revision: `3a519b48a6f2`
- Down revision: `y0a1b2c3d4e5`
- File: `services/audit/alembic/versions/3a519b48a6f2_audit_log_append_only_trigger.py`

Installs a `BEFORE UPDATE OR DELETE` trigger on `audit_logs` that raises
`P0001` on any mutation attempt.

## Apply on prod-ha — required order

**Step 1 — deploy new application code first.** The gateway + audit
service in this bundle write billing lifecycle to `audit_billing_status`
(the sibling table). The old code path was `UPDATE audit_logs SET
billing_status = 'completed'` — that gets blocked by the trigger, so it
MUST already be gone before Step 2. Verify with:

```
git log --grep 'S3 (2026-07-21)' -- services/audit/router.py
```

**Step 2 — run migrations.** From the audit container (or any host with
the audit service DB credentials and the repo mounted):

```
alembic -c services/audit/alembic.ini upgrade head
```

`alembic` applies both revisions in DAG order:
1. `3a519b48a6f2` — installs the append-only trigger on `audit_logs`.
2. `s3_split_billing_status_2026_07_21` — creates `audit_billing_status`,
   backfills, adds the sync trigger.

Expected output includes:

```
INFO  [alembic.runtime.migration] Running upgrade y0a1b2c3d4e5 -> 3a519b48a6f2, audit_logs append-only enforcement (database-level trigger)
INFO  [alembic.runtime.migration] Running upgrade p2_11_scim_audit_2026_06_22 -> s3_split_billing_status_2026_07_21, split billing_status off audit_logs
```

## Verify after apply

Inside `psql` against the audit DB:

```sql
-- Both triggers present.
SELECT tgname, tgenabled
  FROM pg_trigger
 WHERE tgname IN ('deny_audit_log_mutation', 'sync_audit_billing_status');

-- Backfill worked: every audit_logs row has a sibling.
SELECT
  (SELECT COUNT(*) FROM audit_logs)          AS audit_rows,
  (SELECT COUNT(*) FROM audit_billing_status) AS billing_rows;

-- Sync trigger fires on new inserts. Use a maintenance-window row.
BEGIN;
  INSERT INTO audit_logs (
    id, tenant_id, agent_id, action, tool, decision, timestamp
  ) VALUES (
    gen_random_uuid(), gen_random_uuid(), gen_random_uuid(),
    's3_verify_probe', 'noop', 'allow', NOW()
  ) RETURNING id \gset
  SELECT * FROM audit_billing_status WHERE audit_id = :'id';
ROLLBACK;

-- Append-only trigger blocks the old UPDATE path.
-- Both should raise: ERROR  audit_logs is append-only; UPDATE/DELETE is forbidden
BEGIN; UPDATE audit_logs SET reason = 'tamper' WHERE id = (SELECT id FROM audit_logs LIMIT 1); ROLLBACK;
BEGIN; DELETE FROM audit_logs WHERE id = (SELECT id FROM audit_logs LIMIT 1); ROLLBACK;

-- New closure path via the sibling table works.
BEGIN;
  INSERT INTO audit_billing_status (audit_id, tenant_id, status, updated_at)
  VALUES ((SELECT id FROM audit_logs LIMIT 1), gen_random_uuid(), 'pending', NOW())
  ON CONFLICT (audit_id) DO UPDATE SET status = 'completed', updated_at = NOW();
ROLLBACK;
```

## Rollback

Rolls back **both** revisions. Do not rollback selectively — the sync
trigger references the sibling table.

```
alembic -c services/audit/alembic.ini downgrade y0a1b2c3d4e5
```

The downgrade drops both triggers, the functions, the sibling table, and
its indexes. `audit_logs.billing_status` is unaffected (never dropped in
this pair of migrations) — the reconciler's UPDATE path in the old
application code will function again against the same column.
