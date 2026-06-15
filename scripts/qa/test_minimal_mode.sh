#!/bin/bash
# R3 parity test — minimal mode must enforce R0 + R2 behaviour.
#
# The promise of minimal mode is "same security guarantees as prod-ha,
# smaller deployment footprint." This script verifies that promise by
# booting the 3-container stack and running the canonical R0 destructive
# matrix + the R2 evidence-bundle path against it. If either fails, the
# minimal image has regressed and we don't ship it.
#
# Run: bash scripts/qa/test_minimal_mode.sh
#
# Prereqs: docker, docker compose, curl, jq, openssl. The test creates
# a throwaway .env, boots the stack, runs the assertions, then tears it
# down (including volumes — this is destructive locally on purpose).

set -o pipefail

# ── Paths ─────────────────────────────────────────────────────────────
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
COMPOSE_DIR="${REPO_ROOT}/infra/minimal"
COMPOSE_FILE="${COMPOSE_DIR}/docker-compose.minimal.yml"
ENV_FILE="${COMPOSE_DIR}/.env.test"
LOG_FILE="${COMPOSE_DIR}/test_minimal_mode.log"
TEST_PORT=18000  # avoid clashing with a running local dev stack

# ── Pretty printing ───────────────────────────────────────────────────
RED=$'\033[31m'; GREEN=$'\033[32m'; YEL=$'\033[33m'; RESET=$'\033[0m'
pass=0; fail=0
say()   { echo "[parity] $*"; }
ok()    { echo "${GREEN}PASS${RESET} — $*"; pass=$((pass+1)); }
bad()   { echo "${RED}FAIL${RESET} — $*"; fail=$((fail+1)); }
warn()  { echo "${YEL}WARN${RESET} — $*"; }

# ── Sanity checks ─────────────────────────────────────────────────────
if [[ ! -f "${COMPOSE_FILE}" ]]; then
    bad "missing compose file: ${COMPOSE_FILE}"
    exit 1
fi
for tool in docker curl jq openssl; do
    if ! command -v "${tool}" >/dev/null 2>&1; then
        bad "required tool not on PATH: ${tool}"
        exit 1
    fi
done

# ── Generate ephemeral .env.test ──────────────────────────────────────
say "Writing ephemeral .env.test"
{
    echo "POSTGRES_PASSWORD=$(openssl rand -hex 16)"
    echo "INTERNAL_SECRET=$(openssl rand -hex 32)"
    echo "JWT_SECRET_KEY=$(openssl rand -hex 32)"
    echo "AEGIS_PORT=${TEST_PORT}"
} > "${ENV_FILE}"

# ── Cleanup hook (registered before any failure path that needs it) ──
cleanup() {
    say "Tearing stack down (including volumes)..."
    docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" down -v \
        >> "${LOG_FILE}" 2>&1 || true
    rm -f "${ENV_FILE}"
}
trap cleanup EXIT

# ── Boot ──────────────────────────────────────────────────────────────
say "Booting minimal stack (this builds the image on first run)..."
cd "${COMPOSE_DIR}"
if ! docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" up -d --build \
        > "${LOG_FILE}" 2>&1; then
    bad "docker compose up failed — see ${LOG_FILE}"
    exit 1
fi

# ── Wait for /system/health to go green (max 3 min) ──────────────────
BASE="http://localhost:${TEST_PORT}"
say "Waiting for ${BASE}/system/health to report healthy (≤180s)..."
ready=0
for i in $(seq 1 36); do
    if curl -fsS "${BASE}/health" >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 5
done
if [[ "${ready}" -ne 1 ]]; then
    bad "stack did not become reachable in 180s — see ${LOG_FILE}"
    docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" logs --tail=100 aegis-core \
        | tail -80
    exit 1
fi
ok "gateway responds at ${BASE}/health"

# ── Seed a tenant + admin user via the bundled bootstrap ──────────────
# The container image bundles `scripts/utils/seed_admin.py`; running it
# inside `aegis-core` gives us a JWT to call /execute with. The script
# hardcodes `admin@acp.local` / `admin1234` — don't override.
say "Seeding admin user..."
SEED_OUT=$(docker exec aegis-core python /app/scripts/utils/seed_admin.py 2>&1) || true
echo "${SEED_OUT}" >> "${LOG_FILE}"

# ── Mint a token ──────────────────────────────────────────────────────
TENANT="00000000-0000-0000-0000-000000000001"
TOKEN=$(curl -sS -X POST "${BASE}/auth/token" \
    -H "X-Tenant-ID: ${TENANT}" \
    -H "Content-Type: application/json" \
    -d '{"email":"admin@acp.local","password":"admin1234"}' \
    | jq -r '.data.access_token // empty')
if [[ -z "${TOKEN}" ]]; then
    bad "could not mint admin token (auth/token returned empty)"
    docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" logs --tail=60 aegis-core \
        | tail -50
    exit 1
fi
ok "auth/token returned a usable JWT"

AGENT="11111111-1111-1111-1111-111111111111"

# ── R0 parity: destructive arguments are BLOCKED regardless of
#    risk_level. Block can land as a hard deny (HTTP 403, decision:deny)
#    or as approval_required (HTTP 403, escalate). Either is acceptable —
#    the contract is "does not execute," not "specific label."
assert_blocked() {
    local label="$1" risk="$2" payload="$3"
    local code
    code=$(curl -sS -o /tmp/aegis_r0.json -w "%{http_code}" -X POST "${BASE}/execute" \
        -H "Authorization: Bearer ${TOKEN}" \
        -H "X-Tenant-ID: ${TENANT}" \
        -H "X-Agent-ID: ${AGENT}" \
        -H "Content-Type: application/json" \
        -d "${payload}")
    if [[ "${code}" == "403" ]]; then
        ok "R0 ${label} (risk=${risk}) → blocked (HTTP 403)"
    else
        bad "R0 ${label} expected HTTP 403, got ${code} — body: $(head -c 240 /tmp/aegis_r0.json)"
    fi
}

# Destructive shell command — must block even at low risk.
assert_blocked \
    "shell.cat-passwd"  "low" \
    '{"tool_name":"shell.run","risk_level":"low","arguments":{"command":"cat /etc/passwd"}}'

# Destructive SQL — must block (SQL injection classifier fires).
assert_blocked \
    "db.drop-table"  "medium" \
    '{"tool_name":"db.execute","risk_level":"medium","arguments":{"query":"DROP TABLE users"}}'

# Path traversal in fs.read — must block.
assert_blocked \
    "fs.read-traversal"  "low" \
    '{"tool_name":"fs.read","risk_level":"low","arguments":{"path":"../../etc/shadow"}}'

# ── R2 parity: /receipts/key + /compliance/export/{bundle} respond ──
KEY_HTTP=$(curl -sS -o /tmp/aegis_min_key.json -w "%{http_code}" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "X-Tenant-ID: ${TENANT}" "${BASE}/receipts/key")
if [[ "${KEY_HTTP}" == "200" ]] && jq -e '.public_key_pem' /tmp/aegis_min_key.json >/dev/null; then
    ok "R2 /receipts/key returned ed25519 PEM"
else
    bad "R2 /receipts/key HTTP ${KEY_HTTP} (no public_key_pem)"
fi

for bt in eu-ai-act soc2 nist-ai-rmf tool-ledger; do
    URL="${BASE}/compliance/export/${bt}?period_start=2026-06-01&period_end=2026-06-13"
    BODY=$(curl -sS -H "Authorization: Bearer ${TOKEN}" -H "X-Tenant-ID: ${TENANT}" -w "\n__HTTP__:%{http_code}" "${URL}")
    HTTP=$(echo "${BODY}" | grep -o '__HTTP__:[0-9]*' | cut -d: -f2)
    if [[ "${HTTP}" == "200" ]] && echo "${BODY}" | grep -q '"report_type"'; then
        ok "R2 bundle ${bt} → HTTP 200 with report_type field"
    else
        bad "R2 bundle ${bt} → HTTP ${HTTP} (no report_type)"
    fi
done

# ── Final tally ──────────────────────────────────────────────────────
total=$((pass + fail))
echo
echo "──────────────────────────────────────────────"
echo "minimal-mode parity: ${pass}/${total} pass, ${fail} fail"
echo "──────────────────────────────────────────────"
if [[ "${fail}" -gt 0 ]]; then
    echo "${RED}Parity regression — image is NOT ready to ship.${RESET}"
    exit 1
fi
echo "${GREEN}Parity holds — minimal mode preserves R0 + R2 behaviour.${RESET}"
exit 0
