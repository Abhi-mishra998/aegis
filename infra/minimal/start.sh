#!/usr/bin/env bash
# Aegis minimal-mode entrypoint.
#
# R3 (Sprint refactor): the security-review surface for a self-host
# customer is "three containers + one entrypoint script". This script
# is that entrypoint. It runs inside the `aegis-core` container, sees
# AEGIS_MODE=minimal in the env, and does five things in order:
#
#   1. Wait for external Postgres + Redis to accept connections.
#   2. Bootstrap per-service databases + users (first run only; idempotent).
#   3. Render userlist.txt for pgbouncer from env vars.
#   4. Run alembic migrations for each Aegis service.
#   5. Hand off to supervisord, which runs the 10 inner processes.
#
# A CISO can read this top to bottom in 60 seconds and know exactly
# what gets touched on boot. No magic. No conditional code paths that
# behave differently in prod vs dev.

set -euo pipefail

log() { echo "[aegis-start] $*"; }

# ──────────────────────────────────────────────────────────────────────
# 1. Required env (fail fast if anything is missing)
# ──────────────────────────────────────────────────────────────────────
: "${AEGIS_MODE:?must be set (expected: minimal)}"
: "${POSTGRES_HOST:?required}"
: "${POSTGRES_PORT:=5432}"
: "${POSTGRES_USER:=postgres}"
: "${POSTGRES_PASSWORD:?required}"
: "${REDIS_URL:?required (e.g. redis://redis:6379/0)}"
: "${INTERNAL_SECRET:?required (shared service-to-service secret)}"
: "${JWT_SECRET_KEY:?required (HS256 JWT signing key)}"

# Per-service DB passwords default to POSTGRES_PASSWORD for the bundled
# 1-tenant single-host case. Production customers can rotate these to
# distinct Secrets Manager / vault entries by passing them at run time.
# behavior is intentionally absent — that service is Redis-only.
: "${IDENTITY_DB_PASSWORD:=${POSTGRES_PASSWORD}}"
: "${REGISTRY_DB_PASSWORD:=${POSTGRES_PASSWORD}}"
: "${AUDIT_DB_PASSWORD:=${POSTGRES_PASSWORD}}"
: "${AUTONOMY_DB_PASSWORD:=${POSTGRES_PASSWORD}}"

# Export ENV_* aliases that supervisord.conf substitutes per program.
export ENV_IDENTITY_DB_PASSWORD="${IDENTITY_DB_PASSWORD}"
export ENV_REGISTRY_DB_PASSWORD="${REGISTRY_DB_PASSWORD}"
export ENV_AUDIT_DB_PASSWORD="${AUDIT_DB_PASSWORD}"
export ENV_AUTONOMY_DB_PASSWORD="${AUTONOMY_DB_PASSWORD}"

# ──────────────────────────────────────────────────────────────────────
# 2. Wait for Postgres + Redis
# ──────────────────────────────────────────────────────────────────────
log "Waiting for Postgres at ${POSTGRES_HOST}:${POSTGRES_PORT}..."
for i in $(seq 1 60); do
    if PGPASSWORD="${POSTGRES_PASSWORD}" psql -h "${POSTGRES_HOST}" -p "${POSTGRES_PORT}" \
        -U "${POSTGRES_USER}" -d postgres -c "SELECT 1" >/dev/null 2>&1; then
        log "Postgres ready."
        break
    fi
    if [[ "${i}" == "60" ]]; then
        log "FATAL: Postgres did not become reachable in 60s."
        exit 1
    fi
    sleep 1
done

log "Waiting for Redis at ${REDIS_URL}..."
REDIS_HOST=$(echo "${REDIS_URL}" | sed -E 's|^redis(s?)://([^:/@]+@)?([^:/]+).*|\3|')
REDIS_PORT=$(echo "${REDIS_URL}" | sed -E 's|^redis(s?)://([^:/@]+@)?[^:]+:([0-9]+).*|\3|')
REDIS_PORT="${REDIS_PORT:-6379}"
for i in $(seq 1 30); do
    if (echo > "/dev/tcp/${REDIS_HOST}/${REDIS_PORT}") >/dev/null 2>&1; then
        log "Redis ready."
        break
    fi
    if [[ "${i}" == "30" ]]; then
        log "FATAL: Redis did not become reachable in 30s."
        exit 1
    fi
    sleep 1
done

# ──────────────────────────────────────────────────────────────────────
# 3. Bootstrap databases + users (idempotent; safe on re-runs)
# ──────────────────────────────────────────────────────────────────────
log "Bootstrapping per-service databases + roles..."
PGPASSWORD="${POSTGRES_PASSWORD}" psql -v ON_ERROR_STOP=off \
    -h "${POSTGRES_HOST}" -p "${POSTGRES_PORT}" \
    -U "${POSTGRES_USER}" -d postgres <<SQL
CREATE DATABASE acp_identity OWNER ${POSTGRES_USER};
CREATE DATABASE acp_registry OWNER ${POSTGRES_USER};
CREATE DATABASE acp_audit    OWNER ${POSTGRES_USER};
CREATE DATABASE acp_autonomy OWNER ${POSTGRES_USER};
SQL

PGPASSWORD="${POSTGRES_PASSWORD}" psql -v ON_ERROR_STOP=off \
    -h "${POSTGRES_HOST}" -p "${POSTGRES_PORT}" \
    -U "${POSTGRES_USER}" -d postgres <<SQL
CREATE ROLE identity_user LOGIN PASSWORD '${IDENTITY_DB_PASSWORD}';
CREATE ROLE registry_user LOGIN PASSWORD '${REGISTRY_DB_PASSWORD}';
CREATE ROLE audit_user    LOGIN PASSWORD '${AUDIT_DB_PASSWORD}';
CREATE ROLE autonomy_user LOGIN PASSWORD '${AUTONOMY_DB_PASSWORD}';
SQL

PGPASSWORD="${POSTGRES_PASSWORD}" psql -v ON_ERROR_STOP=on \
    -h "${POSTGRES_HOST}" -p "${POSTGRES_PORT}" \
    -U "${POSTGRES_USER}" -d postgres <<SQL
ALTER ROLE identity_user WITH PASSWORD '${IDENTITY_DB_PASSWORD}';
ALTER ROLE registry_user WITH PASSWORD '${REGISTRY_DB_PASSWORD}';
ALTER ROLE audit_user    WITH PASSWORD '${AUDIT_DB_PASSWORD}';
ALTER ROLE autonomy_user WITH PASSWORD '${AUTONOMY_DB_PASSWORD}';

GRANT ALL PRIVILEGES ON DATABASE acp_identity TO identity_user;
GRANT ALL PRIVILEGES ON DATABASE acp_registry TO registry_user;
GRANT ALL PRIVILEGES ON DATABASE acp_audit    TO audit_user;
GRANT ALL PRIVILEGES ON DATABASE acp_autonomy TO autonomy_user;
SQL

for db_user_pair in \
    "acp_identity:identity_user" \
    "acp_registry:registry_user" \
    "acp_audit:audit_user" \
    "acp_autonomy:autonomy_user"; do
    db="${db_user_pair%%:*}"
    user="${db_user_pair##*:}"
    PGPASSWORD="${POSTGRES_PASSWORD}" psql -v ON_ERROR_STOP=on \
        -h "${POSTGRES_HOST}" -p "${POSTGRES_PORT}" \
        -U "${POSTGRES_USER}" -d "${db}" \
        -c "GRANT ALL ON SCHEMA public TO ${user};" >/dev/null
done

# ──────────────────────────────────────────────────────────────────────
# 4. Run alembic migrations for each service
# ──────────────────────────────────────────────────────────────────────
run_migration() {
    local service="$1" db="$2" user="$3" password="$4"
    log "alembic upgrade: ${service} → ${db}"
    cd "/app/services/${service}"
    DATABASE_URL="postgresql+asyncpg://${user}:${password}@${POSTGRES_HOST}:${POSTGRES_PORT}/${db}" \
        alembic upgrade head
    cd /
}

run_migration identity        acp_identity identity_user "${IDENTITY_DB_PASSWORD}"
run_migration registry        acp_registry registry_user "${REGISTRY_DB_PASSWORD}"
# NOTE: services/behavior is Redis-only — no Postgres, no alembic.
run_migration audit           acp_audit    audit_user    "${AUDIT_DB_PASSWORD}"
run_migration autonomy        acp_autonomy autonomy_user "${AUTONOMY_DB_PASSWORD}"

# ──────────────────────────────────────────────────────────────────────
# 5. Hand off to supervisord — runs the inner processes forever.
# ──────────────────────────────────────────────────────────────────────
log "Migrations complete. Starting supervisord."
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/aegis-core.conf
