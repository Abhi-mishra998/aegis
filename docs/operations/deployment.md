# Deployment

*How code reaches the live Aegis environment. Tarball → S3 → SSM, never GitHub Actions on the EC2.*

The live deployment is **prod-ha** at `https://aegisagent.in`: a 2× `m6g.medium` Graviton Auto Scaling Group spanning `ap-south-1a + 1b` behind an Application Load Balancer with WAFv2 in front. RDS is Multi-AZ Postgres (`db.t3.small`); ElastiCache Redis is a single replication group (primary endpoint `master.acp-prodha-redis.1gloza.aps1.cache.amazonaws.com:6379`, `cluster_enabled=false`).

The deploy mechanism (build a tarball on the laptop, push to S3, fan out via SSM `send-command`) is the same shape the dev stack used. What changed in this round of hardening is what reaches a host *and* the order in which containers come up on that host.

## What the latest infra round actually changed

These are the substantive shifts an operator needs to know about. Each is cross-referenced to the file that holds the current truth.

1. **`depends_on: service_healthy` on every gateway dependency.** The six services the gateway calls — `audit`, `policy`, `decision`, `api`, `usage`, `identity` — are now gated by `service_healthy`, not `service_started`. A half-booted identity service no longer silently breaks JWT validation on the gateway's first request. Source: `infra/docker-compose.yml` gateway `depends_on:` block (~lines 480–493).

2. **Pinned image tags.** `pgbouncer:1.23.1` and `opa:0.69.0-debug`. The floating `:latest` tags are gone, so a fresh ASG instance can't drift from what the existing pair are running. Sources: `infra/docker-compose.yml:43` and `infra/docker-compose.yml:103`.

3. **`insight_worker` `start_period: 30s`.** Already in place; recorded here because it's the one healthcheck whose start window matters during the boot race — the worker only flips healthy after it has SETEX'd the heartbeat key, and a tight start window restart-flapped the container. Source: `infra/docker-compose.yml` insight_worker healthcheck (~line 206).

4. **ALB target-group health-check path → `/healthz`.** The target group now probes `/healthz`, which the UI nginx proxies to `gateway:8000/health`. Before this, the ALB hit nginx's hard-coded `200 "ok"` and a dead gateway behind a healthy nginx was *not* deregistered. Sources: `infra/terraform/modules/alb/variables.tf` (default), `infra/terraform/environments/prod-ha/main.tf` (env-level), `ui/nginx.conf` (`location = /healthz` proxy block).

5. **Bundle build canonicalised.** `scripts/ops/build_release_bundle.sh` is the only supported builder. It produces ~23 MB tarballs that *include* `ui/dist` (gitignored), exclude `.git`, `node_modules`, `__pycache__`, `infra/.env*`, and refuse to upload if a secret-bearing `.env` slipped in. The output goes to `s3://acp-backups-prodha-628478946931/releases/current.tar.gz`.

6. **ASG topology.** `acp-prodha-asg-20260613103432397400000003` with 2 instances behind the ALB in `ap-south-1`, RDS Multi-AZ, ElastiCache Redis primary at `master.acp-prodha-redis.1gloza.aps1.cache.amazonaws.com:6379`.

The two changes that are committed to the env files but **not yet applied** — NAT-per-AZ and the ALB-via-gateway health-check path — are called out separately at the end. They need a `terraform apply` in `infra/terraform/environments/prod-ha/`.

## The contract

Three properties the deploy pipeline must guarantee:

1. **No GitHub credentials on either EC2.** The instance role has S3 read for the deploys bucket and SSM agent permissions; nothing else.
2. **One operator step.** From a built bundle on the laptop to live on `https://aegisagent.in` is one `aws s3 cp` plus one staggered `aws ssm send-command` per host.
3. **Staggered rollout, not coordinated cutover.** Compose recreates one host at a time; the ALB drains the in-flight host while the other one keeps serving traffic.

The trade-off vs. a GitHub-Actions-driven deploy is that the laptop becomes the deploy origin. The EC2 host trusts S3 and SSM, not the public internet.

## The path

```mermaid
flowchart LR
    Dev[Developer laptop]
    Build[build_release_bundle.sh]
    Tar[/tmp/aegis-bundle-*.tar.gz, ~23 MB]
    S3D[s3://acp-backups-prodha-…/releases/current.tar.gz]
    SSM[Systems Manager]
    EC2a[ASG host A / ap-south-1a]
    EC2b[ASG host B / ap-south-1b]
    WAF[WAFv2]
    ALB[ALB acp-prodha-alb]
    Dev --> Build
    Build --> Tar
    Tar -->|aws s3 cp| S3D
    Dev -->|ssm send-command host A| SSM
    SSM --> EC2a
    EC2a -->|aws s3 cp| S3D
    Dev -->|verify ALB 200 → ssm host B| SSM
    SSM --> EC2b
    EC2b -->|aws s3 cp| S3D
    WAF --> ALB
    ALB --> EC2a
    ALB --> EC2b
```

## Live targets (prod-ha environment)

| Resource | Value |
|---|---|
| Public URL | `https://aegisagent.in` |
| ALB hostname | `acp-prodha-alb.ap-south-1.elb.amazonaws.com` (alias of `aegisagent.in`) |
| WAFv2 | `acp-prodha-web-acl` — Common rules + KnownBadInputs + SQLi + per-IP rate limit |
| EC2 ASG | `acp-prodha-asg-20260613103432397400000003` — 2 × `m6g.medium` Graviton (1 vCPU / 4 GB), one each in `ap-south-1a` and `ap-south-1b`; **min=max=desired=2** |
| Repo path on each EC2 | `/opt/aegis` |
| Deploy bucket | `s3://acp-backups-prodha-628478946931/releases/` |
| RDS endpoint | `acp-prodha-postgres.<id>.ap-south-1.rds.amazonaws.com:5432` (Multi-AZ `db.t3.small`) |
| Redis endpoint | `master.acp-prodha-redis.1gloza.aps1.cache.amazonaws.com:6379` (`cluster_enabled=false`) |
| KMS CMK | `alias/aegis-audit-envelope` (annual rotation) |
| SSM SecureString prefixes | `/acp-prodha/*` (Secrets Manager) — rds_master_password, jwt_secret_key, redis_auth_token, groq_api_key, stripe_webhook_secret |
| Docker network | `infra_default` — every service-name DNS resolves on this network, per host |
| Pinned images | `edoburu/pgbouncer:1.23.1`, `openpolicyagent/opa:0.69.0-debug` |

## The canonical recipe

This is the only path you should use to push a release to prod-ha. Skip ahead to the AppleDouble landmine section if a deploy has failed on you before — it's almost always that one.

### Step 1 — Build and upload the bundle

From the repo root on your laptop:

```bash
# Builds /tmp/aegis-bundle-YYYYMMDDTHHMMSSZ.tar.gz and pushes it as
# both bundle-<ts>.tar.gz AND current.tar.gz.
SKIP_UI_BUILD=1 UPLOAD=1 bash scripts/ops/build_release_bundle.sh
```

What this does, in order:

1. Validates the four load-bearing paths are present: `./Dockerfile`, `./infra/docker-compose.yml`, `./infra/docker-compose.aws.yml`, `./ui/Dockerfile`, and `./ui/dist/index.html`. If `ui/dist` is missing and `SKIP_UI_BUILD` is unset, it runs `npm ci && npm run build` for you. Pass `SKIP_UI_BUILD=1` when you already built `ui/dist` (most of the time — the build adds 60–90s).
2. Tars the repo with these excludes: `.git`, `node_modules`, `.terraform*`, `__pycache__`, `.pytest_cache`, `.ruff_cache`, `.mypy_cache`, `.hypothesis`, `.coverage*`, `.venv`, `venv`, `*.pyc`, `.DS_Store`, `._*`, `infra/.env*`, `./.env*`, `ui/.env*`, `ui/playwright-report`, `ui/test-results`, `reports`, `**/htmlcov`.
3. Sanity probe — fails if `./Dockerfile`, `./infra/docker-compose.yml`, `./ui/Dockerfile`, or `./ui/dist/index.html` is missing from the archive.
4. Secret-leak guard — fails if any `.env`/`.env.local`/`infra/.env*` snuck in, OR if any text file in the bundle contains a `sk_live_…` / `sk_test_…` / `whsec_…` substring. `.env.example` and `.env.production` are deliberately allowed (public templates + Vite override).
5. With `UPLOAD=1`: uploads to both `s3://acp-backups-prodha-628478946931/releases/current.tar.gz` and `releases/bundle-<ts>.tar.gz`. The timestamped copy is your rollback target; `current.tar.gz` is what fresh ASG instances pull on first boot.

If you want the ASG to refresh itself onto the new bundle (instead of you deploying it manually with SSM), add `ASG_REFRESH=1` and `ASG_NAME=acp-prodha-asg-20260613103432397400000003`. For ordinary deploys, don't — use the staggered SSM path below.

### Step 2 — Staggered SSM deploy (host A, verify, host B)

The hard rule: never deploy to both hosts in parallel. The ALB needs at least one healthy target at all times. Resolve the ASG members and pin them to env vars:

```bash
INSTANCE_IDS=($(aws autoscaling describe-auto-scaling-groups \
  --region ap-south-1 \
  --auto-scaling-group-names acp-prodha-asg-20260613103432397400000003 \
  | jq -r '.AutoScalingGroups[0].Instances[].InstanceId'))
HOST_A="${INSTANCE_IDS[0]}"
HOST_B="${INSTANCE_IDS[1]}"

ALB_TG_ARN=$(aws elbv2 describe-target-groups \
  --region ap-south-1 \
  --names acp-prodha-gateway-tg \
  | jq -r '.TargetGroups[0].TargetGroupArn')
```

The SSM payload that each host runs:

```bash
read -r -d '' SSM_BODY <<'BASH' || true
set -e
aws s3 cp s3://acp-backups-prodha-628478946931/releases/current.tar.gz \
  /tmp/aegis-bundle.tar.gz --region ap-south-1
# Pre-extract AppleDouble scrub — protects against a bundle that was
# accidentally tarred up on a macOS volume mounted via SMB/AFP.
find /tmp -maxdepth 1 -name '._*' -delete
mkdir -p /opt/aegis
tar -xzf /tmp/aegis-bundle.tar.gz -C /opt/aegis
# Post-extract AppleDouble scrub — the load-bearing one. macOS `tar` leaks
# `._foo.py` companion files for any file with extended attributes, and
# alembic dies with `SyntaxError: source code string cannot contain null
# bytes` the moment it tries to import one.
find /opt/aegis -name '._*' -delete
cd /opt/aegis/infra
docker compose -f docker-compose.yml -f docker-compose.aws.yml \
  --env-file .env up -d --no-deps --force-recreate --build \
  audit policy decision api usage identity gateway ui
BASH
```

Deploy host A:

```bash
# Drain host A out of the ALB so in-flight requests finish.
aws elbv2 deregister-targets --region ap-south-1 \
  --target-group-arn "$ALB_TG_ARN" \
  --targets Id="$HOST_A"
sleep 60   # ALB drain timeout

# Fire the SSM command, capture the command id.
CMD_A=$(aws ssm send-command --region ap-south-1 \
  --instance-ids "$HOST_A" \
  --document-name AWS-RunShellScript \
  --comment "prod-ha deploy host A" \
  --parameters commands="$SSM_BODY" \
  --output text --query 'Command.CommandId')

# Wait for the SSM command to land.
aws ssm wait command-executed --region ap-south-1 \
  --command-id "$CMD_A" --instance-id "$HOST_A"

# Smoke the gateway on host A by its private IP (bypasses the ALB).
PRIVATE_IP_A=$(aws ec2 describe-instances --region ap-south-1 \
  --instance-ids "$HOST_A" \
  --query 'Reservations[].Instances[].PrivateIpAddress' --output text)
for n in 1 2 3 4 5 6; do
  if curl -fsS --max-time 5 "http://${PRIVATE_IP_A}:8000/health" >/dev/null; then
    echo "host A health 200 (attempt $n)"; break
  fi
  sleep 5
done

# Re-register and wait for the ALB to mark host A healthy.
aws elbv2 register-targets --region ap-south-1 \
  --target-group-arn "$ALB_TG_ARN" \
  --targets Id="$HOST_A"
aws elbv2 wait target-in-service --region ap-south-1 \
  --target-group-arn "$ALB_TG_ARN" --targets Id="$HOST_A"

# Public probe — only proceed if the ALB itself returns 200 via aegisagent.in.
curl -fsS https://aegisagent.in/health
```

Only when the public probe returns 200, repeat the block above swapping `HOST_A` → `HOST_B` and `PRIVATE_IP_A` → `PRIVATE_IP_B`. The helper script `scripts/ops/deploy_staggered.sh` runs this loop for you given `HOSTS="$HOST_A,$HOST_B"` and `ALB_TG_ARN=…`.

### Step 3 — Smoke test from the public URL

After both hosts are back in service, run the canonical live probe:

```bash
python3 /tmp/live_prodha_test.py
```

Expected outcome: **31/31 PASS**, matching the recorded baseline in `final-testing.md`. The harness exercises:

- Public `https://aegisagent.in/health` and `/system/health`
- Real Clerk RS256 JWT mint → `/auth/clerk/provision` → tenant + role + workspace
- A real upstream Anthropic call through `/v1/messages` (benign + wire-transfer + single-record PII)
- `/audit/logs/search` and `/dashboard/overview` rollup
- SSE on `/events/stream`
- Per-employee virtual key mint + revoke

If anything in `final-testing.md`'s table doesn't reproduce — or you see a 502/503 — pull both hosts' container logs via SSM (`docker compose logs --tail 200 gateway audit`) before rolling forward to host B. The "deploy host A, verify, deploy host B" rhythm is the safety net; don't skip the verify.

## The AppleDouble landmine — read this even if you read nothing else

macOS `tar` and macOS volumes (SMB, AFP, and any external drive that has been touched by Finder) leak `._<filename>` companion files. They carry the extended attributes the original file had. They are valid filesystem entries and `tar -czf` packs them just like any other file. They look like Python source to the alembic loader.

When alembic on Linux imports an `._versions.py`, it reads the binary AppleDouble header, sees a NUL byte at offset ~3, and dies with:

```
SyntaxError: source code string cannot contain null bytes
```

The audit container does this on every boot (it runs `alembic upgrade head` before `uvicorn …`). One AppleDouble file in `services/audit/alembic/versions/` is enough to cycle the audit container, which cascades — gateway never becomes healthy because `audit` never becomes healthy, the ALB target group never gets a green host, and the ASG starts replacing instances.

**The fix is two-step and non-obvious:**

```bash
# 1. Scrub on the laptop BEFORE tarring (build_release_bundle.sh already does
#    this — it passes --exclude='._*' to tar, but defence-in-depth):
find . -name '._*' -delete

# 2. Scrub on every host AFTER extracting:
find /opt/aegis -name '._*' -delete
```

The SSM payload in step 2 above embeds the post-extract scrub. **Do not edit it out.** The first deploy that hit this fault took the prod-ha stack down for ~20 minutes before the cause was found.

A second variant of the same trap: if a developer ran `find /opt/aegis -name '._*'` from inside the Docker host with the project bind-mounted from a macOS volume, the scrub command itself can't see the files (different namespace). Run it from the host's perspective, not from inside a container.

## Rollback

A rollback is "deploy the previous tarball". The S3 deploy bucket retains every artifact under `releases/bundle-*.tar.gz`; the operator picks the previous tarball and re-runs the SSM payload with that key in place of `current.tar.gz`. Optionally, copy the previous tarball over `current.tar.gz` so fresh ASG instances pick it up too.

There is no in-place revert mechanism. The contract: the active bundle is whatever was last deployed. `scripts/ops/rollback.sh` automates the SHA path for git-anchored rollbacks.

## Terraform changes pending a separate `terraform apply`

Two infra changes are committed to `infra/terraform/environments/prod-ha/` but **have not yet been applied** to the live AWS account. The repo and the live AWS state are out of sync on these specific items. Apply them with a focused, low-blast-radius `terraform apply` from `infra/terraform/environments/prod-ha/`.

### NAT-per-AZ (`one_nat_per_az = true`)

The prod-ha network module currently runs a single shared NAT gateway in `ap-south-1a`. If that NAT or its AZ goes dark, the private-subnet ASG host in `ap-south-1b` loses egress and can't reach KMS / SSM / Secrets Manager / `api.anthropic.com`. The committed change flips `one_nat_per_az = true` in `infra/terraform/environments/prod-ha/main.tf` (~line 75) to provision a second NAT gateway in `ap-south-1b`, closing the single-NAT SPOF.

Cost impact: ~$32/month for the second NAT + its EIP. Apply:

```bash
cd infra/terraform/environments/prod-ha
terraform init
terraform plan -target=module.network
terraform apply -target=module.network
```

Expected plan: 1 add (the second `aws_nat_gateway`), 1 modify (the private route table for AZ-B route target changes from the shared NAT to the per-AZ NAT).

### ALB-via-gateway health-check path (`/healthz` → gateway:8000/health)

The ALB target group's health-check path is moving from the nginx-static `/health` (which always returns `200 "ok"`) to `/healthz` (which nginx proxies to `gateway:8000/health` inside the host). The point: a dead gateway behind a healthy nginx will now correctly fail the ALB probe and get deregistered. The change is committed at `infra/terraform/modules/alb/variables.tf` (default `health_check_path = "/healthz"`) and overridden at the env level in `infra/terraform/environments/prod-ha/main.tf` (`module "alb"` block, `health_check_path = "/healthz"`), with the matching nginx `location = /healthz` proxy block in `ui/nginx.conf`.

Apply:

```bash
cd infra/terraform/environments/prod-ha
terraform plan -target=module.alb
terraform apply -target=module.alb
```

Expected plan: 1 modify (the `aws_lb_target_group.health_check.path` attribute). After apply, monitor `aws elbv2 describe-target-health` on the prod-ha gateway target group — both hosts should re-report `healthy` within ~30 s.

If the `/healthz` proxy block hasn't been deployed to both hosts via the bundle pipeline *before* terraform applies the new path, the ALB will mark both hosts unhealthy and the ASG will start cycling. Order matters: deploy the bundle first (step 1–3 above), confirm `curl https://aegisagent.in/healthz` returns 200, then run the terraform apply.

## Smoke verification (post-deploy)

After every deploy, verify externally:

```bash
# 1. Bundle hash from the served index.html (sanity that the UI was rebuilt)
curl -fsS https://aegisagent.in/ | grep -oE 'index-[A-Za-z0-9_-]+\.js'

# 2. Public health
curl -fsS https://aegisagent.in/health
curl -fsS https://aegisagent.in/system/health | jq '.services'

# 3. The canonical 31/31 live probe (real Clerk JWT + real Claude)
python3 /tmp/live_prodha_test.py
# Expect: 31/31 PASS, matches the table in final-testing.md
```

Expect `healthy: 12 / total: 12` from `/system/health`. If it's 11/12, run `scripts/ops/smoke_test.sh` with the operator JWTs set to get a per-endpoint breakdown.

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
| 7 | Dev `.env` clobbered by tar extract, identity service falls back to prod passwords | Local `infra/.env` carries prod credentials and ships with `tar -czf infra/` | `--exclude='infra/.env'` in tar (already in `build_release_bundle.sh`), or re-overwrite `.env` *after* extract |
| 8 | Alembic crashes with `SyntaxError: source code string cannot contain null bytes` | macOS tar leaks `._foo.py` AppleDouble metadata files | See the dedicated section above. `find . -name '._*' -delete` before tar AND `find /opt/aegis -name '._*' -delete` after extract on every host |
| 9 | Compose fails with "service has neither an image nor a build context specified" | Stale `groq_worker:` block in `docker-compose.aws.yml` references a deleted service | Remove the block, or fold the AWS override into the base file |
| 10 | Compose validation fails before any service starts | `GRAFANA_ADMIN_PASSWORD` is a no-default required var; freshly generated dev `.env` files often lack it | Add `GRAFANA_ADMIN_PASSWORD=` to the dev `.env` (prod-ha `user_data.sh` already does this) |
| 11 | EC2 OOMs during initial healthcheck race, Postgres-dependent services exit 255 | `t4g.small` (2 GB) cannot fit the 14-container stack; `t4g.medium`/`t4g.large` returned `InsufficientInstanceCapacity` in `ap-south-1a` | Use `m6g.medium` (4 GB, 1 vCPU Graviton) — immediately available in `ap-south-1a` when the t4g siblings weren't |
| 12 | `/receipts/key` returns 500 with `NoCredentialsError` on a fresh ASG instance | Audit container needs `boto3` for the SSM signing-key provider AND host IMDSv2 needs `http_put_response_hop_limit=2` (containers add one hop via the docker bridge) | Add `boto3>=1.34` to server extras in `pyproject.toml`. Bump `http_put_response_hop_limit` from `1` to `2` in both `infra/terraform/modules/asg/main.tf` and `infra/terraform/modules/compute/main.tf`. Run `terraform apply -target=module.asg`; ASG creates a new LT version and migrates |
| 13 | Audit outbox tight-loops `ConnectError` to `http://localhost:8006/usage/record` | `USAGE_SERVICE_URL` env var is unset on the audit container; `settings.USAGE_SERVICE_URL` falls back to the dev default | Set `USAGE_SERVICE_URL=http://usage:8000` and `POLICY_SERVICE_URL=http://policy:8000` on the audit service in `infra/docker-compose.yml` |
| 14 | Gateway uvicorn workers OOM-killed after each request, ALB health-check fails, ASG cycles the instance | 4 uvicorn workers × ~192 MB each = OOM under any real load on the 768 MB prod-ha cap | Drop `--workers 4` to `--workers 2` in the gateway compose entry. Each worker gets ~384 MB; throughput still covers the 20-user infra |
| 15 | Gateway `relation "shadow_policies" does not exist` on every `/execute` | `shadow_eval_hook.py` imports `services.audit.database.SessionLocal`, which uses `settings.DATABASE_URL`. Gateway's compose set that to `postgres@…/acp`, but `shadow_policies` lives in `acp_audit` | Add `DATABASE_URL=postgresql+asyncpg://audit_user:${AUDIT_DB_PASSWORD}@pgbouncer:6432/acp_audit` to the gateway compose `environment:` block |
| 16 | AuditLogs page shows "Upstream returned HTML 403" | AWS WAFv2 SQLi managed rule blocks any POST body containing `"limit":N` | Migrate `auditService.searchLogs` to `GET /audit/logs` with query params, and extend the audit `list_logs` handler to accept `tool`, `start_date`, `end_date` |
| 17 | Live Demo returns 503 `GROQ_API_KEY not configured` on a fresh ASG instance | The user_data template didn't set `GROQ_API_KEY`; the env var only existed on instances where it was set manually | `user_data.sh` now pulls `groq_api_key` from Secrets Manager and aborts the deploy if the secret is missing or doesn't start with `gsk_` (R1 refactor — fail at deploy time, not when a prospect runs the live demo) |
| 18 | New ASG instance never reaches ALB-healthy because the bundle was partial | Operators using `git archive HEAD` or hand-rolled tar invocations dropped `./Dockerfile`, `infra/docker-compose.yml`, and `ui/dist/` (the last is gitignored). Saw an 811 KB tarball cycle the ASG on 2026-06-15 | Use `scripts/ops/build_release_bundle.sh` exclusively. The sanity-probe step refuses to upload a bundle that's missing the four load-bearing paths |

## What the deploy pipeline does NOT do

- **No automated tests gate the deploy.** Test runs happen on the laptop before the operator decides to deploy. The 31/31 `live_prodha_test.py` runs *after* the deploy as the smoke gate.
- **No staged rollout beyond two hosts.** The ASG is `min=max=desired=2`; staggering is host A → verify → host B.
- **No blue-green.** Compose recreates in place per host. Brief per-service downtime is accepted on the host being recreated; the ALB serves traffic from the unchanged host throughout.
- **No automated rollback.** ALB removes an unhealthy host but does not revert the deploy. Use `scripts/ops/rollback.sh` or re-deploy the previous `bundle-<ts>.tar.gz` artifact.

These omissions are intentional at this footprint. A multi-instance production rollout would add staging, canary, and blue-green.

## Audit append-only migration (one-time, post-deploy)

The audit service ships an idempotent trigger that blocks UPDATE and DELETE on `audit_logs`. Once the gateway is deployed with the new alembic revision (it's already in the current bundle), run the migration on a single host — the trigger creation is idempotent and re-runs are safe:

```bash
aws ssm send-command --region ap-south-1 \
  --instance-ids "$HOST_A" \
  --document-name AWS-RunShellScript \
  --comment "audit append-only migration" \
  --parameters 'commands=["docker exec -w /app/services/audit acp_audit alembic upgrade head"]'
```

The migration is per-database (the trigger is created on `acp_audit`), so you only need to run it once across the fleet — not once per host.

## Next

- [Deployment Topology](../architecture/deployment-topology.md) — what the 2× EC2 ASG behind WAFv2 + ALB looks like end-to-end
- [Backup & Restore](backup-restore.md) — pre-deploy backup strategy
- [Observability](observability.md) — what to watch during a deploy
- [Demo Packs](../introduction/demo-packs.md) — how to populate the UI with demo data after a fresh deploy
