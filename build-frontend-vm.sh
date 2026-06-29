#!/usr/bin/env bash
# ===========================================================================
# build-frontend-vm.sh — rebuild the frontend image with the /compliance base
# path baked in, then recreate the container from it.
#
# podman-compose's build-arg handling has been unreliable, and long one-line
# `podman build` commands get mangled when pasted into the VM terminal (the
# wrapped lines become real newlines and run as separate commands). Running
# this committed script avoids both problems — the command you type is short:
#
#     sudo ./build-frontend-vm.sh
#
# Behind shared-nginx the app lives under /compliance, so Next.js must bake the
# base path at BUILD time (assets become /compliance/_next/...). Without it the
# browser requests /_next/... → nginx 404 (HTML) → "Unexpected token '<'".
# ===========================================================================
set -euo pipefail
cd "$(dirname "$0")"

ENGINE="${ENGINE:-podman}"

echo "Building localhost/compliance-frontend:latest with NEXT_PUBLIC_BASE_PATH=/compliance ..."
"$ENGINE" build --no-cache \
  --build-arg NEXT_PUBLIC_BASE_PATH=/compliance \
  --build-arg NEXT_PUBLIC_API_BASE=/compliance/api \
  --build-arg INTERNAL_API_BASE=http://compliance-backend:8000 \
  -t localhost/compliance-frontend:latest ./frontend

echo "Recreating the frontend container from the freshly built image ..."
"$ENGINE" rm -f compliance-frontend >/dev/null 2>&1 || true
podman-compose -f docker-compose.shared.yml up -d frontend

if "$ENGINE" ps --format '{{.Names}}' 2>/dev/null | grep -qx shared-nginx; then
  echo "Reloading shared-nginx ..."
  "$ENGINE" exec shared-nginx nginx -s reload >/dev/null 2>&1 || true
fi

echo "----"
echo "Baked-in env (expect NEXT_PUBLIC_BASE_PATH=/compliance):"
"$ENGINE" run --rm localhost/compliance-frontend:latest env | grep NEXT_PUBLIC || true
echo "----"
echo "Served-page base-path check (expect a number > 0):"
curl -s http://localhost/compliance | grep -c "/compliance/_next" || true
