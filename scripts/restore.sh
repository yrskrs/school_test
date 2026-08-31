#!/bin/bash

# Exit on error
set -e

# Check if backup path is provided
if [ -z "$1" ]; then
  echo "Usage: ./scripts/restore.sh <path_to_backup_directory>"
  echo "Example: ./scripts/restore.sh backups/2026-08-31_21-00-00"
  exit 1
fi

BACKUP_DIR=$1

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
  export $(cat .env | grep -v '^#' | awk '/=/ {print $1}')
fi

POSTGRES_DB=${POSTGRES_DB:-schooltest}
POSTGRES_USER=${POSTGRES_USER:-appuser}

read -p "WARNING: This will overwrite the current database and files. Are you sure? (y/n): " confirm
if [[ $confirm != [yY] && $confirm != [yY][eE][sS] ]]; then
  echo "Restore cancelled."
  exit 0
fi

echo "Creating an automatic backup of the current state before restore..."
./scripts/backup.sh || echo "Warning: Automatic backup failed. Continuing with restore anyway..."

echo "Stopping application container to avoid conflicts..."
docker compose stop app

echo "Restoring PostgreSQL database..."
# Drop and recreate the database to ensure a clean restore
docker compose exec -T postgres psql -U "$POSTGRES_USER" -d postgres -c "DROP DATABASE IF EXISTS \"$POSTGRES_DB\";"
docker compose exec -T postgres psql -U "$POSTGRES_USER" -d postgres -c "CREATE DATABASE \"$POSTGRES_DB\" OWNER \"$POSTGRES_USER\";"

# Restore the dump
docker compose exec -T postgres pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" -1 < "$BACKUP_DIR/database.dump"

echo "Restoring files..."
mkdir -p "$BACKUP_DIR/tmp_restore"
tar -xzf "$BACKUP_DIR/files.tar.gz" -C "$BACKUP_DIR/tmp_restore"

# We use docker compose run to mount volumes without knowing their exact names
echo "Transferring data files..."
docker compose run --rm -v $(pwd)/$BACKUP_DIR/tmp_restore/data:/src app sh -c "cp -r /src/* /app/data/ 2>/dev/null || true"

echo "Transferring upload files..."
docker compose run --rm -v $(pwd)/$BACKUP_DIR/tmp_restore/tests:/src app sh -c "cp -r /src/* /app/app/static/tests/ 2>/dev/null || true"

rm -rf "$BACKUP_DIR/tmp_restore"

echo "Starting application container..."
docker compose start app

echo "Restore completed successfully!"
