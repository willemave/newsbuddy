#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 || ( $# -eq 2 && "$2" != --maintenance-cutover ) ]]; then
  echo "usage: deploy_blue_green.sh <image> [--maintenance-cutover]" >&2
  exit 2
fi

image="$1"
maintenance_cutover=false
if [[ "${2:-}" == --maintenance-cutover ]]; then
  maintenance_cutover=true
fi
extractor_image="${NEWSLY_EXTRACTOR_IMAGE:-${image}-extractor}"
app_dir="${NEWSLY_DEPLOY_APP_DIR:-/opt/news_app}"
compose_file="${NEWSLY_DEPLOY_COMPOSE_FILE:-${app_dir}/docker-compose.production.yml}"
env_file="${NEWSLY_DEPLOY_ENV_FILE:-${app_dir}/.env.racknerd}"
state_dir="${NEWSLY_DEPLOY_STATE_DIR:-/opt/newsly/state}"
switch_script="${NEWSLY_DEPLOY_SWITCH_SCRIPT:-/opt/newsly/bin/switch-api-slot}"
active_slot_file="${state_dir}/active-api-slot"
sqlx_adoption_marker="${state_dir}/sqlx-baseline-adopted"
sqlx_backup_marker="${state_dir}/sqlx-baseline-backup"
sqlx_backup_dir="${NEWSLY_SQLX_BACKUP_DIR:-/data/backups/sqlx-baseline}"

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
if [[ "${baseline_adoption}" == "true" && "${maintenance_cutover}" == "true" ]]; then
  echo "Baseline adoption and maintenance cutover are separate operations" >&2
  exit 2
fi

if [[ "${maintenance_cutover}" == "true" ]]; then
  deployment_mode="council_retirement"
elif [[ "${baseline_adoption}" == "true" ]]; then
  deployment_mode="baseline_adoption"
else
  deployment_mode="online"
fi

case "${deployment_mode}" in
  online)
    writer_drain_policy="before_switch"
    route_rollback_policy="restore_prior_slot"
    ;;
  baseline_adoption)
    writer_drain_policy="maintenance_barrier"
    route_rollback_policy="fail_closed"
    if [[ -f "${sqlx_adoption_marker}" ]]; then
      echo "SQLx baseline was already adopted; remove NEWSLY_SQLX_BASELINE_ADOPTION=true" >&2
      exit 1
    fi
    ;;
  council_retirement)
    writer_drain_policy="maintenance_barrier"
    route_rollback_policy="fail_closed"
    ;;
  *)
    echo "Internal error: unsupported deployment mode ${deployment_mode}" >&2
    exit 1
    ;;
esac

if [[ "${deployment_mode}" == "baseline_adoption" ]]; then
  case "${sqlx_backup_dir}" in
    /data/*) ;;
    *)
      echo "NEWSLY_SQLX_BACKUP_DIR must be a dedicated directory below /data" >&2
      exit 1
      ;;
  esac
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

restorable_containers=()
failure_restore_policy="none"
restore_recorded_containers() {
  if [[ ${#restorable_containers[@]} -eq 0 ]]; then
    return
  fi
  for container in "${restorable_containers[@]}"; do
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
  local version="$1"
  [[ "$version" =~ ^[0-9]+$ ]] || { printf '%s\n' unknown; return; }
  local migrations_table_exists
  if ! migrations_table_exists="$(
    postgres_scalar "SELECT to_regclass('public._sqlx_migrations') IS NOT NULL;" 2>/dev/null \
      | tr -d '[:space:]'
  )"; then
    printf '%s\n' unknown
    return
  fi
  case "${migrations_table_exists}" in
    t) ;;
    f) printf '%s\n' not_applied; return ;;
    *) printf '%s\n' unknown; return ;;
  esac

  local authority_migration_applied
  if ! authority_migration_applied="$(
    postgres_scalar \
      "SELECT EXISTS (SELECT 1 FROM public._sqlx_migrations WHERE version = ${version} AND success);" \
      2>/dev/null \
      | tr -d '[:space:]'
  )"; then
    printf '%s\n' unknown
    return
  fi
  case "${authority_migration_applied}" in
    t) printf '%s\n' applied ;;
    f) printf '%s\n' not_applied ;;
    *) printf '%s\n' unknown ;;
  esac
}

create_verified_database_backup() {
  local temporary_path="$1"
  local final_path="$2"
  local failure_message="$3"

  if ! docker exec newsly-postgres sh -c \
    'exec pg_dump --format=custom --username "$POSTGRES_USER" --dbname "$POSTGRES_DB"' \
    > "${temporary_path}"; then
    rm -f "${temporary_path}"
    echo "${failure_message}" >&2
    return 1
  fi
  docker exec -i newsly-postgres pg_restore --list < "${temporary_path}" >/dev/null
  if [[ "${temporary_path}" != "${final_path}" ]]; then
    mv "${temporary_path}" "${final_path}"
  fi
  chmod 600 "${final_path}"
  sha256sum "${final_path}" > "${final_path}.sha256"
  chmod 600 "${final_path}.sha256"
  sha256sum --check --status "${final_path}.sha256"
}

verify_recorded_database_backup() {
  local backup_path="$1"
  sha256sum --check --status "${backup_path}.sha256"
  docker exec -i newsly-postgres pg_restore --list < "${backup_path}" >/dev/null
}

record_running_containers() {
  restorable_containers=()
  for container in "$@"; do
    if [[ "$(docker inspect --format '{{.State.Running}}' "${container}" 2>/dev/null || true)" == "true" ]]; then
      restorable_containers+=("${container}")
    fi
  done
}

restore_processes_after_failed_deploy() {
  local status=$?
  trap - EXIT
  if [[ "${status}" -ne 0 ]]; then
    case "${failure_restore_policy}" in
      maintenance_barrier)
        echo "Deployment failed after entering the SQLx maintenance barrier; restoring prior containers" >&2
        restore_recorded_containers
        ;;
      online_writers)
        echo "Deployment failed before writer replacement; restoring prior workers and scheduler" >&2
        restore_recorded_containers
        ;;
      none) ;;
      *)
        echo "Internal error: unsupported failure restoration policy ${failure_restore_policy}" >&2
        ;;
    esac
  fi
  exit "${status}"
}
trap restore_processes_after_failed_deploy EXIT

case "${deployment_mode}" in
  council_retirement)
    if [[ "$(authority_migration_state 20261002010000)" != not_applied ]]; then
      echo "Council retirement is already applied or unverifiable; use normal deployment after verifying its state" >&2
      exit 2
    fi
    echo "Running read-only council retirement preflight before stopping writers"
    compose --profile ops run --rm --no-deps \
      --entrypoint /usr/local/bin/newsly-db \
      migrate verify-council-retirement

    echo "Entering the explicit maintenance cutover; failures leave prior writers stopped"
    # Keep restoration disabled. Migration outcome can be ambiguous, and automatic recovery must
    # never restart old binaries after a one-way cutover.
    compose stop api_blue api_green workers scheduler
    compose --profile ops run --rm --no-deps \
      --entrypoint /usr/local/bin/newsly-db \
      migrate verify-council-retirement

    # Every attempt takes a fresh verified backup after writers have stopped. The state directory
    # is already the deployer's durable, private host directory (also overrideable in the harness).
    cutover_backup_dir="${state_dir}/maintenance-backups"
    mkdir -p "${cutover_backup_dir}"
    chmod 700 "${cutover_backup_dir}"
    backup_timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
    cutover_backup="$(mktemp "${cutover_backup_dir}/pre-cutover-${backup_timestamp}.dump.XXXXXX")"
    create_verified_database_backup \
      "${cutover_backup}" \
      "${cutover_backup}" \
      "Maintenance cutover backup failed; prior writers remain stopped"
    echo "Verified maintenance backup: ${cutover_backup}"

    compose --profile ops run --rm --no-deps \
      -e NEWSLY_SQLX_BASELINE_ADOPTION=false \
      -e NEWSLY_MAINTENANCE_BARRIER_CONFIRMED=true \
      migrate
    echo "Maintenance migration committed; prior application runtime remains stopped"
    ;;
  baseline_adoption)
    echo "Running read-only SQLx baseline eligibility preflight with the exact image"
    compose --profile ops run --rm --no-deps \
      --entrypoint /usr/local/bin/newsly-db \
      migrate verify-baseline

    echo "Entering the one-time SQLx baseline-adoption maintenance barrier"
    record_running_containers newsly-api-blue newsly-api-green newsly-workers newsly-scheduler
    failure_restore_policy="maintenance_barrier"
    compose stop api_blue api_green workers scheduler

    if [[ -f "${sqlx_backup_marker}" ]]; then
      sqlx_backup_path="$(tr -d '\r\n' < "${sqlx_backup_marker}")"
      case "${sqlx_backup_path}" in
        "${sqlx_backup_dir}"/*) ;;
        *)
          echo "Recorded SQLx baseline backup is outside ${sqlx_backup_dir}" >&2
          exit 1
          ;;
      esac
      if [[ ! -f "${sqlx_backup_path}" || ! -f "${sqlx_backup_path}.sha256" ]]; then
        echo "Recorded SQLx baseline backup is missing: ${sqlx_backup_path}" >&2
        exit 1
      fi
      verify_recorded_database_backup "${sqlx_backup_path}"
      echo "Reusing verified pre-adoption database backup: ${sqlx_backup_path}"
    else
      echo "Creating recoverable pre-adoption database backup"
      mkdir -p "${sqlx_backup_dir}"
      chmod 700 "${sqlx_backup_dir}"
      backup_timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
      sqlx_backup_temp="$(mktemp "${sqlx_backup_dir}/pre-adoption-${backup_timestamp}.XXXXXX.dump")"
      sqlx_backup_path="${sqlx_backup_dir}/pre-adoption-${backup_timestamp}.dump"
      create_verified_database_backup \
        "${sqlx_backup_temp}" \
        "${sqlx_backup_path}" \
        "Pre-adoption database backup failed"
      backup_marker_temp="$(mktemp "${state_dir}/sqlx-baseline-backup.XXXXXX")"
      printf '%s\n' "${sqlx_backup_path}" > "${backup_marker_temp}"
      mv "${backup_marker_temp}" "${sqlx_backup_marker}"
      echo "Verified pre-adoption database backup: ${sqlx_backup_path}"
    fi

    echo "Running one-time baseline adoption with the exact-image SQLx binary"
    if ! compose --profile ops run --rm --no-deps \
      -e NEWSLY_SQLX_BASELINE_ADOPTION=true \
      -e NEWSLY_MAINTENANCE_BARRIER_CONFIRMED=true \
      migrate; then
      migration_state="$(authority_migration_state 20260831000000)"
      if [[ "${migration_state}" == "applied" ]]; then
        failure_restore_policy="none"
        echo "Rust authority committed before migration validation failed; prior runtime remains stopped" >&2
      elif [[ "${migration_state}" == "unknown" ]]; then
        failure_restore_policy="none"
        echo "Could not prove that Rust authority is inactive; prior runtime remains stopped" >&2
      fi
      exit 1
    fi
    failure_restore_policy="none"
    touch "${sqlx_adoption_marker}"

    # The authority migration is intentionally one-way. From this point onward, starting an older
    # API or worker could violate the durable route/task fences. Keep the prior processes stopped and
    # fail closed if the new Rust runtime cannot become healthy.
    echo "Rust runtime authority is active; prior application runtime remains stopped during cutover"
    ;;
  online)
    echo "Running backward-compatible database migrations while the active API remains online"
    compose --profile ops run --rm --no-deps \
      -e NEWSLY_SQLX_BASELINE_ADOPTION=false \
      -e NEWSLY_MAINTENANCE_BARRIER_CONFIRMED=false \
      migrate
    ;;
esac

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

if [[ "${writer_drain_policy}" == "before_switch" ]]; then
  echo "Draining workers and scheduler before the API version switch"
  record_running_containers newsly-workers newsly-scheduler
  failure_restore_policy="online_writers"
  compose stop workers scheduler
fi

# Once target switching begins, the new API may emit work even if the public probe later fails.
# Never restart an older writer binary after this point.
failure_restore_policy="none"

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
  if [[ "${route_rollback_policy}" == "restore_prior_slot" && ( "${active_slot}" == "blue" || "${active_slot}" == "green" ) ]]; then
    echo "Restoring public traffic to the previously active ${active_slot} API slot" >&2
    if ! "${switch_script}" "${active_slot}"; then
      echo "Automatic public-route rollback failed; manual intervention is required" >&2
    else
      echo "Prior API route restored; writers remain stopped because the target may have emitted new-version tasks" >&2
    fi
  else
    echo "No compatible prior API slot can be restored automatically" >&2
  fi
  exit 1
fi

echo "Starting YouTube proof-of-origin provider"
compose up -d --no-deps bgutil-provider

echo "Updating workers and scheduler"
compose up -d --no-deps workers scheduler
failure_restore_policy="none"

printf '%s\n' "${image}" > "${state_dir}/active-image"
printf '%s\n' "${image}" > "${state_dir}/${target_slot}-image"
printf '%s\n' "${extractor_image}" > "${state_dir}/active-extractor-image"

echo "Deployment complete"
echo "active_slot=${target_slot}"
echo "active_image=${image}"
echo "active_extractor_image=${extractor_image}"
docker ps --filter name=newsly- --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
