#!/usr/bin/env bash
# ===========================================================================
# up-shared.sh — boot the Compliance Agent against the SHARED platform stack.
#
# Brings up ONLY this agent's containers (compliance-backend + compliance-frontend)
# attached to the external `shared-network`. It does NOT start postgres / redis /
# nginx — those belong to the shared/ stack and must already be running
# (shared-postgres, shared-redis, shared-nginx).
#
# It also removes any leftover *bundled* containers from the standalone
# docker-compose.yml (compliance-postgres / compliance-redis / compliance-backup)
# so a stray run can't leave you talking to the wrong database.
#
# Usage:
#   sudo ./up-shared.sh              # build + (re)start the agent on shared infra
#   sudo ./up-shared.sh down         # stop & remove the agent containers
#   sudo ./up-shared.sh logs         # tail the backend logs
#   sudo ./up-shared.sh ps           # show the running stack
#   sudo ./up-shared.sh ingest       # re-embed the knowledge base
#   sudo ./up-shared.sh tmux         # open all container logs in a tmux split
#
# Env:
#   ENGINE=podman|docker             # container engine (default: podman)
#   NO_TMUX=1                        # skip the auto split-log view after `up`
# ===========================================================================
set -euo pipefail
cd "$(dirname "$0")"

COMPOSE_FILE="docker-compose.shared.yml"
ENGINE="${ENGINE:-podman}"

# Bundled containers owned by the standalone docker-compose.yml. They must NOT
# run in shared mode — the agent talks to shared-postgres / shared-redis.
BUNDLED=(compliance-postgres compliance-redis compliance-backup)

# Containers whose logs the split-screen view tails (only the running ones show).
LOG_CONTAINERS=(shared-postgres shared-redis shared-nginx compliance-backend compliance-frontend)

if [ "$ENGINE" = podman ]; then
  command -v podman-compose >/dev/null 2>&1 || { echo "ERROR: podman-compose not found (set ENGINE=docker?)"; exit 1; }
  compose() { podman-compose "$@"; }
  cli() { podman "$@"; }
else
  compose() { docker compose "$@"; }
  cli() { docker "$@"; }
fi

c_exists() { cli ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "$1"; }
c_running() { cli ps --format '{{.Names}}' 2>/dev/null | grep -qx "$1"; }

require_shared() {
  # The agent joins an EXTERNAL network created by the shared stack.
  if ! cli network exists shared-network 2>/dev/null \
     && ! cli network ls --format '{{.Name}}' 2>/dev/null | grep -qx shared-network; then
    echo "ERROR: network 'shared-network' not found — bring up the shared/ stack first."
    echo "       (it creates shared-postgres / shared-redis / shared-nginx + the network)"
    exit 1
  fi
  for c in shared-postgres shared-redis; do
    c_running "$c" || echo "WARN: '$c' is not running — the agent will fail to reach it."
  done
}

drop_bundled() {
  local found=()
  for c in "${BUNDLED[@]}"; do c_exists "$c" && found+=("$c"); done
  if [ "${#found[@]}" -gt 0 ]; then
    echo "Removing bundled standalone containers (not used in shared mode): ${found[*]}"
    cli rm -f "${found[@]}" >/dev/null 2>&1 || true
  fi
}

# Open a tiled tmux session with one live `logs -f` pane per running container.
open_log_tmux() {
  [ -n "${NO_TMUX:-}" ] && return 0
  if ! command -v tmux >/dev/null 2>&1; then
    echo "tmux not installed — skipping split-log view. Tail one with: $ENGINE logs -f compliance-backend"
    return 0
  fi
  [ -t 1 ] || { echo "(not a TTY — skipping tmux log view; run 'sudo ./up-shared.sh tmux' from a terminal)"; return 0; }

  local running=()
  local c
  for c in "${LOG_CONTAINERS[@]}"; do c_running "$c" && running+=("$c"); done
  [ "${#running[@]}" -gt 0 ] || { echo "No running containers to show logs for."; return 0; }

  local session="compliance-logs"
  tmux kill-session -t "$session" 2>/dev/null || true

  # First pane = first container; split one pane per remaining container, then tile.
  tmux new-session -d -s "$session" -x "$(tput cols 2>/dev/null || echo 200)" -y "$(tput lines 2>/dev/null || echo 50)" \
    "$ENGINE logs -f --tail 50 ${running[0]}"
  tmux select-pane -t "$session" -T "${running[0]}"
  local i
  for ((i = 1; i < ${#running[@]}; i++)); do
    tmux split-window -t "$session" "$ENGINE logs -f --tail 50 ${running[i]}"
    tmux select-pane -T "${running[i]}"
    tmux select-layout -t "$session" tiled >/dev/null
  done
  tmux set-option -t "$session" pane-border-status top >/dev/null 2>&1 || true
  tmux select-layout -t "$session" tiled >/dev/null

  echo "Opening logs in tmux ('$session') — detach: Ctrl-b d  ·  reopen: sudo ./up-shared.sh tmux"
  if [ -n "${TMUX:-}" ]; then
    tmux switch-client -t "$session"        # already inside tmux
  else
    tmux attach-session -t "$session"
  fi
}

cmd_up() {
  require_shared
  drop_bundled
  echo "Bringing up the agent on the shared stack (${COMPOSE_FILE})…"
  compose -f "$COMPOSE_FILE" up -d --build
  # nginx resolves upstreams lazily, but reload so it picks up freshly-(re)created
  # compliance-frontend / compliance-backend immediately.
  if c_running shared-nginx; then
    echo "Reloading shared-nginx…"
    cli exec shared-nginx nginx -s reload 2>/dev/null || true
  fi
  echo
  cmd_ps
  echo
  echo "Health check:"
  if cli exec compliance-backend sh -c 'command -v curl >/dev/null && curl -sf http://localhost:8000/health || true' 2>/dev/null | grep -qi healthy; then
    echo "  backend: healthy"
  else
    echo "  backend: not healthy yet — give migrations a few seconds, then:"
    echo "    curl http://localhost/compliance/api/health"
    echo "    $ENGINE logs compliance-backend"
  fi
  echo
  echo "App: http://<host>/compliance/   ·   API: http://<host>/compliance/api/health"
  echo "If embeddings changed, re-ingest the KB:  sudo ./up-shared.sh ingest"
  echo
  open_log_tmux
}

cmd_down() {
  echo "Stopping the agent (shared infra is left running)…"
  compose -f "$COMPOSE_FILE" down
}

cmd_ps() {
  echo "Stack:"
  cli ps --format '  {{.Names}}\t{{.Status}}' 2>/dev/null \
    | grep -E 'shared-|compliance-' || echo "  (nothing running)"
}

cmd_logs() { cli logs -f compliance-backend; }

cmd_ingest() {
  c_running compliance-backend || { echo "compliance-backend is not running — run 'up' first."; exit 1; }
  cli exec -it compliance-backend python -m scripts.ingest_knowledge_base
}

case "${1:-up}" in
  up)     cmd_up ;;
  down)   cmd_down ;;
  ps)     cmd_ps ;;
  logs)   cmd_logs ;;
  tmux)   open_log_tmux ;;
  ingest) cmd_ingest ;;
  *) echo "Usage: sudo ./up-shared.sh [up|down|ps|logs|tmux|ingest]"; exit 1 ;;
esac
