#!/usr/bin/env bash
set -euo pipefail

bootstrap_ready_file="${NEWSLY_BOOTSTRAP_READY_FILE:-/tmp/newsly-bootstrap.ready}"
postgres_db="${POSTGRES_DB:-newsly}"
postgres_user="${POSTGRES_USER:-newsly}"
postgres_password="${POSTGRES_PASSWORD:-newsly}"
postgres_port="${POSTGRES_PORT:-5432}"

rm -f "${bootstrap_ready_file}"

wait_for_postgres() {
  until runuser -u postgres -- env \
    PGHOST=/var/run/postgresql \
    PGPORT="${postgres_port}" \
    pg_isready -q -d postgres
  do
    echo "Waiting for PostgreSQL bootstrap readiness..." >&2
    sleep 1
  done
}

sql_literal() {
  local value="${1//\'/\'\'}"
  printf "'%s'" "${value}"
}

wait_for_postgres

db_name_sql="$(sql_literal "${postgres_db}")"
db_user_sql="$(sql_literal "${postgres_user}")"
db_password_sql="$(sql_literal "${postgres_password}")"

bootstrap_sql=$(cat <<EOF
DO \$do\$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = ${db_user_sql}) THEN
        EXECUTE format('CREATE ROLE %I LOGIN PASSWORD %L', ${db_user_sql}, ${db_password_sql});
    ELSE
        EXECUTE format('ALTER ROLE %I LOGIN PASSWORD %L', ${db_user_sql}, ${db_password_sql});
    END IF;
END \$do\$;
SELECT format('CREATE DATABASE %I OWNER %I', ${db_name_sql}, ${db_user_sql})
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = ${db_name_sql}) \gexec
EOF
)

printf '%s\n' "${bootstrap_sql}" | runuser -u postgres -- env \
  PGHOST=/var/run/postgresql \
  PGPORT="${postgres_port}" \
  psql -v ON_ERROR_STOP=1 postgres

cd /app
python -m alembic -c /app/migrations/alembic.ini upgrade head

date -u +"%Y-%m-%dT%H:%M:%SZ" >"${bootstrap_ready_file}"
