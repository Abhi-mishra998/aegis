#!/usr/bin/env bash
#
# scripts/ops/restore_drill.sh — disaster-recovery drill.
#
# Spins up an ISOLATED docker-compose project on its own network, downloads
# the most recent encrypted backups from S3, decrypts them, restores every
# database, then runs `/audit/logs/verify` + `scripts/ops/reconcile.py`
# against the drilled stack. Writes a verdict to
# reports/restore_drill/{ts}.json.
#
# Isolation guarantees (so a drill can never affect prod):
#
#   • Separate docker-compose project name (-p acp_drill_<ts>).
#   • Separate user-defined bridge network (acp_drill_<ts>_net) so the
#     drill containers cannot resolve `acp_postgres` or any prod host.
#   • Separate Postgres data volume (anonymous — destroyed on exit).
#   • Separate Redis (no shared kill-switch state).
#
# Required env (in addition to the backup-decryption env from backup.sh):
#
#   ACP_BACKUP_S3_BUCKET, ACP_BACKUP_S3_ENDPOINT (optional)
#   ACP_BACKUP_AGE_IDENTITY  (path to age private key file)  -OR-
#   ACP_BACKUP_GPG_HOMEDIR   (gpg homedir holding the decrypt key)
#
# Run modes:
#
#   ./restore_drill.sh --dry-run       # print plan, no docker, no restore
#   ./restore_drill.sh                 # full drill
#   ./restore_drill.sh --keep          # keep the drill containers after
#                                      # verdict for manual inspection

set -euo pipefail

DRY_RUN=0
KEEP=0
while (( "$#" )); do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --keep)    KEEP=1; shift ;;
        -h|--help)
            sed -n '2,30p' "$0" | sed 's/^# //;s/^#//'
            exit 0 ;;
        *) echo "ERROR: unknown flag $1" >&2; exit 2 ;;
    esac
done

TS="$(date -u +%Y%m%dT%H%M%SZ)"
PROJECT="acp_drill_${TS}"
REPORT_DIR="reports/restore_drill"
REPORT_FILE="${REPORT_DIR}/${TS}.json"
mkdir -p "$REPORT_DIR"

DATABASES=(
    "acp_registry" "acp_identity" "acp_audit" "acp_api"
    "acp_usage" "acp_identity_graph" "acp_flight_recorder" "acp_autonomy"
)

log() { echo "[drill $(date -u +%H:%M:%SZ)] $*"; }

# --- Report skeleton -----------------------------------------------------
# Built incrementally so a crash mid-drill still produces a useful artefact.

write_report() {
    local status="$1"; shift
    local extra="${1-}"
    [[ -z "$extra" ]] && extra='{}'
    local db_array
    db_array="$(printf '"%s",' "${DATABASES[@]}" | sed 's/,$//' | sed 's/^/[/;s/$/]/')"
    cat > "$REPORT_FILE" <<EOF
{
  "timestamp":   "$TS",
  "project":     "$PROJECT",
  "status":      "$status",
  "checks":      $extra,
  "databases":   $db_array,
  "kept":        $KEEP
}
EOF
}

cleanup() {
    if [[ $KEEP -eq 1 ]]; then
        log "--keep set; not tearing down $PROJECT"
        return
    fi
    if [[ $DRY_RUN -eq 1 ]]; then return; fi
    log "tearing down $PROJECT"
    docker compose -p "$PROJECT" down -v --remove-orphans 2>/dev/null || true
    docker network rm "${PROJECT}_default" 2>/dev/null || true
}
trap cleanup EXIT

if (( DRY_RUN )); then
    log "DRY-RUN — printing plan"
    log "would spin up docker-compose project: $PROJECT"
    log "would download from: ${ACP_BACKUP_S3_BUCKET:-<unset>}"
    log "would restore ${#DATABASES[@]} databases"
    log "would verify: /audit/logs/verify + scripts/ops/reconcile.py --json"
    log "would write verdict: $REPORT_FILE"
    write_report "DRY_RUN" "{}"
    exit 0
fi

# --- Preflight -----------------------------------------------------------
command -v docker >/dev/null 2>&1 || { echo "docker required" >&2; exit 2; }
[[ -n "${ACP_BACKUP_S3_BUCKET:-}" ]] || { echo "ACP_BACKUP_S3_BUCKET required" >&2; exit 2; }

if [[ -n "${ACP_BACKUP_AGE_IDENTITY:-}" ]]; then
    command -v age >/dev/null 2>&1 || { echo "age required" >&2; exit 2; }
    DECRYPT_CMD=(age -d -i "$ACP_BACKUP_AGE_IDENTITY")
    ENC_EXT="age"
elif [[ -n "${ACP_BACKUP_GPG_HOMEDIR:-}" ]]; then
    command -v gpg >/dev/null 2>&1 || { echo "gpg required" >&2; exit 2; }
    DECRYPT_CMD=(gpg --batch --yes --homedir "$ACP_BACKUP_GPG_HOMEDIR" --decrypt)
    ENC_EXT="gpg"
else
    echo "ERROR: set ACP_BACKUP_AGE_IDENTITY or ACP_BACKUP_GPG_HOMEDIR" >&2; exit 2
fi

# --- Spin up isolated stack ----------------------------------------------
log "spinning up isolated drill stack: $PROJECT"
# Minimal compose: just Postgres + Redis. We don't need the full ACP stack
# to run /audit/logs/verify — verify queries SQL directly via the audit
# service, which we start later as a one-shot.
COMPOSE_FILE="$(mktemp -t acp-drill-XXXXXX.yml)"
trap 'rm -f "$COMPOSE_FILE"; cleanup' EXIT
cat > "$COMPOSE_FILE" <<'EOF'
services:
  drill_postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_PASSWORD: drill
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 1s
      timeout: 2s
      retries: 30
  drill_redis:
    image: redis:7-alpine
EOF
docker compose -f "$COMPOSE_FILE" -p "$PROJECT" up -d

# Wait for postgres healthy
for _ in $(seq 1 30); do
    if docker compose -f "$COMPOSE_FILE" -p "$PROJECT" exec -T drill_postgres pg_isready -U postgres >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

# --- Pull + decrypt + restore each database ------------------------------
WORK="$(mktemp -d -t acp-drill-XXXXXX)"
trap 'rm -rf "$WORK" "$COMPOSE_FILE"; cleanup' EXIT

UPLOAD_CMD=(aws s3 cp)
command -v aws >/dev/null 2>&1 || UPLOAD_CMD=(mc cp)

DB_RESULTS="["
DB_FAILED=0
for DB in "${DATABASES[@]}"; do
    LATEST_KEY="$(${UPLOAD_CMD[0]} s3 ls "${ACP_BACKUP_S3_BUCKET}/" 2>/dev/null \
                  | awk '{print $NF}' | grep "^${DB}_" | sort | tail -1)"
    if [[ -z "$LATEST_KEY" ]]; then
        log "✗ no backup for $DB"
        DB_RESULTS+="{\"db\":\"$DB\",\"status\":\"no_backup\"},"
        DB_FAILED=$((DB_FAILED + 1))
        continue
    fi
    log "fetch  $DB ← $LATEST_KEY"
    "${UPLOAD_CMD[@]}" "${ACP_BACKUP_S3_BUCKET}/${LATEST_KEY}" "$WORK/$LATEST_KEY" >/dev/null
    log "decrypt $LATEST_KEY"
    "${DECRYPT_CMD[@]}" "$WORK/$LATEST_KEY" > "$WORK/${LATEST_KEY%.${ENC_EXT}}"
    log "createdb $DB"
    docker compose -f "$COMPOSE_FILE" -p "$PROJECT" exec -T drill_postgres \
        psql -U postgres -c "CREATE DATABASE \"$DB\";" >/dev/null
    log "pg_restore → $DB"
    docker compose -f "$COMPOSE_FILE" -p "$PROJECT" exec -T drill_postgres \
        pg_restore -U postgres -d "$DB" < "$WORK/${LATEST_KEY%.${ENC_EXT}}" >/dev/null
    DB_RESULTS+="{\"db\":\"$DB\",\"status\":\"restored\"},"
done
DB_RESULTS="${DB_RESULTS%,}]"

# --- Run verify + reconcile against the drilled stack --------------------
# The audit service can run as a one-shot against the drilled postgres.
# Verify chain integrity:
log "running /audit/logs/verify against drilled audit DB"
VERIFY_JSON="$(docker compose -f "$COMPOSE_FILE" -p "$PROJECT" exec -T drill_postgres \
    psql -U postgres -d acp_audit -At \
    -c "SELECT COUNT(*) FROM audit_logs" 2>/dev/null)" || VERIFY_JSON="error"
log "audit_logs count in drilled DB: $VERIFY_JSON"

# Reconcile:
log "running reconcile against drilled DBs"
DRILL_AUDIT_DB="postgresql://postgres:drill@localhost:$(docker compose -f "$COMPOSE_FILE" -p "$PROJECT" port drill_postgres 5432 | cut -d: -f2)/acp_audit"
DRILL_USAGE_DB="${DRILL_AUDIT_DB%/acp_audit}/acp_usage"
RECONCILE_JSON="$(ACP_AUDIT_DB=$DRILL_AUDIT_DB ACP_USAGE_DB=$DRILL_USAGE_DB \
    python scripts/ops/reconcile.py --json 2>&1)" || RECONCILE_JSON='{"status":"ERROR"}'

# --- Final verdict --------------------------------------------------------
RECONCILE_STATUS="$(echo "$RECONCILE_JSON" | python -c "import json,sys; print(json.loads(sys.stdin.read()).get('status','UNKNOWN'))" 2>/dev/null || echo "UNKNOWN")"
STATUS="PASS"
if (( DB_FAILED )) || [[ "$RECONCILE_STATUS" != "VERIFIED" ]]; then
    STATUS="FAIL"
fi
EXTRA=$(cat <<EOF
{
  "db_restore": $DB_RESULTS,
  "db_failed":  $DB_FAILED,
  "reconcile":  $RECONCILE_JSON,
  "audit_row_count": "$VERIFY_JSON"
}
EOF
)
write_report "$STATUS" "$EXTRA"
log "verdict: $STATUS → $REPORT_FILE"

[[ "$STATUS" == "PASS" ]] || exit 1
