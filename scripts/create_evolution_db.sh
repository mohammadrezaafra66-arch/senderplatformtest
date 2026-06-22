#!/bin/bash
set -e
cd "$(dirname "$0")/../multi-messaging-platform"
docker compose exec postgres psql -U mmp_user -d mmp_db -c \
  "SELECT 'CREATE DATABASE evolution_db' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'evolution_db')\gexec"
docker compose exec postgres psql -U mmp_user -d mmp_db -c \
  "GRANT ALL PRIVILEGES ON DATABASE evolution_db TO mmp_user;"
echo "evolution_db ready (idempotent — safe to re-run)."
