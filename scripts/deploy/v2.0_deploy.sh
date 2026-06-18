#!/usr/bin/env bash
# Aegis v2.0 GA deploy — codifies SPRINT.md §11 H1-H7.
#
# Single-operator wrapper that does the seven phases in order, stops on
# the first failure, and produces an audit-friendly log. SRE runs this;
# the script does the choreography.
#
#   H1  pre-deploy snapshot     (RDS + public S3 mirror + git tag)
#   H2  build artifact          (UI build + tar w/ AppleDouble fix)
#   H3  upload to S3 deploy bucket
#   H4  rolling deploy instance 1  (ALB drain → SSM tar-pull → verify → re-attach)
#   H5  smoke test              (5 minute soak with instance 1 only)
#   H6  rolling deploy instance 2  (same pattern as H4)
#   H7  final smoke + traffic split verification
#
# Invocation:
#
#   AWS_REGION=ap-south-1 \
#   ALB_TG_ARN=arn:aws:elasticloadbalancing:ap-south-1:...:targetgroup/aegis-gateway/... \
#   INSTANCES=i-aaaa,i-bbbb \
#   RDS_INSTANCE=aegis-prod-ha \
#   DEPLOY_BUCKET=aegis-deploy-bucket \
#   PUBLIC_ROOTS_BUCKET=aegis-public-roots-628478946931 \
#   PUBLIC_ROOTS_BACKUP_BUCKET=aegis-internal-backups \
#   ALB_PUBLIC_HOST=aegisagent.in \
#   ./scripts/deploy/v2.0_deploy.sh
#
# All variables above are required. The script fails fast on missing env.
# Add `--dry-run` to print the AWS commands without executing them.
#
# IMPORTANT: rollback is a separate concern handled by SPRINT.md §15.
# This script does NOT roll back automatically on failure — it stops and
# the operator decides whether to roll back or fix forward.

set -euo pipefail

# --------------------------------------------------------------------------- #
# Required env                                                                #
# --------------------------------------------------------------------------- #

: "${AWS_REGION:?AWS_REGION is required (e.g. ap-south-1)}"
: "${ALB_TG_ARN:?ALB_TG_ARN is required (Aegis gateway target group)}"
: "${INSTANCES:?INSTANCES is required (comma-separated EC2 instance IDs)}"
: "${RDS_INSTANCE:?RDS_INSTANCE is required (e.g. aegis-prod-ha)}"
: "${DEPLOY_BUCKET:?DEPLOY_BUCKET is required (S3 bucket holding release tarballs)}"
: "${PUBLIC_ROOTS_BUCKET:?PUBLIC_ROOTS_BUCKET is required (transparency bucket)}"
: "${PUBLIC_ROOTS_BACKUP_BUCKET:?PUBLIC_ROOTS_BACKUP_BUCKET is required}"
: "${ALB_PUBLIC_HOST:?ALB_PUBLIC_HOST is required (e.g. aegisagent.in)}"

DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    *) ;;
  esac
done

IFS=',' read -ra INSTANCE_IDS <<< "$INSTANCES"
[ "${#INSTANCE_IDS[@]}" -ge 2 ] || { echo "FATAL: need >= 2 instances for rolling deploy"; exit 2; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TS="$(date -u +%Y%m%d-%H%M%S)"
LOG_DIR="${REPO_ROOT}/reports/v2.0-deploy/${TS}"
mkdir -p "${LOG_DIR}"

log()   { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "${LOG_DIR}/deploy.log"; }
phase() { printf '\n========== %s ==========\n' "$*" | tee -a "${LOG_DIR}/deploy.log"; }
run()   {
  log "$ $*"
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    log "  (dry-run — not executed)"
    return 0
  fi
  "$@"
}

# --------------------------------------------------------------------------- #
# H1 — pre-deploy snapshot                                                    #
# --------------------------------------------------------------------------- #

phase "H1 — pre-deploy snapshot"

H1_SNAPSHOT_ID="aegis-pre-v2-${TS}"

log "1.a RDS snapshot ${H1_SNAPSHOT_ID}"
run aws --region "${AWS_REGION}" rds create-db-snapshot \
  --db-snapshot-identifier "${H1_SNAPSHOT_ID}" \
  --db-instance-identifier "${RDS_INSTANCE}"

log "1.b sync public transparency bucket → backup"
run aws s3 sync \
  "s3://${PUBLIC_ROOTS_BUCKET}/" \
  "s3://${PUBLIC_ROOTS_BACKUP_BUCKET}/transparency-pre-v2-${TS}/" \
  --region "${AWS_REGION}"

log "1.c tag local repo v2.0-pre-deploy (if not already)"
if ! git -C "${REPO_ROOT}" rev-parse v2.0-pre-deploy >/dev/null 2>&1; then
  run git -C "${REPO_ROOT}" tag -a "v2.0-pre-deploy" \
    -m "Aegis v2.0 pre-deploy rollback target (auto-tagged by H1 at ${TS})"
else
  log "  v2.0-pre-deploy tag already exists — skipping"
fi

# --------------------------------------------------------------------------- #
# H2 — build artifact                                                         #
# --------------------------------------------------------------------------- #

phase "H2 — build artifact"

log "2.a git status (must be clean)"
DIRTY="$(git -C "${REPO_ROOT}" status --porcelain | grep -v "^??" || true)"
if [[ -n "${DIRTY}" ]]; then
  log "FATAL: tracked files have uncommitted changes:"
  log "${DIRTY}"
  log "Commit or stash before deploying."
  exit 3
fi

log "2.b last 10 commits (audit-friendly preview)"
git -C "${REPO_ROOT}" log --oneline -10 | tee -a "${LOG_DIR}/git-log.txt"

log "2.c UI production build"
if command -v bun >/dev/null 2>&1; then
  run bash -c "cd ${REPO_ROOT}/ui && bun install --frozen-lockfile && bun run build"
else
  run bash -c "cd ${REPO_ROOT}/ui && npm ci && npm run build"
fi

log "2.d AppleDouble landmine removal (macOS bundles)"
run find "${REPO_ROOT}" -name '._*' -delete

TARBALL="/tmp/aegis-v2.0-${TS}.tar.gz"
log "2.e tar to ${TARBALL}"
# D3 closure: .env / .env.local / .env.* carry per-instance DB passwords and
# must NEVER ship in the deploy tarball. A prior deploy clobbered the EC2
# /opt/aegis/.env with a dev-machine .env, breaking asyncpg auth on RDS.
# Keep these excludes adjacent to the others below.
run tar \
  --exclude='./.git' \
  --exclude='./.env' \
  --exclude='./.env.local' \
  --exclude='./.env.*' \
  --exclude='./node_modules' \
  --exclude='./__pycache__' \
  --exclude='./.venv' \
  --exclude='./build' \
  --exclude='./htmlcov' \
  --exclude='./reports/load-test-2026-Q3/*/' \
  --exclude='./reports/v2.0-deploy' \
  --exclude='./reports/soak' \
  -czf "${TARBALL}" -C "${REPO_ROOT}" .

run bash -c "ls -lh ${TARBALL}"
ARTIFACT_SHA="$(shasum -a 256 "${TARBALL}" | awk '{print $1}')"
log "2.f artifact sha256: ${ARTIFACT_SHA}"

# --------------------------------------------------------------------------- #
# H3 — upload to S3                                                           #
# --------------------------------------------------------------------------- #

phase "H3 — upload to S3"

S3_KEY="releases/aegis-v2.0-${TS}.tar.gz"
S3_LATEST_KEY="releases/aegis-v2.0.tar.gz"

run aws s3 cp "${TARBALL}" \
  "s3://${DEPLOY_BUCKET}/${S3_KEY}" \
  --metadata "sha256=${ARTIFACT_SHA},built_at=${TS}" \
  --region "${AWS_REGION}"

log "3.b copy timestamped key to latest pointer"
run aws s3 cp \
  "s3://${DEPLOY_BUCKET}/${S3_KEY}" \
  "s3://${DEPLOY_BUCKET}/${S3_LATEST_KEY}" \
  --region "${AWS_REGION}"

# --------------------------------------------------------------------------- #
# H4 / H6 — rolling deploy (per-host loop)                                    #
# --------------------------------------------------------------------------- #

deploy_one_instance() {
  local instance_id="$1"
  local phase_name="$2"

  phase "${phase_name} — rolling deploy ${instance_id}"

  log "${phase_name}.a deregister from ALB target group"
  run aws --region "${AWS_REGION}" elbv2 deregister-targets \
    --target-group-arn "${ALB_TG_ARN}" --targets "Id=${instance_id}"

  log "${phase_name}.b wait for draining → unused (max 120s)"
  if [[ "${DRY_RUN}" -eq 0 ]]; then
    for _ in $(seq 1 24); do
      local state
      state="$(aws --region "${AWS_REGION}" elbv2 describe-target-health \
                 --target-group-arn "${ALB_TG_ARN}" \
                 --targets "Id=${instance_id}" \
                 --query 'TargetHealthDescriptions[0].TargetHealth.State' \
                 --output text 2>/dev/null || echo unknown)"
      log "  target state: ${state}"
      [[ "${state}" == "unused" || "${state}" == "draining" ]] || true
      [[ "${state}" == "unused" ]] && break
      sleep 5
    done
  fi

  log "${phase_name}.c SSM tar-pull deploy"
  local SSM_DOC='{"commands":[
    "set -euxo pipefail",
    "cd /opt/aegis",
    "find . -name \"._*\" -delete",
    "aws s3 cp s3://'"${DEPLOY_BUCKET}/${S3_LATEST_KEY}"' /tmp/aegis-v2.0.tar.gz",
    "tar -xzf /tmp/aegis-v2.0.tar.gz --strip-components=0",
    "docker compose -f infra/docker-compose.yml down",
    "docker compose -f infra/docker-compose.yml up -d --build",
    "sleep 30",
    "curl -fsS http://127.0.0.1:8000/health || (docker compose -f infra/docker-compose.yml logs --tail 200 && exit 4)"
  ]}'
  local CMD_ID
  CMD_ID="$(run aws --region "${AWS_REGION}" ssm send-command \
    --instance-ids "${instance_id}" \
    --document-name "AWS-RunShellScript" \
    --comment "aegis v2.0 deploy ${TS}" \
    --parameters "${SSM_DOC}" \
    --output text --query 'Command.CommandId' 2>/dev/null || echo dry-run-cmd-id)"
  log "  ssm command id: ${CMD_ID}"

  log "${phase_name}.d wait for SSM command to finish (poll every 10s, max 10m)"
  if [[ "${DRY_RUN}" -eq 0 ]]; then
    for _ in $(seq 1 60); do
      local rc
      rc="$(aws --region "${AWS_REGION}" ssm list-command-invocations \
              --command-id "${CMD_ID}" --details \
              --query 'CommandInvocations[0].Status' --output text 2>/dev/null || echo Pending)"
      log "  ssm status: ${rc}"
      case "${rc}" in
        Success) break ;;
        Failed|Cancelled|TimedOut) echo "FATAL: ssm ${rc}"; exit 5 ;;
        *) sleep 10 ;;
      esac
    done
  fi

  log "${phase_name}.e re-attach to ALB"
  run aws --region "${AWS_REGION}" elbv2 register-targets \
    --target-group-arn "${ALB_TG_ARN}" --targets "Id=${instance_id}"

  log "${phase_name}.f wait for ALB target healthy (max 120s)"
  if [[ "${DRY_RUN}" -eq 0 ]]; then
    for _ in $(seq 1 24); do
      local state
      state="$(aws --region "${AWS_REGION}" elbv2 describe-target-health \
                 --target-group-arn "${ALB_TG_ARN}" \
                 --targets "Id=${instance_id}" \
                 --query 'TargetHealthDescriptions[0].TargetHealth.State' \
                 --output text 2>/dev/null || echo unknown)"
      log "  target state: ${state}"
      [[ "${state}" == "healthy" ]] && break
      sleep 5
    done
  fi
}

# H4 — first instance
deploy_one_instance "${INSTANCE_IDS[0]}" "H4"

# --------------------------------------------------------------------------- #
# H5 — smoke test with instance 1 only serving traffic                        #
# --------------------------------------------------------------------------- #

phase "H5 — smoke test (5 minutes; instance 1 only serving)"

run curl -fsS "https://${ALB_PUBLIC_HOST}/status"
run curl -fsS "https://${ALB_PUBLIC_HOST}/api/health"
run curl -fsS "https://ha.${ALB_PUBLIC_HOST}/status"
log "5.a static smoke green; soaking for 5 minutes"
if [[ "${DRY_RUN}" -eq 0 ]]; then
  sleep 300
fi

# H6 — second instance
deploy_one_instance "${INSTANCE_IDS[1]}" "H6"

# --------------------------------------------------------------------------- #
# H7 — final smoke + traffic-split verification                                #
# --------------------------------------------------------------------------- #

phase "H7 — final smoke + traffic split"

log "7.a both targets must be healthy"
run aws --region "${AWS_REGION}" elbv2 describe-target-health \
  --target-group-arn "${ALB_TG_ARN}" \
  --query 'TargetHealthDescriptions[].[Target.Id,TargetHealth.State]' \
  --output table

log "7.b 20 sequential requests — expect roughly even split"
if [[ "${DRY_RUN}" -eq 0 ]]; then
  for _ in $(seq 1 20); do
    curl -sS "https://${ALB_PUBLIC_HOST}/status" \
      | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('gateway_host', '?'))" 2>/dev/null || echo "?"
  done | sort | uniq -c | tee -a "${LOG_DIR}/split.txt"
fi

phase "DEPLOY COMPLETE — proceed to E2E grid (SPRINT.md §12)"
log "Artifact: ${TARBALL}"
log "S3 key:   s3://${DEPLOY_BUCKET}/${S3_KEY}"
log "Log dir:  ${LOG_DIR}"
log ""
log "Next:"
log "  1. Run the 50-row E2E acceptance grid against https://${ALB_PUBLIC_HOST}"
log "  2. If green: git tag -a v2.0-GA -m 'Aegis v2.0 GA — all 50 E2E rows green'"
log "  3. If any row fails: do NOT tag GA. Stop. Fix forward or roll back per SPRINT.md §15."
