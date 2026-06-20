# Multi-region bootstrap — bring up eu-west-1 from zero

Sprint EI-5 (2026-06-20). End-to-end procedure for the operator to
stand up the EU instance the first time. Total elapsed time: **~2.5 hours**
(of which ~30 min is Terraform compute + ~5 min is ACM validation
delay; the rest is hands-on).

Prereqs:
- AWS console access with `iam:CreateRole` on the account
- An AWS CLI profile `aegis-eu` that points at the same account
- Local repo checkout at the tip of `main`

---

## Phase 1 — Pre-Terraform setup (~30 min)

### 1.1 Buckets + roots policy

Run the bootstrap script — creates the 3 EU S3 buckets idempotently
and prints the ACM next-step:

```bash
AWS_PROFILE=aegis-eu bash scripts/ops/bootstrap_eu_region.sh
```

You should see ✓ marks for:

- `aegis-terraform-state-eu-<acct>` — private, versioned
- `aegis-eu-backups-<acct>` — private, versioned
- `aegis-public-roots-eu-<acct>` — public-read on GetObject, bucket-policy set

### 1.2 ACM certificate

ACM is region-bound; the ap-south-1 wildcard cert does **not** cover
EU ALBs. Issue a new one:

```bash
aws acm request-certificate \
  --domain-name '*.aegisagent.in' \
  --validation-method DNS \
  --region eu-west-1
```

In the AWS console (ACM → the new certificate) click **Create record
in Route 53** to add the validation CNAME. ACM moves to `ISSUED`
within ~5 min once the CNAME propagates.

### 1.3 SSM bootstrap parameters

The EU stack expects these SSM SecureStrings to exist *before* the EC2
launch templates fire user_data:

```bash
# Per-tenant secrets — start with empties; the app boots cleanly
# even with placeholders, the operator fills in real values later.
for key in clerk_secret_key clerk_publishable_key anthropic_api_key \
           openai_api_key stripe_secret_key internal_secret; do
  aws ssm put-parameter --region eu-west-1 \
    --name "/aegis/eu/${key}" \
    --value "PENDING_OPERATOR" \
    --type SecureString
done

# The initial bundle SHA — operator overrides post-first-deploy
aws ssm put-parameter --region eu-west-1 \
  --name "/aegis/eu/current_bundle_sha" \
  --value "main" --type String
```

---

## Phase 2 — Terraform apply (~30 min)

```bash
cd infra/terraform

# CRITICAL: switch to the EU backend BEFORE running anything.
terraform init -reconfigure \
  -backend-config=envs/eu-west-1/backend-eu-west-1.hcl

# Copy the EU template and edit if you need to deviate from defaults.
cp envs/eu-west-1/terraform.tfvars.example envs/eu-west-1/terraform.tfvars
$EDITOR envs/eu-west-1/terraform.tfvars

# Dry run — read the plan output carefully. Should create ~50 resources
# (VPC, 4 subnets, 1 NAT, 1 IGW, ALB, ASG launch template, RDS, Redis,
# 7-8 SSM params, ALB listener cert binding, …).
terraform plan -var-file=envs/eu-west-1/terraform.tfvars

# Apply.
terraform apply -var-file=envs/eu-west-1/terraform.tfvars -auto-approve
```

Expected output: `Apply complete! Resources: 47 added, 0 changed, 0
destroyed.` (count may drift as modules evolve; the important number
is "0 changed, 0 destroyed").

---

## Phase 3 — First deploy (~15 min)

The ASG launched empty bundle SHA, so its EC2s are running but failing
the ALB health probe. Build + push the first real bundle:

```bash
# Build the bundle from the local repo (must be in /opt/aegis-eu/...
# build context — i.e. nothing terraform-specific, just the app).
BUNDLE_BUCKET=aegis-eu-backups-${ACCOUNT_ID} \
ASG_NAME=$(aws --region eu-west-1 autoscaling describe-auto-scaling-groups \
  --query "AutoScalingGroups[?contains(Tags[?Key=='Environment'].Value, 'eu')].AutoScalingGroupName | [0]" \
  --output text) \
AWS_REGION=eu-west-1 \
UPLOAD=1 \
ASG_REFRESH=1 \
bash scripts/ops/build_release_bundle.sh
```

Watch the ASG instance refresh in the console; ~5 minutes after the
refresh starts both EC2s should be ALB-healthy.

---

## Phase 4 — Verify (~10 min)

```bash
# 1. DNS resolves to eu-west-1 ALB
dig +short eu.aegisagent.in
# CNAME should end in *.eu-west-1.elb.amazonaws.com

# 2. /health 200
curl -sS https://eu.aegisagent.in/health
# {"status":"ok"}

# 3. /trust 200 (public page; same code as ap-south-1)
curl -sS -o /dev/null -w '%{http_code}\n' https://eu.aegisagent.in/trust
# 200

# 4. Spawn a demo workspace + run a no-op /execute to seed audit_logs
curl -sS -X POST https://eu.aegisagent.in/demo/spawn-workspace
# {"data":{"tenant_id":"…","jwt":"…"}}

# 5. Wait 60s for the transparency_scheduler tick, then verify a root
# landed in the EU bucket.
aws s3 ls "s3://aegis-public-roots-eu-${ACCOUNT_ID}/roots/" --recursive | head
```

---

## Phase 5 — Operational handoff (~15 min)

Tell the rest of the team:

- New endpoint: `https://eu.aegisagent.in`
- Operator profile: `AWS_PROFILE=aegis-eu` (the ap-south-1 profile cannot
  see EU resources — IAM bound)
- New CloudWatch alarms ring to the same SNS topic + same Slack channel,
  but the alarm name carries the suffix `-eu` so on-call knows which
  region.

Update `docs/security/subprocessors.md` if any sub-processor required a
contract amendment for the EU instance. Update
`docs/security/data_residency.md` if any of the per-data-class claims
changed during apply.

---

## Tear-down (rare — for design-partner pivot or contract loss)

```bash
cd infra/terraform
terraform init -reconfigure -backend-config=envs/eu-west-1/backend-eu-west-1.hcl

# Empty buckets that have versioning enabled first (Terraform won't
# delete a non-empty bucket).
aws s3 rm --recursive "s3://aegis-eu-backups-${ACCOUNT_ID}"
aws s3 rm --recursive "s3://aegis-public-roots-eu-${ACCOUNT_ID}"
# State bucket — DO NOT delete until terraform destroy has completed.

terraform destroy -var-file=envs/eu-west-1/terraform.tfvars -auto-approve

# Optional final clean-up: drop the state bucket itself.
aws s3 rb "s3://aegis-terraform-state-eu-${ACCOUNT_ID}" --force
```

Tear-down preserves the CloudTrail bucket (CloudTrail Object Lock
COMPLIANCE prevents deletion for 7 years per the security_controls
tier 3 retention class).
