#!/usr/bin/env sh
# Postgres backup for the compliance agent. Writes a timestamped pg_dump
# to /backups/. Keeps the last RETAIN_DAYS days; older dumps are removed.
#
# Designed to be invoked from a sidecar container that has pg_dump installed
# and the postgres service reachable on the docker network.
#
# Env:
#   PGHOST       (default: postgres)
#   PGPORT       (default: 5432)
#   PGUSER       (default: compliance_user)
#   PGPASSWORD   (required — passed in from docker-compose env_file)
#   PGDATABASE   (default: compliance_db)
#   BACKUP_DIR   (default: /backups)
#   RETAIN_DAYS  (default: 14)
set -eu

: "${PGHOST:=postgres}"
: "${PGPORT:=5432}"
: "${PGUSER:=compliance_user}"
: "${PGDATABASE:=compliance_db}"
: "${BACKUP_DIR:=/backups}"
: "${RETAIN_DAYS:=14}"

mkdir -p "$BACKUP_DIR"

TS=$(date -u +%Y%m%dT%H%M%SZ)
OUT="$BACKUP_DIR/${PGDATABASE}_${TS}.sql.gz"

echo "[$(date -u -Iseconds)] Backing up $PGDATABASE → $OUT"
pg_dump \
    --host="$PGHOST" \
    --port="$PGPORT" \
    --username="$PGUSER" \
    --dbname="$PGDATABASE" \
    --format=plain \
    --no-owner \
    --no-privileges \
    --no-comments \
    | gzip -9 > "$OUT"

# Track size to log
SIZE=$(stat -c%s "$OUT" 2>/dev/null || stat -f%z "$OUT" 2>/dev/null || echo "?")
echo "[$(date -u -Iseconds)] Dump complete (${SIZE} bytes)"

# Retention — delete dumps older than RETAIN_DAYS
find "$BACKUP_DIR" -name "${PGDATABASE}_*.sql.gz" -type f -mtime "+${RETAIN_DAYS}" -print -delete | sed 's/^/[retention] removed /'

# Print latest 3 dumps for log visibility
echo "[$(date -u -Iseconds)] Latest dumps:"
ls -1t "$BACKUP_DIR"/${PGDATABASE}_*.sql.gz 2>/dev/null | head -3 | sed 's/^/  /'
