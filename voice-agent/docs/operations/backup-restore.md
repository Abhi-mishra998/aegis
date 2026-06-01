# Backup & Restore

*The encrypted-nightly-backup loop, the restore drill that runs against an isolated VPC, the SLA for recovery time, and the chain-of-custody for tamper-evidence.*

## What gets backed up

Source: `scripts/ops/backup.sh`.

| Source | Frequency | Encryption | Destination |
|---|---|---|---|
| Every per-service Postgres database | Nightly | `age` (preferred) or `gpg` (fallback) | S3 `acp-backups-prod` |
| `transparency_roots` snapshot | Nightly | Same | Same bucket, separate prefix |
| Audit receipts mirror | Continuous | Server-side S3 encryption | S3 `acp-receipts-prod` |

The 11 logical Postgres databases (see [Data Model](../architecture/data-model.md)) are each `pg_dump`-ed in custom format, encrypted, and uploaded with a timestamp-suffixed key.

S3 buckets carry lifecycle policies: standard for 90 days, then Glacier for 7 years to satisfy SOC 2 retention.

## The backup script

`scripts/ops/backup.sh` runs nightly via cron on a dedicated backup host. Run modes:

```bash
./backup.sh --dry-run       # print plan, no pg_dump, no upload
./backup.sh                 # full run; exits non-zero on any failure
./backup.sh --no-verify     # skip throwaway-restore verify (incremental only)
```

The script does four things, in order:

1. **`pg_dump --format=custom`** every database listed in `DATABASES` (defaults match `infra/pgbouncer.ini`).
2. **Encrypt with `age`** to the public-key recipient `$ACP_BACKUP_AGE_RECIPIENT`. Falls back to `gpg` if `age` is unavailable. Encryption keys NEVER live in the repo — provision them out-of-band (KMS, hardware key) and inject via env.
3. **Upload to S3** via `aws s3 cp`. `$ACP_BACKUP_S3_BUCKET` is required.
4. **Verify restorability** in a throwaway container: `pg_restore --list` of the just-uploaded artifact plus a `SELECT count(*) FROM audit_logs` smoke check against an `acp_backup_verify` ephemeral Postgres.

Required env (unless `--dry-run`):

```
ACP_BACKUP_AGE_RECIPIENT       age public key (preferred)
ACP_BACKUP_GPG_RECIPIENT       gpg recipient (fallback)
ACP_BACKUP_S3_BUCKET           s3://bucket/path
ACP_BACKUP_S3_ENDPOINT         (optional) for MinIO/Wasabi/etc.
POSTGRES_HOST                  default: acp_postgres
POSTGRES_PORT                  default: 5432
POSTGRES_USER                  default: postgres
PGPASSWORD                     required for pg_dump
```

Exit codes:

- `0` — every step succeeded.
- `1` — a per-database failure (`pg_dump` / encrypt / upload / verify).
- `2` — configuration error (missing env, missing tools).

## The restore drill

The drill is "deploy the backup to an isolated stack and verify it works." Runs at least quarterly, ideally monthly.

Source: `scripts/ops/restore_drill.sh`.

### Isolation guarantees

- Separate docker-compose project name (`-p acp_drill_<ts>`).
- Separate user-defined bridge network (`acp_drill_<ts>_net`). Drill containers cannot resolve `acp_postgres` or any prod host.
- Separate Postgres data volume (anonymous, destroyed on exit).
- Separate Redis (no shared kill-switch state).

A drill can never affect production by design.

### Required env

In addition to the backup-decryption env:

```
ACP_BACKUP_AGE_IDENTITY  (path to age private key file)  -OR-
ACP_BACKUP_GPG_HOMEDIR   (gpg homedir holding the decrypt key)
```

### Run modes

```bash
./restore_drill.sh --dry-run    # print plan, no docker
./restore_drill.sh              # full drill
./restore_drill.sh --keep       # keep drill containers for manual inspection
```

The script:

1. Lists the most recent backups: `aws s3 ls s3://acp-backups-prod/`.
2. Downloads each database's encrypted dump.
3. Decrypts with `age` (or `gpg`).
4. Spins up the isolated docker-compose project.
5. Restores each database.
6. Runs `acp verify-chain` against the drilled stack to confirm the audit chain is intact post-restore.
7. Runs `scripts/ops/reconcile.py` to verify audit↔usage parity.
8. Writes a verdict to `reports/restore_drill/{ts}.json`.

Expected output: `Restore drill PASSED — N rows verified`.

### Drill log

Each drill is appended to `docs/runbooks/restore_drill.md`:

| Date | Operator | Backup Date | Duration | Row Counts | Result |
|---|---|---|---|---|---|
| 2026-05-17 | system | 2026-05-16 | 8m | audit=12450 tenants=3 usage=8900 | PASS |

The log is the audit trail for SOC 2: it proves the recovery procedure works.

## Recovery time objective

Target: **full restore in under 15 minutes**. Alert if a drill exceeds this.

The objective implies:

- Backups must be downloadable from S3 in under 1 minute (typical for 100 MB encrypted dumps).
- Decryption must complete in under 1 minute.
- Restore must complete in under 10 minutes per database.
- Verification must complete in under 3 minutes.

If any of those exceed budget on the drill, file an issue.

## Recovery point objective

Target: **at most 24 hours of data loss in a worst-case restore from nightly backup**.

The audit chain's continuous receipt mirror to S3 (`acp-receipts-prod`) reduces this for audit data specifically — each row's receipt is uploaded right after the row is written. A point-in-time recovery from receipts plus the most recent nightly dump can rebuild the chain with sub-minute data loss for the audit chain.

## Chain-of-custody for tamper evidence

Backups are encrypted *before* leaving the host; the encryption key is held outside the platform. A compromise of either the EC2 or the S3 bucket alone does not expose backup contents.

For audit-chain tamper evidence specifically:

- Backups include `transparency_roots`. Customers archiving daily roots can detect any rewrite of the audit chain post-restore.
- The restore drill runs `acp verify-chain` on the drilled stack to confirm the chain reconstructed from backup is internally consistent.

## What the backup does NOT cover

- **Live Redis state.** Kill switch, rate-limit counters, the audit stream. These are runtime state; restoring from backup means the kill switch is disengaged and the rate-limit counters reset. The audit Redis stream is drained by the worker before the row reaches Postgres, so the durable record is in Postgres.
- **In-flight tool executions.** A restore returns the platform to the last `pg_dump` boundary. Requests in flight when the disaster occurred are lost.
- **Customer data outside the platform.** Tool outputs that returned to the caller and were never persisted in Aegis are not backed up — Aegis records only the audit row, not the tool result.

## Quarterly DR exercise

A full disaster-recovery exercise runs quarterly:

1. Run the restore drill against the latest backup.
2. Verify the drilled stack passes all health checks.
3. Run a Playground attack scenario against the drilled stack to confirm policy enforcement still works.
4. Run `acp verify-chain` on the drilled stack.
5. Record the exercise outcome in the drill log.

A failed exercise blocks production deploys until resolved.

## Common failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `age` decrypt fails | Wrong identity file | Confirm `ACP_BACKUP_AGE_IDENTITY` matches the recipient used at backup |
| `pg_restore` ERROR on FK | Restore order wrong | Restore parents first; the drill script does this |
| `acp verify-chain` fails post-restore | Chain rows present but `transparency_historical_keys` empty | Confirm the historical-keys table was included in the backup |
| Drill exceeds 15 minutes | Slow S3 download | Verify endpoint and region |
| Verify smoke fails | DB role / passwords mismatch | The drill uses ephemeral creds; confirm `RESTORE_DB_PASSWORD` set |

## Next

- [Audit service](../services/audit.md) — owns the receipt mirror and `transparency_roots`
- [Key Rotation](key-rotation.md) — runs in lockstep with backup-key rotation
- [Deployment Topology](../architecture/deployment-topology.md) — where the EC2s and RDS live
- [Audit Chain Violation runbook](runbooks/audit-chain-violation.md) — post-restore verification
