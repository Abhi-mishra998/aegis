# Aegis — staging environment

Provisions a deliberately-smaller mirror of prod for nightly soak +
chaos + AEVF verification.

```bash
# First-time setup
cp terraform.tfvars.example terraform.tfvars
# (edit terraform.tfvars if you need to deviate from the defaults)
```

The `.tfvars` itself is git-ignored across the repo (same convention as
`envs/prod/terraform.tfvars`). The `.example` file in this directory is
tracked and sized to ~$80/month standing.

## Standing cost

~$80/month if left running 24×7:

| Component | Class | $/mo |
|---|---|---|
| EC2 | 1× t4g.small | $12 |
| RDS Postgres | db.t4g.micro, Single-AZ, 20 GB gp3 | $18 |
| RDS storage + backups | 20 GB + 1-day backup | $4 |
| ElastiCache Redis | 1× cache.t4g.micro | $11 |
| NAT Gateway | 1, single-AZ | $32 |
| ALB | shared listener idle hours | $5 |
| **Total** | | **~$82** |

## Lifecycle pattern (cheaper)

Staging is throw-away. The recommended pattern is:

```bash
# 1. Bring up before the nightly window starts.
cd infra/terraform
terraform apply -var-file=envs/staging/terraform.tfvars -auto-approve

# 2. Run nightly soak.
bash scripts/ops/deploy_to_staging.sh
# nightly soak GH Actions workflow targets staging.aegisagent.in

# 3. Tear down after the runs complete.
terraform destroy -var-file=envs/staging/terraform.tfvars -auto-approve
```

At a ~3 hours/night run window, monthly cost drops to ~$10 (mostly the
NAT-Gateway-hour minimum + RDS snapshot storage between runs).

## Bringing up the very first time

One-time bootstrap:

```bash
# 1. Create the S3 bundle bucket (Terraform expects it to exist).
aws s3 mb s3://aegis-staging-backups-628478946931 --region ap-south-1

# 2. Seed the SSM bundle parameter so the EC2 user_data has a value to read.
aws ssm put-parameter --name /aegis/staging/current_bundle_sha \
  --value main --type String --region ap-south-1

# 3. Terraform apply.
terraform apply -var-file=envs/staging/terraform.tfvars

# 4. Point staging.aegisagent.in at the new ALB.
#    The Route53 module already creates the A record; verify:
dig +short staging.aegisagent.in
```

## What staging does NOT have

- **No real customer data** — tenants are synthetic and dropped after
  every soak run.
- **No Multi-AZ RDS** — a single-AZ failure ends the run; that's the
  point of a soak harness (we want to discover the failure mode).
- **No production Clerk app** — staging uses its own Clerk dev key set
  in SSM at `/aegis/staging/clerk_*`.
- **No production Stripe** — staging uses Stripe test mode.

Anything you wire up in staging that's missing from this list should
either be (a) ignored by the soak harness or (b) added to prod.
