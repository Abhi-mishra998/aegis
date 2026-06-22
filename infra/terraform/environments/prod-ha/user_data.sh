#!/usr/bin/env bash
# Aegis prod-ha — EC2 launch user-data.
#
# Runs ONCE on first boot. Steps:
#   1. Install Docker + SSM agent.
#   2. Pull /opt/aegis bundle from s3://acp-backups-prodha-…/releases/current.tar.gz.
#   3. Look up RDS / Redis endpoints by tag (no static IPs in this file).
#   4. Render /opt/aegis/infra/.env from Secrets Manager.
#   5. Render /opt/aegis/infra/pgbouncer.aws.ini with the resolved RDS host.
#   6. Render /opt/aegis/infra/userlist.txt with the master password + per-service users.
#   7. docker compose -f docker-compose.yml -f docker-compose.aws.yml up -d.

set -euo pipefail

LOG=/var/log/aegis-user-data.log
exec > >(tee -a "${LOG}") 2>&1

# IMDSv2 is required on this launch template — fetch a session token
# before any /latest/meta-data lookup. Sprint 9 deploy-bug-fix #12.
IMDS_TOKEN="$(curl -sfX PUT \
    -H 'X-aws-ec2-metadata-token-ttl-seconds: 3600' \
    http://169.254.169.254/latest/api/token)"
IMDS() { curl -sf -H "X-aws-ec2-metadata-token: ${IMDS_TOKEN}" \
    "http://169.254.169.254/latest/meta-data/$1"; }
REGION="$(IMDS placement/region)"
INSTANCE_ID="$(IMDS instance-id)"
NAME_PREFIX="acp-prodha"

echo "[user-data] $(date -u +%FT%TZ) region=${REGION} instance=${INSTANCE_ID}"

# ── 1. Docker + SSM agent ─────────────────────────────────────────────
# Amazon Linux 2023 already ships systemd + docker repos.
dnf install -y docker 2>&1 | tail -3 || yum install -y docker 2>&1 | tail -3
systemctl enable --now docker
# SSM agent already preinstalled on AL2023; ensure running.
systemctl enable --now amazon-ssm-agent || true
# docker compose v2 plugin
mkdir -p /usr/local/lib/docker/cli-plugins
if [[ ! -x /usr/local/lib/docker/cli-plugins/docker-compose ]]; then
  curl -sSL -o /usr/local/lib/docker/cli-plugins/docker-compose \
      "https://github.com/docker/compose/releases/download/v2.27.0/docker-compose-linux-aarch64"
  chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
fi

# ── 2. Fetch the latest bundle ─────────────────────────────────────────
# Bundle is built by scripts/ops/build_release_bundle.sh — it ensures the
# tarball contains the root Dockerfile, infra/docker-compose*.yml, and
# pre-built ui/dist/ (the three pieces a partial sprint-delta misses
# and that, when absent, hard-cycle the ASG).
mkdir -p /opt/aegis
aws s3 cp \
    "s3://acp-backups-prodha-628478946931/releases/current.tar.gz" \
    /tmp/aegis-bundle.tar.gz --region "${REGION}"
tar -xzf /tmp/aegis-bundle.tar.gz -C /opt/aegis
# Sprint 9 bug-fix #8 — strip AppleDouble files (NUL bytes crash alembic).
find /opt/aegis -name '._*' -delete
# Sprint 9 bug-fix #7 — never inherit the bundle's .env on a prod host.
rm -f /opt/aegis/infra/.env

# ── 3. Resolve RDS + Redis endpoints from AWS APIs ─────────────────────
RDS_ENDPOINT="$(aws rds describe-db-instances --region "${REGION}" \
    --db-instance-identifier "${NAME_PREFIX}-postgres" \
    --query 'DBInstances[0].Endpoint.Address' --output text)"
REDIS_PRIMARY="$(aws elasticache describe-replication-groups --region "${REGION}" \
    --replication-group-id "${NAME_PREFIX}-redis" \
    --query 'ReplicationGroups[0].NodeGroups[0].PrimaryEndpoint.Address' --output text)"
echo "[user-data] RDS=${RDS_ENDPOINT}"
echo "[user-data] REDIS=${REDIS_PRIMARY}"

fetch_secret() {
    aws secretsmanager get-secret-value --region "${REGION}" \
        --secret-id "${NAME_PREFIX}/$1" --query SecretString --output text
}

RDS_PASSWORD="$(fetch_secret rds_master_password)"
REDIS_AUTH="$(fetch_secret redis_auth_token)"
JWT_KEY="$(fetch_secret jwt_secret_key)"
INTERNAL_SECRET="$(fetch_secret internal_secret)"
MESH_SECRET="$(fetch_secret mesh_jwt_secret)"
GROQ_KEY="$(fetch_secret groq_api_key || echo EMPTY)"
STRIPE_SECRET="$(fetch_secret stripe_webhook_secret || echo EMPTY)"

# R1 — Sprint refactor: surface a bad Groq key at deploy time, not when a
# prospect runs the live demo on a fresh ASG instance. The previous
# behavior was to silently write `GROQ_API_KEY=EMPTY` to .env, and the
# gateway would only fail when the demo route called Groq mid-call — by
# which point we'd already lost the prospect. Stripe is non-critical for
# the demo path; warn but don't block.
if [[ -z "${GROQ_KEY}" || "${GROQ_KEY}" == "EMPTY" || ! "${GROQ_KEY}" =~ ^gsk_ ]]; then
    echo "[user-data] FATAL: groq_api_key secret is missing or invalid (value=${GROQ_KEY:0:6}***)"
    echo "[user-data] aws secretsmanager put-secret-value --region ${REGION} --secret-id ${NAME_PREFIX}/groq_api_key --secret-string '<real-key>'"
    echo "[user-data] aborting deploy — fresh instances must have a working Groq key before the gateway starts"
    exit 1
fi
if [[ -z "${STRIPE_SECRET}" || "${STRIPE_SECRET}" == "EMPTY" ]]; then
    echo "[user-data] WARN: stripe webhook secret missing (deploy proceeds — non-critical for the demo path)"
fi

# ── 4. Render /opt/aegis/infra/.env ────────────────────────────────────
cat > /opt/aegis/infra/.env <<EOF
AEGIS_ENV=prod
AWS_REGION=${REGION}
NAME_PREFIX=${NAME_PREFIX}

# Cryptographically routed via SSM — disk-fallback is REFUSED by the prod
# guard in sdk/common/signing_keys.py.
RECEIPT_SIGNING_PROVIDER=ssm
RECEIPT_SIGNING_SSM_PARAMETER=/${NAME_PREFIX}/receipt-signing-key

# Secrets used by the application services.
JWT_SECRET_KEY=${JWT_KEY}
INTERNAL_SECRET=${INTERNAL_SECRET}
MESH_JWT_SECRET=${MESH_SECRET}
GROQ_API_KEY=${GROQ_KEY}
STRIPE_WEBHOOK_SECRET=${STRIPE_SECRET}

# DB + Redis URLs the services consume directly. The local pgbouncer
# container fronts the per-service DB connections; services connect to
# pgbouncer:6432, NOT directly to RDS.
DATABASE_URL=postgresql+asyncpg://postgres:${RDS_PASSWORD}@pgbouncer:6432/acp
REDIS_URL=rediss://default:${REDIS_AUTH}@${REDIS_PRIMARY}:6379/0

# Per-service DB passwords (used in docker-compose.yml DATABASE_URL).
# In this 20-user testing infra every service uses the master user/password
# against its own database. For real production load, mint per-service
# users via aegis_prodha_db_bootstrap.sql.
REGISTRY_DB_PASSWORD=${RDS_PASSWORD}
IDENTITY_DB_PASSWORD=${RDS_PASSWORD}
AUDIT_DB_PASSWORD=${RDS_PASSWORD}
API_DB_PASSWORD=${RDS_PASSWORD}
USAGE_DB_PASSWORD=${RDS_PASSWORD}
IDENTITY_GRAPH_DB_PASSWORD=${RDS_PASSWORD}
FLIGHT_RECORDER_DB_PASSWORD=${RDS_PASSWORD}
AUTONOMY_DB_PASSWORD=${RDS_PASSWORD}
BEHAVIOR_DB_PASSWORD=${RDS_PASSWORD}

# Grafana — Sprint 9 bug-fix #10: required for docker-compose validation.
GRAFANA_ADMIN_PASSWORD=${JWT_KEY}
EOF
chmod 600 /opt/aegis/infra/.env

# ── 4b. Overlay operator-owned vars from SSM SecureString ──────────────
# Sprint-10 follow-on. The bundle's exclude list drops the repo-root .env
# (so dev secrets never ship to prod), which means CLERK_*, STRIPE_*, and
# ACP_AUTH_PROVIDER live ONLY in SSM. Without this overlay, every fresh
# instance launches with /webhooks/clerk → 503 (no CLERK_WEBHOOK_SECRET),
# /billing/checkout-session → 500 (no STRIPE_SECRET_KEY), and Clerk RS256
# JWTs are rejected by the LocalTokenValidator (no JWKS URL).
# Same parameter map as scripts/ops/restore_prod_env_from_ssm.sh.
#
# SSM prefix is hardcoded `/aegis-prodha` — that's where SecretsManager-
# style operator params live. (The NAME_PREFIX `acp-prodha` is for AWS
# resource naming and is a different namespace from SSM SecureString.)
SSM_PREFIX="/aegis-prodha"
ssm_pull() {
    aws --region "${REGION}" ssm get-parameter --name "$1" \
        --with-decryption --query 'Parameter.Value' --output text 2>/dev/null || true
}
declare -A SSM_OVERLAY=(
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
    # Sprint 17 — Aegis for Teams: corporate Anthropic API key the
    # /v1/messages proxy forwards to. Set in SSM as SecureString. Empty
    # disables the proxy (router returns 503 with config message).
    ["${SSM_PREFIX}/anthropic/upstream-key"]="UPSTREAM_ANTHROPIC_KEY"
    # N11 deploy wiring (2026-06-21) — dedicated Prometheus /metrics scrape
    # secret. After A11's middleware change, gateway /metrics rejects the
    # raw INTERNAL_SECRET and only accepts X-Mesh-Token OR X-Prometheus-Secret
    # (= PROMETHEUS_SCRAPE_SECRET). Without this overlay every fresh ASG
    # instance launches with prometheus → gateway scrapes returning 401.
    # SSM param is SecureString; rotate independently of every other secret.
    ["${SSM_PREFIX}/prometheus/scrape-secret"]="PROMETHEUS_SCRAPE_SECRET"
    # P2-3 (2026-06-22) — mesh ES256 keys. Each service mints its own
    # outbound X-Mesh-Token with MESH_<SVC>_PRIVATE_KEY and verifies
    # inbound mesh tokens against ACP_MESH_TRUSTED_KEYS (the public-key
    # bundle of every trusted service). Previously these were injected
    # post-boot by scripts/ops/safe_deploy.sh, which meant a freshly
    # scaled-out ASG instance had no mesh keys and every cross-service
    # call failed with 403 until an operator ran the script. Pulling
    # them at user_data time closes that window.
    ["${SSM_PREFIX}/mesh/trusted-keys"]="ACP_MESH_TRUSTED_KEYS"
    ["${SSM_PREFIX}/mesh/api/private"]="MESH_API_PRIVATE_KEY"
    ["${SSM_PREFIX}/mesh/audit/private"]="MESH_AUDIT_PRIVATE_KEY"
    ["${SSM_PREFIX}/mesh/autonomy/private"]="MESH_AUTONOMY_PRIVATE_KEY"
    ["${SSM_PREFIX}/mesh/behavior/private"]="MESH_BEHAVIOR_PRIVATE_KEY"
    ["${SSM_PREFIX}/mesh/decision/private"]="MESH_DECISION_PRIVATE_KEY"
    ["${SSM_PREFIX}/mesh/flight_recorder/private"]="MESH_FLIGHT_RECORDER_PRIVATE_KEY"
    ["${SSM_PREFIX}/mesh/forensics/private"]="MESH_FORENSICS_PRIVATE_KEY"
    ["${SSM_PREFIX}/mesh/gateway/private"]="MESH_GATEWAY_PRIVATE_KEY"
    ["${SSM_PREFIX}/mesh/identity/private"]="MESH_IDENTITY_PRIVATE_KEY"
    ["${SSM_PREFIX}/mesh/identity_graph/private"]="MESH_IDENTITY_GRAPH_PRIVATE_KEY"
    ["${SSM_PREFIX}/mesh/insight/private"]="MESH_INSIGHT_PRIVATE_KEY"
    ["${SSM_PREFIX}/mesh/policy/private"]="MESH_POLICY_PRIVATE_KEY"
    ["${SSM_PREFIX}/mesh/registry/private"]="MESH_REGISTRY_PRIVATE_KEY"
    ["${SSM_PREFIX}/mesh/usage/private"]="MESH_USAGE_PRIVATE_KEY"
)
{
    echo ""
    echo "# Overlaid from SSM at $(date -u +%Y-%m-%dT%H:%M:%SZ) — see user_data.sh §4b"
    for path in "${!SSM_OVERLAY[@]}"; do
        env_name="${SSM_OVERLAY[$path]}"
        val="$(ssm_pull "$path")"
        if [[ -n "$val" && "$val" != "None" ]]; then
            echo "${env_name}=${val}"
        else
            echo "[user-data] WARN: ${path} missing — ${env_name} not set" >&2
        fi
    done
} >> /opt/aegis/infra/.env
chmod 600 /opt/aegis/infra/.env

# ── 5. Render pgbouncer.aws.ini with the resolved RDS host ─────────────
cat > /opt/aegis/infra/pgbouncer.aws.ini <<EOF
[databases]
acp                 = host=${RDS_ENDPOINT} port=5432 dbname=acp
acp_registry        = host=${RDS_ENDPOINT} port=5432 dbname=acp_registry
acp_identity        = host=${RDS_ENDPOINT} port=5432 dbname=acp_identity
acp_audit           = host=${RDS_ENDPOINT} port=5432 dbname=acp_audit
acp_api             = host=${RDS_ENDPOINT} port=5432 dbname=acp_api
acp_usage           = host=${RDS_ENDPOINT} port=5432 dbname=acp_usage
acp_identity_graph  = host=${RDS_ENDPOINT} port=5432 dbname=acp_identity_graph
acp_flight_recorder = host=${RDS_ENDPOINT} port=5432 dbname=acp_flight_recorder
acp_autonomy        = host=${RDS_ENDPOINT} port=5432 dbname=acp_autonomy
acp_behavior        = host=${RDS_ENDPOINT} port=5432 dbname=acp_behavior

[pgbouncer]
listen_port = 6432
listen_addr = 0.0.0.0
auth_type = plain
auth_file = /etc/pgbouncer/userlist.txt
pool_mode = transaction
max_client_conn = 500
default_pool_size = 25
reserve_pool_size = 5
reserve_pool_timeout = 3
server_lifetime = 3600
server_idle_timeout = 600
query_wait_timeout = 30
ignore_startup_parameters = extra_float_digits,statement_timeout,idle_in_transaction_session_timeout
admin_users = postgres
stats_users = postgres
EOF
# pgbouncer container runs as non-root (uid != owner here), needs r perm.
chmod 644 /opt/aegis/infra/pgbouncer.aws.ini

# ── 6. Render userlist.txt — master + per-service users ────────────────
# For 20-user testing infra every per-service user shares the master
# password. Bootstrap SQL later splits these into distinct creds.
cat > /opt/aegis/infra/userlist.txt <<EOF
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
EOF
chmod 644 /opt/aegis/infra/userlist.txt

# ── 6b. Docker Hub login (prevents anonymous rate-limit on burst pulls) ─
# Recurring outage root cause: Docker Hub's 100/6h anonymous limit on the
# NAT egress IP gets burned through during a sequence of ASG refreshes,
# leaving fresh instances unable to pull postgres/redis/grafana/etc — they
# fail with `toomanyrequests` mid-compose-up and never become ALB-healthy.
# Authenticated free-tier limit is 200/6h per user — enough headroom for
# the rare burst we trigger during a release.
HUB_USER="$(ssm_pull "${SSM_PREFIX}/docker/hub-user")"
HUB_PAT="$(ssm_pull "${SSM_PREFIX}/docker/hub-pat")"
if [[ -n "${HUB_USER}" && -n "${HUB_PAT}" && "${HUB_USER}" != "None" ]]; then
    echo "${HUB_PAT}" | docker login --username "${HUB_USER}" --password-stdin
    echo "[user-data] docker hub login ${HUB_USER} OK"
else
    echo "[user-data] WARN: docker hub creds missing in SSM — pulls will be anonymous (rate-limited)"
fi

# ── 7. Boot the stack ──────────────────────────────────────────────────
cd /opt/aegis/infra
docker compose -f docker-compose.yml -f docker-compose.aws.yml \
    --env-file .env up -d --remove-orphans

echo "[user-data] $(date -u +%FT%TZ) aegis prod-ha stack started on ${INSTANCE_ID}"
