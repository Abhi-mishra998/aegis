# Disaster Recovery — Drill Log

Append-only log of monthly RDS restore drills. Each entry confirms that an
AWS RDS snapshot can be restored to a working PostgreSQL instance and that
the data set on disk matches the snapshot's point-in-time.

Pattern per drill:
1. Take a manual snapshot of `aegis-prod-postgres`.
2. Restore it to a throw-away instance `aegis-prod-drill-restore-<ts>`.
3. Connect from a prod EC2 (same VPC/SG) and run row counts on key tables.
4. Compare to live prod. Live prod will always have *more* rows in
   append-only tables (audit_logs) and may have *fewer* tenants if the
   demo-cleanup job ran between snapshot and now — both expected.
5. Drop the restored instance + the snapshot.
6. Append a row to this file.

Target: RTO < 1 hour, RPO < 24h (daily automated backups + on-demand drill snapshot).

| Date (UTC) | Snapshot ID | Restore Instance | Restore Mins | Drill Outcome | Notes |
|---|---|---|---|---|---|
| 2026-06-20 | aegis-prod-drill-20260620-1605 | aegis-drill-restore-20260620-1609 | ~12 | PASS | acp_identity/acp_audit/acp_registry all restored. Prod: 7 tenants / 5 users / 5755 audit_logs. Snapshot: 29 tenants / 27 users / 5422 audit_logs (snapshot was taken *before* the hourly demo-cleanup TTL job; prod has since reaped 22 expired demo tenants and added 333 audit rows). Connectivity verified from prod EC2 (sg-03ab1e520b602a268) → restored RDS (sg-0011368a3f8e64f83) over port 5432. Both throw-away instance + snapshot dropped after verify. |

## Next drill due

- 2026-07-20 (monthly cadence).

## If a drill fails

Page on-call. Open SEV-2 incident. Halt next production deploy until the
underlying issue (snapshot corruption, restore IAM, encryption-key access)
is root-caused and fixed. See `disaster_recovery.md` §9 for runbook.
