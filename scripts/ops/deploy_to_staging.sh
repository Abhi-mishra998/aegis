#!/usr/bin/env bash
# Deploy the current `main` bundle to the staging environment.
#
# Sprint EI-4 (2026-06-20). Wraps scripts/ops/build_release_bundle.sh
# with staging-specific BUNDLE_BUCKET + ASG_NAME so the nightly soak
# workflow has a one-line invocation to refresh staging before the
# run.
#
# Difference vs scripts/ops/deploy_staggered.sh:
#   - staggered.sh assumes 2 hosts and drains between; staging is 1 host
#     and just blue-greens via ASG instance refresh.
#   - this script never touches prod targets.
#
# Usage:
#   bash scripts/ops/deploy_to_staging.sh
#
# Required env:
#   AWS credentials configured (via gh OIDC role or local profile)
# Optional env:
#   STAGING_BUNDLE_BUCKET (default: aegis-staging-backups-628478946931)
#   STAGING_ASG_NAME      (default: looked up by tag Environment=staging)
#   AWS_REGION            (default: ap-south-1)
#
# Exits non-zero on:
#   - bundle build failure
#   - bundle upload failure
#   - ASG instance refresh failure

set -euo pipefail

AWS_REGION="${AWS_REGION:-ap-south-1}"
STAGING_BUNDLE_BUCKET="${STAGING_BUNDLE_BUCKET:-aegis-staging-backups-628478946931}"
STAGING_ASG_NAME="${STAGING_ASG_NAME:-}"

echo "════════════════════════════════════════"
echo " Aegis — deploy to staging"
echo " region: ${AWS_REGION}"
echo " bucket: ${STAGING_BUNDLE_BUCKET}"
echo "════════════════════════════════════════"

# If the operator didn't pin the ASG name, look it up by tag — this is
# robust against terraform regenerating the ASG (the name has a suffix).
if [[ -z "$STAGING_ASG_NAME" ]]; then
    STAGING_ASG_NAME="$(aws --region "$AWS_REGION" autoscaling describe-auto-scaling-groups \
        --query "AutoScalingGroups[?contains(Tags[?Key=='Environment'].Value, 'staging')].AutoScalingGroupName | [0]" \
        --output text)"
    if [[ -z "$STAGING_ASG_NAME" || "$STAGING_ASG_NAME" == "None" ]]; then
        echo "FAIL — no ASG tagged Environment=staging found" >&2
        echo "       Did you run \`terraform apply -var-file=envs/staging/terraform.tfvars\` first?" >&2
        exit 1
    fi
fi
echo " ASG  : ${STAGING_ASG_NAME}"
echo ""

REPO_ROOT="$(git rev-parse --show-toplevel)"
SHA="$(git rev-parse --short HEAD)"

# Reuse the prod bundle builder, but point it at the staging bucket and
# ASG. The builder respects BUNDLE_BUCKET + ASG_NAME + UPLOAD + ASG_REFRESH
# env vars (see scripts/ops/build_release_bundle.sh).
BUNDLE_BUCKET="$STAGING_BUNDLE_BUCKET" \
ASG_NAME="$STAGING_ASG_NAME" \
AWS_REGION="$AWS_REGION" \
UPLOAD=1 \
ASG_REFRESH=1 \
bash "$REPO_ROOT/scripts/ops/build_release_bundle.sh"

# Update the staging SSM bundle SHA pointer so freshly-launched instances
# also pick up this build.
echo ""
echo "→ Updating /aegis/staging/current_bundle_sha → $SHA"
aws --region "$AWS_REGION" ssm put-parameter \
    --name "/aegis/staging/current_bundle_sha" \
    --value "$SHA" \
    --type "String" \
    --overwrite >/dev/null

echo ""
echo "✓ Staging deploy initiated (instance refresh runs ~3-5 min)."
echo "  Check ALB health:"
echo "    aws --region $AWS_REGION elbv2 describe-target-health \\"
echo "      --target-group-arn \$(aws elbv2 describe-target-groups --names aegis-staging-tg --query 'TargetGroups[0].TargetGroupArn' --output text)"
