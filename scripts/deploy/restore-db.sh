#!/usr/bin/env bash
# =============================================================================
# restore-db.sh — seed the VM's Postgres with the knowledge base.
#
# The compliance agent's knowledge base (RAG chunks, embeddings, rules,
# precedent examples, product-doc chunks) lives ENTIRELY in Postgres — not in
# the ./docs or ./dataset folders. A fresh `docker compose up` starts an EMPTY
# Postgres, so the agent would come up brain-dead. This script loads the seed
# dump (scripts/deploy/seed/compliance_db_seed.sql.gz) into it.
#
# It is IDEMPOTENT: if the KB tables already hold data it skips the restore so
# re-running a deploy never clobbers a live DB. Use FORCE=1 to wipe + reload.
#
# Prereqs on the target:
#   - The stack's postgres service is reachable as container `compliance-postgres`
#     (this script will start just that service if it isn't running).
#   - The repo (with scripts/deploy/seed/*.sql.gz) is present.
#   - A filled-in .env at repo root (for POSTGRES_USER/PASSWORD/DB).
#
# Usage:
#   ./scripts/deploy/restore-db.sh
#   FORCE=1 ./scripts/deploy/restore-db.sh        # drop & reload even if populated
#   SEED=/path/to/other.sql.gz ./scripts/deploy/restore-db.sh
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
SEED="${SEED:-scripts/deploy/seed/compliance_db_seed.sql.gz}"
CONTAINER="${CONTAINER:-compliance-postgres}"

# Pull DB creds from .env if present (defaults match the compose file).
PGUSER="compliance_user"
PGDATABASE="compliance_db"
if [ -f ".env" ]; then
  # shellcheck disable=SC1091
  set -a; . ./.env; set +a
  PGUSER="${POSTGRES_USER:-$PGUSER}"
  PGDATABASE="${POSTGRES_DB:-$PGDATABASE}"
fi

# A table we expect to be non-empty in a seeded KB.
MARKER_TABLE="rag_compliance_examples"

if [ ! -f "${SEED}" ]; then
  echo "!! Seed dump not found: ${SEED}" >&2
  echo "   (expected a *.sql.gz produced by pg_dump — see README / db_backup.sh)" >&2
  exit 1
fi

# Bring up ONLY postgres if it isn't already running (restore must happen
# before the backend starts so its startup migrations see a DB already at HEAD).
if ! docker ps --format '{{.Names}}' | grep -qx "${CONTAINER}"; then
  echo ">> Starting postgres only ..."
  docker compose -f "${COMPOSE_FILE}" up -d postgres
fi

echo ">> Waiting for postgres to accept connections ..."
for _ in $(seq 1 30); do
  if docker exec "${CONTAINER}" pg_isready -U "${PGUSER}" -d "${PGDATABASE}" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
docker exec "${CONTAINER}" pg_isready -U "${PGUSER}" -d "${PGDATABASE}" >/dev/null 2>&1 \
  || { echo "!! postgres did not become ready" >&2; exit 1; }

# psql helper — connects over the local socket inside the container (trust auth),
# so no password handling is needed here.
psql_q() { docker exec -i "${CONTAINER}" psql -tA -U "${PGUSER}" -d "${PGDATABASE}" "$@"; }

# Is the KB already populated?
EXISTING="$(psql_q -c "SELECT COALESCE((SELECT count(*) FROM ${MARKER_TABLE}), 0);" 2>/dev/null || echo 0)"
EXISTING="${EXISTING:-0}"

if [ "${EXISTING}" -gt 0 ] && [ "${FORCE:-0}" != "1" ]; then
  echo ">> KB already populated (${MARKER_TABLE}=${EXISTING} rows). Skipping restore."
  echo "   Re-run with FORCE=1 to wipe and reload from ${SEED}."
  exit 0
fi

if [ "${FORCE:-0}" = "1" ]; then
  echo ">> FORCE=1 — dropping & recreating public schema ..."
  psql_q -v ON_ERROR_STOP=1 -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" >/dev/null
fi

echo ">> Restoring KB from ${SEED} (this can take a minute) ..."
gunzip -c "${SEED}" | docker exec -i "${CONTAINER}" \
  psql -v ON_ERROR_STOP=1 -U "${PGUSER}" -d "${PGDATABASE}" >/dev/null

echo ">> Restore complete. Row counts:"
psql_q -c "
  SELECT 'rag_compliance_examples', count(*) FROM rag_compliance_examples
  UNION ALL SELECT 'rag_product_docs', count(*) FROM rag_product_docs
  UNION ALL SELECT 'rag_rules', count(*) FROM rag_rules
  UNION ALL SELECT 'rag_chunks', count(*) FROM rag_chunks
  UNION ALL SELECT 'rules', count(*) FROM rules;" | sed 's/^/   /'

echo ">> Done. You can now bring up the rest of the stack:"
echo "     docker compose -f ${COMPOSE_FILE} up -d"
