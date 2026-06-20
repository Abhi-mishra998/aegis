# Aegis on Azure — Reference architecture

**Audience:** Customer Cloud Architect whose default cloud is Azure.
**Status:** Target architecture — every Aegis component is portable to
Azure; the Terraform skeleton is **on roadmap, not shipped**. AWS
(`aegis-on-aws.md`) is the production reference today. This document
describes the Azure deployment a customer with a "must run in our Azure
tenant" requirement would pay for as a hosted instance OR self-host on
their own Azure subscription.

---

## 1. Service mapping (Azure ↔ AWS)

Every box in `aegis-on-aws.md` §2 maps cleanly to an Azure equivalent.
The application code is unchanged — Docker images run the same compose
file regardless of cloud, and the data-plane services (Postgres, Redis,
S3-equivalent, KMS-equivalent) are all standard-protocol.

| Aegis component | AWS service (today) | Azure equivalent | Notes |
|---|---|---|---|
| Edge: WAF + L7 LB | ALB + WAFv2 | Front Door + WAF policy + Application Gateway | Front Door handles TLS + global routing; App Gateway handles per-region rate-limit |
| Compute: ASG | EC2 ASG (m6g.large × 2) | VM Scale Set (Standard_D4ps_v5 × 2, ARM64) | Same docker-compose user_data; ARM Ampere parts available in eastus, westeurope |
| Database | RDS Postgres 15 Multi-AZ | Azure Database for PostgreSQL Flexible Server, Zone-Redundant HA | Same Postgres 15; alembic migrations unchanged |
| Cache | ElastiCache Redis 7 (2 nodes) | Azure Cache for Redis, Premium tier, 2 shards | Same Redis 7 protocol |
| Storage (backups) | S3 + Object Lock GOVERNANCE | Storage Account + Immutable Blob Storage (time-based, unlocked) | Equivalent to GOVERNANCE — admin can override |
| Storage (CloudTrail) | S3 + Object Lock COMPLIANCE | Storage Account + Immutable Blob Storage (time-based, **locked**) | Equivalent to COMPLIANCE — cannot be lowered |
| Storage (public roots) | S3 anonymous Get | Storage Account static-website mode | Same anonymous-read pattern |
| KMS | Customer-managed CMK (one per region) | Key Vault Premium (HSM-backed) — one per region | Premium tier required for HSM-backed keys; standard tier works for non-regulated tenants |
| DNS | Route 53 alias → ALB | Azure DNS CNAME → Front Door | Same apex + status subdomain shape |
| Network | VPC + 4 subnets | VNet + 4 subnets | Distinct address space per env (mirror the prod 10.20 / staging 10.30 / EU 10.40 pattern) |
| Telemetry | CloudWatch + Prometheus + Grafana | Azure Monitor + Log Analytics + same in-cluster Prometheus/Grafana | Application metrics path unchanged (still Prometheus); Azure Monitor for infra |
| Secrets | Secrets Manager + SSM Parameter Store | Key Vault Standard | Same `/aegis/<env>/*` naming convention |
| IAM | EC2 instance role + GHA OIDC | Managed Identity + GHA OIDC to Azure | Identical OIDC trust pattern; the Azure-side role HCL is the one missing artefact |
| Audit immutability | RDS storage encryption + DB trigger `deny_audit_log_mutation` | Same Postgres trigger (cross-cloud — Postgres feature, not AWS) | Application-level immutability identical |

The application code knows nothing about cloud-specific services — every
external dependency is reached through an URL or library that's the same
across both clouds:

- Postgres: `psycopg2` / `asyncpg`
- Redis: `redis-py` / `redis-py-asyncio`
- Blob storage: `boto3` today (S3-compatible API; Azure Storage has S3
  compatibility OR we'd swap to `azure-storage-blob` — one PR)
- KMS: today via `boto3.client('kms')`; Azure path is `azure-keyvault-keys`
  with a thin abstraction layer (~50 lines).
- Object signing (cosign keyless): identical — Sigstore is cloud-agnostic
  by design.

---

## 2. Topology (one-region Azure)

```
                  Internet
                      │
                      ▼
              ┌───────────────┐
              │  Front Door   │  (global anycast; TLS + caching for /trust)
              └───────┬───────┘
                      │
                      ▼
              ┌───────────────┐
              │  WAF policy   │  (managed rule set + custom rate-limit)
              └───────┬───────┘
                      │
                      ▼
              ┌────────────────────┐
              │ Application Gateway │  (Zone-Redundant; TLS terminator;
              │                     │   per-region; backend pool = VMSS)
              └─────────┬───────────┘
                        │
                        ▼
              ┌──────────────────────────────────────────┐
              │       Virtual Machine Scale Set          │
              │  min=2 / max=4                           │
              │  Standard_D4ps_v5 (ARM64 Ampere)         │
              │  Same docker-compose stack as AWS        │
              └────┬──────────────────────────────┬──────┘
                   │ (private subnet AZ-1)        │ (private subnet AZ-2)
                   ▼                              ▼
              ┌─────────┐                    ┌─────────┐
              │ VM #1   │                    │ VM #2   │
              │ 32 services in docker-compose                       │
              └────┬────┘                    └────┬────┘
                   │                              │
                   ▼                              ▼
            ┌────────────────────────────────────────────┐
            │   Azure DB for PostgreSQL Flexible Server  │
            │   Zone-Redundant HA (active + standby)     │
            │   General Purpose, 4 vCore                 │
            └────────────────────────────────────────────┘

            ┌────────────────────────────────────────────┐
            │   Azure Cache for Redis (Premium)          │
            │   2 shards, TLS-only                       │
            └────────────────────────────────────────────┘

       ┌─────────────────────────┐  ┌──────────────────────────────┐
       │  Storage Account        │  │  Storage Account             │
       │  (backups)              │  │  (public roots, static-website│
       │  Immutable Blob, time-  │  │   anonymous Get)             │
       │  based, unlocked        │  │                              │
       └─────────────────────────┘  └──────────────────────────────┘

       ┌─────────────────────────┐  ┌──────────────────────────────┐
       │  Storage Account        │  │  Key Vault (Premium)         │
       │  (Activity Log archive) │  │  HSM-backed CMK (per-region) │
       │  Immutable Blob, LOCKED │  │  Auto-rotation 12 mo         │
       └─────────────────────────┘  └──────────────────────────────┘
```

**Single NAT Gateway** (Azure equivalent: NAT Gateway resource) for
outbound to Anthropic / OpenAI / Stripe / Sigstore. **No public IPs**
on VMSS instances; Front Door is the only ingress.

---

## 3. Per-tier sizing on Azure

Three tiers matching the AWS reference:

| Field | Design-Partner | Enterprise | Multi-Region |
|---|---|---|---|
| VM size | Standard_B2pls_v2 (2 vCore / 4 GB) | Standard_D4ps_v5 (4 vCore / 16 GB) | Standard_D4ps_v5 |
| VMSS desired | 1 | 2 | 2 |
| Postgres SKU | Burstable B2s, Single zone | General Purpose D4ds_v5, Zone-Redundant HA | Same |
| Postgres backup retention | 7 days | 14 days | 14 days |
| Redis tier | Standard C0 (250 MB) | Premium P1 (6 GB, 1 shard) | Same |
| Front Door | Standard | Premium (private link to App GW) | Premium |
| Key Vault | Standard | Premium (HSM) | Premium |
| Monthly cost (estimate) | ~$120 | ~$520 | ~$520 |

Azure pricing is ~20-30% higher than AWS at every tier; same reliability
shape. Enterprise customers on contractual SLA should sit on Premium
Front Door + Premium Key Vault (HSM-backed).

---

## 4. EU residency on Azure

Mirror of the AWS EU pattern (`docs/security/data_residency.md` +
`infra/terraform/envs/eu-west-1/`):

- Separate Azure subscription OR distinct resource group with no
  cross-region private link.
- Region: `westeurope` (Amsterdam) or `northeurope` (Dublin).
- Key Vault: separate vault in the EU region; the West Europe vault has
  no role-grant to anything outside West Europe.
- Postgres + Redis + storage accounts: separate per region.
- Front Door: per-tenant routing rule sends EU customers to the EU App
  Gateway backend (avoid cross-region origin pull).

Customer-side verification:

```bash
# Confirm Postgres + storage + KV are all in westeurope (the EU instance).
az postgres flexible-server list --query "[?location=='westeurope']" \
  --output table
az storage account list --query "[?location=='westeurope']" --output table
az keyvault list --query "[?properties.location=='westeurope']" --output table
```

---

## 5. What's missing today + roadmap

| Item | Status | Owner / blocker |
|---|---|---|
| Terraform skeleton for Azure | **Not shipped.** AWS is the source of truth. | Engineering — 2-3 day sprint when first Azure-customer contract lands |
| `azure-storage-blob` abstraction layer | **Not shipped.** boto3 + S3-compatible Azure works today but lossy. | Engineering — 1-day port when needed |
| `azure-keyvault-keys` shim for KMS calls | **Not shipped.** | Engineering — 1-day port |
| Azure OIDC role for the equivalent of `aegis-gha-release` | **Not shipped.** | Engineering — covered in the Terraform sprint above |
| Pen-test against Azure deployment | **Not scheduled.** AWS pen-test scheduled Q3 2026 (per `docs/security/pentest-sow-template.md`). | Sales — second pen-test when first Azure customer signs |

We are honest: today Aegis-on-Azure is a customer-contract trigger, not
an off-the-shelf product. The application is portable; the operator
muscle for Azure-specific issues (Key Vault throttling, Front Door cache
invalidation, P-series ARM availability per region) lives in AWS muscle
memory and would build up over the first customer's onboarding.

---

## 6. What this architecture explicitly does NOT use on Azure

Mirrors the AWS reference:

- **No AKS** — VMSS with Docker Compose; same operability story as
  EC2 ASG (ADR-007).
- **No Azure Service Bus / Event Grid** — Redis Streams are the
  internal queue; SSE for outbound to the UI.
- **No App Service / Functions** — same long-running-container model.
- **No Azure SQL / Cosmos DB** — Postgres Flexible Server only.
- **No Application Insights agent** — Prometheus + Grafana + Jaeger
  in-cluster.
