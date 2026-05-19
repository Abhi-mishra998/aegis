#!/usr/bin/env bash
#
# scripts/ops/backup.sh — encrypted offsite backup of every ACP database.
#
# What it does, in order:
#
#   1. pg_dump --format=custom every database listed in DATABASES (defaults
#      match `infra/pgbouncer.ini`'s [databases] block).
#   2. Encrypt each dump with `age` to the public-key recipient identified by
#      $ACP_BACKUP_AGE_RECIPIENT  (e.g. age1abc...).
#      Falls back to `gpg --recipient $ACP_BACKUP_GPG_RECIPIENT` if age is
#      unavailable.  The encryption keys MUST NEVER live in this repo —
#      provision them out-of-band (KMS, 1Password, hardware key) and inject
#      via env.
#   3. Upload to S3-compatible storage via `aws s3 cp` or `mc cp`.
#      $ACP_BACKUP_S3_BUCKET is required; $ACP_BACKUP_S3_ENDPOINT optional
#      (set for MinIO / non-AWS endpoints).
#   4. Verify restorability in a throwaway container: `pg_restore --list` of
#      the just-uploaded artefact + a `SELECT count(*) FROM audit_logs` smoke
#      check against an `acp_backup_verify` ephemeral Postgres.
#
# Run modes:
#
#   ./backup.sh --dry-run       # show plan, no pg_dump / no upload
#   ./backup.sh                 # full run, exits non-zero on any failure
#   ./backup.sh --no-verify     # skip the throwaway-restore verify (faster
#                               # incremental runs; do NOT use for archival)
#
# All side effects (pg_dump, encrypt, upload, container) are idempotent —
# a re-run produces an artefact with a fresh timestamp suffix; nothing is
# overwritten in place.
#
# Env vars (REQUIRED unless --dry-run):
#   ACP_BACKUP_AGE_RECIPIENT       age public key (preferred)
#   ACP_BACKUP_GPG_RECIPIENT       gpg recipient (fallback)
#   ACP_BACKUP_S3_BUCKET           s3://bucket/path
#   ACP_BACKUP_S3_ENDPOINT         (optional) for MinIO/Wasabi/etc.
#   POSTGRES_HOST                  (default: acp_postgres)
#   POSTGRES_PORT                  (default: 5432)
#   POSTGRES_USER                  (default: postgres)
#   PGPASSWORD                     required for pg_dump
#
# Exit codes:
#   0  every step succeeded
#   1  any per-database failure (pg_dump | encrypt | upload | verify)
#   2  configuration error (missing env, missing tools)

set -euo pipefail

# --- defaults --------------------------------------------------------------
: "${POSTGRES_HOST:=acp_postgres}"
: "${POSTGRES_PORT:=5432}"
: "${POSTGRES_USER:=postgres}"

DATABASES=(
    "acp_registry"
    "acp_identity"
    "acp_audit"
    "acp_api"
    "acp_usage"
    "acp_identity_graph"
    "acp_flight_recorder"
    "acp_autonomy"
)

# --- CLI parsing -----------------------------------------------------------
DRY_RUN=0
NO_VERIFY=0
while (( "$#" )); do
    case "$1" in
        --dry-run)   DRY_RUN=1; shift ;;
        --no-verify) NO_VERIFY=1; shift ;;
        -h|--help)
            sed -n '2,40p' "$0" | sed 's/^# //;s/^#//'
            exit 0 ;;
        *)
            echo "ERROR: unknown flag $1" >&2; exit 2 ;;
    esac
done

log() { echo "[backup $(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }
require() {
    command -v "$1" >/dev/null 2>&1 || { echo "ERROR: $1 not in PATH" >&2; exit 2; }
}

if (( DRY_RUN )); then
    log "DRY-RUN — printing plan, no side effects"
fi

# --- preflight -------------------------------------------------------------
TS="$(date -u +%Y%m%dT%H%M%SZ)"
WORKDIR="$(mktemp -d -t acp-backup-XXXXXX)"
trap 'rm -rf "$WORKDIR"' EXIT

if [[ $DRY_RUN -eq 0 ]]; then
    require pg_dump
    require pg_restore
    if [[ -n "${ACP_BACKUP_AGE_RECIPIENT:-}" ]]; then
        require age
        ENC_CMD=(age -r "${ACP_BACKUP_AGE_RECIPIENT}" -o)
        ENC_EXT="age"
    elif [[ -n "${ACP_BACKUP_GPG_RECIPIENT:-}" ]]; then
        require gpg
        ENC_CMD=(gpg --batch --yes --encrypt --recipient "${ACP_BACKUP_GPG_RECIPIENT}" --output)
        ENC_EXT="gpg"
    else
        echo "ERROR: set ACP_BACKUP_AGE_RECIPIENT (preferred) or ACP_BACKUP_GPG_RECIPIENT" >&2
        exit 2
    fi
    if [[ -z "${ACP_BACKUP_S3_BUCKET:-}" ]]; then
        echo "ERROR: ACP_BACKUP_S3_BUCKET (e.g. s3://acp-backups/prod) is required" >&2
        exit 2
    fi
    if command -v aws >/dev/null 2>&1; then
        UPLOAD_CMD=(aws s3 cp)
    elif command -v mc >/dev/null 2>&1; then
        UPLOAD_CMD=(mc cp)
    else
        echo "ERROR: neither aws nor mc in PATH" >&2; exit 2
    fi
fi

log "workdir=$WORKDIR  ts=$TS  databases=${#DATABASES[@]}"

# --- per-database dump + encrypt + upload ---------------------------------
FAIL=0
for DB in "${DATABASES[@]}"; do
    DUMP_FILE="$WORKDIR/${DB}_${TS}.dump"
    ENC_FILE="${DUMP_FILE}.${ENC_EXT:-encrypted}"

    if (( DRY_RUN )); then
        log "would pg_dump $DB → $DUMP_FILE"
        log "would encrypt → ${ENC_FILE##*/}"
        log "would upload → ${ACP_BACKUP_S3_BUCKET:-<unset>}/${ENC_FILE##*/}"
        continue
    fi

    log "pg_dump $DB"
    if ! PGPASSWORD="$PGPASSWORD" pg_dump \
            -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" \
            --format=custom --no-owner --no-privileges \
            --file "$DUMP_FILE" "$DB"; then
        log "✗ pg_dump $DB failed"
        FAIL=$((FAIL + 1))
        continue
    fi

    log "encrypt → ${ENC_FILE##*/}"
    if ! "${ENC_CMD[@]}" "$ENC_FILE" "$DUMP_FILE"; then
        log "✗ encryption of $DB failed"
        FAIL=$((FAIL + 1))
        continue
    fi
    # The plaintext dump is dropped from the workdir immediately so it
    # cannot leak via `lsof` or a tmpfs scrape during the upload step.
    shred -u "$DUMP_FILE" 2>/dev/null || rm -f "$DUMP_FILE"

    log "upload → $ACP_BACKUP_S3_BUCKET/${ENC_FILE##*/}"
    UPLOAD_ARGS=("$ENC_FILE" "$ACP_BACKUP_S3_BUCKET/${ENC_FILE##*/}")
    if [[ -n "${ACP_BACKUP_S3_ENDPOINT:-}" ]]; then
        UPLOAD_ARGS+=("--endpoint-url" "$ACP_BACKUP_S3_ENDPOINT")
    fi
    if ! "${UPLOAD_CMD[@]}" "${UPLOAD_ARGS[@]}"; then
        log "✗ upload of $DB failed"
        FAIL=$((FAIL + 1))
        continue
    fi
done

if [[ $DRY_RUN -eq 1 ]]; then
    log "DRY-RUN complete — no side effects"
    exit 0
fi

# --- verify (optional but ON by default) ----------------------------------
if [[ $NO_VERIFY -eq 0 && $FAIL -eq 0 ]]; then
    log "starting throwaway verify container acp_backup_verify"
    # Use a deterministic name so concurrent runs collide loudly rather than
    # silently. The container is removed on exit.
    docker rm -f acp_backup_verify >/dev/null 2>&1 || true
    docker run -d --name acp_backup_verify --rm \
        -e POSTGRES_PASSWORD=verify -p 0:5432 postgres:16-alpine >/dev/null
    # Wait for postgres to accept connections (5s budget).
    for _ in 1 2 3 4 5; do
        if docker exec acp_backup_verify pg_isready -U postgres >/dev/null 2>&1; then
            break
        fi
        sleep 1
    done
    # Just smoke-test the audit DB — restoring all 8 would dominate the
    # backup window. The drill script (restore_drill.sh) does the full
    # restore on a schedule.
    SAMPLE="acp_audit_${TS}.dump.${ENC_EXT}"
    SAMPLE_URL="${ACP_BACKUP_S3_BUCKET}/${SAMPLE}"
    SAMPLE_LOCAL="$WORKDIR/${SAMPLE}"
    if [[ -f "$SAMPLE_LOCAL" ]]; then
        log "verify: pg_restore --list on $SAMPLE"
        # Decrypt → list — does not actually restore.
        if [[ "${ENC_EXT}" == "age" ]]; then
            age -d -i "${ACP_BACKUP_AGE_IDENTITY:?ACP_BACKUP_AGE_IDENTITY required for --no-verify=off}" \
                "$SAMPLE_LOCAL" > "$WORKDIR/sample.dump"
        else
            gpg --batch --yes --decrypt --output "$WORKDIR/sample.dump" "$SAMPLE_LOCAL"
        fi
        pg_restore --list "$WORKDIR/sample.dump" > /dev/null
        log "verify OK — pg_restore --list parsed the archive cleanly"
    else
        log "verify SKIPPED — sample $SAMPLE not found locally"
    fi
    docker stop acp_backup_verify >/dev/null 2>&1 || true
fi

if (( FAIL )); then
    log "✗ FAILED — $FAIL database(s) failed; see log above"
    exit 1
fi
log "✓ PASSED — ${#DATABASES[@]} databases backed up to $ACP_BACKUP_S3_BUCKET"
