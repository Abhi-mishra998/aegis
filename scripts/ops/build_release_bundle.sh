#!/usr/bin/env bash
# Build the prod-ha release bundle that user_data.sh extracts to /opt/aegis.
#
# Background: the launch-template user_data downloads
# s3://${BUNDLE_BUCKET}/releases/current.tar.gz, untars it into /opt/aegis,
# and runs `docker compose -f infra/docker-compose.yml -f infra/docker-compose.aws.yml up -d`.
# That requires three things that `git archive HEAD` silently drops:
#   1. Root ./Dockerfile (compose's build context is .. from infra/)
#   2. ./infra/docker-compose.yml + .aws.yml
#   3. ./ui/dist/ — pre-built Vite assets (gitignored)
#
# On 2026-06-15 a 811 KB partial bundle landed in current.tar.gz which
# cycled the ASG: new instances failed `docker compose build` (no
# Dockerfile / no ui/dist) and never became ALB-healthy. This script
# pins the working recipe so it doesn't recur.
#
# Usage:
#   ./scripts/ops/build_release_bundle.sh                 # build only → /tmp/aegis-bundle-$(ts).tar.gz
#   UPLOAD=1 ./scripts/ops/build_release_bundle.sh        # also upload as current.tar.gz
#   UPLOAD=1 ASG_REFRESH=1 ./scripts/ops/build_release_bundle.sh   # also trigger ASG instance refresh
#
# Env overrides:
#   BUNDLE_BUCKET=acp-backups-prodha-628478946931
#   AWS_REGION=ap-south-1
#   ASG_NAME=acp-prodha-asg-20260613103432397400000003

set -euo pipefail

BUNDLE_BUCKET="${BUNDLE_BUCKET:-acp-backups-prodha-628478946931}"
AWS_REGION="${AWS_REGION:-ap-south-1}"
ASG_NAME="${ASG_NAME:-}"
UPLOAD="${UPLOAD:-0}"
ASG_REFRESH="${ASG_REFRESH:-0}"

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="/tmp/aegis-bundle-${TS}.tar.gz"

# ── Pre-flight: required files ──────────────────────────────────────────
require() {
    if [[ ! -e "$1" ]]; then
        echo "FAIL — required path missing: $1" >&2
        exit 1
    fi
}
require Dockerfile
require infra/docker-compose.yml
require infra/docker-compose.aws.yml
require ui/Dockerfile

# ui/dist is gitignored. If it's missing, build it now so the bundle is
# complete. Skip with SKIP_UI_BUILD=1 if you've already built it.
if [[ ! -d ui/dist || -z "$(ls -A ui/dist 2>/dev/null)" ]]; then
    if [[ "${SKIP_UI_BUILD:-0}" = "1" ]]; then
        echo "FAIL — ui/dist is empty/missing and SKIP_UI_BUILD=1" >&2
        exit 1
    fi
    echo "→ ui/dist missing, running vite build"
    (cd ui && npm ci --silent && npm run build)
fi
require ui/dist/index.html

# infra/.env / infra/.env.local are tar-excluded below. We don't fail
# if they exist locally (developers may keep them on disk), but we do
# verify after tar that they didn't sneak into the archive.

# ── Build ──────────────────────────────────────────────────────────────
echo "→ Building bundle at $OUT"
tar \
    --exclude='.git' \
    --exclude='node_modules' \
    --exclude='.terraform*' \
    --exclude='__pycache__' \
    --exclude='.pytest_cache' \
    --exclude='.ruff_cache' \
    --exclude='.mypy_cache' \
    --exclude='.hypothesis' \
    --exclude='.coverage*' \
    --exclude='.venv' \
    --exclude='venv' \
    --exclude='*.pyc' \
    --exclude='.DS_Store' \
    --exclude='._*' \
    --exclude='infra/.env' \
    --exclude='infra/.env.local' \
    --exclude='./.env' \
    --exclude='./.env.local' \
    --exclude='./.env.aws*' \
    --exclude='ui/.env' \
    --exclude='ui/.env.local' \
    --exclude='ui/playwright-report' \
    --exclude='ui/test-results' \
    --exclude='reports' \
    --exclude='**/htmlcov' \
    -czf "$OUT" \
    .

SIZE=$(du -h "$OUT" | cut -f1)
echo "→ Bundle size: $SIZE ($OUT)"

# Sanity probe: the three load-bearing files must be inside.
for path in './Dockerfile' './infra/docker-compose.yml' './ui/Dockerfile' './ui/dist/index.html'; do
    if ! tar -tzf "$OUT" | grep -qE "^${path}$"; then
        echo "FAIL — bundle missing $path" >&2
        exit 1
    fi
done
echo "→ Sanity check passed (root Dockerfile + compose + ui/dist present)"

# Post-tar safety: confirm no SECRET-bearing .env snuck in. We deliberately
# allow .env.example (public template) and .env.production (Vite override
# that only carries VITE_GATEWAY_URL). The forbidden shapes are the
# secret-bearing ones: bare .env, .env.local, infra/.env, infra/.env.local,
# ui/.env, ui/.env.local, .env.aws*.
LEAKED_ENV=$(tar -tzf "$OUT" \
    | grep -E '^(\./)?(\.env(\.local|\.aws[A-Za-z0-9.]*)?|infra/\.env(\.local)?|ui/\.env(\.local)?)$' \
    || true)
if [[ -n "$LEAKED_ENV" ]]; then
    echo "FAIL — bundle contains secret-bearing env files; aborting upload" >&2
    echo "$LEAKED_ENV" >&2
    exit 1
fi
# Defensive: also confirm no Stripe/Clerk secret-key string leaked into
# any text file (other than docs that intentionally illustrate them).
SECRET_LEAK=$(tar -xOzf "$OUT" 2>/dev/null | grep -E '(sk_live_[A-Za-z0-9_-]{20,}|sk_test_[A-Za-z0-9_-]{20,}|whsec_[A-Za-z0-9+/]{20,})' | head -3 || true)
if [[ -n "$SECRET_LEAK" ]]; then
    echo "FAIL — bundle contains live-looking Stripe/Clerk secret strings; aborting upload" >&2
    # Echoes a redacted hint, not the full secret.
    echo "$SECRET_LEAK" | sed -E 's/(sk_live_|sk_test_|whsec_)[A-Za-z0-9+/_-]+/\1***REDACTED***/g' >&2
    exit 1
fi

# ── Upload ─────────────────────────────────────────────────────────────
if [[ "$UPLOAD" != "1" ]]; then
    echo "→ Done. Set UPLOAD=1 to push to s3://${BUNDLE_BUCKET}/releases/current.tar.gz"
    echo "$OUT"
    exit 0
fi

echo "→ Uploading to s3://${BUNDLE_BUCKET}/releases/current.tar.gz"
aws --region "$AWS_REGION" s3 cp "$OUT" \
    "s3://${BUNDLE_BUCKET}/releases/current.tar.gz" --quiet
aws --region "$AWS_REGION" s3 cp "$OUT" \
    "s3://${BUNDLE_BUCKET}/releases/bundle-${TS}.tar.gz" --quiet
echo "→ Uploaded current.tar.gz + bundle-${TS}.tar.gz"

# ── Optional: cycle the ASG ─────────────────────────────────────────────
if [[ "$ASG_REFRESH" != "1" ]]; then
    echo "→ Done. Set ASG_REFRESH=1 to trigger an ASG instance refresh."
    exit 0
fi

if [[ -z "$ASG_NAME" ]]; then
    echo "FAIL — ASG_REFRESH=1 but ASG_NAME unset" >&2
    exit 1
fi

echo "→ Triggering instance refresh on ASG $ASG_NAME"
aws --region "$AWS_REGION" autoscaling start-instance-refresh \
    --auto-scaling-group-name "$ASG_NAME" \
    --preferences '{"MinHealthyPercentage":50,"InstanceWarmup":300}' \
    --query 'InstanceRefreshId' --output text
echo "→ Done. Poll: aws autoscaling describe-instance-refreshes --auto-scaling-group-name $ASG_NAME"
