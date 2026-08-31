#!/bin/bash

# Exit on error
set -e

# Load environment variables
if [ -f .env ]; then
  export $(cat .env | grep -v '^#' | awk '/=/ {print $1}')
fi

POSTGRES_DB=${POSTGRES_DB:-schooltest}
POSTGRES_USER=${POSTGRES_USER:-appuser}

TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")
BACKUP_DIR="backups/$TIMESTAMP"

echo "Creating backup directory: $BACKUP_DIR"
mkdir -p "$BACKUP_DIR"

echo "Backing up PostgreSQL database..."
docker compose exec -T postgres pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -F c > "$BACKUP_DIR/database.dump"

echo "Backing up application data (data directory)..."
# We need to copy from the container to avoid permission issues if running as non-root host
docker compose cp app:/app/data "$BACKUP_DIR/data"

echo "Backing up application uploads (static/tests directory)..."
docker compose cp app:/app/app/static/tests "$BACKUP_DIR/tests"

echo "Compressing files..."
tar -czf "$BACKUP_DIR/files.tar.gz" -C "$BACKUP_DIR" data tests
rm -rf "$BACKUP_DIR/data" "$BACKUP_DIR/tests"

echo "Backup completed successfully!"
echo "Backup location: $BACKUP_DIR"
