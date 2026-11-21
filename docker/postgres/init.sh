#!/bin/bash
# Local database bootstrap (runs once, on an empty data volume).
# Mirrors the production bootstrap: databases, roles and extensions are created here, never by
# application migrations (ADR-0004).
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-EOSQL
  CREATE ROLE policypilot LOGIN PASSWORD '${POLICYPILOT_DB_PASSWORD}';
  CREATE DATABASE policypilot OWNER policypilot;
  CREATE DATABASE policypilot_test OWNER policypilot;
  CREATE ROLE n8n LOGIN PASSWORD '${N8N_DB_PASSWORD}';
  CREATE DATABASE n8n OWNER n8n;
  REVOKE CONNECT ON DATABASE policypilot FROM PUBLIC;
  REVOKE CONNECT ON DATABASE policypilot_test FROM PUBLIC;
  REVOKE CONNECT ON DATABASE n8n FROM PUBLIC;
  GRANT CONNECT ON DATABASE policypilot TO policypilot;
  GRANT CONNECT ON DATABASE policypilot_test TO policypilot;
  GRANT CONNECT ON DATABASE n8n TO n8n;
EOSQL

for db in policypilot policypilot_test; do
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$db" \
    -c "CREATE EXTENSION IF NOT EXISTS vector;"
done
