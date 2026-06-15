#!/usr/bin/env bash
# Sprint 9 — CI guard: refuse any commit that lands a private key, PEM,
# or other obviously-sensitive file inside services/ or infra/.
#
# This is the failsafe behind the audit's S5/S7 finding: signing keys
# live on disk in production. The runtime guard at
# sdk/common/signing_keys.py refuses LocalFile in prod; this CI guard
# refuses to even commit the file.
#
# Allowlist: test fixtures, third-party-vendored assets, .well-known
# files (RFC 9116 + signing-keys.json), and the dev-only sample envs
# under scripts/utils/.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${REPO_ROOT}"

# Patterns we never want inside services/ or infra/ (deploy paths) or
# at the repo root.
BAD_NAME_PATTERNS=(
    -name "*.pem"
    -o -name "id_rsa"
    -o -name "id_rsa.pub"
    -o -name "id_ed25519"
    -o -name "id_ed25519.pub"
    -o -name "*.p12"
    -o -name "*.pfx"
    -o -name "*.key"
    -o -name "*.keystore"
    -o -name ".env"
    -o -name ".env.production"
    -o -name ".env.prod"
)

ALLOWLIST_RE='/(node_modules|\.venv|venv|tests/fixtures/|tests/test_signing_keys_prod_guard\.py|ui/public/\.well-known/|docs/security/|services/audit/transparency_keys/|scripts/utils/|/migrations/|cryptography/hazmat/)'

# Sprint 9 — Pre-existing baseline (KNOWN, tracked as follow-up).
#
# These files predate the CI guard. They are tracked in
# docs/security/soc2_tracker.md as open evidence items and will be
# removed during the prod-ha cut-over. The guard's job is to refuse
# NEW additions, not retroactively block the working tree.
BASELINE=(
    "voice-agent/infrastructure/aegis-voice-guide.pem"
    "infra/.env"
    ".env"
)

# Search services/, infra/, sdk/, voice-agent/ — the deploy + runtime
# paths. Tests + fixtures are explicitly exempt.
is_baseline() {
    local path="$1"
    local rel="${path#${REPO_ROOT}/}"
    for b in "${BASELINE[@]}"; do
        if [[ "${rel}" == "${b}" || "${path}" == "${b}" ]]; then
            return 0
        fi
    done
    return 1
}

HITS=()
while IFS= read -r path; do
    if [[ "${path}" =~ ${ALLOWLIST_RE} ]]; then
        continue
    fi
    if is_baseline "${path}"; then
        continue
    fi
    HITS+=("${path}")
done < <(find services sdk infra voice-agent \( "${BAD_NAME_PATTERNS[@]}" \) -type f 2>/dev/null)

# Also reject .env* at repo root (a common foot-gun).
for root_env in .env .env.production .env.prod; do
    full="${REPO_ROOT}/${root_env}"
    if [[ -f "${full}" ]] && ! is_baseline "${full}" && ! is_baseline "${root_env}"; then
        HITS+=("${full}")
    fi
done

if (( ${#HITS[@]} > 0 )); then
    echo "[no-secrets] FAIL — found ${#HITS[@]} candidate secret file(s):" >&2
    for path in "${HITS[@]}"; do
        echo "  - ${path}" >&2
    done
    echo "" >&2
    echo "If any of these is a test fixture, add the path to the" >&2
    echo "ALLOWLIST_RE in scripts/ci/no_secrets_on_disk.sh." >&2
    echo "" >&2
    echo "If it's a real key — REMOVE IT, rotate it at the issuer," >&2
    echo "and use SSM Parameter Store / KMS / Secrets Manager." >&2
    exit 1
fi

echo "[no-secrets] OK — no candidate secret files on the deploy paths."
