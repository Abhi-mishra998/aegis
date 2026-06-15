#!/usr/bin/env bash
# scripts/ops/run_e2e.sh — Sprint 4
#
# One-command Playwright e2e launcher that fetches credentials from AWS
# SSM Parameter Store and refuses to run while the values are still
# Sprint-4 placeholders. Matches the activation pattern Sprint 1.3 (KMS
# CMK), Sprint 2b (SIEM endpoint smoke tests), and Sprint 3 (SSM signing
# keys) all use, so an operator only has one mental model for "real-env
# tests come from SSM."
#
# Required SSM parameters (provisioned by Sprint 4 — see SPRINT_4_REPORT.md):
#
#   /aegis-playwright/E2E_BASE_URL    plain string, e.g. https://dev.aegisagent.in
#   /aegis-playwright/E2E_USER        SecureString — login email
#   /aegis-playwright/E2E_PASSWORD    SecureString — login password
#
# Activation (one-time, from any host with AWS admin creds):
#
#   aws ssm put-parameter --region ap-south-1 \
#     --name /aegis-playwright/E2E_USER \
#     --type SecureString --overwrite \
#     --value admin@aegisagent.in
#
#   aws ssm put-parameter --region ap-south-1 \
#     --name /aegis-playwright/E2E_PASSWORD \
#     --type SecureString --overwrite \
#     --value "<the real password>"
#
# Then run:
#
#   bash scripts/ops/run_e2e.sh
#   bash scripts/ops/run_e2e.sh fleet.spec.ts         # one spec
#   bash scripts/ops/run_e2e.sh --ui                  # Playwright UI mode
#
# Exit status:
#   0   — all tests passed
#   1   — at least one test failed
#   2   — Playwright config / spec compile error
#   3   — SSM parameter still on the Sprint-4 PENDING placeholder
#   4   — AWS CLI / network / credential error

set -euo pipefail

# Locate the repo root from the script's own location so the launcher
# works whether the operator runs it from acp/, ui/, or anywhere else.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
UI_DIR="${REPO_ROOT}/ui"
REGION="${AWS_REGION:-ap-south-1}"

if [[ ! -d "${UI_DIR}/node_modules/@playwright" ]]; then
  echo "[run_e2e] node_modules missing or stale. Installing UI deps…"
  (cd "${UI_DIR}" && npm install --silent)
fi

fetch_param() {
  local name="$1"
  local value
  if ! value=$(aws ssm get-parameter \
      --region "${REGION}" \
      --name "${name}" \
      --with-decryption \
      --query 'Parameter.Value' --output text 2>/dev/null); then
    echo "[run_e2e] FAIL — could not read ${name} from SSM in ${REGION}." >&2
    echo "             Check AWS credentials and the IAM role's ssm:GetParameter" >&2
    echo "             permission on that ARN." >&2
    exit 4
  fi
  if [[ "${value}" == PENDING_* ]]; then
    echo "[run_e2e] FAIL — ${name} is still on the Sprint-4 placeholder:" >&2
    echo "             ${value}" >&2
    echo "" >&2
    echo "    Populate it with the real value via:" >&2
    echo "       aws ssm put-parameter --region ${REGION} \\" >&2
    echo "         --name ${name} \\" >&2
    echo "         --type SecureString --overwrite \\" >&2
    echo "         --value '<the real value>'" >&2
    exit 3
  fi
  printf '%s' "${value}"
}

echo "[run_e2e] Fetching credentials from SSM (${REGION})…"
PLAYWRIGHT_USER="$(fetch_param /aegis-playwright/E2E_USER)"
PLAYWRIGHT_PASSWORD="$(fetch_param /aegis-playwright/E2E_PASSWORD)"
PLAYWRIGHT_TENANT_ID="$(fetch_param /aegis-playwright/E2E_TENANT_ID)"
AEGIS_BASE_URL="$(fetch_param /aegis-playwright/E2E_BASE_URL)"

export PLAYWRIGHT_USER PLAYWRIGHT_PASSWORD PLAYWRIGHT_TENANT_ID AEGIS_BASE_URL

# Mask the password in any process snapshot — never print it back to stdout.
echo "[run_e2e] target:       ${AEGIS_BASE_URL}"
echo "[run_e2e] user:         ${PLAYWRIGHT_USER}"
echo "[run_e2e] password:     ********  (length=${#PLAYWRIGHT_PASSWORD})"
echo "[run_e2e] launching Playwright…"
echo ""

cd "${UI_DIR}"
exec npx playwright test --reporter=list "$@"
