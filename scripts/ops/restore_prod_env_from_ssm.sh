#!/usr/bin/env bash
# Sprint 10 follow-on — Re-hydrate /opt/aegis/infra/.env from SSM SecureString.
#
# WHY: The bundle's exclude list intentionally drops repo-root .env so
#      dev secrets don't ship to prod. Operator-owned vars (CLERK_*,
#      STRIPE_*, ACP_AUTH_PROVIDER) therefore live in AWS SSM, and any
#      ASG instance refresh pulls a fresh /opt/aegis/infra/.env that
#      lacks them. Run this script after every refresh — OR have
#      user_data.sh call it on launch (Phase 6 polish).
#
# RUN MODES:
#
#   Local (ops machine):
#       ./scripts/ops/restore_prod_env_from_ssm.sh \
#           --asg acp-prodha-asg-20260613103432397400000003
#
#       Fans out via SSM send-command to every InService instance,
#       overlays the env, force-recreates identity + gateway.
#
#   On the instance (called from user_data or by SSM directly):
#       AEGIS_SSM_PREFIX=/aegis-prodha \
#       ENV_FILE=/opt/aegis/infra/.env \
#       ./scripts/ops/restore_prod_env_from_ssm.sh --local
#
# SSM parameter naming convention (kebab-case sub-keys map to UPPER_SNAKE_CASE env names):
#
#   /aegis-prodha/clerk/secret-key         → CLERK_SECRET_KEY
#   /aegis-prodha/clerk/webhook-secret     → CLERK_WEBHOOK_SECRET
#   /aegis-prodha/clerk/publishable-key    → CLERK_PUBLISHABLE_KEY
#   /aegis-prodha/clerk/frontend-api       → CLERK_FRONTEND_API
#   /aegis-prodha/clerk/jwks-url           → CLERK_JWKS_URL
#   /aegis-prodha/clerk/issuer             → CLERK_ISSUER
#   /aegis-prodha/clerk/jwt-template       → CLERK_JWT_TEMPLATE
#   /aegis-prodha/aegis/auth-provider      → ACP_AUTH_PROVIDER
#   /aegis-prodha/stripe/secret-key        → STRIPE_SECRET_KEY
#   /aegis-prodha/stripe/pro-price-id      → STRIPE_PRO_PRICE_ID
#   /aegis-prodha/stripe/enterprise-price-id → STRIPE_ENTERPRISE_PRICE_ID
#   /aegis-prodha/stripe/webhook-secret    → STRIPE_WEBHOOK_SECRET
#   /aegis-prodha/prometheus/scrape-secret → PROMETHEUS_SCRAPE_SECRET   (N11)

set -euo pipefail

REGION="${AWS_REGION:-ap-south-1}"
SSM_PREFIX="${AEGIS_SSM_PREFIX:-/aegis-prodha}"
ENV_FILE="${ENV_FILE:-/opt/aegis/infra/.env}"
ASG_NAME=""
MODE="remote"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --local)  MODE="local"; shift ;;
    --asg)    ASG_NAME="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --prefix) SSM_PREFIX="$2"; shift 2 ;;
    --env-file) ENV_FILE="$2"; shift 2 ;;
    *) echo "Unknown flag: $1" >&2; exit 2 ;;
  esac
done

# ssm-path → env-var-name mapping. Update this if you add new params.
declare -A MAP=(
  ["${SSM_PREFIX}/clerk/secret-key"]="CLERK_SECRET_KEY"
  ["${SSM_PREFIX}/clerk/webhook-secret"]="CLERK_WEBHOOK_SECRET"
  ["${SSM_PREFIX}/clerk/publishable-key"]="CLERK_PUBLISHABLE_KEY"
  ["${SSM_PREFIX}/clerk/frontend-api"]="CLERK_FRONTEND_API"
  ["${SSM_PREFIX}/clerk/jwks-url"]="CLERK_JWKS_URL"
  ["${SSM_PREFIX}/clerk/issuer"]="CLERK_ISSUER"
  ["${SSM_PREFIX}/clerk/jwt-template"]="CLERK_JWT_TEMPLATE"
  ["${SSM_PREFIX}/aegis/auth-provider"]="ACP_AUTH_PROVIDER"
  ["${SSM_PREFIX}/stripe/secret-key"]="STRIPE_SECRET_KEY"
  ["${SSM_PREFIX}/stripe/pro-price-id"]="STRIPE_PRO_PRICE_ID"
  ["${SSM_PREFIX}/stripe/enterprise-price-id"]="STRIPE_ENTERPRISE_PRICE_ID"
  ["${SSM_PREFIX}/stripe/webhook-secret"]="STRIPE_WEBHOOK_SECRET"
  # N11 deploy wiring (2026-06-21) — dedicated Prometheus /metrics scrape
  # secret. After A11's middleware change, gateway /metrics rejects
  # X-Internal-Secret entirely. Prometheus now ships X-Prometheus-Secret
  # = PROMETHEUS_SCRAPE_SECRET; rotate independently of every other secret.
  ["${SSM_PREFIX}/prometheus/scrape-secret"]="PROMETHEUS_SCRAPE_SECRET"
)

if [[ "$MODE" == "local" ]]; then
  # In-instance path: pull each value from SSM (the instance role has
  # ssm:GetParameter), overlay it into ENV_FILE, then docker compose
  # up --force-recreate identity gateway.
  BACKUP="${ENV_FILE}.bak-$(date -u +%Y%m%dT%H%M%SZ)"
  cp "$ENV_FILE" "$BACKUP"
  echo "Backup: $BACKUP"

  # Strip any prior values for the env names we're about to set, then
  # write fresh values.
  ENV_NAMES=$(printf '%s\n' "${MAP[@]}" | sort -u)
  PATTERN="^($(echo "$ENV_NAMES" | tr '\n' '|' | sed 's/|$//'))="
  grep -v -E "$PATTERN" "$BACKUP" > "$ENV_FILE"

  echo "" >> "$ENV_FILE"
  echo "# Sprint 10 — re-hydrated from SSM at $(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$ENV_FILE"
  for path in "${!MAP[@]}"; do
    env_name="${MAP[$path]}"
    val=$(aws --region "$REGION" ssm get-parameter --name "$path" --with-decryption --query 'Parameter.Value' --output text 2>/dev/null || echo "")
    if [[ -n "$val" ]]; then
      echo "${env_name}=${val}" >> "$ENV_FILE"
    else
      echo "  WARN: $path is empty or missing — skipping ${env_name}" >&2
    fi
  done

  echo "Verifying:"
  grep -cE "^($(echo "$ENV_NAMES" | tr '\n' '|' | sed 's/|$//'))=" "$ENV_FILE"

  cd /opt/aegis
  # N11 deploy wiring (2026-06-21) — also recreate `prometheus` so the
  # newly-overlaid PROMETHEUS_SCRAPE_SECRET env var is picked up; gateway
  # must also recreate so the in-process middleware reloads it. Without
  # restarting both, prometheus continues scraping with the stale
  # X-Prometheus-Secret value and gateway keeps returning 401 on /metrics.
  docker compose -f infra/docker-compose.yml -f infra/docker-compose.aws.yml \
    up -d --force-recreate --no-deps identity gateway prometheus 2>&1 | tail -8
  sleep 5
  docker ps --filter name=^acp_identity$ --filter name=^acp_gateway$ \
    --filter name=^acp_prometheus$ \
    --format '{{.Names}}\t{{.Status}}'
  exit 0
fi

# Remote path: ssh-free fan-out via SSM send-command.
if [[ -z "$ASG_NAME" ]]; then
  echo "FAIL: --asg <name> required (or pass --local to run in-instance)" >&2
  exit 2
fi

echo "→ Looking up InService instances in $ASG_NAME"
INSTANCES_JSON=$(aws --region "$REGION" autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names "$ASG_NAME" \
  --query 'AutoScalingGroups[0].Instances[?LifecycleState==`InService`].InstanceId' \
  --output json)
INSTANCES=$(echo "$INSTANCES_JSON" | python3 -c "import json,sys; print(' '.join(json.load(sys.stdin)))")
if [[ -z "$INSTANCES" ]]; then
  echo "FAIL: no InService instances under $ASG_NAME" >&2
  exit 3
fi
echo "  Targets: $INSTANCES"

# Inline the same logic as the --local block, executed via shell on
# the instances. The script body is JSON-escaped so the SSM Parameters
# document accepts it.
SCRIPT=$(cat <<'INNER'
set -euo pipefail
REGION=__REGION__
SSM_PREFIX=__SSM_PREFIX__
ENV_FILE=__ENV_FILE__
BACKUP="${ENV_FILE}.bak-$(date -u +%Y%m%dT%H%M%SZ)"
cp "$ENV_FILE" "$BACKUP"
echo "Backup: $BACKUP"

PATHS=(
  "${SSM_PREFIX}/clerk/secret-key:CLERK_SECRET_KEY"
  "${SSM_PREFIX}/clerk/webhook-secret:CLERK_WEBHOOK_SECRET"
  "${SSM_PREFIX}/clerk/publishable-key:CLERK_PUBLISHABLE_KEY"
  "${SSM_PREFIX}/clerk/frontend-api:CLERK_FRONTEND_API"
  "${SSM_PREFIX}/clerk/jwks-url:CLERK_JWKS_URL"
  "${SSM_PREFIX}/clerk/issuer:CLERK_ISSUER"
  "${SSM_PREFIX}/clerk/jwt-template:CLERK_JWT_TEMPLATE"
  "${SSM_PREFIX}/aegis/auth-provider:ACP_AUTH_PROVIDER"
  "${SSM_PREFIX}/stripe/secret-key:STRIPE_SECRET_KEY"
  "${SSM_PREFIX}/stripe/pro-price-id:STRIPE_PRO_PRICE_ID"
  "${SSM_PREFIX}/stripe/enterprise-price-id:STRIPE_ENTERPRISE_PRICE_ID"
  "${SSM_PREFIX}/stripe/webhook-secret:STRIPE_WEBHOOK_SECRET"
  # N11 deploy wiring (2026-06-21) — dedicated Prometheus /metrics scrape
  # secret. Gateway middleware rejects raw INTERNAL_SECRET after A11.
  "${SSM_PREFIX}/prometheus/scrape-secret:PROMETHEUS_SCRAPE_SECRET"
)

ENV_NAMES=$(printf '%s\n' "${PATHS[@]}" | cut -d: -f2 | sort -u)
PATTERN="^($(echo "$ENV_NAMES" | tr '\n' '|' | sed 's/|$//'))="
grep -v -E "$PATTERN" "$BACKUP" > "$ENV_FILE"
echo "" >> "$ENV_FILE"
echo "# Re-hydrated from SSM at $(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$ENV_FILE"

for pair in "${PATHS[@]}"; do
  P="${pair%%:*}"; N="${pair#*:}"
  V=$(aws --region "$REGION" ssm get-parameter --name "$P" --with-decryption --query 'Parameter.Value' --output text 2>/dev/null || echo "")
  if [[ -n "$V" ]]; then
    echo "${N}=${V}" >> "$ENV_FILE"
  fi
done

COUNT=$(grep -cE "^($(echo "$ENV_NAMES" | tr '\n' '|' | sed 's/|$//'))=" "$ENV_FILE")
echo "Restored $COUNT env vars"

cd /opt/aegis
# N11 deploy wiring (2026-06-21) — recreate prometheus + gateway too so the
# overlaid PROMETHEUS_SCRAPE_SECRET reaches both sides of the scrape.
docker compose -f infra/docker-compose.yml -f infra/docker-compose.aws.yml up -d --force-recreate --no-deps identity gateway prometheus 2>&1 | tail -6
sleep 8
docker ps --filter name=^acp_identity$ --filter name=^acp_gateway$ --filter name=^acp_prometheus$ --format '{{.Names}}\t{{.Status}}'
INNER
)
SCRIPT="${SCRIPT//__REGION__/$REGION}"
SCRIPT="${SCRIPT//__SSM_PREFIX__/$SSM_PREFIX}"
SCRIPT="${SCRIPT//__ENV_FILE__/$ENV_FILE}"

# Encode the script as JSON commands array.
PARAMS=$(python3 -c "
import json, sys
script = sys.stdin.read()
print(json.dumps({'commands': [script]}))
" <<< "$SCRIPT")

echo "→ Dispatching SSM send-command"
CMD_ID=$(aws --region "$REGION" ssm send-command \
  --instance-ids $INSTANCES \
  --document-name AWS-RunShellScript \
  --comment "Restore /opt/aegis/infra/.env from SSM" \
  --parameters "$PARAMS" \
  --query 'Command.CommandId' --output text)
echo "  command-id: $CMD_ID"

echo "→ Waiting for completion"
while true; do
  PENDING=$(aws --region "$REGION" ssm list-command-invocations \
    --command-id "$CMD_ID" \
    --query 'CommandInvocations[?Status==`Pending` || Status==`InProgress`] | length(@)' \
    --output text)
  [[ "$PENDING" == "0" ]] && break
  sleep 5
done

aws --region "$REGION" ssm list-command-invocations \
  --command-id "$CMD_ID" \
  --query 'CommandInvocations[].{Instance:InstanceId,Status:Status}' --output table

for IID in $INSTANCES; do
  echo "=== $IID ==="
  aws --region "$REGION" ssm get-command-invocation \
    --command-id "$CMD_ID" --instance-id "$IID" \
    --query 'StandardOutputContent' --output text
done
