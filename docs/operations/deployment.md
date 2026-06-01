# Deployment

*How code reaches the live Aegis environment. Tarball → S3 → SSM, never GitHub Actions on the EC2.*

As of 2026-06-01 the historical two-EC2 production stack at `aegisagent.in` has been decommissioned. The only live deployment is `dev.aegisagent.in`, a single-EC2 footprint sized for ten concurrent reviewers. The deploy mechanism described here is unchanged in shape; the host count, paths, and bucket names are.

## The contract

Three properties the deploy pipeline must guarantee:

1. **No GitHub credentials on the EC2.** The instance role has S3 read for `acp-dev-backups-628478` and SSM agent permissions; nothing else.
2. **One operator step.** From a tarball on the laptop to live on `dev.aegisagent.in` is one `aws s3 cp` plus one `aws ssm send-command`.
3. **No coordinated cutover required.** Compose recreates one container at a time; the ALB removes the host only if the whole instance fails.

The trade-off vs. a GitHub-Actions-driven deploy is that the laptop becomes the deploy origin. The EC2 host trusts S3 and SSM, not the public internet.

## The path

```mermaid
flowchart LR
    Dev[Developer laptop]
    Build[Local build]
    Tar[tarball /tmp/aegis_*.tar.gz]
    S3D[S3 acp-dev-backups-628478]
    SSM[Systems Manager]
    EC2[EC2 i-0f720c100f904291a / m6g.medium / AZ-a]
    ALB[ALB acp-dev-alb-1541605899]
    Dev --> Build
    Build --> Tar
    Tar -->|aws s3 cp| S3D
    Dev -->|aws ssm send-command| SSM
    SSM --> EC2
    EC2 -->|aws s3 cp| S3D
    ALB --> EC2
```

## Live targets (dev environment)

| Resource | Value |
|---|---|
| ALB hostname | `acp-dev-alb-1541605899.ap-south-1.elb.amazonaws.com` (alias of `dev.aegisagent.in`) |
| EC2 instance | `i-0f720c100f904291a` — `m6g.medium`, 1 vCPU / 4 GB Graviton, `ap-south-1a` |
| Repo path on EC2 | `/opt/aegis` (not `/home/ubuntu/aegis` — that was the prod layout) |
| Deploy bucket | `s3://acp-dev-backups-628478/deployments/` |
| RDS endpoint | `acp-postgres-dev.cz0qqg60keaj.ap-south-1.rds.amazonaws.com:5432` (Single-AZ `db.t4g.micro`) |
| Redis endpoint | `acp-redis-dev.1gloza.0001.aps1.cache.amazonaws.com:6379` (1 × `cache.t3.micro`) |
| Docker network | `infra_default` — every service-name DNS resolves on this network |

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
  s3://acp-dev-backups-628478/deployments/ --region ap-south-1

# 4. SSM deploy
aws ssm send-command --region ap-south-1 \
  --instance-ids i-0f720c100f904291a \
  --document-name AWS-RunShellScript \
  --comment "UI deploy ${STAMP}" \
  --parameters "commands=[\"aws s3 cp s3://acp-dev-backups-628478/deployments/aegis_ui_${STAMP}.tar.gz /tmp/ui.tar.gz --region ap-south-1 && rm -rf /tmp/_x && mkdir /tmp/_x && tar -xzf /tmp/ui.tar.gz -C /tmp/_x && find /tmp/_x -name '._*' -delete && rm -rf /opt/aegis/ui/dist && cp -r /tmp/_x/ui/* /opt/aegis/ui/ && cd /opt/aegis/infra && docker compose -f docker-compose.yml -f docker-compose.aws.yml build ui && docker compose -f docker-compose.yml -f docker-compose.aws.yml up -d --force-recreate --no-deps ui\"]"
```

The `find -name '._*' -delete` step removes macOS AppleDouble metadata files; without it, alembic on Linux fails with `SyntaxError: source code string cannot contain null bytes`. This is mandatory on any tar built on macOS — see gotcha #8.

### Single backend service

When one Python service changes (e.g., `services/decision/router.py`):

```bash
STAMP=$(date +%s)
tar --exclude='__pycache__' --exclude='._*' \
    -czf /tmp/aegis_decision_${STAMP}.tar.gz services/decision/
aws s3 cp /tmp/aegis_decision_${STAMP}.tar.gz \
  s3://acp-dev-backups-628478/deployments/ --region ap-south-1
```

SSM script body:

```bash
aws s3 cp s3://acp-dev-backups-628478/deployments/aegis_decision_${STAMP}.tar.gz /tmp/d.tar.gz --region ap-south-1
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
curl -fsS https://dev.aegisagent.in/ | grep -oE 'index-[A-Za-z0-9_-]+\.js'

# Login probe (no plaintext password in this file)
curl -sS -X POST https://dev.aegisagent.in/auth/token \
  -H 'Content-Type: application/json' \
  -H 'X-Tenant-ID: 00000000-0000-0000-0000-000000000001' \
  -d '{"email":"admin@acp.local","password":"REDACTED"}' | head -c 100

# System health
TOKEN=...
curl -sS https://dev.aegisagent.in/system/health \
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

## What the deploy pipeline does NOT do

- **No automated tests gate the deploy.** Test runs happen on the laptop before the operator decides to deploy.
- **No staged rollout.** A single EC2 cannot canary against itself.
- **No blue-green.** Compose recreates in place. Brief per-service downtime is accepted.
- **No automated rollback.** ALB removes an unhealthy host but does not revert the deploy.

These omissions are intentional at this footprint. A multi-instance production rollout would add staging, canary, and blue-green.

## Next

- [Deployment Topology](../architecture/deployment-topology.md) — what the single EC2 looks like end-to-end
- [Backup & Restore](backup-restore.md) — pre-deploy backup strategy
- [Observability](observability.md) — what to watch during a deploy
- [Demo Packs](../introduction/demo-packs.md) — how to populate the UI with demo data after a fresh deploy
