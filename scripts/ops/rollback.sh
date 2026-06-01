#!/usr/bin/env bash
# Rollback to the previously-deployed SHA — sprint-4.B.
#
# Reads s3://acp-backups-prod-am/deploys/previous.txt (written by deploy.yml
# at the start of every successful deploy) and reverts the host to that SHA.
#
# On success: swaps current.txt ↔ previous.txt so a second rollback goes
# back to the deploy before the bad one. On failure: leaves the S3 markers
# alone so the operator can re-attempt without losing the rollback target.
#
# This is intentionally a separate script (not embedded in the workflow) so
# operators can run it directly via SSM/SSH during an incident without
# needing GitHub Actions to be functional.
#
# Usage:
#   ./scripts/ops/rollback.sh             # rollback to previous.txt
#   ./scripts/ops/rollback.sh <git-sha>   # rollback to a specific SHA

set -euo pipefail

BUCKET="${ROLLBACK_S3_BUCKET:-acp-backups-prod-am}"
CURRENT_KEY="deploys/current.txt"
PREVIOUS_KEY="deploys/previous.txt"

cd "$(dirname "$0")/../.."

# Pick target SHA: arg first, then S3 previous.txt.
TARGET_SHA="${1:-}"
if [ -z "$TARGET_SHA" ]; then
  TARGET_SHA=$(aws s3 cp "s3://$BUCKET/$PREVIOUS_KEY" - 2>/dev/null | tr -d '[:space:]' || true)
fi
if [ -z "$TARGET_SHA" ]; then
  echo "ERROR: no rollback target — pass an explicit SHA or ensure" >&2
  echo "       s3://$BUCKET/$PREVIOUS_KEY exists from a prior deploy." >&2
  exit 2
fi

CURRENT_SHA=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
echo "=== Rollback ==="
echo "  current: $CURRENT_SHA"
echo "  target:  $TARGET_SHA"

if [ "$TARGET_SHA" = "$CURRENT_SHA" ]; then
  echo "Target SHA equals current — nothing to do."
  exit 0
fi

echo "=== Fetching target SHA ==="
git fetch origin "$TARGET_SHA" --depth 1
git checkout --detach "$TARGET_SHA"

# Re-pull config from S3 — important if the previous deploy was on different
# env vars.
echo "=== Restoring config from S3 ==="
aws s3 cp "s3://$BUCKET/config/.env"               infra/.env
aws s3 cp "s3://$BUCKET/config/pgbouncer.aws.ini"  infra/pgbouncer.aws.ini
aws s3 cp "s3://$BUCKET/config/userlist.txt"       infra/userlist.txt

echo "=== Rebuilding images for target SHA ==="
cd infra
# UI build separately to mirror deploy.yml.
cd ../ui
docker run --rm \
  -v "$(pwd)":/app -w /app \
  -e NODE_OPTIONS="--max-old-space-size=512" \
  node:20-alpine sh -c "rm -rf /app/dist && npm ci && npm run build"
cd ../infra
docker compose -f docker-compose.yml -f docker-compose.aws.yml build --no-cache ui

OTHER=$(docker compose -f docker-compose.yml -f docker-compose.aws.yml config --services | grep -v '^ui$' | tr '\n' ' ')
docker compose -f docker-compose.yml -f docker-compose.aws.yml build --parallel $OTHER

echo "=== Bringing up rollback target ==="
docker compose -f docker-compose.yml -f docker-compose.aws.yml up -d --no-build

echo "=== Waiting for health (up to 3 min) ==="
REQUIRED="acp_audit acp_gateway acp_identity acp_registry acp_api acp_usage acp_decision acp_pgbouncer acp_opa acp_ui"
WAITED=0
while [ $WAITED -lt 180 ]; do
  ALL_HEALTHY=1
  for svc in $REQUIRED; do
    STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$svc" 2>/dev/null || echo "missing")
    [ "$STATUS" = "healthy" ] || ALL_HEALTHY=0
  done
  if [ $ALL_HEALTHY -eq 1 ]; then break; fi
  sleep 10; WAITED=$((WAITED + 10))
done

# Health gate
FAILED=""
for svc in $REQUIRED; do
  STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$svc" 2>/dev/null || echo "missing")
  [ "$STATUS" = "healthy" ] || FAILED="$FAILED $svc[$STATUS]"
done

if [ -n "$FAILED" ]; then
  echo "ROLLBACK FAILED — not healthy:$FAILED" >&2
  echo "S3 markers unchanged; operator action required." >&2
  exit 1
fi

# Swap S3 markers: the SHA we rolled back FROM becomes the new "previous"
# so a second rollback recovers to it.
echo "=== Swapping S3 deploy markers ==="
echo "$TARGET_SHA"  | aws s3 cp - "s3://$BUCKET/$CURRENT_KEY"
echo "$CURRENT_SHA" | aws s3 cp - "s3://$BUCKET/$PREVIOUS_KEY"

echo "=== Rollback complete ==="
echo "  now serving: $TARGET_SHA"
echo "  rollback-from: $CURRENT_SHA  (this becomes the next 'previous' target)"
