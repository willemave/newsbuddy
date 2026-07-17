#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: deploy_blue_green.sh <image>" >&2
  exit 2
fi

image="$1"
app_dir="${NEWSLY_DEPLOY_APP_DIR:-/opt/news_app}"
compose_file="${NEWSLY_DEPLOY_COMPOSE_FILE:-${app_dir}/docker-compose.production.yml}"
env_file="${NEWSLY_DEPLOY_ENV_FILE:-${app_dir}/.env.racknerd}"
state_dir="${NEWSLY_DEPLOY_STATE_DIR:-/opt/newsly/state}"
switch_script="${NEWSLY_DEPLOY_SWITCH_SCRIPT:-/opt/newsly/bin/switch-api-slot}"
active_slot_file="${state_dir}/active-api-slot"

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
  NEWSLY_IMAGE="${image}" docker compose \
    --env-file "${env_file}" \
    -f "${compose_file}" \
    "$@"
}

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

echo "Running database migrations"
compose --profile ops run --rm --no-deps migrate

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

echo "Switching Nginx to ${target_slot}"
"${switch_script}" "${target_slot}"

echo "Updating workers and scheduler"
compose up -d --no-deps workers scheduler

printf '%s\n' "${image}" > "${state_dir}/active-image"
printf '%s\n' "${image}" > "${state_dir}/${target_slot}-image"

echo "Deployment complete"
echo "active_slot=${target_slot}"
echo "active_image=${image}"
docker ps --filter name=newsly- --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
