# Aegis on AWS — Reference architecture

**Audience:** Customer Cloud Architect / Principal Security Architect doing
a vendor design review. **Status:** in production at
`https://aegisagent.in` since 2026-05; reviewed by external code audit
2026-06-19. Every claim in this document cites a file path in
`infra/terraform/modules/`.

---

## 1. Topology (one-region)

```
                Internet
                    │
                    ▼
            ┌───────────────┐
            │  CloudFront   │  (optional — used by /status mirror only)
            └───────┬───────┘
                    │
                    ▼
            ┌───────────────┐         ┌────────────────────┐
            │   AWS WAFv2   │◄────────│  Rate-limit rule    │
            │   on the ALB  │         │  2000 req / 5min/IP │
            └───────┬───────┘         └────────────────────┘
                    │
                    ▼
        ┌────────────────────┐
        │  Application LB    │  (Multi-AZ in 2 public subnets)
        │  TLS terminator    │  ACM cert *.aegisagent.in
        └─────────┬──────────┘
                  │  HTTP to instance health-check :8000/health
                  ▼
        ┌──────────────────────────────────────────┐
        │       Auto Scaling Group                 │
        │  min=2 / desired=2 / max=4               │
        │  m6g.large ARM64 (Graviton2)             │
        │  user_data: pull bundle + cosign-verify  │
        │  + docker compose up                     │
        └────┬──────────────────────────────┬──────┘
             │ (private subnet AZ-A)        │ (private subnet AZ-B)
             ▼                              ▼
        ┌─────────┐                    ┌─────────┐
        │ EC2 #1  │                    │ EC2 #2  │
        │ 32 services in docker-compose                       │
        │ gateway·identity·policy·decision·audit·registry·… │
        └────┬────┘                    └────┬────┘
             │                              │
             ▼                              ▼
        ┌────────────────────────────────────────────┐
        │        RDS Postgres 15  (Multi-AZ)         │
        │        db.t3.small × 2 (active+standby)    │
        │        4 logical DBs: identity / audit /   │
        │        registry / api                      │
        │        Daily backups (14-day retention)    │
        └────────────────────────────────────────────┘

        ┌────────────────────────────────────────────┐
        │      ElastiCache Redis 7  (Multi-AZ)       │
        │      cache.t3.micro × 2 (primary+replica)  │
        │      TLS in transit                        │
        └────────────────────────────────────────────┘

   ┌─────────────────────────┐  ┌──────────────────────────────┐
   │  S3 (backups, v2)       │  │  S3 (public-roots)           │
   │  Object Lock GOVERNANCE │  │  Anonymous Get; 5d Merkle    │
   │  35-day retention       │  │  roots + 30d nightly status  │
   └─────────────────────────┘  └──────────────────────────────┘

   ┌─────────────────────────┐  ┌──────────────────────────────┐
   │  S3 (cloudtrail, v2)    │  │  KMS CMK (audit envelope)    │
   │  Object Lock COMPLIANCE │  │  Annual rotation; per-region │
   │  180-day retention      │  │  alias/aegis-audit-envelope  │
   └─────────────────────────┘  └──────────────────────────────┘
```

**Single NAT Gateway** (or one-per-AZ in Enterprise tier) carries every
outbound call to Anthropic / OpenAI / Stripe / Sigstore. **No public IPs**
on EC2 instances.

---

## 2. Service mapping

Every box above maps to a Terraform module in `infra/terraform/modules/`.
The customer can `cd` into each module and verify the configuration is
what we claim:

| Aegis component | AWS service | Terraform module | Key config |
|---|---|---|---|
| Edge | ALB + WAF v2 | `alb/`, `waf/` | `waf_rate_limit_per_ip_per_5min`, ACM cert lookup |
| Compute | EC2 ASG (m6g.large ARM64) | `asg/` | user_data signs-verify cosign bundle before extraction |
| Database | RDS Postgres 15 Multi-AZ | `rds/` | `db_multi_az = true`, 14-day backup retention, KMS-encrypted storage |
| Cache | ElastiCache Redis 7 | `elasticache/` | 2 nodes (primary + replica), TLS in transit |
| Storage (backups) | S3 + Object Lock GOVERNANCE | `s3/` | 35-day retention; admin needs `s3:BypassGovernanceRetention` to delete |
| Storage (CloudTrail) | S3 + Object Lock COMPLIANCE | `cloudtrail/` | 180-day retention; cannot be lowered even by root |
| Storage (public roots) | S3, anonymous Get | `s3/` | `aegis-public-roots-…` bucket; 5d Merkle roots + 30d nightly status |
| KMS | Customer-managed CMK | `audit_kms/` | One per region; annual rotation; envelope wraps audit signatures |
| DNS | Route 53 alias → ALB | `route53/` | A record for the apex + status subdomain |
| Network | VPC + 4 subnets | `network/` | Distinct CIDR per env (prod 10.20/16, staging 10.30/16, EU 10.40/16) |
| Telemetry | CloudWatch + Prometheus | `cloudwatch/`, in-cluster Prometheus | 6 security counters, 5 alert rules, 4 Grafana dashboards |
| Secrets | AWS Secrets Manager + SSM | `secrets/`, `params/` | 11 secrets in `/aegis/<env>/*`, mesh JWT keys in `/aegis-prodha/mesh/*` |
| IAM | EC2 instance role + GHA OIDC | `iam/` | Least-privilege per ARN; OIDC role scoped to `repo:Abhi-mishra998/aegis:ref:refs/heads/main` |

The full topology (VPC + 4 subnets + 1 NAT + ALB + 2 EC2 + RDS Multi-AZ + 2
Redis nodes + 5 S3 buckets + 1 KMS CMK) is **47 Terraform resources**
created by one `terraform apply -var-file=envs/prod/terraform.tfvars`.

---

## 3. Per-tier sizing

Three reference sizing tiers, all from the same Terraform stack with
different `tfvars`:

| Field | Design-Partner (`envs/staging/`) | Enterprise (`envs/prod/`) | Multi-Region (`envs/eu-west-1/`) |
|---|---|---|---|
| Instance type | t4g.small (1 vCPU / 2 GB) | m6g.large (2 vCPU / 8 GB) | m6g.large |
| ASG desired | 1 | 2 | 2 |
| RDS class | db.t4g.micro Single-AZ | db.t3.small Multi-AZ | db.t3.small Multi-AZ |
| RDS backups | 1 day | 14 days | 14 days |
| Redis nodes | 1 | 2 | 2 |
| NAT Gateway | Single | Single (one-per-AZ on contract) | Single |
| WAF rate-limit | 20,000 / 5 min / IP (so soak runs through) | 2,000 / 5 min / IP | 2,000 |
| Monthly cost | ~$82 | ~$420 | ~$420 |

Larger customer tiers (e.g. m6g.2xlarge × 4 ASG, db.r6g.large RDS Multi-AZ
+ read replica) are mechanical — flip the `tfvars` values; no architecture
change.

---

## 4. Failure modes + recovery

| Failure | Detected by | Recovery |
|---|---|---|
| Single EC2 dies | ALB health-check (HTTP 200 on `/health`) | ASG launches a replacement; user_data re-pulls the signed bundle in ~3 min |
| AZ-wide outage | ALB drains the AZ; one EC2 remains | RDS Multi-AZ failover in <60s; Redis replica promotes; service continues at half capacity |
| RDS instance failure | RDS health-check | Multi-AZ failover; <60s RPO; standby promotes |
| Bundle deploy failure | EC2 user_data exits non-zero on cosign-verify fail (if `/aegis/prod/require_signed_bundle=true`) | ASG continues on the prior instances; `scripts/ops/rollback.sh` repoints SSM bundle SHA |
| Region outage | external — AWS region down | EU instance unaffected (separate VPC + region); ap-south-1 customers see downtime; DR plan documented in `docs/runbooks/disaster_recovery.md` |
| Audit chain tampering | DB trigger `deny_audit_log_mutation` (raises SQLSTATE P0001 on UPDATE / DELETE) | Cannot occur from the application; DBA mutation requires DROP TRIGGER which is itself a logged DDL event |
| KMS key compromised | CloudTrail KMS event spike | `kms:DisableKey` is a hard kill switch — every audit-envelope decrypt fails until rotation |
| Cross-tenant leak | EH-1 RBAC + EH-3 telemetry + DB CHECK constraint `org_id = tenant_id` | Three independent layers (Sprint EH-1/EH-3 + ADR-003) all have to fail simultaneously |

Monthly DR drill (`docs/runbooks/dr_drill_log.md`) restores a snapshot to a
throw-away RDS instance and verifies row counts. First successful drill
documented 2026-06-20; RTO < 1 hour, RPO < 24 hours.

---

## 5. Compliance posture per region

This stack carries (per `docs/security/data_residency.md`):

| Data class | Region | Crosses regions? | Verification |
|---|---|---|---|
| Audit rows (`audit_logs`) | RDS in selected region | NO | Separate RDS instance per region |
| Decisions / identity / sessions | Same RDS | NO | Same |
| Public transparency Merkle roots | `aegis-public-roots-eu-…` (EU) / `aegis-public-roots-…` (ap-south-1) | NO | Distinct buckets per region |
| Deploy bundle (`current.tar.gz`) | S3 in selected region | CROSSES | Built on GitHub (US); no personal data — only application code + UI assets |
| UI static assets | CloudFront global edge | CROSSES | Public, immutable, no personal data |

ap-south-1 prod is the default. eu-west-1 EU instance brings a separate
KMS CMK, separate Postgres + Redis, separate CloudTrail bucket. The two
stacks share NO IAM grant that would let an ap-south-1 EC2 read EU data.

---

## 6. Bring-up procedure

One operator, fresh AWS account, ~2 hours:

```bash
# Pre-Terraform: 3 S3 buckets + ACM cert (see envs/prod/README.md or
# docs/runbooks/multi_region_bootstrap.md for the EU bring-up).
cd infra/terraform
terraform init                                     # uses S3 backend
terraform apply -var-file=envs/prod/terraform.tfvars   # 47 resources
# Then:
bash scripts/ops/build_release_bundle.sh           # builds + cosign-signs
# (Or trigger .github/workflows/release_bundle.yml for keyless OIDC signing)
```

Verifies in <5 min:

```bash
curl -sS https://aegisagent.in/health         # 200
curl -sS https://aegisagent.in/trust          # 200 — trust center
aws s3 ls s3://aegis-public-roots-628478946931/ --no-sign-request | head
```

---

## 7. Customer-side review checklist

For a Cloud Architect / Principal Security reviewer:

- [ ] Read `infra/terraform/modules/` end-to-end (~600 lines HCL; one
      sitting)
- [ ] Cross-check the **per-region CMK** claim — ADR-006 +
      `infra/terraform/modules/audit_kms/main.tf:12-29`
- [ ] Cross-check the **three-layer tenant isolation** — ADR-003 +
      DB CHECK constraints in `services/identity/alembic/versions/
      a1b2c3d4e5f6_*.py`
- [ ] Cross-check the **append-only audit chain** — ADR-001 +
      `services/audit/alembic/versions/3a519b48a6f2_*.py` trigger
- [ ] Cross-check the **keyless cosign chain** — ADR pending (EI-10) +
      `.github/workflows/release_bundle.yml` + ASG user_data verify
- [ ] Walk through one nightly run on the public status page:
      `https://aegisagent.in/status` + raw artefact at
      `s3://aegis-public-roots-628478946931/nightly/latest.json`
- [ ] Run the customer security package: `bash scripts/ops/build_
      customer_security_package.sh` (produces 220+ KB ZIP with 67 files
      including SBOM, threat model, all 10 ADRs, full DPA/BAA/MSA/SLA)

If any of those don't reconcile with this document, the document is
wrong — file an issue with the discrepancy and a maintainer will
correct it in the same PR as the code change.

---

## 8. What this architecture explicitly does NOT use

For procurement teams whose audit checklist asks the inverse question:

- **No Kubernetes** — Docker Compose on EC2; see ADR-007.
- **No service mesh sidecar (Istio / Linkerd / Envoy)** — ES256 mesh JWTs
  per ADR-008 are the inter-service auth; no mTLS today.
- **No third-party APM (Datadog, New Relic)** — Prometheus + Grafana +
  Jaeger run in-cluster; no agent on the application path. SIEM
  integration is opt-in per tenant (Splunk HEC / Datadog Logs / Elastic
  Cloud / Sentinel / Chronicle).
- **No Lambda** — every service is a long-running container; no
  serverless cold-start surface.
- **No DynamoDB / Redshift / Glue** — single Postgres for all
  application data; per-tenant analytics via SQL views, not warehouse.
- **No EFS / FSx** — EC2 instances are stateless; named docker volumes
  on instance store get wiped on ASG instance refresh by design.
- **No GovCloud / FedRAMP / China region** — roadmap; ap-south-1 +
  eu-west-1 today.
