#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
(($#)) || fail "Usage: scripts/restore.sh BACKUP_DIRECTORY [-y|--yes]"
BACKUP_DIR="$1"
shift
FORCE_CONFIRM=false
[[ "${RESTORE_FORCE:-0}" == 1 ]] && FORCE_CONFIRM=true
while (($#)); do
  case "$1" in -y|--yes) FORCE_CONFIRM=true; shift ;; *) fail "Unknown option: $1" ;; esac
done
[[ -d "$BACKUP_DIR" ]] || fail "Backup directory does not exist."
BACKUP_DIR="$(cd "$BACKUP_DIR" && pwd)"
require_tools
# Verify checksums, dump header, gzip and every archive path BEFORE touching DB.
python3 "$SCRIPT_DIR/backup_bundle.py" verify "$BACKUP_DIR"
if ! $FORCE_CONFIRM; then
  read -r -p "Overwrite database and files from $BACKUP_DIR? (y/n): " confirm
  [[ "$confirm" == y || "$confirm" == Y || "$confirm" == yes || "$confirm" == YES ]] || { printf 'Restore cancelled.\n'; exit 0; }
fi
docker compose up -d postgres
wait_for_postgres
docker compose exec -T postgres pg_restore --list < "$BACKUP_DIR/database.dump" >/dev/null
# Supports a fresh server, but requires the matching code/image to be prepared.
if [[ -z "$(app_container)" ]]; then docker compose create app; fi
SAFETY_DIR="$(new_backup_path)"
"$SCRIPT_DIR/backup.sh" --output "$SAFETY_DIR" --keep-stopped
SUCCESS=false
cleanup() {
  local status=$?
  trap - EXIT
  if ! $SUCCESS; then
    docker compose stop app || true
    printf 'Restore failed. App left stopped to avoid serving incomplete data.\nSafety backup: %s\n' "$SAFETY_DIR" >&2
  fi
  exit "$status"
}
trap cleanup EXIT
# backup.sh has stopped the app, including when it was running initially.
docker compose stop app
# psql variables quote identifiers/literals; .env is read by Compose, never eval.
docker compose exec -T postgres sh -c 'exec psql -X -v ON_ERROR_STOP=1 -U "${POSTGRES_USER:-postgres}" -d postgres --set=db_name="${POSTGRES_DB:-schooltest}" --set=db_owner="${POSTGRES_USER:-postgres}"' <<'SQL'
-- ON_ERROR_STOP aborts before DROP for a system database.
SELECT 1 / CASE WHEN :'db_name' IN ('postgres', 'template0', 'template1') THEN 0 ELSE 1 END;
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :'db_name' AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS :"db_name" WITH (FORCE);
CREATE DATABASE :"db_name" OWNER :"db_owner";
SQL
docker compose exec -T postgres sh -c 'exec pg_restore --exit-on-error --single-transaction -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-schooltest}"' < "$BACKUP_DIR/database.dump"
RESTORE_CODE="$(cat "$SCRIPT_DIR/backup_bundle.py")"
docker compose run --rm --no-deps -T app python -c "$RESTORE_CODE" restore-files < "$BACKUP_DIR/files.tar.gz"
docker compose up -d --no-build --no-deps app
wait_for_app
SUCCESS=true
printf 'Restore completed and HTTP readiness confirmed.\nSafety backup: %s\nCode/image was not rolled back; verify compatibility with the restored data.\n' "$SAFETY_DIR"
