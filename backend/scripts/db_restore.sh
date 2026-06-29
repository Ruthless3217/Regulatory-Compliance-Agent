#!/usr/bin/env sh
# Postgres restore for the compliance agent — the counterpart to db_backup.sh.
# Restores a gzip'd pg_dump (plain format) into the target database, then prints
# a post-restore summary: row counts and the *embedding provenance* of the
# corpus (which model produced the stored vectors, and at what dimension).
#
# Designed to run the same way db_backup.sh does — from a sidecar/utility
# container that has psql installed and can reach the postgres service on the
# docker network — or directly on a host with psql.
#
# The stored vectors are restored VERBATIM. No embeddings are recomputed, so a
# corpus embedded with Cohere embed-v3 (1024-dim) lands ready to query, as long
# as the running backend embeds queries with the SAME model (see the provenance
# check below — "same dimension" is NOT the same as "same embedding space").
#
# Env:
#   PGHOST       (default: postgres)
#   PGPORT       (default: 5432)
#   PGUSER       (default: compliance_user)
#   PGPASSWORD   (required — passed in from docker-compose env_file)
#   PGDATABASE   (default: compliance_db)
#
# Usage:
#   ./db_restore.sh /backups/compliance_db_20260624T103230Z.sql.gz
#   # or, from the repo root via the backup sidecar:
#   docker compose exec backup sh /scripts/db_restore.sh /backups/<dump>.sql.gz
set -eu

: "${PGHOST:=postgres}"
: "${PGPORT:=5432}"
: "${PGUSER:=compliance_user}"
: "${PGDATABASE:=compliance_db}"

DUMP="${1:-}"
if [ -z "$DUMP" ]; then
    echo "ERROR: pass the dump file to restore." >&2
    echo "  usage: $0 <path/to/compliance_db_*.sql.gz>" >&2
    exit 2
fi
if [ ! -f "$DUMP" ]; then
    echo "ERROR: dump not found: $DUMP" >&2
    exit 2
fi

# Guard against the empty/failed-dump trap: a real dump is tens of MB, a failed
# one is a few hundred bytes. Refuse anything suspiciously tiny.
SIZE=$(stat -c%s "$DUMP" 2>/dev/null || stat -f%z "$DUMP" 2>/dev/null || echo 0)
if [ "$SIZE" -lt 10240 ]; then
    echo "ERROR: '$DUMP' is only ${SIZE} bytes — looks like a failed/empty dump." >&2
    echo "       A real corpus dump is tens of MB. Refusing to restore it." >&2
    exit 3
fi

# Verify gzip integrity before touching the database.
if ! gzip -t "$DUMP" 2>/dev/null; then
    echo "ERROR: '$DUMP' failed gzip integrity check (gzip -t)." >&2
    exit 3
fi

PSQL="psql --host=$PGHOST --port=$PGPORT --username=$PGUSER --dbname=$PGDATABASE"

echo "[$(date -u -Iseconds)] Restoring $DUMP (${SIZE} bytes) -> $PGDATABASE @ $PGHOST"
echo "[$(date -u -Iseconds)] (pgvector 'vector' extension must exist on the target)"

# Stream the dump straight into psql. ON_ERROR_STOP makes a bad restore fail
# loudly instead of leaving a half-populated DB.
gunzip -c "$DUMP" | $PSQL --set ON_ERROR_STOP=on --quiet

echo "[$(date -u -Iseconds)] Restore complete. Summary:"

# --- row counts for the embedding-bearing tables --------------------------
$PSQL --quiet --tuples-only --no-align --command "
  SELECT '  rows  ' || rpad(t, 24) || count
  FROM (
    SELECT 'rag_chunks'            AS t, count(*) FROM rag_chunks
    UNION ALL SELECT 'rag_rules',            count(*) FROM rag_rules
    UNION ALL SELECT 'rag_source_docs',      count(*) FROM rag_source_docs
    UNION ALL SELECT 'rag_compliance_examples', count(*) FROM rag_compliance_examples
    UNION ALL SELECT 'rag_product_docs',     count(*) FROM rag_product_docs
    UNION ALL SELECT 'precedent_cases',      count(*) FROM precedent_cases
  ) s;" 2>/dev/null || echo "  (some tables absent — apply migrations, then re-check)"

# --- embedding provenance: is the corpus single-model? --------------------
# 0009 stamped each vector with the model that produced it. If more than one
# (model, dim) pair shows up, the corpus is MIXED — vectors from different
# models are not comparable even at equal dimension, and the backend's
# fail-closed mismatch guard will drop the off-model ones at query time.
echo "[$(date -u -Iseconds)] Embedding provenance (model | dim | rows):"
$PSQL --quiet --command "
  SELECT coalesce(embedding_model,'(null)') AS model,
         coalesce(embedding_dim, vector_dims(embedding)) AS dim,
         count(*) AS rows
  FROM rag_chunks
  WHERE embedding IS NOT NULL
  GROUP BY 1, 2
  ORDER BY rows DESC;" 2>/dev/null \
  || echo "  (could not read embedding identity — older schema?)"

echo "[$(date -u -Iseconds)] If more than one (model,dim) row appears above, the"
echo "  corpus is MIXED-MODEL: a single-model re-embed (all Cohere v3) is the fix."
