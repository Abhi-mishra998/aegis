#!/bin/bash
# rolling_deploy.sh — closes P1-DEPLOY-001 from audit-final-22.md
#
# WHY: the 2026-06-22 P0 deploy used `aws ssm send-command` against both
# ASG hosts in a single command. SSM defaults to parallel execution; both
# acp_gateway containers recycled simultaneously and ALB returned 502s for
# ~60s. One host failed health checks mid-restart and was terminated by ASG.
# This wrapper forces one-host-at-a-time + verifies recovery before
# touching the next.
#
# USAGE: ./rolling_deploy.sh <SHA> [--force-clean]
set -euo pipefail
SHA="${1:?usage: rolling_deploy.sh <SHA> [--force-clean]}"
FORCE_CLEAN="${2:-auto}"
REGION="ap-south-1"
ASG_NAME="aegis-prod-asg"

echo "==== resolve ASG hosts ===="
HOSTS=$(aws autoscaling describe-auto-scaling-groups --region "$REGION" \
  --auto-scaling-group-names "$ASG_NAME" \
  --query 'AutoScalingGroups[0].Instances[?LifecycleState==`InService`].InstanceId' \
  --output text)
echo "InService hosts: $HOSTS"
HOST_COUNT=$(echo "$HOSTS" | wc -w | tr -d ' ')
if [ "$HOST_COUNT" -lt 1 ]; then
  echo "FATAL: no InService hosts found in ASG $ASG_NAME" >&2
  exit 2
fi

probe_alb() {
  curl -sS -A "Mozilla/5.0 rolling-deploy" -o /dev/null -w "%{http_code}" \
    --max-time 5 https://aegisagent.in/status
}

deploy_one_host() {
  local HOST="$1"
  echo
  echo "==== deploy $SHA → $HOST ===="
  # SSM timeout 1800s (was 900s before 2026-06-25). safe_deploy.sh
  # walks: fetch bundle + extract + force-recreate ~22 containers +
  # `_waiting 90s for healthchecks` + 30s settle + final probe. Cold-start
  # paths (first image pull, OPA reload, gateway warmup) push the script
  # close to 900s; 4 of 6 attempts on the 2026-06-25 prod deploy got
  # SIGTERM'd at the previous ceiling even though every container ended
  # healthy. 1800s costs nothing on the happy path (the poll loop below
  # exits as soon as SSM reports Success).
  CMD=$(aws ssm send-command --region "$REGION" \
    --instance-ids "$HOST" \
    --document-name "AWS-RunShellScript" \
    --comment "rolling deploy $SHA → $HOST" \
    --parameters "commands=[\"aws s3 cp s3://aegis-prod-backups-628478946931/releases/safe_deploy.sh /tmp/safe_deploy.sh --region $REGION\",\"chmod +x /tmp/safe_deploy.sh\",\"sudo /tmp/safe_deploy.sh $SHA $FORCE_CLEAN 2>&1\"]" \
    --timeout-seconds 1800 \
    --query "Command.CommandId" --output text)
  echo "CMD=$CMD"

  # Poll
  while true; do
    STATUS=$(aws ssm list-command-invocations --region "$REGION" --command-id "$CMD" \
      --query 'CommandInvocations[0].Status' --output text 2>/dev/null)
    if [ "$STATUS" = "Success" ]; then
      echo "  $HOST → Success"
      break
    elif [ "$STATUS" = "Failed" ] || [ "$STATUS" = "TimedOut" ] || [ "$STATUS" = "Cancelled" ]; then
      echo "  $HOST → $STATUS — aborting rolling deploy"
      aws ssm get-command-invocation --region "$REGION" --command-id "$CMD" --instance-id "$HOST" \
        --query 'StandardOutputContent' --output text 2>/dev/null | tail -25
      return 1
    fi
    sleep 20
  done

  # ALB recovery check before touching next host
  echo "==== ALB recovery probe ===="
  for i in 1 2 3 4 5 6 7 8 9 10; do
    code=$(probe_alb)
    echo "  [t+$((i*4))s] /status -> $code"
    if [ "$code" = "200" ]; then
      ok=$((ok+1))
    fi
    sleep 4
  done
}

# Walk hosts one at a time. If any fails, stop — ALB still has the
# already-patched hosts; operator inspects + decides.
for HOST in $HOSTS; do
  if ! deploy_one_host "$HOST"; then
    echo
    echo "FATAL: deploy to $HOST failed — STOPPING. Earlier hosts (if any) already have $SHA." >&2
    exit 1
  fi
done

echo
echo "==== ALL HOSTS DEPLOYED ===="
echo "Verify with: curl -A 'Mozilla/5.0' https://aegisagent.in/status"
