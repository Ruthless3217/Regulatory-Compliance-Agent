#!/usr/bin/env bash
# =============================================================================
# load-and-up.sh — run this on the TARGET machine (RHEL 9 VM / friend's laptop).
#
# Loads the gzipped image tarballs produced by build-and-save.sh, then starts
# the stack with docker-compose.prod.yml. No build, no registry needed.
#
# Prerequisites on the target:
#   1. Docker + the compose plugin installed and the daemon running.
#   2. This repo present (for docker-compose.prod.yml + mounted ./docs, ./dataset,
#      ./backend/scripts/db_backup.sh).
#   3. A filled-in .env at the repo root (cp .env.prod.example .env first).
#   4. The dist/ folder (the *.tar.gz files) copied next to the repo.
#
# Usage:
#   ./scripts/deploy/load-and-up.sh
#   DIST_DIR=/media/usb/dist ./scripts/deploy/load-and-up.sh
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

DIST_DIR="${DIST_DIR:-dist}"
COMPOSE_FILE="docker-compose.prod.yml"

if [ ! -d "${DIST_DIR}" ]; then
  echo "!! ${DIST_DIR}/ not found. Copy the dist/ folder from the build machine here." >&2
  exit 1
fi

if [ ! -f ".env" ]; then
  echo "!! No .env at repo root. Run:  cp .env.prod.example .env  and fill it in." >&2
  exit 1
fi

# Pick up the tag recorded at build time (falls back to v1).
if [ -f "${DIST_DIR}/IMAGE_TAG" ]; then
  IMAGE_TAG="$(cat "${DIST_DIR}/IMAGE_TAG")"
  export IMAGE_TAG
  echo ">> Using IMAGE_TAG=${IMAGE_TAG} (from ${DIST_DIR}/IMAGE_TAG)"
fi

echo ">> Loading images from ${DIST_DIR}/ ..."
shopt -s nullglob
tarballs=( "${DIST_DIR}"/*.tar.gz )
if [ ${#tarballs[@]} -eq 0 ]; then
  echo "!! No *.tar.gz files in ${DIST_DIR}/." >&2
  exit 1
fi
for f in "${tarballs[@]}"; do
  echo "   - loading ${f}"
  gunzip -c "${f}" | docker load
done

# Seed the knowledge base into Postgres BEFORE the backend starts. The KB
# (RAG chunks, embeddings, rules, precedents) lives in Postgres, not in the
# mounted folders — a fresh volume is empty. restore-db.sh starts just postgres,
# loads the seed dump, and is idempotent (skips if the KB is already populated).
echo ">> Seeding knowledge base ..."
"${SCRIPT_DIR}/restore-db.sh"

echo ">> Starting stack with ${COMPOSE_FILE} ..."
docker compose -f "${COMPOSE_FILE}" up -d

echo
echo ">> Up. Check status with:"
echo "     docker compose -f ${COMPOSE_FILE} ps"
echo "     docker compose -f ${COMPOSE_FILE} logs -f backend"
echo ">> App:      http://localhost:3000"
echo ">> API docs: http://localhost:8000/docs"
