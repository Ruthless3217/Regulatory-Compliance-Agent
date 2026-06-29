#!/usr/bin/env bash
# =============================================================================
# /opt/shared/start-all.sh
#
# Boots the whole VM in the correct order:
#   1. Shared infra (postgres + redis + nginx)
#   2. Wait for shared-postgres to report healthy
#   3. Each registered app (in order)
#   4. Restart nginx so it (re)resolves the now-running app upstreams
#   5. Print the status of every container
#
# Run as root (rootful Podman):
#     sudo /opt/shared/start-all.sh
# =============================================================================
set -euo pipefail

SHARED_DIR="/opt/shared"

# Registered apps, in start order. Format: "<name>:<dir>:<compose-file>".
# The compose file is passed with -f so each app's local docker-compose.override
# (dev-only) is NOT auto-merged on the VM.
APPS=(
  "compliance:/opt/Regulatory-Compliance-Agent:docker-compose.shared.yml"
)

# Use sudo unless we are already root (Podman here is rootful).
if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="sudo"; fi
PODMAN="${SUDO} podman"
COMPOSE="${SUDO} podman-compose"

echo "==> [1/5] Starting shared infrastructure (postgres, redis, nginx) ..."
cd "${SHARED_DIR}"
${COMPOSE} up -d

echo "==> [2/5] Waiting for shared-postgres to become healthy ..."
for i in $(seq 1 30); do
  status="$(${PODMAN} inspect -f '{{.State.Health.Status}}' shared-postgres 2>/dev/null || echo 'starting')"
  if [ "${status}" = "healthy" ]; then
    echo "    shared-postgres is healthy."
    break
  fi
  echo "    ... still ${status} (attempt ${i}/30)"
  sleep 2
  if [ "${i}" -eq 30 ]; then
    echo "!!  shared-postgres did not become healthy in time." >&2
    exit 1
  fi
done

echo "==> [3/5] Starting registered apps ..."
for entry in "${APPS[@]}"; do
  name="${entry%%:*}"
  rest="${entry#*:}"
  dir="${rest%%:*}"
  file="${rest#*:}"
  if [ ! -f "${dir}/${file}" ]; then
    echo "!!  ${name}: no ${file} at ${dir} — skipping." >&2
    continue
  fi
  echo "    -> ${name} (${dir}/${file})"
  ( cd "${dir}" && ${COMPOSE} -f "${file}" up -d )
done

echo "==> [4/5] Restarting nginx so it resolves the app upstreams ..."
# nginx may have failed/looped at boot when the app containers did not yet
# exist; this guarantees a clean start now that they are up.
${PODMAN} restart shared-nginx

echo "==> [5/5] Container status:"
${PODMAN} ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'

echo
echo "Done. Verify with:"
echo "    curl http://localhost/health"
echo "    curl http://localhost/compliance/"
echo "    curl http://localhost/compliance/api/docs"
