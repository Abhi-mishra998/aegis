# Data residency — what crosses regions, what doesn't

Sprint EI-5 (2026-06-20). This document is the answer to the GDPR /
Schrems II / India DPDP question:

> "When my EU users' data flows into Aegis, where does it physically
>  reside? Does any of it ever leave the EU?"

The short answer for an EU customer:

> Your tenant's runtime data — audit rows, decisions, agent state,
> session intelligence — **never leaves eu-west-1**. The only Aegis
> resources that cross regions are static, non-personal: the deploy
> bundle, policy bundles, UI assets, and the published static docs.

This page enumerates every data class and its residency claim with
the source-code path that enforces it.

---

## 1. Per-data-class residency table

| Data class | Stored in | Crosses to ap-south-1? | Enforcement |
|---|---|---|---|
| Audit rows (`audit_logs`) | RDS in eu-west-1 | **NO** | `infra/terraform/envs/eu-west-1/terraform.tfvars.example:48` — separate RDS instance. App has no replication code. |
| Decisions (`decisions`) | RDS in eu-west-1 | **NO** | Same RDS as above. |
| Identity (tenants, users) | RDS in eu-west-1 | **NO** | Same RDS. Clerk identity provider has its own EU data residency story — see §3. |
| Session intelligence, IAG, flight recorder | Redis in eu-west-1 | **NO** | `redis_node_type = "cache.t3.micro"` in eu-west-1 vars. App connects to `REDIS_URL` env, set at boot to the eu-west-1 endpoint. |
| Backups (RDS snapshots, S3) | `aegis-eu-backups-<acct>` in eu-west-1 | **NO** | `bundle_bucket` in EU vars points to the EU bucket. The terraform `s3` module sets `object_lock_enabled` per-region. |
| Public transparency Merkle roots | `aegis-public-roots-eu-<acct>` in eu-west-1 | **NO** | `public_roots_bucket` in EU vars. The transparency_scheduler writes to this bucket only. |
| CloudTrail | `aegis-cloudtrail-eu-<acct>` in eu-west-1 | **NO** | CloudTrail in EU is region-bound; ap-south-1 CloudTrail never sees EU events. |
| Deploy bundle (`current.tar.gz`) | S3 in eu-west-1 | **CROSSES** | Built from GitHub repo (US-east, GitHub-owned); replicated by operator into the EU bucket before EC2 launch. Contains zero personal data — only application code + UI assets. |
| Policy bundles (`bundles/<tenant-id>.tar.gz`) | OPA in-memory | **NO** | Built per-tenant inside the eu-west-1 cluster; never leaves the policy service. |
| UI static assets (`/assets/*`) | CloudFront edge cache, global | **CROSSES** | These are public, anonymous, immutable Vite-bundle JS+CSS. No personal data. |
| Static docs (`/docs/*`) | This GitHub repo (US-east-owned) | **CROSSES** | Markdown only; no personal data; same shape as any open-source project's docs. |

The line: **runtime data NEVER crosses regions; static artifacts MAY
cross regions because they contain no personal data.**

---

## 2. How an EU customer sees this

The customer signs up via `https://eu.aegisagent.in` (DNS resolves to
the eu-west-1 ALB). Every request they make stays in eu-west-1. The
ap-south-1 prod stack has no IAM role that lets it read from the
eu-west-1 RDS, ElastiCache, or S3 buckets — even an Aegis insider
operating from the ap-south-1 deploy console cannot pull EU customer
data without a fresh cross-region AWS access grant (which is itself
logged by CloudTrail in the EU).

For verification on the customer's side:

```bash
# 1. Confirm the EU instance's ALB is in eu-west-1.
dig +short eu.aegisagent.in
# CNAME chain should end in *.eu-west-1.elb.amazonaws.com

# 2. Pull the public EU transparency root for yesterday.
aws s3 cp --no-sign-request \
  "s3://aegis-public-roots-eu-628478946931/roots/<your-tenant-id>/$(date -u -d 'yesterday' +%Y-%m-%d).json" -

# 3. Verify it with the public CLI.
pip install aegis-aevf
aegis-verify --bucket aegis-public-roots-eu-628478946931 \
  --tenant <your-tenant-id>
# expect: V1-V6 PASS.
```

---

## 3. Sub-processors with their own residency story

Some sub-processors are global services that we cannot region-bind.
For the EU instance the matching EU posture is:

| Sub-processor | What we send them | EU residency posture |
|---|---|---|
| Clerk (auth/SSO) | Email, name, hashed password | Clerk supports EU residency at the org tier (`region = "eu-west-1"`); the EU Aegis stack uses a separate Clerk org pinned to EU. See <https://clerk.com/docs/deployments/regions>. |
| Anthropic API | Customer prompt + LLM response | Anthropic offers EU-residency endpoints (`api.eu.anthropic.com`); the EU stack's `ANTHROPIC_API_BASE` is set to that endpoint in SSM. |
| OpenAI API | Customer prompt + LLM response | OpenAI offers EU residency via the Azure-hosted variant; default in EU stack is the Azure EU endpoint. Document the Azure path with the customer in their DPA. |
| Stripe (billing) | Email, company name, charge metadata | Stripe processes EU customers via its EU entity automatically. We do NOT send any prompt or policy data to Stripe. |
| GitHub (source code only) | None | We use GitHub to host application source; no customer data ever lands here. |
| AWS | All of the above, bound to eu-west-1 | Standard AWS DPA + Schrems II SCCs. |
| Sigstore (cosign verify) | None | Public transparency log; we publish artifact signatures, never customer data. |

---

## 4. Cross-region operator tooling

The operator team works from a single GitHub repo + a single AWS
account. To prevent accidental cross-region writes:

- **Terraform state** is per-region (`backend-eu-west-1.hcl` in
  `infra/terraform/envs/eu-west-1/`). Running `terraform init -reconfigure`
  is the gating action that switches the operator's working state between
  ap-south-1 and eu-west-1.
- **`scripts/ops/deploy_to_*` wrappers** look up the ASG by
  `Environment=` tag; there is no `aegis-prod` ASG in eu-west-1 and no
  `aegis-eu` ASG in ap-south-1, so a wrong-region deploy fails fast.
- **CloudTrail** in both regions is enabled by the
  `infra/terraform/modules/cloudtrail` module. Any operator action
  against EU resources is auditable from the EU CloudTrail bucket.

---

## 5. What this document does NOT cover

- **Active failover between regions**: not supported. The EU instance
  is an independent product surface (`eu.aegisagent.in`); it is not a
  hot/warm replica of `aegisagent.in`. An ap-south-1 outage does not
  fail traffic over to EU because the EU stack has no copy of an
  ap-south-1 customer's data.
- **Cross-region data export for analytics**: not implemented.
  Customers who want their EU data pulled into a Snowflake / BigQuery
  warehouse in another region must do so through their own ETL with
  the SCIM / audit-log export endpoints; Aegis never moves the data
  ourselves.
- **GovCloud / FedRAMP / China**: not supported. Roadmap.

---

*Reviewed 2026-06-20. Re-review on any sub-processor change or new
region launch.*
