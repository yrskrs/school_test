#!/usr/bin/env bash
# Shared helpers. This file is sourced by maintenance scripts.
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }
require_tools() {
  [[ "${READY_TIMEOUT:-90}" =~ ^[1-9][0-9]*$ ]] || fail "READY_TIMEOUT must be a positive number of seconds."
  command -v docker >/dev/null || fail "Docker is required."
  command -v python3 >/dev/null || fail "Python 3 is required for archive validation."
  docker compose version >/dev/null
  docker compose config --quiet
}
app_container() { docker compose ps -aq app; }
app_running() {
  local container running
  container="$(app_container)" || fail "Cannot inspect the app container."
  [[ -n "$container" ]] || return 1
  running="$(docker inspect --format '{{.State.Running}}' "$container")" || fail "Cannot inspect the app running state."
  [[ "$running" == true || "$running" == false ]] || fail "Unknown app running state."
  [[ "$running" == true ]]
}
wait_for_app() {
  local timeout="${READY_TIMEOUT:-90}" deadline
  [[ "$timeout" =~ ^[1-9][0-9]*$ ]] || fail "READY_TIMEOUT must be a positive number of seconds."
  deadline=$((SECONDS + timeout))
  while (( SECONDS < deadline )); do
    if docker compose exec -T app python -c 'import urllib.request; r = urllib.request.urlopen("http://127.0.0.1:8000/", timeout=3); assert r.status == 200' >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  printf 'Application readiness check failed. Inspect: docker compose logs --tail=100 app\n' >&2
  docker compose logs --no-color --tail=100 app >&2 || true
  return 1
}
wait_for_postgres() {
  local deadline=$((SECONDS + 90))
  while (( SECONDS < deadline )); do
    if docker compose exec -T postgres sh -c 'pg_isready -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-schooltest}"' >/dev/null 2>&1; then return 0; fi
    sleep 2
  done
  fail "PostgreSQL is not ready."
}
new_backup_path() {
  mkdir -p "$PROJECT_ROOT/backups"
  mktemp -d "$PROJECT_ROOT/backups/$(date +%Y-%m-%d_%H-%M-%S)-XXXXXX"
}
