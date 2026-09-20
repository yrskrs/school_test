#!/bin/bash

# Exit on error
set -e

# Always run from project root directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

# Check if backup path is provided
if [ -z "$1" ]; then
  echo "Usage: ./scripts/restore.sh <path_to_backup_directory> [-y|--yes]"
  echo "Example: ./scripts/restore.sh backups/2026-08-31_21-00-00"
  exit 1
fi

BACKUP_DIR=$1
FORCE_CONFIRM=false

if [ "$2" = "-y" ] || [ "$2" = "--yes" ] || [ "$RESTORE_FORCE" = "1" ]; then
  FORCE_CONFIRM=true
fi

if [ ! -d "$BACKUP_DIR" ]; then
  echo "Error: Directory $BACKUP_DIR does not exist."
  exit 1
fi

if [ ! -f "$BACKUP_DIR/database.dump" ]; then
  echo "Error: $BACKUP_DIR/database.dump not found."
  exit 1
fi

if [ ! -f "$BACKUP_DIR/files.tar.gz" ]; then
  echo "Error: $BACKUP_DIR/files.tar.gz not found."
  exit 1
fi

# Load environment variables
if [ -f .env ]; then
  set -a
  eval "$(grep -v '^#' .env | grep -v '^\s*$' | sed 's/^/export /')" 2>/dev/null || true
  set +a
fi

POSTGRES_DB=${POSTGRES_DB:-schooltest}
POSTGRES_USER=${POSTGRES_USER:-appuser}

if [ "$FORCE_CONFIRM" != "true" ]; then
  read -p "WARNING: This will overwrite the current database and files. Are you sure? (y/n): " confirm
  if [[ $confirm != [yY] && $confirm != [yY][eE][sS] ]]; then
    echo "Restore cancelled."
    exit 0
  fi
fi

echo "Creating an automatic backup of the current state before restore..."
./scripts/backup.sh || echo "Warning: Automatic backup failed. Continuing with restore anyway..."

echo "Stopping application container to avoid database conflicts..."
docker compose stop app

echo "Restoring PostgreSQL database..."
# Terminate existing connections to database so DROP DATABASE will not fail
docker compose exec -T postgres psql -U "$POSTGRES_USER" -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$POSTGRES_DB' AND pid <> pg_backend_pid();" 2>/dev/null || true

# Drop and recreate the database
docker compose exec -T postgres psql -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE IF EXISTS \"$POSTGRES_DB\" WITH (FORCE);" 2>/dev/null || \
docker compose exec -T postgres psql -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE IF EXISTS \"$POSTGRES_DB\";"

docker compose exec -T postgres psql -U "$POSTGRES_USER" -d postgres -c "CREATE DATABASE \"$POSTGRES_DB\" OWNER \"$POSTGRES_USER\";"

# Restore the dump
docker compose exec -T postgres pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" -1 < "$BACKUP_DIR/database.dump"

echo "Restoring files..."
mkdir -p "$BACKUP_DIR/tmp_restore"
tar -xzf "$BACKUP_DIR/files.tar.gz" -C "$BACKUP_DIR/tmp_restore"

# Transfer data and upload files cleanly using docker compose cp
if [ -d "$BACKUP_DIR/tmp_restore/data" ]; then
  echo "Transferring data files..."
  docker compose cp "$BACKUP_DIR/tmp_restore/data/." app:/app/data/
fi

if [ -d "$BACKUP_DIR/tmp_restore/tests" ]; then
  echo "Transferring upload files..."
  docker compose cp "$BACKUP_DIR/tmp_restore/tests/." app:/app/app/static/tests/
fi

rm -rf "$BACKUP_DIR/tmp_restore"

echo "Starting application container..."
docker compose start app

# Wait for application to become ready
sleep 3
docker compose ps

echo "Restore completed successfully!"
