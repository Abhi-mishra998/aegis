#!/usr/bin/env bash
# Staggered ALB deploy — sprint-4.C.
#
# Deploys to host A, smoke-tests, returns it to the ALB target group, THEN
# deploys to host B. Adds ~90 seconds to deploy time vs the previous
# parallel-build pattern; eliminates the "both hosts unhealthy at the same
# moment" failure mode that the audits flagged in §7.1.
#
# Operator invocation:
#   ALB_TG_ARN=arn:aws:elasticloadbalancing:ap-south-1:.../targetgroup/aegis-gw/...
#   HOSTS="i-aaaa,i-bbbb"  # EC2 instance IDs
#   ./scripts/ops/deploy_staggered.sh
#
# Requires AWS CLI configured + the EC2 instances behind the ALB.
# Runs `git pull && docker compose up` per host via SSM, not SSH — SSM is
# what production already uses for deploy.yml and bypasses the SSH-key
# rotation problem.

set -euo pipefail

ALB_TG_ARN="${ALB_TG_ARN:?ALB_TG_ARN env var required}"
HOSTS="${HOSTS:?HOSTS env var required (comma-separated EC2 instance IDs)}"
AWS_REGION="${AWS_REGION:-ap-south-1}"
DRAIN_WAIT_SECONDS="${DRAIN_WAIT_SECONDS:-60}"

IFS=',' read -ra HOST_IDS <<< "$HOSTS"
[ "${#HOST_IDS[@]}" -ge 2 ] || { echo "Need at least 2 hosts for staggered deploy"; exit 2; }

deploy_one_host() {
  local instance_id="$1"
  echo ""
  echo "============================================================"
  echo "  Deploying $instance_id"
  echo "============================================================"

  echo "→ Deregistering from ALB target group"
  aws --region "$AWS_REGION" elbv2 deregister-targets \
    --target-group-arn "$ALB_TG_ARN" \
    --targets Id="$instance_id"

  echo "→ Waiting ${DRAIN_WAIT_SECONDS}s for in-flight requests to drain"
  sleep "$DRAIN_WAIT_SECONDS"

  echo "→ Running deploy via SSM"
  COMMAND_ID=$(aws --region "$AWS_REGION" ssm send-command \
    --instance-ids "$instance_id" \
    --document-name "AWS-RunShellScript" \
    --comment "staggered deploy" \
    --parameters 'commands=["set -e","cd /home/ec2-user/aegis","git fetch origin main","git reset --hard origin/main","cd infra","docker-compose -f docker-compose.yml -f docker-compose.aws.yml build --parallel","docker-compose -f docker-compose.yml -f docker-compose.aws.yml up -d"]' \
    --output text --query 'Command.CommandId')

  echo "  SSM command: $COMMAND_ID  — waiting for completion"
  aws --region "$AWS_REGION" ssm wait command-executed \
    --command-id "$COMMAND_ID" \
    --instance-id "$instance_id" || {
      echo "✗ SSM command failed on $instance_id — leaving DEREGISTERED for safety"
      aws --region "$AWS_REGION" ssm get-command-invocation \
        --command-id "$COMMAND_ID" --instance-id "$instance_id" \
        --query 'StandardErrorContent' --output text || true
      exit 1
    }

  echo "→ Smoke-testing gateway on $instance_id"
  local PRIVATE_IP
  PRIVATE_IP=$(aws --region "$AWS_REGION" ec2 describe-instances \
    --instance-ids "$instance_id" \
    --query 'Reservations[].Instances[].PrivateIpAddress' --output text)

  for attempt in 1 2 3 4 5 6; do
    if curl -fsS --max-time 5 "http://${PRIVATE_IP}:8000/health" >/dev/null; then
      echo "  smoke ok (attempt $attempt)"
      break
    fi
    [ $attempt -eq 6 ] && {
      echo "✗ Smoke test failed after 6 attempts — leaving DEREGISTERED"
      exit 1
    }
    sleep 5
  done

  echo "→ Re-registering with ALB"
  aws --region "$AWS_REGION" elbv2 register-targets \
    --target-group-arn "$ALB_TG_ARN" \
    --targets Id="$instance_id"

  echo "→ Waiting for ALB to mark $instance_id healthy"
  aws --region "$AWS_REGION" elbv2 wait target-in-service \
    --target-group-arn "$ALB_TG_ARN" \
    --targets Id="$instance_id"
  echo "  ALB healthy: $instance_id"
}

# Deploy hosts one at a time. If any host fails, leave the rest alone — the
# ALB still serves traffic from the unchanged hosts.
for instance_id in "${HOST_IDS[@]}"; do
  deploy_one_host "$instance_id"
done

echo ""
echo "============================================================"
echo "  Staggered deploy complete across ${#HOST_IDS[@]} hosts"
echo "============================================================"
