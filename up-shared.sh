#!/usr/bin/env bash
# ===========================================================================
# up-shared.sh — build AND boot the Compliance Agent on the SHARED platform.
#
# One command takes a fresh `git pull` all the way to a verified running stack:
#
#   guard worktree -> build images -> smoke-test images -> recreate containers
#   -> reload nginx -> wait for health -> migrate + seed -> report
#
# It brings up ONLY this agent's containers (compliance-backend /
# compliance-frontend / compliance-backup) on the external `shared-network`.
# postgres / redis / nginx belong to the shared/ stack and must already run.
#
# WHY IT DOES THE WORK ITSELF instead of `compose up --build` (2026-07-29):
#   * podman-compose built the images but left the OLD containers running, so a
#     "successful" deploy ran six-day-old code. We now force-recreate.
#   * podman-compose tagged PARTIAL images as success. We now smoke-test every
#     image before a container is allowed to start.
#   * A platform scaffold had overwritten backend/Dockerfile + frontend/Dockerfile
#     with placeholder stubs; the build "worked" and produced an app with no
#     migrations, no seeds and no fact cards. We now refuse to build those.
#   * The same scaffold also truncates the DEPENDENCY MANIFESTS (2026-09-03:
#     backend/requirements.txt 77 lines -> 5). That build succeeds, still ships
#     the whole app tree, and passed the image smoke-test — then the container
#     died on `alembic upgrade head` with ModuleNotFoundError. We now marker-check
#     the manifests, and import the boot path out of the built image.
#   * And it truncates APP SOURCE. 2026-09-03: backend/app/main.py 220 lines -> 28,
#     with all 14 include_router calls gone. That image builds, installs, boots,
#     and answers /health 200 — while every real route 404s, so nobody can log in.
#     Nothing above catches it, because the file it stubs is not a build input.
#     Hence: a marker check on main.py, a dirty check over the whole backend/ and
#     frontend/ trees, and a route-registration check inside the built image.
#   * The old health probe shelled out to curl, which the backend image does not
#     install — so it ALWAYS printed "not healthy yet". We now probe with python.
#   * rules.product_line only lands via `scripts.seed_rules`; skipping it silently
#     disables product-aware retrieval. Seeding is now part of the deploy.
#
# Usage:
#   sudo bash up-shared.sh            # full deploy (build, verify, up, migrate, seed)
#   sudo bash up-shared.sh build      # build + smoke-test the images only
#   sudo bash up-shared.sh verify     # check what is CURRENTLY running
#   sudo bash up-shared.sh seed       # re-run scripts.seed_rules
#   sudo bash up-shared.sh ingest     # re-embed the knowledge base
#   sudo bash up-shared.sh down|ps|logs|tmux
#
# Env:
#   ENGINE=podman|docker      container engine (default: podman)
#   NO_CACHE=1                build images from scratch
#   NO_SEED=1                 skip scripts.seed_rules after boot
#   ALLOW_DIRTY=1             build even with local edits to build inputs
#   NO_TMUX=1                 skip the split-log view after `up`
#   HEALTH_TRIES=45           health poll attempts (2s apart)
# ===========================================================================
set -euo pipefail
cd "$(dirname "$0")"

COMPOSE_FILE="docker-compose.shared.yml"
ENGINE="${ENGINE:-podman}"

# MUST match the `image:` keys in docker-compose.shared.yml. We build these tags
# directly; compose runs them by name (pull_policy: never).
BACKEND_IMAGE="localhost/compliance-backend:latest"
FRONTEND_IMAGE="localhost/compliance-frontend:latest"

# Baked into the Next.js standalone server at BUILD time. KEEP IN SYNC with the
# frontend build.args block in docker-compose.shared.yml — behind shared-nginx
# the app lives under /compliance, and a wrong base path 404s every asset.
FE_BASE_PATH="/compliance"
FE_API_BASE="/compliance/api"
FE_INTERNAL_API="http://compliance-backend:8000"

# Containers this compose project owns — always force-recreated so a rebuilt
# image can never be left unused behind a still-running old container.
APP_CONTAINERS=(compliance-backend compliance-frontend compliance-backup)

# Containers owned by the standalone docker-compose.yml. They must NOT run in
# shared mode — the agent talks to shared-postgres / shared-redis.
BUNDLED=(compliance-postgres compliance-redis)

LOG_CONTAINERS=(shared-postgres shared-redis shared-nginx compliance-backend compliance-frontend)

if [ "$ENGINE" = podman ]; then
  command -v podman-compose >/dev/null 2>&1 || { echo "ERROR: podman-compose not found (set ENGINE=docker?)"; exit 1; }
  compose() { podman-compose "$@"; }
  cli() { podman "$@"; }
else
  compose() { docker compose "$@"; }
  cli() { docker "$@"; }
fi

c_exists()  { cli ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "$1"; }
c_running() { cli ps    --format '{{.Names}}' 2>/dev/null | grep -qx "$1"; }
die()       { echo "ERROR: $*" >&2; exit 1; }

require_shared() {
  if ! cli network exists shared-network 2>/dev/null \
     && ! cli network ls --format '{{.Name}}' 2>/dev/null | grep -qx shared-network; then
    die "network 'shared-network' not found — bring up the shared/ stack first
       (it creates shared-postgres / shared-redis / shared-nginx + the network)"
  fi
  for c in shared-postgres shared-redis; do
    c_running "$c" || echo "WARN: '$c' is not running — the agent will fail to reach it."
  done
}

# --- Guard: are we about to build the REAL Dockerfiles? --------------------
# A platform scaffold once replaced both Dockerfiles with placeholder stubs
# (backend: `COPY app ./app` only, no alembic/scripts/data, no appuser;
# frontend: node:20-alpine + `npm start`). Both built cleanly and produced an
# unusable app. These markers are the load-bearing lines of the real files.
guard_worktree() {
  grep -q 'useradd' backend/Dockerfile \
    || die "backend/Dockerfile has no 'useradd' — this is not the real Dockerfile.
       docker-compose.shared.yml starts the app as appuser (uid 10001).
       Restore it:  git checkout -- backend/Dockerfile"
  grep -q '^COPY \. \.' backend/Dockerfile \
    || die "backend/Dockerfile never copies the app tree — alembic/, scripts/ and
       data/ would be missing from the image.  git checkout -- backend/Dockerfile"
  grep -q '\.next/standalone' frontend/Dockerfile \
    || die "frontend/Dockerfile is not the Next.js standalone build.
       Restore it:  git checkout -- frontend/Dockerfile"

  # The manifests get stubbed the same way, and a stubbed manifest is worse than
  # a stubbed Dockerfile: pip/npm install the few packages that are left, COPY . .
  # still ships alembic/, scripts/ and data/, and verify_images passes. Nothing
  # complains until uvicorn cannot import its own framework. Marker-check the
  # packages the app provably cannot boot without. Deliberately NOT gated on
  # ALLOW_DIRTY — that flag is for intentional edits, and a stub is never one.
  local pkg dep
  for pkg in fastapi uvicorn sqlalchemy alembic langgraph openai; do
    grep -qi "^${pkg}[]=<>[]" backend/requirements.txt \
      || die "backend/requirements.txt does not pin '$pkg' — that is a truncated
       dependency manifest ($(wc -l < backend/requirements.txt) lines), not the real one.
       Restore it:  git checkout -- backend/requirements.txt"
  done
  for dep in '"next"' '"react"' '"lexical"' '"recharts"'; do
    grep -q "$dep" frontend/package.json \
      || die "frontend/package.json does not depend on $dep — truncated manifest.
       Restore it:  git checkout -- frontend/package.json"
  done

  # The stub main.py keeps `app = FastAPI(...)` and a /health route, so the
  # container boots and looks healthy while serving no API at all. The routers
  # are the load-bearing part.
  local routers
  routers="$(grep -c 'include_router' backend/app/main.py || true)"
  if [ "${routers:-0}" -lt 5 ]; then
    die "backend/app/main.py registers only ${routers:-0} routers ($(wc -l < backend/app/main.py) lines).
       That is the scaffold stub: it boots, answers /health, and 404s every real
       route, so nobody can log in.  Restore it:  git checkout -- backend/app/main.py"
  fi

  if command -v git >/dev/null 2>&1 && [ -d .git ]; then
    local dirty
    # The whole app tree, not a hand-listed handful: the 2026-09-03 stub landed in
    # backend/app/main.py, which was not on the old list and so sailed through.
    # --untracked-files=no keeps runtime junk (uploads/, logs/, .env) out of it.
    dirty="$(git status --porcelain --untracked-files=no -- backend frontend \
             "$COMPOSE_FILE" 2>/dev/null || true)"
    if [ -n "$dirty" ] && [ -z "${ALLOW_DIRTY:-}" ]; then
      echo "ERROR: build inputs have uncommitted local edits:" >&2
      echo "$dirty" >&2
      die "production would not match git. Save them (git diff > /opt/local-edits.patch)
       then 'git checkout -- <file>', or re-run with ALLOW_DIRTY=1 if intentional."
    fi
  fi
}

fix_mounts() {
  # appuser inside the image is UID 10001 (backend/Dockerfile). On a fresh
  # checkout these bind mounts are root-owned → PermissionError on upload.
  mkdir -p backend/uploads backend/logs backend/backups
  chown -R 10001:10001 backend/uploads backend/logs 2>/dev/null || true
}

build_images() {
  local args=()
  [ -n "${NO_CACHE:-}" ] && args+=(--no-cache)

  echo "==> Building backend image ($BACKEND_IMAGE)…"
  cli build "${args[@]+"${args[@]}"}" -t "$BACKEND_IMAGE" -f backend/Dockerfile backend \
    || die "backend image build failed (see the STEP that died above)"

  echo "==> Building frontend image ($FRONTEND_IMAGE)…"
  cli build "${args[@]+"${args[@]}"}" -t "$FRONTEND_IMAGE" -f frontend/Dockerfile frontend \
    --build-arg "NEXT_PUBLIC_BASE_PATH=$FE_BASE_PATH" \
    --build-arg "NEXT_PUBLIC_API_BASE=$FE_API_BASE" \
    --build-arg "INTERNAL_API_BASE=$FE_INTERNAL_API" \
    || die "frontend image build failed (see the STEP that died above)"
}

# --- Smoke-test the images BEFORE any container starts ---------------------
# A tagged image is not a working image: partial builds have been tagged as
# success. Every check below maps to a failure that actually reached this VM.
verify_images() {
  echo "==> Smoke-testing images…"

  cli run --rm "$BACKEND_IMAGE" sh -c '
    set -e
    id -u appuser >/dev/null            # compose runs uvicorn as appuser
    ls alembic/versions/*.py >/dev/null  # migrations must ship in the image
    ls scripts/seeds/*.yaml  >/dev/null  # seed_rules reads these
    ls data/product_fact_cards/*.json >/dev/null
    ls data/disclaimers/*.json        >/dev/null
    # The CMD is "alembic upgrade head && uvicorn app.main:app". With a truncated
    # requirements.txt every check above still passes and the container dies
    # seconds after start, so import the boot path here instead.
    python -c "import fastapi, uvicorn, sqlalchemy, alembic, langgraph, openai"
    # Last line of defence: whatever stubbed main.py, the shipped file must still
    # mount the routers. Checked as text, not by importing app.main, so it cannot
    # fail merely because this throwaway container has no DATABASE_URL.
    test "$(grep -c include_router app/main.py)" -ge 5
  ' || die "backend image is incomplete — missing appuser, migrations, seeds, data/
       or its core Python dependencies. That is a partial/stub build, not a
       deployable image."

  cli run --rm "$FRONTEND_IMAGE" sh -c 'test -f server.js && test -d .next' \
    || die "frontend image has no standalone server.js — the Next.js build stage
       did not run (placeholder Dockerfile, or 'npm run build' failed)."

  echo "    backend:  appuser + migrations + seeds + data  OK"
  echo "    frontend: standalone server.js + .next          OK"
}

# --- Recreate, never reuse -------------------------------------------------
# podman-compose leaves a running container alone even when its image changed,
# which silently keeps stale code in production. Remove first, then up.
recreate() {
  local found=()
  for c in "${APP_CONTAINERS[@]}"; do c_exists "$c" && found+=("$c"); done
  if [ "${#found[@]}" -gt 0 ]; then
    echo "==> Removing existing app containers so the new images take effect: ${found[*]}"
    cli rm -f "${found[@]}" >/dev/null 2>&1 || true
  fi

  echo "==> Starting the agent on the shared stack ($COMPOSE_FILE)…"
  # Stamp the running frontend with the deployed commit (NEXT_PUBLIC_BUILD_SHA).
  # Must be exported, not prefixed onto the call — `VAR=x func` does not reliably
  # reach commands run *inside* a shell function.
  export BUILD_SHA="${BUILD_SHA:-$(git rev-parse --short HEAD 2>/dev/null || echo prod)}"
  # No --build: the images are already built and verified above.
  compose -f "$COMPOSE_FILE" up -d

  if c_running shared-nginx; then
    echo "==> Reloading shared-nginx…"
    cli exec shared-nginx nginx -s reload 2>/dev/null || true
  fi
}

drop_bundled() {
  local found=()
  for c in "${BUNDLED[@]}"; do c_exists "$c" && found+=("$c"); done
  if [ "${#found[@]}" -gt 0 ]; then
    echo "==> Removing bundled standalone containers (not used in shared mode): ${found[*]}"
    cli rm -f "${found[@]}" >/dev/null 2>&1 || true
  fi
}

# --- Health: probe with python, NOT curl -----------------------------------
# The backend image installs no curl, so the old `command -v curl && curl -sf`
# probe could never succeed and always reported failure. Probe GET / (cheap);
# /health additionally round-trips to Azure for llm_available and can be slow.
wait_healthy() {
  local tries="${HEALTH_TRIES:-45}" i=0
  echo "==> Waiting for backend (alembic upgrade head, then uvicorn)…"
  while [ "$i" -lt "$tries" ]; do
    if ! c_running compliance-backend; then
      # "Not running" is not the same as "failed". podman-compose returns before
      # the container is listed, and the CMD then spends its first seconds in
      # `alembic upgrade head`. Bailing on the first poll reported a healthy
      # deploy as "the stack is NOT deployed" (2026-09-03). Only a container that
      # has actually exited is terminal; anything else just needs another 2s.
      if c_exists compliance-backend \
         && [ "$(cli inspect compliance-backend --format '{{.State.Status}}' 2>/dev/null)" = exited ]; then
        echo "    backend container exited. Last 40 log lines:"
        cli logs --tail 40 compliance-backend 2>&1 | sed 's/^/      /' || true
        return 1
      fi
      i=$((i + 1)); sleep 2; continue
    fi
    if cli exec compliance-backend python -c \
         'import urllib.request as u; u.urlopen("http://localhost:8000/", timeout=5)' \
         >/dev/null 2>&1; then
      echo "    backend: serving on :8000"
      return 0
    fi
    i=$((i + 1)); sleep 2
  done
  echo "    backend did not answer within $((tries * 2))s. Last 40 log lines:"
  cli logs --tail 40 compliance-backend 2>&1 | sed 's/^/      /' || true
  return 1
}

cmd_seed() {
  c_running compliance-backend || die "compliance-backend is not running — run 'up' first."
  echo "==> Seeding rules (idempotent; backfills rules.product_line)…"
  cli exec compliance-backend python -m scripts.seed_rules
}

# --- Report what is actually deployed --------------------------------------
# Evidence, not assumptions: schema head, the product_line tags that make
# product-aware retrieval work, and the commit the images were built from.
psql_report() {
  local label="$1" sql="$2" out
  echo "--- $label ---"
  out="$(cli exec shared-postgres psql -U postgres -d compliance_db -tA -F' | ' -c "$sql" 2>/dev/null || true)"
  if [ -n "$out" ]; then echo "$out" | sed 's/^/    /'; else echo "    (query returned nothing / shared-postgres unreachable)"; fi
}

report() {
  echo
  echo "==> Deployed state"
  echo "--- alembic ---"
  cli exec compliance-backend alembic current 2>&1 | grep -v '^INFO' | sed 's/^/    /' || true
  psql_report "rules.product_line ((global) = untagged, applies to every product)" \
    "SELECT COALESCE(product_line,'(global)'), count(*) FROM rules
      WHERE is_active GROUP BY 1 ORDER BY 2 DESC;"
  psql_report "precedent_cases.product_category" \
    "SELECT COALESCE(product_category,'(untagged)'), count(*) FROM precedent_cases
      GROUP BY 1 ORDER BY 2 DESC LIMIT 8;"
  echo
  echo "  commit: $(git rev-parse --short HEAD 2>/dev/null || echo '?')"
  echo "  App: http://<host>/compliance/   ·   API: http://<host>/compliance/api/health"
}

cmd_up() {
  require_shared
  guard_worktree
  drop_bundled
  fix_mounts
  build_images
  verify_images
  recreate
  if ! wait_healthy; then
    die "backend did not come up — the stack is NOT deployed. Logs are above."
  fi
  # Seeding must not tear down an otherwise healthy deploy — warn and continue.
  if [ -z "${NO_SEED:-}" ]; then
    cmd_seed || echo "WARN: seeding failed — retry with: sudo bash up-shared.sh seed"
  fi
  cmd_ps
  report
  open_log_tmux
}

cmd_build()  { guard_worktree; build_images; verify_images; }

cmd_verify() {
  c_running compliance-backend || die "compliance-backend is not running."
  echo "==> Verifying the RUNNING container (not just the image)…"
  cli exec compliance-backend sh -c '
    id -u appuser >/dev/null && echo "    appuser: present"
    ls alembic/versions/*.py | tail -1 | sed "s|^|    latest migration: |"
    echo "    fact cards: $(ls data/product_fact_cards/*.json | wc -l)"
    echo "    seed files: $(ls scripts/seeds/*.yaml | wc -l)"
  '
  report
}

cmd_down()   { echo "Stopping the agent (shared infra left running)…"; compose -f "$COMPOSE_FILE" down; }
cmd_ps()     { echo; echo "Stack:"; cli ps --format '  {{.Names}}\t{{.Status}}\t{{.Image}}' 2>/dev/null | grep -E 'shared-|compliance-' || echo "  (nothing running)"; }
cmd_logs()   { cli logs -f compliance-backend; }
cmd_ingest() {
  c_running compliance-backend || die "compliance-backend is not running — run 'up' first."
  cli exec -it compliance-backend python -m scripts.ingest_knowledge_base
}

# Tiled tmux session with one live `logs -f` pane per running container, plus a
# final pane running `<engine> stats` for live CPU/mem/net/IO usage.
open_log_tmux() {
  [ -n "${NO_TMUX:-}" ] && return 0
  if ! command -v tmux >/dev/null 2>&1; then
    echo "tmux not installed — tail one with: $ENGINE logs -f compliance-backend"
    return 0
  fi
  [ -t 1 ] || { echo "(not a TTY — run 'sudo bash up-shared.sh tmux' from a terminal)"; return 0; }

  local running=() c
  for c in "${LOG_CONTAINERS[@]}"; do c_running "$c" && running+=("$c"); done
  [ "${#running[@]}" -gt 0 ] || { echo "No running containers to show logs for."; return 0; }

  local session="compliance-logs"
  tmux kill-session -t "$session" 2>/dev/null || true
  tmux new-session -d -s "$session" -x "$(tput cols 2>/dev/null || echo 200)" -y "$(tput lines 2>/dev/null || echo 50)" \
    "$ENGINE logs -f --tail 50 ${running[0]}"
  tmux select-pane -t "$session" -T "${running[0]}"
  local i
  for ((i = 1; i < ${#running[@]}; i++)); do
    tmux split-window -t "$session" "$ENGINE logs -f --tail 50 ${running[i]}"
    tmux select-pane -T "${running[i]}"
    tmux select-layout -t "$session" tiled >/dev/null
  done
  tmux split-window -t "$session" "$ENGINE stats ${running[*]}"
  tmux select-pane -T "stats"
  tmux set-option -t "$session" pane-border-status top >/dev/null 2>&1 || true
  tmux select-layout -t "$session" tiled >/dev/null

  echo "Opening logs in tmux ('$session') — detach: Ctrl-b d  ·  reopen: sudo bash up-shared.sh tmux"
  if [ -n "${TMUX:-}" ]; then tmux switch-client -t "$session"; else tmux attach-session -t "$session"; fi
}

case "${1:-up}" in
  up)     cmd_up ;;
  build)  cmd_build ;;
  verify) cmd_verify ;;
  seed)   cmd_seed ;;
  down)   cmd_down ;;
  ps)     cmd_ps ;;
  logs)   cmd_logs ;;
  tmux)   open_log_tmux ;;
  ingest) cmd_ingest ;;
  *) echo "Usage: sudo bash up-shared.sh [up|build|verify|seed|ingest|down|ps|logs|tmux]"; exit 1 ;;
esac
