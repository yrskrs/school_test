#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
# Parse the whole update body before Git can replace this script on disk.
update_main() {
  require_tools
  command -v git >/dev/null || fail "Git is required."
  [[ "$(git rev-parse --show-toplevel)" == "$PROJECT_ROOT" ]] || fail "Run update from the project checkout."
  [[ "$(git branch --show-current)" == main ]] || fail "Update requires the main branch."
  [[ -z "$(git status --porcelain)" ]] || fail "Commit or stash local changes before updating."
  # Fetch and reject divergence before stopping the application.
  python3 "$SCRIPT_DIR/fetch_update.py" --branch main
  git merge-base --is-ancestor HEAD origin/main || fail "Local main has diverged; resolve it before updating."
  CONTAINER="$(app_container)"
  [[ -n "$CONTAINER" ]] || fail "App container is missing; use initial deployment instead (docker compose up -d --build)."
  WAS_RUNNING=false
  app_running && WAS_RUNNING=true
  BACKUP_DIR="$(new_backup_path)"
  OLD_IMAGE="$(docker inspect --format '{{.Image}}' "$CONTAINER")"
  ROLLBACK_TAG="schooltest-rollback:$(basename "$BACKUP_DIR")"
  docker image tag "$OLD_IMAGE" "$ROLLBACK_TAG"
  DEPLOY_STARTED=false
  SUCCESS=false
  cleanup() {
    local status=$?
    trap - EXIT
    if ! $SUCCESS; then
      if $DEPLOY_STARTED; then
        docker compose stop app || true
        printf 'Update failed after deployment started. App left stopped. Safety backup: %s\nPrevious image: %s\n' "$BACKUP_DIR" "$ROLLBACK_TAG" >&2
      elif $WAS_RUNNING; then
        docker start "$CONTAINER" || status=1
      fi
    fi
    exit "$status"
  }
  trap cleanup EXIT
  "$SCRIPT_DIR/backup.sh" --output "$BACKUP_DIR" --keep-stopped
  git merge --ff-only origin/main
  python3 "$SCRIPT_DIR/select_release_image.py" schooltest5
  export VCS_REF="$(git rev-parse HEAD)"
  docker compose config --quiet
  docker compose build app
  # Preserve uploads from older containers when introducing the uploads volume.
  RESTORE_CODE="$(cat "$SCRIPT_DIR/backup_bundle.py")"
  docker compose run --rm --no-deps -T app python -c "$RESTORE_CODE" restore-uploads < "$BACKUP_DIR/files.tar.gz"
  DEPLOY_STARTED=true
  docker compose up -d --no-deps app
  wait_for_app
  python3 "$SCRIPT_DIR/configure_integration_network.py"
  SUCCESS=true
  printf 'Update completed and HTTP readiness confirmed.\nBackup: %s\nPrevious image: %s\n' "$BACKUP_DIR" "$ROLLBACK_TAG"

}
update_main; exit $?
