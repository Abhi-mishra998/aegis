# Aegis Terraform — operator quickstart

Right-sized AWS stack for 10–50 concurrent users, ~$300/mo all-in.
Spec lives at `terraform.md` (repo root). Migration playbook at `MIGRATION.md`.

## Layout

```
infra/terraform/
├── bootstrap/          one-time S3 state bucket setup (already applied)
├── envs/prod/          terraform.tfvars (20 inputs)
└── modules/            13 modules, exactly 3 files each
    network/ security_groups/ iam/ secrets/ route53/ waf/
    alb/ asg/ rds/ elasticache/ s3/ cloudwatch/ ssm/
```

## Backend

S3 native locking (`use_lockfile = true`). No DynamoDB table.
Bucket `aegis-terraform-state-628478946931`, key `prod/terraform.tfstate`.

## The three commands you actually run

```bash
cd infra/terraform

# 1. download providers, configure backend
terraform init

# 2. catch type/ref errors locally (no AWS call)
terraform validate

# 3. preview every resource change (calls AWS read-only)
terraform plan -var-file=envs/prod/terraform.tfvars -out=tfplan
```

When you're satisfied with the plan:

```bash
terraform apply tfplan
```

## Security scan (optional but recommended pre-apply)

```bash
tfsec .                # static security analysis
tflint --recursive     # lint check
```

## Promote a new bundle (post-deploy lifecycle)

```bash
SHA=$(git rev-parse --short HEAD)

# build + upload (see scripts/ops/build_release_bundle.sh)
bash scripts/ops/build_release_bundle.sh
aws s3 cp /tmp/aegis-bundle-*.tar.gz \
  s3://acp-backups-prodha-628478946931/releases/bundle-${SHA}.tar.gz

# flip SSM pointer
aws ssm put-parameter --name /aegis/prod/current_bundle_sha \
  --value "${SHA}" --overwrite

# zero-downtime instance refresh
aws autoscaling start-instance-refresh \
  --auto-scaling-group-name aegis-prod-asg \
  --preferences MinHealthyPercentage=100,InstanceWarmup=300
```

## Rollback in 5 minutes

```bash
aws ssm put-parameter --name /aegis/prod/current_bundle_sha \
  --value "<earlier_sha>" --overwrite
aws autoscaling start-instance-refresh \
  --auto-scaling-group-name aegis-prod-asg
```

## Pre-existing resources NOT created by this stack

| Resource                     | Why preserved                                                |
|------------------------------|--------------------------------------------------------------|
| ACM cert `aegisagent.in`     | Re-validating takes 24h; pulled via `data "aws_acm_certificate"`. |
| Route 53 zone `aegisagent.in.` | Authoritative for the apex; pulled via `data "aws_route53_zone"`. |
| `aegis-public-roots-628478946931` S3 | Customer-visible cryptographic archive. `lifecycle { prevent_destroy = true }`. |
| `aegis-terraform-state-628478946931` S3 | This stack's state lives here. Bootstrap dir manages. |
| `acp-backups-prodha-628478946931` S3 | Existing bundle/snapshot bucket. Referenced as `bundle_bucket`. |

## What `terraform destroy` does NOT touch

The four PRESERVE rows above. Everything else (VPC, ALB, ASG, RDS,
Redis, alarms, etc.) is destroyed on `terraform destroy`. Solo founder,
zero customers, design-partner stage — destroying + rebuilding is a
valid recovery mode. Do not destroy without taking a final RDS snapshot.

## Migration from the old `infra/terraform.old/` codebase

See `MIGRATION.md`. Zero-downtime swap via Route 53 cutover.
