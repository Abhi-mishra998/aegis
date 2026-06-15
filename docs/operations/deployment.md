# Deployment

*How code reaches the live Aegis environment. Tarball → S3 → SSM, never GitHub Actions on the EC2.*

As of 2026-06-13 the live deployment is **prod-ha** at `ha.aegisagent.in`: a 2× `m6g.medium` Graviton Auto Scaling Group spanning `ap-south-1a + 1b` behind an Application Load Balancer with WAFv2 in front. RDS is Multi-AZ Postgres (`db.t3.small`); Redis is an ElastiCache replication group (primary + reader). The earlier single-EC2 dev (at `dev.aegisagent.in`) and the 2026-06-01 single-EC2 reference were both folded into this stack. The deploy mechanism (tarball + SSM `send-command`) is unchanged in shape from the dev recipe; the targets list (both ASG members) and the rolling restart are the only material differences.

## The contract

Three properties the deploy pipeline must guarantee:

1. **No GitHub credentials on either EC2.** The instance role has S3 read for the deploys bucket and SSM agent permissions; nothing else.
2. **One operator step.** From a tarball on the laptop to live on `ha.aegisagent.in` is one `aws s3 cp` plus one `aws ssm send-command` targeting both ASG members.
3. **No coordinated cutover required.** Compose recreates one container at a time per host; the ALB drains the in-flight host while the other one keeps serving traffic.

The trade-off vs. a GitHub-Actions-driven deploy is that the laptop becomes the deploy origin. The EC2 host trusts S3 and SSM, not the public internet.

## The path

```mermaid
flowchart LR
    Dev[Developer laptop]
    Build[Local build]
    Tar[tarball /tmp/aegis_*.tar.gz]
    S3D[S3 acp-backups-prodha-…]
    SSM[Systems Manager]
    EC2a[EC2 ASG / m6g.medium / AZ a]
    EC2b[EC2 ASG / m6g.medium / AZ b]
    WAF[WAFv2]
    ALB[ALB acp-prodha-alb]
    Dev --> Build
    Build --> Tar
    Tar -->|aws s3 cp| S3D
    Dev -->|aws ssm send-command --targets a,b| SSM
    SSM --> EC2a
    SSM --> EC2b
    EC2a -->|aws s3 cp| S3D
    EC2b -->|aws s3 cp| S3D
    WAF --> ALB
    ALB --> EC2a
    ALB --> EC2b
```

## Live targets (prod-ha environment)

| Resource | Value |
|---|---|
| ALB hostname | `acp-prodha-alb.ap-south-1.elb.amazonaws.com` (alias of `ha.aegisagent.in`) |
| WAFv2 | `acp-prodha-web-acl` — Common rules + KnownBadInputs + SQLi + per-IP rate limit |
| EC2 ASG | 2 × `m6g.medium` Graviton (1 vCPU / 4 GB), one each in `ap-south-1a` and `ap-south-1b`; **min=max=desired=2** (locked 2026-06-14 to defeat the CPU target-tracking auto-scale-down — see "ASG capacity policy" below) |
| Repo path on each EC2 | `/opt/aegis` |
| Deploy bucket | `s3://acp-backups-prodha-…/deployments/` |
| RDS endpoint | `acp-prodha-postgres.<id>.ap-south-1.rds.amazonaws.com:5432` (Multi-AZ `db.t3.small`) |
| Redis endpoint | `acp-prodha-redis.<id>.aps1.cache.amazonaws.com:6379` (replication group: 1 primary + 1 reader) |
| KMS CMK | `alias/aegis-audit-envelope` (annual rotation) |
| SSM SecureString prefixes | `/aegis-audit/*`, `/aegis-siem/*`, `/aegis-voice-guide/*` |
| Docker network | `infra_default` — every service-name DNS resolves on this network, per host |

## Per-deploy-type recipes

The same path serves three deploy shapes — each requires a different rebuild target.

### UI-only deploy

When only `ui/dist`, `ui/index.html`, `ui/nginx.conf`, or `ui/Dockerfile` changes:

```bash
# 1. Build locally
cd ui && npm run build

# 2. Tar the build output + Dockerfile glue
STAMP=$(date +%s)
tar --exclude='._*' -czf /tmp/aegis_ui_${STAMP}.tar.gz \
    ui/dist ui/index.html ui/nginx.conf ui/Dockerfile

# 3. Upload
aws s3 cp /tmp/aegis_ui_${STAMP}.tar.gz \
  s3://acp-backups-prodha-628478/deployments/ --region ap-south-1

# 4. SSM deploy — target both ASG members so the change lands on both EC2s.
#    Resolve current ASG members:
INSTANCE_IDS=$(aws autoscaling describe-auto-scaling-groups \
  --region ap-south-1 \
  --auto-scaling-group-names acp-prodha-asg \
  | jq -r '.AutoScalingGroups[0].Instances[].InstanceId' | paste -sd ' ' -)

aws ssm send-command --region ap-south-1 \
  --instance-ids $INSTANCE_IDS \
  --document-name AWS-RunShellScript \
  --comment "UI deploy ${STAMP}" \
  --parameters "commands=[\"aws s3 cp s3://acp-backups-prodha-628478/deployments/aegis_ui_${STAMP}.tar.gz /tmp/ui.tar.gz --region ap-south-1 && rm -rf /tmp/_x && mkdir /tmp/_x && tar -xzf /tmp/ui.tar.gz -C /tmp/_x && find /tmp/_x -name '._*' -delete && rm -rf /opt/aegis/ui/dist && cp -r /tmp/_x/ui/* /opt/aegis/ui/ && cd /opt/aegis/infra && docker compose -f docker-compose.yml -f docker-compose.aws.yml build ui && docker compose -f docker-compose.yml -f docker-compose.aws.yml up -d --force-recreate --no-deps ui\"]"
```

The `find -name '._*' -delete` step removes macOS AppleDouble metadata files; without it, alembic on Linux fails with `SyntaxError: source code string cannot contain null bytes`. This is mandatory on any tar built on macOS — see gotcha #8.

### Single backend service

When one Python service changes (e.g., `services/decision/router.py`):

```bash
STAMP=$(date +%s)
tar --exclude='__pycache__' --exclude='._*' \
    -czf /tmp/aegis_decision_${STAMP}.tar.gz services/decision/
aws s3 cp /tmp/aegis_decision_${STAMP}.tar.gz \
  s3://acp-backups-prodha-628478/deployments/ --region ap-south-1
```

SSM script body:

```bash
aws s3 cp s3://acp-backups-prodha-628478/deployments/aegis_decision_${STAMP}.tar.gz /tmp/d.tar.gz --region ap-south-1
rm -rf /tmp/_d && mkdir /tmp/_d && tar -xzf /tmp/d.tar.gz -C /tmp/_d
find /tmp/_d -name '._*' -delete
cp -r /tmp/_d/services/decision/* /opt/aegis/services/decision/
cd /opt/aegis/infra
docker compose -f docker-compose.yml -f docker-compose.aws.yml build decision
docker compose -f docker-compose.yml -f docker-compose.aws.yml up -d --force-recreate --no-deps decision
```

`--no-deps --force-recreate decision` recreates only the decision container; the rest of the stack stays up. Gateway will reconnect on its next health probe.

### Multi-service deploy

Tar each affected tree into the same bundle and run the compose build for each service:

```bash
tar --exclude='._*' -czf /tmp/aegis_multi_${STAMP}.tar.gz \
    services/gateway/ services/decision/ ui/dist ui/nginx.conf
```

Build order doesn't matter — gateway reconnects to its dependencies on the next call.

## Rollback

A rollback is "deploy the previous tarball". The deploy bucket retains every artifact under `deployments/`; the operator picks the previous tarball and re-runs the SSM command with that key.

There is no in-place revert mechanism. The contract: the active bundle is whatever was last deployed.

## Smoke verification

After every deploy, verify externally:

```bash
# Bundle hash from the served index.html
curl -fsS https://ha.aegisagent.in/ | grep -oE 'index-[A-Za-z0-9_-]+\.js'

# Login probe (no plaintext password in this file)
curl -sS -X POST https://ha.aegisagent.in/auth/token \
  -H 'Content-Type: application/json' \
  -H 'X-Tenant-ID: 00000000-0000-0000-0000-000000000001' \
  -d '{"email":"admin@acp.local","password":"REDACTED"}' | head -c 100

# System health
TOKEN=...
curl -sS https://ha.aegisagent.in/system/health \
  -H "Authorization: Bearer $TOKEN" \
  -H 'X-Tenant-ID: 00000000-0000-0000-0000-000000000001' | jq '.services'
```

All three should return healthy values. Expect `healthy: 12 / total: 12` from `/system/health`.

## Non-obvious gotchas catalogued during the 2026-06-01 dev rebuild

These are infra-config bugs that would bite any fresh prod deploy too. Most are not dev-specific; surface them when provisioning new infra.

| # | Symptom | Root cause | Fix |
|---|---|---|---|
| 1 | `terraform destroy` hangs on ALB delete with no clear error | `enable_deletion_protection=true` is the default in `modules/alb` | `aws elbv2 modify-load-balancer-attributes ... Key=deletion_protection.enabled,Value=false` before destroy; set `enable_deletion_protection=false` in the module for non-prod |
| 2 | S3 bucket destroy fails | Versioned buckets retain `Versions[]` and `DeleteMarkers[]` | Purge both via `s3api list-object-versions` + batched `s3api delete-objects` before destroy |
| 3 | Re-apply fails with "secret scheduled for deletion" | `recovery_window_in_days=7` soft-deletes | `aws secretsmanager delete-secret --force-delete-without-recovery` after destroy |
| 4 | RDS master password is the literal string `REPLACE_ME_BEFORE_RDS_APPLY` | `data.aws_secretsmanager_secret_version` reads at plan time | Two-phase apply: `apply -target=module.secrets` → `put-secret-value` → second `apply` so RDS picks up the real value |
| 5 | App boots but every DB call fails password auth | Dev RDS bootstraps only `acp` DB with master `postgres` user; the app expects 9 per-service DBs + 9 `*_user` roles | Run `aegis_dev_db_bootstrap.sql` as master from the EC2 (RDS is private-subnet only) |
| 6 | `identity_graph` logs "password authentication failed" every 30s | `pgbouncer.aws.ini` ships with a hardcoded prod RDS hostname | Rewrite `pgbouncer.aws.ini` post-extract with the dev RDS DNS and a `userlist.txt` whose passwords match the bootstrap SQL. The compose mount is `:ro` — restart pgbouncer after the swap |
| 7 | Dev `.env` clobbered by tar extract, identity service falls back to prod passwords | Local `infra/.env` carries prod credentials and ships with `tar -czf infra/` | `--exclude='infra/.env'` in tar, or re-overwrite `.env` *after* extract |
| 8 | Alembic crashes with `SyntaxError: source code string cannot contain null bytes` | macOS tar leaks `._foo.py` AppleDouble metadata files | `find /opt/aegis -name '._*' -delete` before `docker compose build` on any macOS-sourced tar |
| 9 | Compose fails with "service has neither an image nor a build context specified" | Stale `groq_worker:` block in `docker-compose.aws.yml` references a deleted service | Remove the block, or fold the AWS override into the base file |
| 10 | Compose validation fails before any service starts | `GRAFANA_ADMIN_PASSWORD` is a no-default required var; freshly generated dev `.env` files often lack it | Add `GRAFANA_ADMIN_PASSWORD=` to the dev `.env` |
| 11 | EC2 OOMs during initial healthcheck race, Postgres-dependent services exit 255 | `t4g.small` (2 GB) cannot fit the 14-container stack; `t4g.medium`/`t4g.large` returned `InsufficientInstanceCapacity` in `ap-south-1a` | Use `m6g.medium` (4 GB, 1 vCPU Graviton) — it was immediately available in `ap-south-1a` when the t4g siblings weren't |
| 12 | `/receipts/key` returns 500 with `NoCredentialsError` on a fresh ASG instance | Audit container needs `boto3` for the SSM signing-key provider AND host IMDSv2 needs `http_put_response_hop_limit=2` (containers add one hop via the docker bridge). The Sprint 9 launch template hardcoded `hop_limit=1` | Add `boto3>=1.34` to server extras in `pyproject.toml`. Bump `http_put_response_hop_limit` from `1` to `2` in **both** `infra/terraform/modules/asg/main.tf:34` and `infra/terraform/modules/compute/main.tf:51`. Run `terraform apply -target=module.asg`; ASG creates a new LT version and migrates. |
| 13 | Audit outbox tight-loops `ConnectError` to `http://localhost:8006/usage/record` | `USAGE_SERVICE_URL` env var is unset on the audit container; `settings.USAGE_SERVICE_URL` falls back to the dev default. Saw ~157k retries / 19 min on prod-ha before this was caught | Set `USAGE_SERVICE_URL=http://usage:8000` and `POLICY_SERVICE_URL=http://policy:8000` on the audit service in `infra/docker-compose.yml`. Same pattern as the sprint-8 `BEHAVIOR_SERVICE_URL` fix on the gateway |
| 14 | Gateway uvicorn workers OOM-killed after each request, ALB health-check fails, ASG cycles the instance | 4 uvicorn workers × ~192 MB each = OOM under any real load on the 768 MB prod-ha cap | Drop `--workers 4` to `--workers 2` in the gateway compose entry. Each worker gets ~384 MB; throughput still covers the 20-user infra |
| 15 | Gateway `relation "shadow_policies" does not exist` on every `/execute` | `shadow_eval_hook.py` is the only DB consumer in the gateway and it imports `services.audit.database.SessionLocal`, which uses `settings.DATABASE_URL`. Gateway's compose set that to `postgres@…/acp`, but `shadow_policies` lives in `acp_audit` | Add `DATABASE_URL=postgresql+asyncpg://audit_user:${AUDIT_DB_PASSWORD}@pgbouncer:6432/acp_audit` to the gateway compose `environment:` block — overrides the host `.env` value |
| 16 | AuditLogs page shows "Upstream returned HTML 403" | AWS WAFv2 SQLi managed rule blocks any POST body containing `"limit":N` (reads `LIMIT N` as SQL injection). The UI's `searchLogs` posted to `/audit/logs/search` with `{"limit":15}` | Migrate `auditService.searchLogs` to `GET /audit/logs` with query params, and extend the audit `list_logs` handler to accept `tool`, `start_date`, `end_date` (was POST-only) |
| 17 | Live Demo returns 503 `GROQ_API_KEY not configured` on a fresh ASG instance | The user_data template doesn't set `GROQ_API_KEY`; the env var only exists on instances where it was set manually | **Today**: SSM `sudo sed -i 's|^GROQ_API_KEY=.*|GROQ_API_KEY=<key>|' /opt/aegis/infra/.env` then `docker compose up -d gateway --force-recreate`. **Proper fix**: put the key in Secrets Manager (`acp-prodha/groq_api_key`), pull it in `infra/terraform/environments/prod-ha/user_data.sh` alongside the other secrets, write to `/opt/aegis/infra/.env` |

## What the deploy pipeline does NOT do

- **No automated tests gate the deploy.** Test runs happen on the laptop before the operator decides to deploy.
- **No staged rollout.** A single EC2 cannot canary against itself.
- **No blue-green.** Compose recreates in place. Brief per-service downtime is accepted.
- **No automated rollback.** ALB removes an unhealthy host but does not revert the deploy.

These omissions are intentional at this footprint. A multi-instance production rollout would add staging, canary, and blue-green.

## Next

- [Deployment Topology](../architecture/deployment-topology.md) — what the 2× EC2 ASG behind WAFv2 + ALB looks like end-to-end
- [Backup & Restore](backup-restore.md) — pre-deploy backup strategy
- [Observability](observability.md) — what to watch during a deploy
- [Demo Packs](../introduction/demo-packs.md) — how to populate the UI with demo data after a fresh deploy
