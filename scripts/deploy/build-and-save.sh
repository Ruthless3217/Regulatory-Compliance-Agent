#!/usr/bin/env bash
# =============================================================================
# build-and-save.sh — run this on a machine WITH internet (your dev laptop/CI).
#
# Builds the two app images, pulls the three infra images, and saves all FIVE
# as gzipped tarballs into dist/. Copy that dist/ folder to the target machine
# (RHEL 9 VM or a friend's laptop) and run scripts/deploy/load-and-up.sh there.
#
# The target machine never builds and never contacts a registry — this is why
# it works air-gapped AND fixes "build breaks on my friend's laptop".
#
# Usage:
#   ./scripts/deploy/build-and-save.sh
#   IMAGE_TAG=v2 FRONTEND_API_BASE=http://10.0.0.5:8000 ./scripts/deploy/build-and-save.sh
#
# Env vars:
#   IMAGE_TAG           image version tag         (default: v1)
#   FRONTEND_API_BASE   browser-facing API base baked into the frontend
#                       (default: http://localhost:8000 — correct when the user
#                        opens the app on the SAME machine that runs it. For a
#                        VM accessed remotely, set http://<vm-host-or-ip>:8000)
#   DIST_DIR            output folder             (default: dist)
# =============================================================================
set -euo pipefail

# Resolve repo root from this script's location so it runs from anywhere.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

IMAGE_TAG="${IMAGE_TAG:-v1}"
FRONTEND_API_BASE="${FRONTEND_API_BASE:-http://localhost:8000}"
DIST_DIR="${DIST_DIR:-dist}"

BACKEND_IMAGE="compliance-backend:${IMAGE_TAG}"
FRONTEND_IMAGE="compliance-frontend:${IMAGE_TAG}"
# Infra images — pinned to the same tags docker-compose.prod.yml expects.
PG_IMAGE="pgvector/pgvector:pg15"
REDIS_IMAGE="redis:7-alpine"
PG_BACKUP_IMAGE="postgres:15-alpine"

echo ">> Repo root      : ${REPO_ROOT}"
echo ">> Image tag      : ${IMAGE_TAG}"
echo ">> Frontend API   : ${FRONTEND_API_BASE}"
echo ">> Output dir     : ${DIST_DIR}"
echo

mkdir -p "${DIST_DIR}"

# Behind SSL-inspecting corporate proxies, BuildKit's base-image metadata HEAD
# to Docker Hub can intermittently time out even when every layer is cached.
# Retry a few times before giving up.
build_with_retry() {
  local attempt
  for attempt in 1 2 3; do
    if docker build "$@"; then
      return 0
    fi
    echo "   .. build failed (attempt ${attempt}/3); retrying in 8s" >&2
    sleep 8
  done
  return 1
}

# SKIP_BUILD=1 reuses images already present locally (e.g. a previous run built
# them and only the network-dependent steps failed). Saves you from fighting a
# flaky registry just to repackage unchanged images.
if [ "${SKIP_BUILD:-0}" = "1" ]; then
  echo ">> [1-2/5] SKIP_BUILD=1 — using existing local images, skipping builds."
  docker image inspect "${BACKEND_IMAGE}" >/dev/null  || { echo "!! ${BACKEND_IMAGE} not found locally; unset SKIP_BUILD to build it." >&2; exit 1; }
  docker image inspect "${FRONTEND_IMAGE}" >/dev/null || { echo "!! ${FRONTEND_IMAGE} not found locally; unset SKIP_BUILD to build it." >&2; exit 1; }
else
  echo ">> [1/5] Building backend image (${BACKEND_IMAGE}) ..."
  build_with_retry -t "${BACKEND_IMAGE}" ./backend

  echo ">> [2/5] Building frontend image (${FRONTEND_IMAGE}) ..."
  build_with_retry -t "${FRONTEND_IMAGE}" \
    --build-arg "NEXT_PUBLIC_API_BASE=${FRONTEND_API_BASE}" \
    --build-arg "INTERNAL_API_BASE=http://backend:8000" \
    ./frontend
fi

# Pull with retries; tolerate a flaky registry (Docker Hub EOF behind the
# corporate proxy is common) as long as the image is already present locally.
pull_with_retry() {
  local image="$1" attempt
  for attempt in 1 2 3; do
    if docker pull "${image}"; then
      return 0
    fi
    echo "   .. pull of ${image} failed (attempt ${attempt}/3); retrying in 5s"
    sleep 5
  done
  if docker image inspect "${image}" >/dev/null 2>&1; then
    echo "   .. registry unreachable but ${image} is already present locally — using it."
    return 0
  fi
  echo "!! Could not pull ${image} and it is not present locally." >&2
  return 1
}

echo ">> [3/5] Pulling infra images ..."
pull_with_retry "${PG_IMAGE}"
pull_with_retry "${REDIS_IMAGE}"
pull_with_retry "${PG_BACKUP_IMAGE}"

echo ">> [4/5] Saving images to ${DIST_DIR}/ (gzipped) ..."
save() {
  local image="$1" outfile="$2"
  echo "   - ${image} -> ${outfile}"
  docker save "${image}" | gzip > "${DIST_DIR}/${outfile}"
}
save "${BACKEND_IMAGE}"   "compliance-backend-${IMAGE_TAG}.tar.gz"
save "${FRONTEND_IMAGE}"  "compliance-frontend-${IMAGE_TAG}.tar.gz"
save "${PG_IMAGE}"        "pgvector-pg15.tar.gz"
save "${REDIS_IMAGE}"     "redis-7-alpine.tar.gz"
save "${PG_BACKUP_IMAGE}" "postgres-15-alpine.tar.gz"

# Record the tag so load-and-up.sh knows which version to run.
echo "${IMAGE_TAG}" > "${DIST_DIR}/IMAGE_TAG"

echo ">> [5/5] Done. Contents of ${DIST_DIR}/:"
ls -lh "${DIST_DIR}/"
echo
echo ">> Next: copy these to the target machine alongside the repo, then run:"
echo "     ./scripts/deploy/load-and-up.sh"
