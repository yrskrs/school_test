#!/bin/bash

# Exit on error
set -e

echo "Starting application update process..."

echo "1. Checking system status..."
docker compose ps

echo "2. Creating pre-update backup..."
./scripts/backup.sh || {
  echo "Backup failed! Aborting update to protect data."
  exit 1
}

echo "3. Pulling or building the new version..."
# If you use pre-built images, uncomment the next line:
# docker compose pull
# If you build locally:
docker compose build

echo "4. Starting new containers (this will recreate them with new images)..."
docker compose up -d

echo "5. Waiting for PostgreSQL to be ready..."
# The healthcheck in compose.yaml will ensure app waits for postgres.
# We can just wait a few seconds for the app to initialize.
sleep 5

echo "6. Checking health status..."
docker compose ps

echo "Update process completed successfully!"
echo "Your data and files have been preserved."
