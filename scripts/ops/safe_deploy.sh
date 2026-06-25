#!/bin/bash
# safe_deploy.sh v2 — runs ON each ASG host to extract a bundle, recreate
# containers, and write success markers to SSM Parameter Store.
#
# MIRROR — this script is identical to s3://aegis-prod-backups-628478946931/releases/safe_deploy.sh
# which is what the prod ASG instances actually pull at boot AND what
# scripts/ops/rolling_deploy.sh fetches before running on each host.
# When editing this file you MUST also upload it to S3:
#   aws s3 cp scripts/ops/safe_deploy.sh \
#       s3://aegis-prod-backups-628478946931/releases/safe_deploy.sh \
#       --region ap-south-1
# Drift between the two will silently break fresh-instance bootstrap.
# Filed in matrix-25 Section M.5 as the root cause of the dual-SSM-param
# mismatch that bit the 2026-06-25 prod deploy.
#
# Adds vs v1:
#   • --force-clean idempotency block (P1-DEPLOY-003 closure): reset
#     /run/aegis/pgbouncer/* tmpfs + remove half-created containers
#     before recreate, so a previous failed run doesn't poison this one.
#   • SSM parameter write on success — see "all_healthy" block at the
#     bottom. Writes BOTH names so the ASG launch-template user_data
#     (which reads /aegis/prod/current_bundle_sha) and any external
#     tooling that greps the legacy /aegis-prodha/current-sha stay in
#     sync. Fixing this drift was the 2026-06-25 sprint closure.
#   • Stricter health-check pass before exit: gateway must return 200
#     AND every container must be (healthy) — otherwise exit non-zero
#     so the deploy wrapper sees a failure on this host and the rolling
#     batch stops.
set +e
SHA="$1"
FORCE_CLEAN="${2:-auto}"
REGION="ap-south-1"
PARAM_PREFIX="aegis-prodha"
ENV=/opt/aegis/infra/.env

echo "==== _backup current .env ===="
cp -p "$ENV" "${ENV}.bak.$(date +%s)" 2>/dev/null || true
ls -la /opt/aegis/infra/.env* 2>/dev/null | tail -5

echo "==== _fetch+extract bundle ${SHA} ===="
aws s3 cp s3://aegis-prod-backups-628478946931/releases/bundle-${SHA}.tar.gz /tmp/bundle.tar.gz --region "$REGION" >/dev/null
mkdir -p /opt/aegis
LATEST_BAK=$(ls -t ${ENV}.bak.* 2>/dev/null | head -1)
tar -xzf /tmp/bundle.tar.gz -C /opt/aegis 2>&1 | tail -2
find /opt/aegis -name "._*" -delete 2>/dev/null
if [ -n "$LATEST_BAK" ] && [ -f "$LATEST_BAK" ]; then
  cp -p "$LATEST_BAK" "$ENV"
  echo "_restored .env from $LATEST_BAK"
fi

# P1-DEPLOY-003 — idempotent reset block. Runs unconditionally when
# `--force-clean` is passed; runs implicitly when /run/aegis/pgbouncer/
# userlist.txt is missing OR is a directory (Docker's bind-mount behavior
# creates a directory if the host path doesn't exist; that wrong shape is
# the audit-final-22 incident root cause).
NEED_CLEAN=0
if [ "$FORCE_CLEAN" = "--force-clean" ]; then NEED_CLEAN=1; fi
if [ ! -f /run/aegis/pgbouncer/userlist.txt ]; then NEED_CLEAN=1; fi
if [ -d /run/aegis/pgbouncer/userlist.txt ]; then NEED_CLEAN=1; fi
# Empty file (size 0 byte) is also wrong — pgbouncer auth needs the per-db
# password lines. Re-render from Secrets Manager.
if [ -f /run/aegis/pgbouncer/userlist.txt ] && [ ! -s /run/aegis/pgbouncer/userlist.txt ]; then NEED_CLEAN=1; fi

if [ $NEED_CLEAN -eq 1 ]; then
  echo "==== _force-clean reset (idempotency) ===="
  cd /opt/aegis 2>/dev/null
  docker compose -f infra/docker-compose.yml -f infra/docker-compose.aws.yml down --remove-orphans 2>&1 | tail -3 || true
  # Remove any partial-failure container leftovers that block name reuse
  docker ps -a --filter "name=acp_" --format '{{.Names}}' 2>/dev/null | xargs -r docker rm -f 2>&1 | tail -3 || true

  # Reset the tmpfs mountpoint completely. If userlist.txt is a directory
  # left over from Docker's auto-create, rm -rf nukes it. Then we re-render
  # the per-db userlist from AWS Secrets Manager (same shape as user_data).
  rm -rf /run/aegis/pgbouncer/userlist.txt 2>/dev/null
  mkdir -p /run/aegis/pgbouncer
  chmod 755 /run/aegis/pgbouncer

  echo "_re-rendering userlist.txt from Secrets Manager (aegis-prod-db-master-password)"
  RDS_PASSWORD="$(aws secretsmanager get-secret-value --region "$REGION" --secret-id "aegis-prod-db-master-password" --query SecretString --output text 2>/dev/null)"
  # SecretString may be a JSON object {"password":"..."} or a bare string;
  # try the bare string first, fall back to a JSON .password / .rds_master_password parse.
  if [ -n "$RDS_PASSWORD" ]; then
    if echo "$RDS_PASSWORD" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('password') or d.get('rds_master_password') or '', end='')" 2>/dev/null > /tmp/_pw; then
      _PW_CAND="$(cat /tmp/_pw)"
      [ -n "$_PW_CAND" ] && RDS_PASSWORD="$_PW_CAND"
    fi
  fi
  if [ -z "$RDS_PASSWORD" ]; then
    # Fall back to /opt/aegis/infra/.env which user_data should have already
    # populated. POSTGRES_PASSWORD is one of the canonical keys.
    if [ -f "$ENV" ]; then
      RDS_PASSWORD="$(grep -E '^POSTGRES_PASSWORD=' "$ENV" 2>/dev/null | head -1 | sed 's/^POSTGRES_PASSWORD=//')"
    fi
  fi
  if [ -z "$RDS_PASSWORD" ]; then
    echo "_FATAL: could not fetch rds_master_password from Secrets Manager (aegis-prod-db-master-password) or from $ENV"
    exit 2
  fi
  cat > /run/aegis/pgbouncer/userlist.txt <<USERLIST
"postgres"            "${RDS_PASSWORD}"
"registry_user"       "${RDS_PASSWORD}"
"identity_user"       "${RDS_PASSWORD}"
"audit_user"          "${RDS_PASSWORD}"
"api_user"            "${RDS_PASSWORD}"
"usage_user"          "${RDS_PASSWORD}"
"identity_graph_user" "${RDS_PASSWORD}"
"flight_recorder_user" "${RDS_PASSWORD}"
"autonomy_user"       "${RDS_PASSWORD}"
"behavior_user"       "${RDS_PASSWORD}"
USERLIST
  chown 70:70 /run/aegis/pgbouncer/userlist.txt
  chmod 640 /run/aegis/pgbouncer/userlist.txt
  echo "_userlist.txt rendered ($(wc -l < /run/aegis/pgbouncer/userlist.txt) lines, $(stat -c %s /run/aegis/pgbouncer/userlist.txt 2>/dev/null || stat -f %z /run/aegis/pgbouncer/userlist.txt) bytes)"
fi

echo "==== _ensure MESH_*_PRIVATE_KEY + ACP_MESH_TRUSTED_KEYS ===="
ssm() { aws ssm get-parameter --region "$REGION" --name "$1" --with-decryption --query Parameter.Value --output text 2>/dev/null || echo ""; }

ensure_kv() {
  local key="$1" val="$2"
  if [ -z "$val" ]; then return; fi
  if grep -q "^${key}=" "$ENV"; then
    local b64=$(printf '%s' "$val" | base64 -w0)
    python3 -c "
import os, base64
env_path = '$ENV'
key = '$key'
val = base64.b64decode('$b64').decode()
with open(env_path) as f:
    lines = f.readlines()
out = []
for line in lines:
    if line.startswith(key + '='):
        out.append(f'{key}={val}\n')
    else:
        out.append(line)
with open(env_path, 'w') as f:
    f.writelines(out)
"
  else
    printf '%s=%s\n' "$key" "$val" >> "$ENV"
  fi
}

ensure_kv "ACP_MESH_TRUSTED_KEYS" "$(ssm "/${PARAM_PREFIX}/mesh/trusted-keys")"
for svc in api audit autonomy behavior decision flight_recorder forensics gateway identity identity_graph insight policy registry usage; do
  KEY_UPPER=$(echo "$svc" | tr '[:lower:]' '[:upper:]')
  ensure_kv "MESH_${KEY_UPPER}_PRIVATE_KEY" "$(ssm "/${PARAM_PREFIX}/mesh/${svc}/private")"
done

chmod 600 "$ENV"
echo "_env lines: $(wc -l "$ENV" | awk '{print $1}')"
echo "_mesh keys present: $(grep -cE '^(ACP_MESH_TRUSTED_KEYS|MESH_[A-Z_]+_PRIVATE_KEY)=' "$ENV")"

echo "==== _check uioverride exists ===="
ls -la /opt/aegis/infra/docker-compose.uioverride.yml 2>&1 || echo "WARN: uioverride missing from bundle"

echo "==== _fix pgbouncer.aws.ini DB host (acp-postgres-prod -> aegis-prod-postgres) ===="
if [ -f /opt/aegis/infra/pgbouncer.aws.ini ]; then
  sed -i "s|acp-postgres-prod\.cz0qqg60keaj|aegis-prod-postgres.cz0qqg60keaj|g" /opt/aegis/infra/pgbouncer.aws.ini
  echo "_pgbouncer DB host after fix:"
  grep -E "host=" /opt/aegis/infra/pgbouncer.aws.ini | head -1
fi

echo "==== _recreate fleet ===="
cd /opt/aegis
COMPOSE_FILES="-f infra/docker-compose.yml -f infra/docker-compose.aws.yml"
if [ -f infra/docker-compose.uioverride.yml ] && [ -d /opt/aegis/ui/dist ]; then
  COMPOSE_FILES="$COMPOSE_FILES -f infra/docker-compose.uioverride.yml"
  echo "_using uioverride (host ui/dist available)"
fi

docker compose $COMPOSE_FILES up -d --remove-orphans --force-recreate pgbouncer opa 2>&1 | tail -3
sleep 15
docker compose $COMPOSE_FILES up -d --remove-orphans --force-recreate \
  audit api decision policy registry identity usage autonomy forensics behavior \
  insight identity_graph flight_recorder gateway prometheus ui bundle-server 2>&1 | tail -10
echo "==== _waiting 90s for healthchecks ===="
sleep 90
echo "==== _re-up any stranded services ===="
docker compose $COMPOSE_FILES up -d --no-recreate \
  audit api decision policy registry identity usage autonomy forensics behavior \
  insight identity_graph flight_recorder gateway prometheus ui bundle-server 2>&1 | tail -10
sleep 30

echo "==== _final state ===="
docker compose $COMPOSE_FILES ps --format '{{.Name}} {{.Status}}' 2>&1 | head -30
echo "---"
UNHEALTHY=$(docker compose $COMPOSE_FILES ps --format '{{.Name}} {{.Status}}' 2>&1 | grep -vE 'healthy|^NAME' || true)

echo "==== _gateway probe ===="
GW=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 5 http://localhost:8000/status 2>&1)
echo "gateway_internal_status: $GW"

if [ -z "$UNHEALTHY" ] && [ "$GW" = "200" ]; then
  echo "all_healthy"
  # P1-DEPLOY-002 hook — record current-deployed SHA in SSM so future fresh
  # ASG hosts can self-bootstrap to the same code by reading this value
  # in user_data and running safe_deploy.sh themselves.
  #
  # 2026-06-25 — write BOTH parameter names. Historical accident: the
  # launch-template user_data reads `/aegis/prod/current_bundle_sha`
  # while safe_deploy.sh has always written `/${PARAM_PREFIX}/current-sha`
  # (i.e. `/aegis-prodha/current-sha`). They were never aliased, so a
  # deploy left them out of sync — an ASG-launched fresh instance would
  # bootstrap with the stale bundle the user_data param still pointed at.
  # Caught in the prod deploy this afternoon when three ASG replacements
  # came up running Jun-23 code instead of today's bundle. We now write
  # both names; the legacy param stays around for any external tooling
  # that already grep'd it.
  aws ssm put-parameter --region "$REGION" --name "/${PARAM_PREFIX}/current-sha" --type "String" --value "$SHA" --overwrite 2>&1 | tail -3 || true
  aws ssm put-parameter --region "$REGION" --name "/aegis/prod/current_bundle_sha" --type "String" --value "$SHA" --overwrite 2>&1 | tail -3 || true
  exit 0
else
  echo "UNHEALTHY (will not update current-sha SSM):"
  echo "$UNHEALTHY"
  exit 1
fi
