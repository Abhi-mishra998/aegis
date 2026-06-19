#!/usr/bin/env bash
# Coordinated destruction of every old ap-south-1 resource so the new
# Terraform stack applies cleanly. Aligned with terraform.md §1 PRESERVE
# list.
#
# PRESERVE (this script does NOT touch):
#   - s3://aegis-public-roots-628478946931            customer-visible
#   - s3://aegis-terraform-state-628478946931          state bucket
#   - ACM cert aegisagent.in                           24h to re-validate
#   - Route 53 zone Z033117538JKIIKDBDPUJ              authoritative for apex
#   - SSM /aegis-alerts/* /aegis-playwright/* /aegis-siem/* (cross-cutting)
#
# DESTROY (in this order):
#   1. ASG (drains, then terminates EC2 instances)
#   2. ALB + target group + listeners + WAF association
#   3. Launch Template
#   4. RDS instance (deletion_protection disabled first)
#   5. ElastiCache replication group
#   6. SSM Parameters under /aegis-prodha/ and /acp-prodha/
#   7. Secrets Manager secrets under acp-prodha/
#   8. KMS alias/aegis-audit-envelope (alias + schedule key deletion)
#   9. WAFv2 ACL acp-prodha-web-acl
#  10. CloudTrail acp-mgmt-events
#  11. CloudWatch alarms acp-prodha-*
#  12. CloudWatch log groups under /aegis/* and /aws/rds/instance/acp-prodha-*
#  13. S3 buckets: acp-alb-logs-prodha-*, acp-backups-prodha-*, acp-cloudtrail-*
#  14. IAM roles + instance profiles: acp-ec2-role, acp-prodha-ec2-role
#  15. VPC endpoints (S3 + DynamoDB)
#  16. NAT Gateway + EIP
#  17. Subnets + Internet Gateway + Route tables
#  18. VPC vpc-0cf8ccc4a74fbd633 + default VPC vpc-089e6e43e7874a3d6
#  19. SNS topic acp-prod-alerts
#  20. Orphan EIPs (3 unattached)
#
# RUN ONLY AFTER:
#   1. backup_cloud_state.sh has produced + verified an encrypted archive.
#   2. You have inspected this file and acknowledged each step.
#   3. CTO email approval (this is a destructive, customer-visible operation).
#
# USAGE:
#   AWS_REGION=ap-south-1 ./scripts/destroy_old_cloud.sh           # interactive prompts
#   AWS_REGION=ap-south-1 ./scripts/destroy_old_cloud.sh --yes-i-am-sure  # no prompts
#   AWS_REGION=ap-south-1 ./scripts/destroy_old_cloud.sh --dry-run        # show commands

set -euo pipefail

: "${AWS_REGION:=ap-south-1}"

YES=0
DRY=0
for arg in "$@"; do
  case "$arg" in
    --yes-i-am-sure) YES=1 ;;
    --dry-run)        DRY=1 ;;
    *) echo "Unknown arg: $arg"; exit 2 ;;
  esac
done

confirm() {
  if [[ "$YES" -eq 1 ]]; then return 0; fi
  printf '\n  >>> %s [y/N]: ' "$1"
  read -r ans
  [[ "$ans" == "y" || "$ans" == "Y" ]] || { echo "  aborted."; exit 1; }
}

run() {
  if [[ "${DRY}" -eq 1 ]]; then
    echo "  (dry-run) $*"
    return 0
  fi
  "$@" 2>&1 || echo "  (continued past error: $?)"
}

phase() { printf '\n========== %s ==========\n' "$*"; }

# ─── Pre-flight ────────────────────────────────────────────────────────
phase "Pre-flight"
echo "  AWS account: $(aws sts get-caller-identity --query Account --output text)"
echo "  Region:      ${AWS_REGION}"
echo
echo "  This will DESTROY every ap-south-1 resource on the prod-ha brownfield."
echo "  PRESERVE list is documented at the top of this file."
confirm "Continue?"

# ─── 1. ASG ────────────────────────────────────────────────────────────
phase "1. ASG drain + delete"
ASG_NAME=$(aws --region "${AWS_REGION}" autoscaling describe-auto-scaling-groups \
  --query 'AutoScalingGroups[?contains(AutoScalingGroupName,`acp-prodha-asg`)].AutoScalingGroupName' \
  --output text | head -1 || true)
if [[ -n "${ASG_NAME}" ]]; then
  echo "  ASG: ${ASG_NAME}"
  run aws --region "${AWS_REGION}" autoscaling update-auto-scaling-group \
    --auto-scaling-group-name "${ASG_NAME}" --min-size 0 --desired-capacity 0
  echo "  Waiting 60s for instances to terminate ..."
  [[ "${DRY}" -eq 0 ]] && sleep 60
  run aws --region "${AWS_REGION}" autoscaling delete-auto-scaling-group \
    --auto-scaling-group-name "${ASG_NAME}" --force-delete
fi

# ─── 2. ALB / target group / WAF association ───────────────────────────
phase "2. ALB + target group + WAF disassociation"
ALB_ARN=$(aws --region "${AWS_REGION}" elbv2 describe-load-balancers \
  --query 'LoadBalancers[?contains(LoadBalancerName,`acp-prodha-alb`)].LoadBalancerArn' \
  --output text | head -1 || true)
if [[ -n "${ALB_ARN}" ]]; then
  # Disassociate WAF first (otherwise WAF refuses ACL delete).
  WAF_ARN=$(aws --region "${AWS_REGION}" wafv2 list-web-acls --scope REGIONAL \
    --query 'WebACLs[?contains(Name,`acp-prodha-web-acl`)].ARN' --output text || true)
  if [[ -n "${WAF_ARN}" ]]; then
    run aws --region "${AWS_REGION}" wafv2 disassociate-web-acl --resource-arn "${ALB_ARN}"
  fi
  # Listeners + target groups go with the ALB delete.
  run aws --region "${AWS_REGION}" elbv2 delete-load-balancer --load-balancer-arn "${ALB_ARN}"
fi
TG_ARN=$(aws --region "${AWS_REGION}" elbv2 describe-target-groups \
  --query 'TargetGroups[?contains(TargetGroupName,`acp-prodha-tg`)].TargetGroupArn' \
  --output text | head -1 || true)
if [[ -n "${TG_ARN}" ]]; then
  run aws --region "${AWS_REGION}" elbv2 delete-target-group --target-group-arn "${TG_ARN}"
fi

# ─── 3. Launch Template ────────────────────────────────────────────────
phase "3. Launch Template"
LT_ID=$(aws --region "${AWS_REGION}" ec2 describe-launch-templates \
  --query 'LaunchTemplates[?contains(LaunchTemplateName,`acp-prodha-lt`)].LaunchTemplateId' \
  --output text | head -1 || true)
[[ -n "${LT_ID}" ]] && run aws --region "${AWS_REGION}" ec2 delete-launch-template --launch-template-id "${LT_ID}"

# ─── 4. RDS ────────────────────────────────────────────────────────────
phase "4. RDS — disable deletion protection + delete (skips final snapshot; you took manual)"
if aws --region "${AWS_REGION}" rds describe-db-instances --db-instance-identifier acp-prodha-postgres >/dev/null 2>&1; then
  run aws --region "${AWS_REGION}" rds modify-db-instance \
    --db-instance-identifier acp-prodha-postgres --no-deletion-protection --apply-immediately
  [[ "${DRY}" -eq 0 ]] && sleep 10
  run aws --region "${AWS_REGION}" rds delete-db-instance \
    --db-instance-identifier acp-prodha-postgres --skip-final-snapshot
  echo "  RDS delete kicked off (10+ min). Continuing with parallel destroys..."
fi

# ─── 5. ElastiCache ────────────────────────────────────────────────────
phase "5. ElastiCache replication group"
if aws --region "${AWS_REGION}" elasticache describe-replication-groups --replication-group-id acp-prodha-redis >/dev/null 2>&1; then
  run aws --region "${AWS_REGION}" elasticache delete-replication-group \
    --replication-group-id acp-prodha-redis --no-retain-primary-cluster
fi

# ─── 6. SSM Parameters under /aegis-prodha/ + /acp-prodha/ ─────────────
phase "6. SSM Parameters"
NAMES=$(aws --region "${AWS_REGION}" ssm describe-parameters \
  --parameter-filters 'Key=Name,Option=BeginsWith,Values=/aegis-prodha/,/acp-prodha/' \
  --query 'Parameters[].Name' --output text 2>/dev/null || echo "")
for n in ${NAMES}; do
  run aws --region "${AWS_REGION}" ssm delete-parameter --name "${n}"
done

# ─── 7. Secrets Manager under acp-prodha/ ──────────────────────────────
phase "7. Secrets Manager"
NAMES=$(aws --region "${AWS_REGION}" secretsmanager list-secrets \
  --query 'SecretList[?starts_with(Name,`acp-prodha/`)].Name' --output text || echo "")
for n in ${NAMES}; do
  run aws --region "${AWS_REGION}" secretsmanager delete-secret \
    --secret-id "${n}" --force-delete-without-recovery
done

# ─── 8. KMS alias + schedule key deletion (30-day window) ──────────────
phase "8. KMS alias/aegis-audit-envelope"
KMS_KEY=$(aws --region "${AWS_REGION}" kms describe-key --key-id alias/aegis-audit-envelope \
  --query 'KeyMetadata.KeyId' --output text 2>/dev/null || echo "")
if [[ -n "${KMS_KEY}" ]]; then
  run aws --region "${AWS_REGION}" kms delete-alias --alias-name alias/aegis-audit-envelope
  run aws --region "${AWS_REGION}" kms schedule-key-deletion --key-id "${KMS_KEY}" --pending-window-in-days 30
fi

# ─── 9. WAFv2 ──────────────────────────────────────────────────────────
phase "9. WAFv2 Web ACL"
WAF_INFO=$(aws --region "${AWS_REGION}" wafv2 list-web-acls --scope REGIONAL \
  --query 'WebACLs[?contains(Name,`acp-prodha-web-acl`)].[Name,Id]' --output text || echo "")
if [[ -n "${WAF_INFO}" ]]; then
  WAF_NAME=$(echo "${WAF_INFO}" | awk '{print $1}')
  WAF_ID=$(echo "${WAF_INFO}" | awk '{print $2}')
  LOCK_TOKEN=$(aws --region "${AWS_REGION}" wafv2 get-web-acl --scope REGIONAL --name "${WAF_NAME}" --id "${WAF_ID}" \
    --query 'LockToken' --output text || echo "")
  [[ -n "${LOCK_TOKEN}" ]] && run aws --region "${AWS_REGION}" wafv2 delete-web-acl \
    --scope REGIONAL --name "${WAF_NAME}" --id "${WAF_ID}" --lock-token "${LOCK_TOKEN}"
fi

# ─── 10. CloudTrail ────────────────────────────────────────────────────
phase "10. CloudTrail"
run aws --region "${AWS_REGION}" cloudtrail delete-trail --name acp-mgmt-events

# ─── 11. CloudWatch alarms ─────────────────────────────────────────────
phase "11. CloudWatch alarms"
ALARMS=$(aws --region "${AWS_REGION}" cloudwatch describe-alarms \
  --query 'MetricAlarms[?contains(AlarmName,`acp-prodha`)].AlarmName' --output text || echo "")
if [[ -n "${ALARMS}" ]]; then
  run aws --region "${AWS_REGION}" cloudwatch delete-alarms --alarm-names ${ALARMS}
fi

# ─── 12. CloudWatch log groups ─────────────────────────────────────────
phase "12. CloudWatch log groups"
for prefix in "/aegis/agent" "/aws/rds/instance/acp-prodha-postgres"; do
  GROUPS=$(aws --region "${AWS_REGION}" logs describe-log-groups --log-group-name-prefix "${prefix}" \
    --query 'logGroups[].logGroupName' --output text || echo "")
  for g in ${GROUPS}; do
    run aws --region "${AWS_REGION}" logs delete-log-group --log-group-name "${g}"
  done
done

# ─── 13. S3 buckets (empty + delete) ───────────────────────────────────
phase "13. S3 buckets (alb-logs / backups / cloudtrail). PRESERVE public-roots + tf-state."
for b in "acp-alb-logs-prodha-628478946931" "acp-backups-prodha-628478946931" "acp-cloudtrail-628478946931"; do
  if aws s3api head-bucket --bucket "${b}" 2>/dev/null; then
    echo "  Emptying s3://${b}"
    run aws s3 rm "s3://${b}" --recursive --quiet
    # Versioned buckets need version-delete to actually empty
    run aws s3api delete-objects --bucket "${b}" --delete \
      "$(aws s3api list-object-versions --bucket "${b}" --query '{Objects: [].{Key: Key, VersionId: VersionId}}' --output json 2>/dev/null || echo '{}')" 2>/dev/null || true
    run aws s3 rb "s3://${b}" --force
  fi
done

# ─── 14. IAM roles + instance profiles ─────────────────────────────────
phase "14. IAM roles + instance profiles (acp-ec2-role, acp-prodha-ec2-role)"
for r in "acp-ec2-role" "acp-prodha-ec2-role"; do
  # Detach managed policies
  ATTACHED=$(aws iam list-attached-role-policies --role-name "${r}" \
    --query 'AttachedPolicies[].PolicyArn' --output text 2>/dev/null || echo "")
  for p in ${ATTACHED}; do
    run aws iam detach-role-policy --role-name "${r}" --policy-arn "${p}"
  done
  # Delete inline policies
  INLINE=$(aws iam list-role-policies --role-name "${r}" \
    --query 'PolicyNames[]' --output text 2>/dev/null || echo "")
  for p in ${INLINE}; do
    run aws iam delete-role-policy --role-name "${r}" --policy-name "${p}"
  done
  # Remove from instance profiles
  PROFILES=$(aws iam list-instance-profiles-for-role --role-name "${r}" \
    --query 'InstanceProfiles[].InstanceProfileName' --output text 2>/dev/null || echo "")
  for ip in ${PROFILES}; do
    run aws iam remove-role-from-instance-profile --instance-profile-name "${ip}" --role-name "${r}"
    run aws iam delete-instance-profile --instance-profile-name "${ip}"
  done
  run aws iam delete-role --role-name "${r}"
done

# ─── 15. VPC endpoints ─────────────────────────────────────────────────
phase "15. VPC endpoints"
VPCE_IDS=$(aws --region "${AWS_REGION}" ec2 describe-vpc-endpoints \
  --filters Name=vpc-id,Values=vpc-0cf8ccc4a74fbd633 \
  --query 'VpcEndpoints[].VpcEndpointId' --output text || echo "")
if [[ -n "${VPCE_IDS}" ]]; then
  run aws --region "${AWS_REGION}" ec2 delete-vpc-endpoints --vpc-endpoint-ids ${VPCE_IDS}
fi

# ─── 16. NAT + EIP ─────────────────────────────────────────────────────
phase "16. NAT Gateway + EIP"
NAT_ID=$(aws --region "${AWS_REGION}" ec2 describe-nat-gateways \
  --filter Name=vpc-id,Values=vpc-0cf8ccc4a74fbd633 \
  --query 'NatGateways[?State==`available`].NatGatewayId' --output text | head -1 || echo "")
if [[ -n "${NAT_ID}" ]]; then
  run aws --region "${AWS_REGION}" ec2 delete-nat-gateway --nat-gateway-id "${NAT_ID}"
  echo "  Waiting 90s for NAT to release EIP ..."
  [[ "${DRY}" -eq 0 ]] && sleep 90
fi

# ─── 17. Subnets + IGW + route tables ──────────────────────────────────
phase "17. Subnets + IGW + route tables"
SUBNETS=$(aws --region "${AWS_REGION}" ec2 describe-subnets \
  --filters Name=vpc-id,Values=vpc-0cf8ccc4a74fbd633 \
  --query 'Subnets[].SubnetId' --output text || echo "")
for s in ${SUBNETS}; do
  run aws --region "${AWS_REGION}" ec2 delete-subnet --subnet-id "${s}"
done

IGW_ID=$(aws --region "${AWS_REGION}" ec2 describe-internet-gateways \
  --filters Name=attachment.vpc-id,Values=vpc-0cf8ccc4a74fbd633 \
  --query 'InternetGateways[].InternetGatewayId' --output text | head -1 || echo "")
if [[ -n "${IGW_ID}" ]]; then
  run aws --region "${AWS_REGION}" ec2 detach-internet-gateway --internet-gateway-id "${IGW_ID}" --vpc-id vpc-0cf8ccc4a74fbd633
  run aws --region "${AWS_REGION}" ec2 delete-internet-gateway --internet-gateway-id "${IGW_ID}"
fi

RTS=$(aws --region "${AWS_REGION}" ec2 describe-route-tables \
  --filters Name=vpc-id,Values=vpc-0cf8ccc4a74fbd633 \
  --query 'RouteTables[?Associations[0].Main!=`true`].RouteTableId' --output text || echo "")
for rt in ${RTS}; do
  run aws --region "${AWS_REGION}" ec2 delete-route-table --route-table-id "${rt}"
done

# ─── 18. VPCs (prod-ha + default) ──────────────────────────────────────
phase "18. VPC delete"
run aws --region "${AWS_REGION}" ec2 delete-vpc --vpc-id vpc-0cf8ccc4a74fbd633
confirm "Delete the DEFAULT VPC vpc-089e6e43e7874a3d6 too? (recommended)"
run aws --region "${AWS_REGION}" ec2 delete-vpc --vpc-id vpc-089e6e43e7874a3d6

# ─── 19. SNS topic ─────────────────────────────────────────────────────
phase "19. SNS"
run aws --region "${AWS_REGION}" sns delete-topic \
  --topic-arn "arn:aws:sns:${AWS_REGION}:$(aws sts get-caller-identity --query Account --output text):acp-prod-alerts"

# ─── 20. Orphan EIPs ───────────────────────────────────────────────────
phase "20. Orphan EIPs"
ORPHANS=$(aws --region "${AWS_REGION}" ec2 describe-addresses \
  --query 'Addresses[?AssociationId==null].AllocationId' --output text || echo "")
for eip in ${ORPHANS}; do
  run aws --region "${AWS_REGION}" ec2 release-address --allocation-id "${eip}"
done

phase "DESTROY COMPLETE"
echo
echo "PRESERVED (verify these still exist):"
echo "  s3://aegis-public-roots-628478946931"
echo "  s3://aegis-terraform-state-628478946931"
echo "  ACM cert aegisagent.in"
echo "  Route 53 zone Z033117538JKIIKDBDPUJ"
echo
echo "NEXT:"
echo "  cd infra/terraform"
echo "  terraform plan -var-file=envs/prod/terraform.tfvars -out=tfplan"
echo "  terraform apply tfplan"
echo
echo "  After apply succeeds, restore secrets/parameters from the encrypted"
echo "  backup using the values you captured in backup_cloud_state.sh."
