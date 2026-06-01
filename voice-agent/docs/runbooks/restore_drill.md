# Restore Drill Runbook

## Purpose
Verify that the backup restore process works before it is needed in a real incident. Must be run at least quarterly.

## Prerequisites
- AWS CLI configured with access to `s3://acp-backups-abhishek-prod/prod`
- Docker available for isolated-network restore
- `scripts/ops/restore_drill.sh` present

## Steps

### 1. List available backups
```bash
aws s3 ls s3://acp-backups-abhishek-prod/prod/ --region ap-south-1 | sort | tail -5
```

### 2. Run the restore drill
```bash
# Pulls the latest backup, decrypts with age, restores into an isolated Postgres container
bash scripts/ops/restore_drill.sh

# Expected output: "Restore drill PASSED — N rows verified"
```

The script:
1. Downloads the latest `.sql.age` backup from S3
2. Decrypts with the age private key (from `ACP_AGE_PRIVATE_KEY` env var)
3. Spins up an isolated Postgres container on a private Docker network
4. Restores the dump
5. Runs row-count sanity checks on `audit_logs`, `tenants`, `usage_events`
6. Tears down the isolated container

### 3. Record the result
Append a row to the drill log below.

## Drill Log

| Date | Operator | Backup Date | Duration | Row Counts | Result |
|------|----------|-------------|----------|------------|--------|
| 2026-05-17 | system | 2026-05-16 | 8m | audit=12450 tenants=3 usage=8900 | PASS |

## Recovery time objective
Target: full restore in under 15 minutes. Alert if drill exceeds this.

## See also
- `scripts/ops/restore_drill.sh`
- `scripts/ops/backup.sh`
