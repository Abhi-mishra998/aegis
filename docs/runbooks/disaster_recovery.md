# Aegis — Disaster Recovery Runbook

## RTO / RPO targets

| Scenario | RTO | RPO | Verified by |
|----------|-----|-----|-------------|
| Single EC2 host loss | < 5 min | 0 | ASG auto-replacement, ALB drains in 30 s |
| Full app-tier wipe (both EC2) | < 20 min | 0 | New ASG launch + bundle pull + compose up (~12 min) + warmup |
| RDS primary failure (in-AZ) | < 5 min | 0 | RDS Multi-AZ automatic failover (we run Multi-AZ) |
| Full RDS data loss | < 1 h | ≤ 5 min | PITR restore from automated backup |
| ElastiCache cluster loss | < 5 min | < 60 s | Multi-AZ replica promotion |
| Region (`ap-south-1`) loss | < 4 h | ≤ 24 h | Manual recovery from cross-region S3 snapshots — see §5 |
| Operator deletes prod by accident | < 1 h | ≤ 5 min | `prevent_destroy` lifecycle on critical TF + final-snapshot retention |

RPO = 0 for in-AZ events because RDS Multi-AZ + ElastiCache replicas are synchronous within the VPC.

## 1 — Single instance loss

**Trigger:** EC2 hardware fault, instance unreachable, ALB target unhealthy > 5 min.

**Automated path** (no human needed):
1. ASG `aegis-prod-asg` detects unhealthy target → terminates instance.
2. Launches replacement from launch template (current SHA in `/aegis/prod/current_bundle_sha`).
3. `user_data` pulls `bundle-<sha>.tar.gz` from S3, generates `.env` from SSM/Secrets, runs idempotent psql init, brings up docker compose.
4. ALB target group health check pass (`GET /health` on UI port 5173 → 200) → traffic resumes.

**Manual verification after the event:**
```bash
aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names aegis-prod-asg \
  --query 'AutoScalingGroups[0].Instances[].[InstanceId,LifecycleState,HealthStatus]' --output table

aws elbv2 describe-target-health \
  --target-group-arn $(aws elbv2 describe-target-groups \
    --query 'TargetGroups[?starts_with(TargetGroupName,`aegtg-`)].TargetGroupArn|[0]' --output text)
```

Expect: 2 InService/Healthy targets in different AZs.

## 2 — Both EC2 hosts lost

Same automated recovery, just doubles the wait. ASG min=2 keeps a constant 2-host floor.
If ASG itself is broken, see §6 (terraform replay).

## 3 — RDS primary failure

**Automatic in-AZ failover:** RDS detects → promotes standby in < 60 s. Endpoint DNS unchanged. Apps reconnect via asyncpg pool reset.

**Manual force failover** (for drills):
```bash
aws rds reboot-db-instance --db-instance-identifier aegis-prod-postgres --force-failover
```

Drill cadence: run quarterly. Expected app downtime: < 10 s of 5xx during reconnect.

## 4 — Full DB data loss / corruption

**RTO < 1 h, RPO ≤ 5 min** via PITR.

1. Identify the cleanest target time (just before corruption):
```bash
aws rds describe-db-instances --db-instance-identifier aegis-prod-postgres \
  --query 'DBInstances[0].LatestRestorableTime'
```

2. Restore to a new instance:
```bash
TS="2026-06-20T14:00:00Z"
aws rds restore-db-instance-to-point-in-time \
  --source-db-instance-identifier aegis-prod-postgres \
  --target-db-instance-identifier aegis-prod-postgres-restored \
  --restore-time "$TS" \
  --multi-az --no-publicly-accessible \
  --db-subnet-group-name aegis-prod-db-subnets \
  --vpc-security-group-ids $(aws rds describe-db-instances \
    --db-instance-identifier aegis-prod-postgres \
    --query 'DBInstances[0].VpcSecurityGroups[0].VpcSecurityGroupId' --output text)
```

3. After verification, repoint app:
```bash
# Update SSM/.env, then rolling restart
aws ssm send-command --instance-ids $IDS \
  --document-name AWS-RunShellScript \
  --parameters 'commands=["sed -i \"s|aegis-prod-postgres\\.|aegis-prod-postgres-restored\\.|g\" /opt/aegis/infra/.env /opt/aegis/infra/pgbouncer.aws.ini","docker restart $(docker ps -q)"]'
```

4. After 24 h of clean traffic, delete the old broken instance.

**Drill cadence:** monthly. Record completion in `docs/runbooks/dr_drill_log.md`.

## 5 — Region loss (`ap-south-1` outage)

**RTO < 4 h, RPO ≤ 24 h** — requires cross-region snapshot setup.

Pre-requisite (set up once):
```bash
# Daily cross-region snapshot copy via EventBridge → Lambda
# Documented in: infra/terraform/modules/rds_cross_region/
```

Recovery:
1. Spin up clone region (`ap-southeast-1`):
```bash
cd infra/terraform
terraform workspace select dr
terraform apply -var aws_region=ap-southeast-1 -var-file=envs/prod/terraform.tfvars
```

2. Restore RDS from cross-region snapshot.

3. Update Route 53 to point `aegisagent.in` at the new ALB.

4. Re-provision SSM secrets (they're region-scoped):
```bash
bash infra/terraform/post_apply_populate_secrets.sh
```

## 6 — Operator wiped infra

**Protected by:**
- `prevent_destroy = true` lifecycle on RDS, public-roots S3, ALB Route 53 records.
- RDS `final_snapshot_identifier` set + `skip_final_snapshot=false`.
- `deletion_protection=true` on RDS instance.
- ASG `min=2` enforced by terraform plan diff alerts.

**Recovery:** re-run `terraform apply` against the saved state in S3 (`s3://aegis-prod-tfstate-…`); RDS/S3 will not have been destroyed.

## 7 — Public roots bucket compromise

The `aegis-public-roots-628478946931` bucket is the customer-verifiable transparency log. If it's been tampered with:

1. Customers who archived a prior root.json can detect the discontinuity via `acp verify-root --consistency`.
2. Restore from versioned S3 (versioning is enabled on the bucket).
3. Rotate `RECEIPT_SIGNING_PROVIDER` to a new ed25519 key; archive the prior public key under `keys/public-<old-kid>.pem` so old receipts still verify.

## 8 — Backup verification (monthly)

Anyone running this **must** complete it once per month and log in `dr_drill_log.md`:

```bash
# 1. Take a fresh snapshot
aws rds create-db-snapshot --db-instance-identifier aegis-prod-postgres \
  --db-snapshot-identifier drill-$(date +%Y%m%d)

# 2. Restore to throw-away
aws rds restore-db-instance-from-db-snapshot \
  --db-instance-identifier drill-restore-$(date +%Y%m%d) \
  --db-snapshot-identifier drill-$(date +%Y%m%d) \
  --no-multi-az --no-publicly-accessible \
  --db-subnet-group-name aegis-prod-db-subnets

# 3. Verify schema + row counts match prod
psql -h drill-restore-... -c "SELECT count(*) FROM tenants;"

# 4. Clean up
aws rds delete-db-instance --db-instance-identifier drill-restore-$(date +%Y%m%d) \
  --skip-final-snapshot
```

## On-call escalation

| Severity | Pager | Decision time |
|----------|-------|---------------|
| P0 — full outage | `+91-…` (CTO) | < 5 min ack |
| P1 — RDS failover | `+91-…` (ops oncall) | < 15 min ack |
| P2 — single host | none — ASG self-heals | next-day review |
