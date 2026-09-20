#!/bin/bash

# Exit on error
set -e

# Always run from project root directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

echo "Starting application update process..."

echo "1. Checking current system status..."
docker compose ps

echo "2. Creating pre-update safety backup..."
./scripts/backup.sh || {
  echo "Backup failed! Aborting update to protect data."
  exit 1
}

echo "3. Pulling latest changes from Git..."
if [ -d .git ]; then
  git pull origin main || git pull || echo "Notice: git pull skipped or failed, continuing with current files..."
fi

echo "4. Building updated application image..."
docker compose build app

echo "5. Starting updated containers..."
docker compose up -d app

echo "6. Waiting for application to initialize..."
sleep 4

echo "7. Checking health status..."
docker compose ps

echo "Update process completed successfully!"
echo "Your data and files have been preserved."
