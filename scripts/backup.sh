#!/usr/bin/env bash
# A consistent PostgreSQL + files snapshot; no shell evaluation of .env.
set -Eeuo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
OUTPUT=""
KEEP_STOPPED=false
while (($#)); do
  case "$1" in
    --output) (($# >= 2)) || fail "--output requires a directory"; OUTPUT="$2"; shift 2 ;;
    --keep-stopped) KEEP_STOPPED=true; shift ;;
    *) fail "Usage: scripts/backup.sh [--output DIRECTORY] [--keep-stopped]" ;;
  esac
done
require_tools
CONTAINER="$(app_container)"
[[ -n "$CONTAINER" ]] || fail "Create the app container before backing up."
WAS_RUNNING=false
app_running && WAS_RUNNING=true
STOPPED=false
COMPLETE=false
cleanup() {
  local status=$?
  trap - EXIT
  if $STOPPED && $WAS_RUNNING && { ! $KEEP_STOPPED || ! $COMPLETE; }; then
    docker compose start app || status=1
  fi
  if ((status != 0)); then printf 'Backup failed; do not use the incomplete directory: %s\n' "$OUTPUT" >&2; fi
  exit "$status"
}
trap cleanup EXIT
if [[ -z "$OUTPUT" ]]; then OUTPUT="$(new_backup_path)"; else mkdir -p "$OUTPUT"; fi
[[ -z "$(ls -A "$OUTPUT")" ]] || fail "Backup directory must be empty."
OUTPUT="$(cd "$OUTPUT" && pwd)"
if $WAS_RUNNING; then
  STOPPED=true
  docker compose stop app
fi
printf 'Creating consistent backup: %s\n' "$OUTPUT"
docker compose exec -T postgres sh -c 'exec pg_dump -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-schooltest}" -F c' > "$OUTPUT/database.dump"
docker compose cp app:/app/data "$OUTPUT/data"
docker compose cp app:/app/app/static/tests "$OUTPUT/tests"
docker compose cp app:/app/app/static/uploads "$OUTPUT/uploads"
tar -czf "$OUTPUT/files.tar.gz" -C "$OUTPUT" data tests uploads
COMMIT="$(git rev-parse HEAD 2>/dev/null || printf unknown)"
IMAGE="$(docker inspect --format '{{.Image}}' "$CONTAINER")"
python3 "$SCRIPT_DIR/backup_bundle.py" create "$OUTPUT" --commit "$COMMIT" --image "$IMAGE"
python3 "$SCRIPT_DIR/backup_bundle.py" verify "$OUTPUT"
rm -rf -- "$OUTPUT/data" "$OUTPUT/tests" "$OUTPUT/uploads"
COMPLETE=true
printf 'Backup completed: %s\n' "$OUTPUT"
