# Aegis — Terraform Distributed Cloud System Design

**Audience:** the next Claude session. Open this file first, build the Terraform from it.
**Goal:** one `terraform apply` brings up the entire Aegis production stack — VPC, RDS, Redis, ALB, ASG, Route 53, ACM, WAF, S3, Secrets Manager, CloudWatch, IAM. One `terraform destroy` tears it cleanly to zero.
**Constraints:** 50 concurrent users sustained, zero-downtime deploys, p95 < 400 ms, AWS `ap-south-1` (Mumbai) single-region, **single founder ops budget**.
**Stance:** every section is a decision with the reason. No "and also..." features.

---

## 1. What's deployed today (the brownfield to destroy)

Honest snapshot taken 2026-06-19 via AWS CLI against account `628478946931`:

### VPC + Networking
```
VPC:        vpc-0cf8ccc4a74fbd633 (acp-prodha-vpc, 10.20.0.0/16)
Subnets:
  10.20.1.0/24  ap-south-1a  PUBLIC   (NAT/ALB)
  10.20.2.0/24  ap-south-1b  PUBLIC   (NAT/ALB)
  10.20.3.0/24  ap-south-1a  PRIVATE  (EC2 + RDS)
  10.20.4.0/24  ap-south-1b  PRIVATE  (EC2 + RDS)
Default VPC:  vpc-089e6e43e7874a3d6 (unused, can delete)
```

### Compute
```
ASG:  acp-prodha-asg-20260613103432397400000003 (min=2, max=2, desired=2)
EC2:  m6g.large (2 vCPU, 8 GiB) arm64
```
**Problems today:** ASG flapped repeatedly during 2026-06-18 → 2026-06-19 session because:
- New instances launching off a broken bundle (`prepared_statement_name_func` change) failed ALB health checks
- ASG terminated healthy inst-2 alongside the failing one
- Bundle versioning is destructive (`current.tar.gz` is overwritten) — no easy rollback

### Data tier
```
RDS:         acp-prodha-postgres  db.t3.small  PostgreSQL  Multi-AZ ✓
ElastiCache: acp-prodha-redis-001 + 002  cache.t3.micro  (replication-group, not cluster-mode)
```

### Edge
```
ALB:        acp-prodha-alb (application)
            DNS: acp-prodha-alb-1931545235.ap-south-1.elb.amazonaws.com
Target Group: acp-prodha-tg
Route 53:   aegisagent.in (Z033117538JKIIKDBDPUJ)
ACM cert:   aegisagent.in (ISSUED)
WAFv2:      attached to ALB
```

### Storage
```
acp-backups-prodha-628478946931      (bundles + RDS snapshots)
acp-alb-logs-prodha-628478946931     (ALB access logs)
acp-cloudtrail-628478946931          (AWS audit log)
aegis-public-roots-628478946931      (public transparency log — KEEP, see §6)
aegis-terraform-state-628478946931   (TF state — KEEP, see §3)
```

### Terraform state
```
infra/terraform/environments/prod-ha/  (live + drifted from real state)
infra/terraform/environments/prod/     (older, abandoned)
infra/terraform/environments/dev/      (abandoned)
infra/terraform/modules/  (15 modules: acm, alb, asg, compute, elasticache, elasticache_ha,
                            iam, network, rds, route53, s3, secrets, security_groups, waf)
```

### What to PRESERVE during the rebuild
- ✅ `aegis-public-roots-628478946931` — **public transparency log. NEVER destroy.** Customer-visible cryptographic archive. Import into new TF state as `data` or `terraform import`.
- ✅ `aegis-terraform-state-628478946931` — new state goes here.
- ✅ ACM certificate (validation can take 24 h — keep the existing one).
- ✅ Route 53 hosted zone (records will be replaced; zone stays).
- ✅ RDS snapshot taken just before destroy → restore into new RDS.

### What to DESTROY
- ❌ Default VPC `vpc-089e6e43e7874a3d6` (unused).
- ❌ Old `prod-ha` ASG + EC2 + ALB + target group.
- ❌ Old RDS instance (after final snapshot).
- ❌ Old ElastiCache replication group.
- ❌ Three `environments/` dirs (start fresh — see §3 for the single env strategy).

---

## 2. Target architecture (one diagram, one page)

```
                         ┌──────────────────────────────────┐
                         │   Route 53 — aegisagent.in       │
                         │   A record → ALB DNS             │
                         │   AAAA record (IPv6) → ALB DNS   │
                         │   www CNAME → aegisagent.in      │
                         └──────────────┬───────────────────┘
                                        │ HTTPS:443
                         ┌──────────────▼───────────────────┐
                         │   ACM cert: *.aegisagent.in      │
                         │   + apex (DNS-validated)         │
                         └──────────────┬───────────────────┘
                                        │
                         ┌──────────────▼───────────────────┐
                         │   WAFv2 → ALB (Application LB)   │
                         │   HTTP 80 → 301 → HTTPS 443      │
                         │   HTTPS 443 → target group       │
                         │   Access logs → S3 (90-day TTL)  │
                         └──────────────┬───────────────────┘
                                        │
                  ┌─────────────────────┼─────────────────────┐
                  │                                           │
       ┌──────────▼────────┐                       ┌──────────▼────────┐
       │ Target Group      │                       │ Target Group      │
       │ acp-prodha-tg     │  (ASG instance refresh: blue/green)       │
       │ Health:           │                       │                   │
       │  /healthz, 200,   │                       │                   │
       │  2/3 healthy      │                       │                   │
       │  deregister 30s   │                       │                   │
       └──────────┬────────┘                       └──────────┬────────┘
                  │                                           │
       ┌──────────▼────────────────┐         ┌────────────────▼──────────┐
       │ ASG (min=2, max=10, des=2)│         │ Launch Template (versioned)│
       │ Instances spread across   │ ◄──────►│ AMI: Amazon Linux 2023 arm64│
       │ ap-south-1a + 1b          │         │ Type: m6g.large           │
       │ Health check: ELB         │         │ user_data: tarball pull   │
       │ Grace period: 300s        │         │ Bundle ref: SSM Parameter │
       └──────────┬────────────────┘         └───────────────────────────┘
                  │ private subnets only
                  │
       ┌──────────▼────────────────────────────────────────────┐
       │ EC2 instance (m6g.large)                              │
       │   Docker Compose: 23 services                         │
       │   user_data fetches s3://...bundle-{version}.tar.gz   │
       │   from Secrets Manager: DB creds, JWT keys, Anthropic │
       └──────┬────────────────┬────────────────┬──────────────┘
              │                │                │
              ▼                ▼                ▼
  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐
  │ RDS Postgres   │  │ ElastiCache    │  │ S3 buckets     │
  │ db.t4g.medium  │  │ Redis Cluster  │  │ backups        │
  │ Multi-AZ       │  │ cluster mode=Y │  │ public-roots   │
  │ Auto backup 7d │  │ cache.t4g.micro│  │ cloudtrail     │
  │ TLS at rest+wire│  │ TLS in transit │  │ tf-state       │
  └────────────────┘  └────────────────┘  └────────────────┘

  ┌────────────────────────────────────────────────────────┐
  │ Observability (single page)                            │
  │   CloudWatch Logs   ← compose logs via cloudwatch-agent│
  │   CloudWatch Alarms → SNS topic → PagerDuty + email    │
  │   Prometheus + Grafana on-box (private subnet only)    │
  │   AWS X-Ray (optional, off by default — cost)          │
  └────────────────────────────────────────────────────────┘
```

**Capacity math** for 50 concurrent users:
- 50 users × 0.5 req/s average per user = 25 req/s sustained, 100 req/s peak
- Aegis gateway p95 ≈ 200 ms under load (verified Section 3 of `final-test-live-matrix.md`)
- 1 × `m6g.large` (2 vCPU, 8 GiB) handles ~150 req/s comfortably with all 23 compose containers
- ASG min=2 means **EITHER instance alone serves 100% of peak**; second instance is for AZ redundancy
- Scale-out trigger at 70% sustained CPU → ASG bumps to 3, then 4, up to max=10

**Why m6g.large not t4g.medium:** the audit/decision/policy containers each take ~400 MiB resident; 6.4 GiB working set is borderline on t4g.medium (4 GiB). m6g.large gives headroom without the burst-credit anxiety of t3.

---

## 3. Module layout (this is the file tree the next session writes)

```
infra/terraform/
├── README.md                        # operator quickstart (10 lines)
├── versions.tf                      # provider versions pinned
├── backend.tf                       # S3 + DynamoDB state lock
├── variables.tf                     # all top-level inputs
├── outputs.tf                       # what to surface to operator
├── main.tf                          # composes every module — the only file
│                                    #   you edit when adding things
│
├── envs/
│   └── prod/                        # ONLY ENVIRONMENT — see §4 for why
│       └── terraform.tfvars         # the 30 inputs prod expects
│
└── modules/
    ├── network/                     # VPC, subnets, NAT, IGW, route tables
    │   ├── main.tf
    │   ├── variables.tf
    │   └── outputs.tf
    ├── security_groups/             # ALB SG, EC2 SG, RDS SG, Redis SG, VPC endpoints
    ├── iam/                         # EC2 instance profile + Secrets Manager read + S3 read/write
    ├── secrets/                     # DB password (random_password), JWT signing key, Anthropic key
    ├── acm/                         # *.aegisagent.in + apex DNS validation
    ├── route53/                     # Apex A, AAAA → ALB; www CNAME; ACM validation records
    ├── waf/                         # WAFv2 ACL: AWS managed rules + per-IP rate limit
    ├── alb/                         # HTTP→HTTPS redirect, HTTPS listener, target group
    ├── asg/                         # Launch template + ASG + instance refresh strategy
    ├── rds/                         # Postgres Multi-AZ + parameter group + final snapshot
    ├── elasticache/                 # Redis cluster mode + AUTH + TLS in-transit
    ├── s3/                          # backups, alb-logs, cloudtrail; public-roots IMPORTED
    ├── cloudwatch/                  # log groups + 5 prod alarms + SNS topic
    └── ssm/                         # bundle version Parameter Store + SSM agent role
```

**Why this layout:**
- **Modules are stateless** — every variable is explicit, no hidden coupling between modules.
- **Only `main.tf` composes** — no module calls another module. Composition lives in one file you can see end-to-end.
- **Single env directory (`envs/prod/`)** — see §4.

---

## 4. Environment strategy (controversial — one env, not three)

**Today:** three `environments/` directories (dev, prod, prod-ha) — all drifted, only `prod-ha` matches reality.

**Proposed:** **ONE environment**, called `prod`. Reasons:

1. **Solo founder, 0 paying customers, 1 region.** The cost of maintaining 3 parallel Terraform stacks (dev/staging/prod) is real engineering time. Each pre-Series-A startup that maintains a "staging" env that lags prod by months actually gets WORSE signal than no staging at all (staging doesn't get the same traffic shape).

2. **Use Terraform workspaces if/when you need staging.** `terraform workspace new staging` + same modules + smaller `*.tfvars`. Add this later when a customer demands "staging API key for our security review." Not now.

3. **Single bucket for tf-state** (`aegis-terraform-state-628478946931`) with key prefix `prod/`. When workspaces land, key becomes `<workspace>/prod/`.

4. **No dev environment in the cloud.** Dev runs locally via `infra/docker-compose.yml`. That's already working.

---

## 5. State management

```hcl
# backend.tf
terraform {
  backend "s3" {
    bucket         = "aegis-terraform-state-628478946931"
    key            = "prod/terraform.tfstate"
    region         = "ap-south-1"
    encrypt        = true
    dynamodb_table = "aegis-terraform-locks"   # NEW — create in bootstrap step
  }
}
```

**Why DynamoDB lock:** two parallel `terraform apply` (e.g., one from your laptop + one from CI) without a lock will race-corrupt state. DynamoDB lock cost: ~$0.10/mo at our query volume.

**Bootstrap order** (chicken-and-egg — the S3 bucket + DynamoDB lock table must exist before the backend can use them):
```bash
# One-time, in infra/terraform/bootstrap/ (already exists, just verify):
cd infra/terraform/bootstrap/
terraform init -backend=false
terraform apply
# Creates: S3 bucket, DynamoDB lock table.
# All subsequent terraform commands use these.
```

---

## 6. Bundle versioning + deploy strategy (the part that hurt today)

**Today's broken pattern:**
- `current.tar.gz` on S3 is overwritten on every deploy.
- ASG-launched instances always download `current.tar.gz`.
- A bad bundle on `current.tar.gz` poisons every new instance.
- Rollback = re-upload an old bundle to `current.tar.gz` (manual, error-prone).

**Proposed:**
- Every bundle is `s3://acp-backups-prodha-…/releases/bundle-{git_sha}.tar.gz`.
- An SSM Parameter `/aegis/prod/current_bundle_sha` holds the active git_sha.
- The ASG Launch Template `user_data` script reads the SSM Parameter at boot and downloads that exact bundle:

```bash
# user_data.sh (in the Launch Template)
#!/bin/bash
set -euo pipefail
BUNDLE_SHA=$(aws ssm get-parameter \
  --region ap-south-1 \
  --name /aegis/prod/current_bundle_sha \
  --query 'Parameter.Value' \
  --output text)
aws s3 cp \
  s3://acp-backups-prodha-628478946931/releases/bundle-${BUNDLE_SHA}.tar.gz \
  /tmp/bundle.tar.gz
mkdir -p /opt/aegis && tar -xzf /tmp/bundle.tar.gz -C /opt/aegis
cd /opt/aegis && docker compose -f infra/docker-compose.yml -f infra/docker-compose.aws.yml up -d
```

**Promote a new bundle:**
```bash
# Push bundle to S3 with sha-pinned name
BUNDLE_SHA=$(git rev-parse --short HEAD)
bash scripts/ops/build_release_bundle.sh
aws s3 cp /tmp/aegis-bundle-*.tar.gz \
  s3://acp-backups-prodha-628478946931/releases/bundle-${BUNDLE_SHA}.tar.gz

# Flip the active version atomically
aws ssm put-parameter \
  --name /aegis/prod/current_bundle_sha \
  --value "${BUNDLE_SHA}" \
  --overwrite

# Trigger blue/green via ASG instance refresh
aws autoscaling start-instance-refresh \
  --auto-scaling-group-name aegis-prod-asg \
  --preferences MinHealthyPercentage=100,InstanceWarmup=300
# → ASG launches new instance with new bundle BEFORE terminating old one
# → if new instance fails health check, ASG keeps the old one
# → zero downtime, automatic rollback gate
```

**Rollback:**
```bash
# Find the last-known-good sha
git log --oneline | head -10
# Flip SSM back
aws ssm put-parameter \
  --name /aegis/prod/current_bundle_sha \
  --value "<earlier_sha>" \
  --overwrite
aws autoscaling start-instance-refresh \
  --auto-scaling-group-name aegis-prod-asg
# Done. 5 minutes to roll back to any prior bundle.
```

**This is the single biggest change.** Today's outage was caused by `current.tar.gz` being mutable. After this change, bad deploys cannot break healthy instances.

---

## 7. The 30 variables this stack accepts

```hcl
# envs/prod/terraform.tfvars

# Identity
aws_region          = "ap-south-1"
project             = "aegis"
environment         = "prod"
domain              = "aegisagent.in"

# Network
vpc_cidr            = "10.20.0.0/16"
azs                 = ["ap-south-1a", "ap-south-1b"]
public_subnet_cidrs  = ["10.20.1.0/24", "10.20.2.0/24"]
private_subnet_cidrs = ["10.20.3.0/24", "10.20.4.0/24"]
one_nat_per_az      = true   # cost++ but AZ-fault isolation

# Compute
instance_type       = "m6g.large"
asg_min             = 2
asg_max             = 10
asg_desired         = 2
ami_id              = ""  # auto-resolved via data lookup of Amazon Linux 2023 arm64
key_pair_name       = "aegis-prod-ec2"   # for break-glass SSH; usually unused (SSM)

# Bundle
bundle_sha_initial  = "abc1234"   # first SHA to deploy
bundle_bucket       = "acp-backups-prodha-628478946931"
ssm_bundle_parameter = "/aegis/prod/current_bundle_sha"

# Database
db_engine_version   = "15.7"
db_instance_class   = "db.t4g.medium"
db_allocated_storage = 100
db_max_allocated_storage = 500
db_backup_retention = 14
db_multi_az         = true

# Redis
redis_node_type     = "cache.t4g.micro"
redis_num_shards    = 1
redis_replicas_per_shard = 1   # 1 primary + 1 replica per shard

# Edge
acm_cert_arn        = ""  # auto-imported from existing certificate
alb_log_retention_days = 90

# WAF
waf_rate_limit_per_ip_per_5min = 2000

# Observability
sns_alarm_email     = "founder@aegisagent.in"
pagerduty_routing_key = ""   # optional

# Secrets (managed via random_password — never set by hand)
# - aegis_db_master_password
# - aegis_jwt_signing_key
# - aegis_anthropic_api_key   # set via terraform apply -var=...
```

**Naming convention:** every resource gets `${var.project}-${var.environment}-<resource>` (e.g. `aegis-prod-alb`). This makes a future EU region (`aegis-prod-eu`, `aegis-prod-vpc-eu`) trivial.

---

## 8. The 5 prod alarms (and no more)

Every CloudWatch alarm costs $0.10/mo + on-call attention. Five high-signal alarms beat 50 low-signal ones.

| Alarm | Threshold | Action |
|---|---|---|
| ALB 5xx rate | > 1% over 5 min | PagerDuty critical |
| ALB target unhealthy | < 1 healthy target over 2 min | PagerDuty critical |
| RDS CPU | > 80% over 10 min | PagerDuty warning |
| RDS free storage | < 20 GiB | PagerDuty warning |
| Redis CPU | > 70% over 10 min | Email |

In-platform alarms (chain-violation, gateway p99, etc.) live in Alertmanager already wired to the same SNS topic.

---

## 9. Cost estimate (steady state, 50 concurrent users)

| Component | Spec | Monthly USD |
|---|---|---:|
| EC2 m6g.large × 2 (Reserved 1y) | arm64, 2 vCPU, 8 GiB | $96 |
| RDS db.t4g.medium Multi-AZ | 100 GB gp3 | $108 |
| ElastiCache cache.t4g.micro × 2 | TLS in transit | $20 |
| ALB | + ~200 req/s | $25 |
| WAFv2 | AWS managed + 1 custom rule | $7 |
| Route 53 | 1 zone + 10 queries/sec | $2 |
| ACM | wildcard cert | $0 |
| S3 (backups + logs + state) | 100 GB + transfer | $5 |
| Secrets Manager | 3 secrets | $1 |
| NAT Gateway × 2 | one per AZ | $66 |
| Data transfer | 200 GB/mo outbound | $18 |
| CloudWatch (logs + 5 alarms) | 50 GB/mo | $30 |
| **Total** | | **~$378 / mo** |

**Optimization at <$1K MRR:** drop to single-AZ NAT (`one_nat_per_az = false`) → saves $33/mo, costs AZ failure isolation. Acceptable until first paid customer.

---

## 10. Migration plan — old to new (the deploy day playbook)

The goal: **zero customer impact** during the swap. Aegis is multi-tenant SaaS; if a customer hits a 502 during the migration, our story is over.

### T-2 hours
1. Take an **RDS snapshot** of `acp-prodha-postgres` → `acp-prodha-postgres-pre-tf-rebuild-<ts>`.
2. Export the current Terraform state to a file (for rollback): `terraform state pull > /tmp/old-state.json`.
3. Document the live ACM cert ARN, Route 53 zone ID, S3 bucket ARNs in a checklist.

### T-0 (the swap)
1. `terraform apply` the new stack in a **separate workspace** (`terraform workspace new prod-v2`) — this creates a parallel VPC, ALB, ASG, RDS (restored from snapshot), Redis.
2. New stack comes up with a **temporary DNS name** (`v2.aegisagent.in`).
3. Smoke-test v2 with 10 synthetic users + the Suite A/B/D/E probes from `validation-report.md`.
4. Once v2 is green for 30 minutes:
5. Flip the apex Route 53 A record from old ALB to new ALB. **TTL is 60 s**, so existing connections drain within 1 minute.
6. Wait 10 minutes. Watch the v2 traffic + verify no 5xx.

### T+1 hour
1. `terraform destroy` the old workspace.
2. The public S3 transparency bucket and ACM cert are imported into v2 state, so the destroy doesn't touch them.
3. Confirm `aegis-public-roots-628478946931` is intact.
4. Update DNS TTL back to 300 s (default).

### Total operator time: ~3 hours. Total customer-visible downtime: 0.

---

## 11. The operator's one-line commands

```bash
# First-time bootstrap (creates state bucket + lock table)
cd infra/terraform/bootstrap && terraform apply -auto-approve

# Apply the full stack
cd infra/terraform && terraform apply -auto-approve

# Promote a new bundle (after pushing to S3)
aws ssm put-parameter --name /aegis/prod/current_bundle_sha --value "$(git rev-parse --short HEAD)" --overwrite
aws autoscaling start-instance-refresh --auto-scaling-group-name aegis-prod-asg

# Scale up for traffic spike
terraform apply -var="asg_desired=4" -auto-approve

# Tear everything down (testing only — NEVER in prod!)
cd infra/terraform && terraform destroy -auto-approve
```

---

## 12. What the next Claude session needs to do

1. **Read this file end to end.** Every decision is here with the why.
2. **Write the 14 module dirs** under `infra/terraform/modules/`. Each module has exactly 3 files: `main.tf`, `variables.tf`, `outputs.tf`. No nested modules.
3. **Write `envs/prod/terraform.tfvars`** with the 30 variables from §7.
4. **Write `backend.tf`, `versions.tf`, `variables.tf`, `outputs.tf`, `main.tf`** at the repo root of `infra/terraform/`.
5. **Verify with `terraform plan`** that the new stack would create the same shape as today's prod (minus the bugs).
6. **Migration runbook** in `infra/terraform/MIGRATION.md` — the playbook from §10 with the actual commands.
7. **Don't import the old state.** Start fresh — pull RDS via snapshot restore, pull S3 via `terraform import`, pull ACM via `terraform import`. Old state has drift; new state should be exact.

---

## 13. What I deliberately left OUT (and why)

| Out | Why |
|---|---|
| **Multi-region (active-active or active-passive)** | Premature at 0 paying customers. Add when 1st EU customer asks. ~6 eng-weeks. |
| **EKS / Kubernetes** | Docker Compose works fine for 23 containers and a single founder. Migrate when a SRE team exists. |
| **CloudFront in front of ALB** | <50 users + Indian tenant = no meaningful CDN win yet. Add when serving global. |
| **Reserved Instances** | Buy after 6 months of steady production. Wait until billing is predictable. |
| **AWS Backup** | RDS Multi-AZ + automated daily snapshots + 14-day retention is sufficient. AWS Backup adds operational complexity. |
| **Bastion host / VPN** | SSM Session Manager replaces both — no SSH key to lose, no VPN to manage. |
| **VPC peering / Transit Gateway** | Single VPC. Add only when multi-region lands. |
| **AWS Inspector / GuardDuty** | $30/mo each, low signal at this stage. Turn on at Series A or first F500 customer. |
| **Customer-managed KMS keys** | AWS-managed KMS is FREE for RDS, S3, Secrets Manager. Move to CMKs only when an F500 customer demands BYOK. |
| **CloudFormation StackSets / Org-level SCPs** | Single-account, single-region. Premature. |
| **Spot instances for cost savings** | Aegis hot path can't tolerate 2-min interrupts. Stick with On-Demand. |

---

## 14. Worked example — what `terraform apply` produces

After a clean `terraform apply` against an empty AWS account (besides the bootstrap state bucket + ACM cert + Route 53 zone — preserved), here is what you get:

```
aws_vpc.main                            10.20.0.0/16
aws_subnet.public[0..1]                 in 2 AZs
aws_subnet.private[0..1]                in 2 AZs
aws_internet_gateway.main
aws_nat_gateway.main[0..1]              one per AZ
aws_eip.nat[0..1]
aws_route_table.public + private[0..1]
aws_security_group.alb                  ingress 80,443
aws_security_group.ec2                  ingress 8000 from alb_sg only
aws_security_group.rds                  ingress 5432 from ec2_sg only
aws_security_group.redis                ingress 6379 from ec2_sg only
aws_iam_role.ec2_instance               assume_role: ec2
aws_iam_role_policy_attachment * 4      SSM, S3 read, Secrets read, CloudWatch agent
aws_iam_instance_profile.ec2
aws_secretsmanager_secret.db_password   random_password()
aws_secretsmanager_secret.jwt_signing   random_password(64)
aws_secretsmanager_secret.anthropic_key set via -var
aws_route53_record.apex_a               aegisagent.in → ALB
aws_route53_record.apex_aaaa            (IPv6)
aws_route53_record.www_cname            www → aegisagent.in
aws_wafv2_web_acl.main                  + AWS managed rules + rate limit
aws_wafv2_web_acl_association.alb
aws_lb.main                             internet-facing application LB
aws_lb_listener.http                    redirect → https
aws_lb_listener.https                   forward → target_group, cert from ACM
aws_lb_target_group.main                health: /healthz, 200, 2/3 thresholds
aws_launch_template.main                AMI=Amazon Linux 2023 arm64, m6g.large
                                        user_data reads SSM /aegis/prod/current_bundle_sha
                                        IAM profile = ec2_instance
aws_autoscaling_group.main              min=2, max=10, des=2, health=ELB, grace=300
aws_autoscaling_policy.scale_out        target tracking: avg CPU 60%
aws_db_subnet_group.main
aws_db_parameter_group.main             shared_preload_libraries=pg_stat_statements
aws_db_instance.main                    db.t4g.medium, multi_az=true, 14d backup
aws_elasticache_subnet_group.main
aws_elasticache_replication_group.main  cluster_enabled=true, TLS in transit
aws_s3_bucket.backups                   versioning + 90-day lifecycle on bundle-*
aws_s3_bucket.alb_logs                  + bucket policy allowing ALB
aws_s3_bucket_public_access_block.*     blocks public access on backups + alb_logs
aws_cloudwatch_log_group.alb_logs       30-day retention
aws_cloudwatch_metric_alarm.* 5         (the 5 from §8)
aws_sns_topic.alarms                    + email subscription
aws_ssm_parameter.current_bundle_sha    initial value from var.bundle_sha_initial
```

**Total: ~50 resources, ~12-minute first apply, ~3-minute incremental applies.**

---

## 15. Honest closing — what this design solves vs doesn't

**Solves:**
- Today's bundle-poisoning outage (immutable versioned bundles + SSM-driven Launch Template).
- Today's "ASG terminated the healthy instance" surprise (instance refresh with MinHealthyPercentage=100 means new instance must pass health checks before old one is terminated).
- The 3-environment drift (single `envs/prod` + workspaces if needed later).
- The manual deploy choreography (SSM Parameter flip + `start-instance-refresh` is the entire deploy).

**Does NOT solve:**
- Multi-region failover (still single `ap-south-1`).
- 24x7 on-call (still solo founder).
- SOC 2 evidence collection (still in `30-day-product-plan.md`).
- Customer GTM (still 0 paying customers).
- pgbouncer connection-pool tuning at scale (still the same `pool_mode=transaction`).

**The honest scope:** this is the rebuild that lets the next 6 weeks of feature work happen without operational chaos. It does NOT magically produce customers. **Run S0 of the sprint plan in parallel — sign Drata + 10 outreach emails. The infra rebuild buys 6 weeks of stability; only customer money buys the next 6 months.**

---

*Generated 2026-06-19. Next session: read this file, write Terraform, run §10 migration playbook. ~3-4 hours of focused work for a clean rebuild.*
