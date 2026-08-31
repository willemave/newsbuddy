#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: deploy_blue_green.sh <image>" >&2
  exit 2
fi

image="$1"
extractor_image="${NEWSLY_EXTRACTOR_IMAGE:-${image}-extractor}"
app_dir="${NEWSLY_DEPLOY_APP_DIR:-/opt/news_app}"
compose_file="${NEWSLY_DEPLOY_COMPOSE_FILE:-${app_dir}/docker-compose.production.yml}"
env_file="${NEWSLY_DEPLOY_ENV_FILE:-${app_dir}/.env.racknerd}"
state_dir="${NEWSLY_DEPLOY_STATE_DIR:-/opt/newsly/state}"
switch_script="${NEWSLY_DEPLOY_SWITCH_SCRIPT:-/opt/newsly/bin/switch-api-slot}"
active_slot_file="${state_dir}/active-api-slot"
sqlx_adoption_marker="${state_dir}/sqlx-baseline-adopted"

if [[ ! -f "${compose_file}" ]]; then
  echo "production Compose file not found: ${compose_file}" >&2
  exit 1
fi
if [[ ! -f "${env_file}" ]]; then
  echo "production env file not found: ${env_file}" >&2
  exit 1
fi
if [[ ! -x "${switch_script}" ]]; then
  echo "API switch script is not executable: ${switch_script}" >&2
  exit 1
fi

mkdir -p "${state_dir}"
if ! docker network inspect newsly-internal >/dev/null 2>&1; then
  docker network create newsly-internal >/dev/null
fi

active_slot=""
if [[ -f "${active_slot_file}" ]]; then
  active_slot="$(tr -d '[:space:]' < "${active_slot_file}")"
fi

case "${active_slot}" in
  blue)
    target_slot="green"
    target_service="api_green"
    target_port=8002
    ;;
  green)
    target_slot="blue"
    target_service="api_blue"
    target_port=8001
    ;;
  *)
    target_slot="blue"
    target_service="api_blue"
    target_port=8001
    ;;
esac

compose() {
  NEWSLY_IMAGE="${image}" NEWSLY_EXTRACTOR_IMAGE="${extractor_image}" docker compose \
    --env-file "${env_file}" \
    -f "${compose_file}" \
    "$@"
}

baseline_adoption="${NEWSLY_SQLX_BASELINE_ADOPTION:-}"
if [[ -z "${baseline_adoption}" ]]; then
  baseline_adoption="$(
    awk -F= '$1 == "NEWSLY_SQLX_BASELINE_ADOPTION" { value = $2 } END { print value }' "${env_file}" \
      | tr -d "'\"[:space:]"
  )"
fi
baseline_adoption="$(printf '%s' "${baseline_adoption:-false}" | tr '[:upper:]' '[:lower:]')"
if [[ "${baseline_adoption}" != "true" && "${baseline_adoption}" != "false" ]]; then
  echo "NEWSLY_SQLX_BASELINE_ADOPTION must be true or false" >&2
  exit 1
fi
if [[ "${baseline_adoption}" == "true" && -f "${sqlx_adoption_marker}" ]]; then
  echo "SQLx baseline was already adopted; remove NEWSLY_SQLX_BASELINE_ADOPTION=true" >&2
  exit 1
fi

echo "Starting persistent PostgreSQL"
compose up -d postgres

echo "Waiting for PostgreSQL readiness"
for attempt in $(seq 1 60); do
  postgres_status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' newsly-postgres 2>/dev/null || true)"
  if [[ "${postgres_status}" == "healthy" ]]; then
    break
  fi
  if [[ "${attempt}" -eq 60 ]]; then
    echo "PostgreSQL did not become healthy" >&2
    docker logs --tail 200 newsly-postgres || true
    exit 1
  fi
  sleep 5
done

barrier_containers=()
restore_barrier_on_failure=false
restore_barrier_containers() {
  for container in "${barrier_containers[@]}"; do
    docker start "${container}" >/dev/null || true
  done
}

postgres_scalar() {
  local sql="$1"
  docker exec newsly-postgres sh -c \
    'psql --no-psqlrc --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --tuples-only --no-align --command "$1"' \
    sh "${sql}"
}

authority_migration_state() {
  local migrations_table_exists
  if ! migrations_table_exists="$(
    postgres_scalar "SELECT to_regclass('public._sqlx_migrations') IS NOT NULL;" 2>/dev/null \
      | tr -d '[:space:]'
  )"; then
    printf '%s\n' unknown
    return
  fi
  if [[ "${migrations_table_exists}" != "t" ]]; then
    printf '%s\n' not_applied
    return
  fi

  local authority_migration_applied
  if ! authority_migration_applied="$(
    postgres_scalar \
      "SELECT EXISTS (SELECT 1 FROM public._sqlx_migrations WHERE version = 20260831000000 AND success);" \
      2>/dev/null \
      | tr -d '[:space:]'
  )"; then
    printf '%s\n' unknown
    return
  fi
  if [[ "${authority_migration_applied}" == "t" ]]; then
    printf '%s\n' applied
  else
    printf '%s\n' not_applied
  fi
}

restore_barrier_after_failed_deploy() {
  local status=$?
  trap - EXIT
  if [[ "${status}" -ne 0 && "${restore_barrier_on_failure}" == "true" ]]; then
    echo "Deployment failed after entering the SQLx maintenance barrier; restoring prior containers" >&2
    restore_barrier_containers
  fi
  exit "${status}"
}
trap restore_barrier_after_failed_deploy EXIT

echo "Entering SQLx authority maintenance barrier"
for container in newsly-api-blue newsly-api-green newsly-workers newsly-scheduler; do
  if [[ "$(docker inspect --format '{{.State.Running}}' "${container}" 2>/dev/null || true)" == "true" ]]; then
    barrier_containers+=("${container}")
  fi
done
restore_barrier_on_failure=true
compose stop api_blue api_green workers scheduler

echo "Running database migrations with the exact-image SQLx binary"
if ! compose --profile ops run --rm --no-deps \
  -e "NEWSLY_SQLX_BASELINE_ADOPTION=${baseline_adoption}" \
  -e NEWSLY_MAINTENANCE_BARRIER_CONFIRMED=true \
  migrate; then
  migration_state="$(authority_migration_state)"
  if [[ "${migration_state}" == "applied" ]]; then
    restore_barrier_on_failure=false
    echo "Rust authority committed before migration validation failed; prior runtime remains stopped" >&2
  elif [[ "${migration_state}" == "unknown" ]]; then
    restore_barrier_on_failure=false
    echo "Could not prove that Rust authority is inactive; prior runtime remains stopped" >&2
  fi
  exit 1
fi
restore_barrier_on_failure=false
touch "${sqlx_adoption_marker}"

# The authority migration is intentionally one-way. From this point onward, starting an older
# API or worker could violate the durable route/task fences. Keep the prior processes stopped and
# fail closed if the new Rust runtime cannot become healthy.
echo "Rust runtime authority is active; prior application runtime remains stopped during cutover"

echo "Starting database-free document extractor"
compose up -d --no-deps document-extractor

echo "Waiting for document extractor readiness"
for attempt in $(seq 1 60); do
  extractor_status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' newsly-document-extractor 2>/dev/null || true)"
  if [[ "${extractor_status}" == "healthy" ]]; then
    break
  fi
  if [[ "${attempt}" -eq 60 ]]; then
    echo "Document extractor did not become healthy" >&2
    docker logs --tail 200 newsly-document-extractor || true
    exit 1
  fi
  sleep 5
done

echo "Starting inactive API slot: ${target_slot}"
compose up -d --no-deps "${target_service}"

echo "Waiting for ${target_slot} API readiness"
for attempt in $(seq 1 60); do
  if curl --fail --silent --show-error --max-time 5 "http://127.0.0.1:${target_port}/health" >/dev/null 2>&1; then
    break
  fi
  if [[ "${attempt}" -eq 60 ]]; then
    echo "${target_slot} API did not become ready" >&2
    docker logs --tail 200 "newsly-api-${target_slot}" || true
    exit 1
  fi
  sleep 5
done

public_base_url="$(docker exec "newsly-api-${target_slot}" printenv PUBLIC_BASE_URL)"
if [[ "${public_base_url}" != https://* ]]; then
  echo "PUBLIC_BASE_URL must be an HTTPS origin" >&2
  exit 1
fi

echo "Switching Nginx to ${target_slot}"
"${switch_script}" "${target_slot}"

echo "Verifying public HTTPS origin"
public_origin_healthy=false
for attempt in $(seq 1 3); do
  if curl --fail --silent --show-error --max-time 15 \
    "${public_base_url%/}/health" >/dev/null; then
    public_origin_healthy=true
    break
  fi
  if [[ "${attempt}" -lt 3 ]]; then
    sleep 2
  fi
done
if [[ "${public_origin_healthy}" != true ]]; then
  echo "Public HTTPS origin did not reach the new API slot" >&2
  echo "Rust authority is already active; refusing to restart or route to the prior runtime" >&2
  exit 1
fi

echo "Starting YouTube proof-of-origin provider"
compose up -d --no-deps bgutil-provider

echo "Updating workers and scheduler"
compose up -d --no-deps workers scheduler
restore_barrier_on_failure=false

printf '%s\n' "${image}" > "${state_dir}/active-image"
printf '%s\n' "${image}" > "${state_dir}/${target_slot}-image"
printf '%s\n' "${extractor_image}" > "${state_dir}/active-extractor-image"

echo "Deployment complete"
echo "active_slot=${target_slot}"
echo "active_image=${image}"
echo "active_extractor_image=${extractor_image}"
docker ps --filter name=newsly- --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
