#!/usr/bin/env bash
# Sprint EI-5 (2026-06-20). One-shot bootstrap of the eu-west-1 stack's
# pre-Terraform dependencies.
#
# Terraform itself needs four AWS resources to exist BEFORE its first
# `apply` against eu-west-1:
#   1. Per-region state bucket            (Terraform writes the state file here)
#   2. Per-region bundle bucket           (EC2 user_data downloads from here)
#   3. Per-region public-roots bucket     (transparency_scheduler uploads here)
#   4. *.aegisagent.in ACM cert in EU     (ALB listener attaches to it)
#
# This script creates the three buckets idempotently and prints the
# CLI you need to run for the cert (the cert validation involves Route53
# CNAME, which is interactive enough that scripting it is more fragile
# than a 90-second copy-paste).
#
# Usage:
#   AWS_PROFILE=aegis-eu bash scripts/ops/bootstrap_eu_region.sh
#
# Safe to re-run — exits 0 if every resource already exists.

set -euo pipefail

AWS_REGION="${AWS_REGION:-eu-west-1}"
ACCOUNT_ID="${ACCOUNT_ID:-$(aws sts get-caller-identity --query 'Account' --output text)}"

STATE_BUCKET="aegis-terraform-state-eu-${ACCOUNT_ID}"
BUNDLE_BUCKET="aegis-eu-backups-${ACCOUNT_ID}"
ROOTS_BUCKET="aegis-public-roots-eu-${ACCOUNT_ID}"

echo "════════════════════════════════════════"
echo " Aegis EU bootstrap"
echo "  account: ${ACCOUNT_ID}"
echo "  region:  ${AWS_REGION}"
echo "════════════════════════════════════════"

create_bucket_if_missing() {
    local name="$1"
    local visibility="$2"   # 'private' or 'public-read-objects'
    if aws s3api head-bucket --bucket "$name" --region "$AWS_REGION" 2>/dev/null; then
        echo "  ✓ bucket exists: $name"
        return 0
    fi
    echo "  → creating $name ($visibility)"
    aws s3api create-bucket \
        --bucket "$name" \
        --region "$AWS_REGION" \
        --create-bucket-configuration "LocationConstraint=$AWS_REGION" >/dev/null

    aws s3api put-bucket-encryption --bucket "$name" \
        --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'

    aws s3api put-bucket-versioning --bucket "$name" \
        --versioning-configuration Status=Enabled

    if [ "$visibility" = "public-read-objects" ]; then
        # Transparency roots are anonymously fetchable — same pattern as
        # the ap-south-1 aegis-public-roots bucket. Block public access
        # at the BUCKET level except for GetObject, which we open via a
        # bucket policy.
        aws s3api put-public-access-block --bucket "$name" \
            --public-access-block-configuration \
              "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=false,RestrictPublicBuckets=false"

        cat > /tmp/aegis-eu-roots-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "PublicReadAegisRoots",
    "Effect": "Allow",
    "Principal": "*",
    "Action": "s3:GetObject",
    "Resource": "arn:aws:s3:::${name}/*"
  }]
}
EOF
        aws s3api put-bucket-policy --bucket "$name" --policy file:///tmp/aegis-eu-roots-policy.json
        rm /tmp/aegis-eu-roots-policy.json
    else
        # State + bundle buckets stay fully private.
        aws s3api put-public-access-block --bucket "$name" \
            --public-access-block-configuration \
              "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
    fi
}

# 1. Terraform state bucket
create_bucket_if_missing "$STATE_BUCKET" "private"

# 2. Per-region deploy bundle bucket
create_bucket_if_missing "$BUNDLE_BUCKET" "private"

# 3. Public transparency roots — anonymously readable
create_bucket_if_missing "$ROOTS_BUCKET" "public-read-objects"

# 4. ACM cert prompt (interactive — operator must validate via Route53)
echo ""
echo "── Step 4: ACM cert (manual, ~90 seconds) ───────────────"
echo "ACM is region-bound. The ap-south-1 wildcard cert does NOT cover"
echo "eu-west-1 ALBs. Issue a NEW *.aegisagent.in cert in eu-west-1:"
echo ""
echo "  aws acm request-certificate \\"
echo "    --domain-name '*.aegisagent.in' \\"
echo "    --validation-method DNS \\"
echo "    --region $AWS_REGION"
echo ""
echo "Then in the AWS console (ACM → the new cert → 'Create record in Route53')"
echo "click the auto-add CNAME, wait ~5 min for ISSUED. Terraform's"
echo "data.aws_acm_certificate lookup in main.tf will then find it."
echo ""
echo "════════════════════════════════════════"
echo " Bootstrap complete."
echo " Next: terraform init -reconfigure -backend-config=envs/eu-west-1/backend-eu-west-1.hcl"
echo "       terraform apply -var-file=envs/eu-west-1/terraform.tfvars"
echo "════════════════════════════════════════"
