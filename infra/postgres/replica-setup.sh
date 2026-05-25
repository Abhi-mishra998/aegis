#!/bin/bash
set -e
# Wait for primary to be ready
until pg_isready -h acp_postgres -U ${POSTGRES_USER:-postgres}; do
  echo "Waiting for primary..."
  sleep 2
done
# Run pg_basebackup if data dir is empty
if [ ! -f "$PGDATA/PG_VERSION" ]; then
  pg_basebackup -h acp_postgres -U replicator -D $PGDATA -Fp -Xs -P -R
  echo "primary_conninfo = 'host=acp_postgres port=5432 user=replicator'" >> $PGDATA/postgresql.conf
fi
exec postgres "$@"
