#!/usr/bin/env bash
set -euo pipefail

usage() {
  sed -n '/^# Usage:/,/^$/p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}
# Usage: scripts/smoke_local_staging.sh --allow-live-provider-costs [options]
#
# Builds each Newsly Docker image once, starts one disposable local-staging
# stack, and runs all live API scenarios against that same stack.
#
# Options:
#   --allow-live-provider-costs  Required acknowledgement for live LLM/E2B calls.
#   --env-file PATH              Defaults to .env.smoke.local, then .env.
#   --source-url URL             Public source used by all live scenarios.
#   --agent-vm-template-id ID    Override the E2B template used by local workers.
#   --reuse-application-image I  Reuse a previously built image after infrastructure failure.
#   --reuse-extractor-image I    Reuse a previously built image after infrastructure failure.
#   --keep-on-failure            Preserve the scoped stack after failure.
#   --report-dir PATH            Evidence directory root.
#   --help                       Show this message.

die() {
  echo "smoke_local_staging: $*" >&2
  exit 1
}

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
allow_costs=false
keep_on_failure=false
env_file=""
source_url="https://raw.githubusercontent.com/rust-lang/book/main/src/ch04-01-what-is-ownership.md"
report_root="$repo_root/test-results/local-staging-smoke"
reused_application_image=""
reused_extractor_image=""
agent_vm_template_id=""

while (($#)); do
  case "$1" in
    --allow-live-provider-costs)
      allow_costs=true
      shift
      ;;
    --env-file)
      (($# >= 2)) || die "--env-file requires a path"
      env_file="$2"
      shift 2
      ;;
    --source-url)
      (($# >= 2)) || die "--source-url requires a URL"
      source_url="$2"
      shift 2
      ;;
    --agent-vm-template-id)
      (($# >= 2)) || die "--agent-vm-template-id requires an ID"
      agent_vm_template_id="$2"
      shift 2
      ;;
    --reuse-application-image)
      (($# >= 2)) || die "--reuse-application-image requires an image"
      reused_application_image="$2"
      shift 2
      ;;
    --reuse-extractor-image)
      (($# >= 2)) || die "--reuse-extractor-image requires an image"
      reused_extractor_image="$2"
      shift 2
      ;;
    --keep-on-failure)
      keep_on_failure=true
      shift
      ;;
    --report-dir)
      (($# >= 2)) || die "--report-dir requires a path"
      report_root="$2"
      shift 2
      ;;
    --help|-h)
      usage
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

[[ "$allow_costs" == true ]] || die "refusing live calls without --allow-live-provider-costs"
command -v docker >/dev/null 2>&1 || die "docker is required"
docker info >/dev/null 2>&1 || die "the Docker daemon is not available"
command -v cargo >/dev/null 2>&1 || die "cargo is required"
command -v nc >/dev/null 2>&1 || die "nc is required for safe port allocation"

if [[ -z "$env_file" ]]; then
  if [[ -f "$repo_root/.env.smoke.local" ]]; then
    env_file="$repo_root/.env.smoke.local"
  elif [[ -f "$repo_root/.env" ]]; then
    env_file="$repo_root/.env"
  else
    die "create .env.smoke.local or pass --env-file"
  fi
fi
[[ "$env_file" = /* ]] || env_file="$repo_root/$env_file"
[[ -f "$env_file" ]] || die "env file does not exist: $env_file"

has_env_value() {
  local name="$1"
  [[ -n "${!name:-}" ]] || awk -F= -v key="$name" '
    $1 == key {
      sub(/^[^=]*=/, "")
      gsub(/^[[:space:]]+|[[:space:]]+$/, "")
      if (length($0) > 0) found = 1
    }
    END { exit(found ? 0 : 1) }
  ' "$env_file"
}

has_env_value OPENAI_API_KEY || die "OPENAI_API_KEY is required"
if ! has_env_value E2B_API_KEY && ! has_env_value LLM_TASK_SANDBOX_E2B_API_KEY; then
  die "E2B_API_KEY or LLM_TASK_SANDBOX_E2B_API_KEY is required"
fi
has_env_value EXA_API_KEY || die "EXA_API_KEY is required"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
suffix="$(od -An -N3 -tx1 /dev/urandom | tr -d ' \n')"
run_id="$timestamp-$suffix"
project="newsly-smoke-$suffix"
report_dir="$report_root/$run_id"
mkdir -p "$report_dir"
run_data_root="$(mktemp -d "${TMPDIR:-/tmp}/newsly-smoke.$suffix.XXXXXX")"

find_free_port() {
  local candidate
  for _ in {1..100}; do
    candidate=$((18000 + RANDOM % 20000))
    if ! nc -z 127.0.0.1 "$candidate" >/dev/null 2>&1; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

api_port="$(find_free_port)" || die "could not allocate API port"
postgres_port="$(find_free_port)" || die "could not allocate PostgreSQL port"
while [[ "$postgres_port" == "$api_port" ]]; do
  postgres_port="$(find_free_port)" || die "could not allocate PostgreSQL port"
done

application_image="${reused_application_image:-newsly-smoke:$run_id}"
extractor_image="${reused_extractor_image:-newsly-document-extractor-smoke:$run_id}"
build_sha="$(git -C "$repo_root" rev-parse HEAD)"

export NEWSLY_ENV_FILE="$env_file"
export NEWSLY_IMAGE="$application_image"
export NEWSLY_EXTRACTOR_IMAGE="$extractor_image"
export NEWSLY_BUILD_SHA="$build_sha"
export NEWSLY_DATA_ROOT_HOST_PATH="$run_data_root"
export PORT="$api_port"
export POSTGRES_PORT="$postgres_port"
export POSTGRES_DB="newsly_smoke"
export POSTGRES_USER="newsly_smoke"
export POSTGRES_PASSWORD="smoke-$suffix-postgres"
export JWT_SECRET_KEY="smoke-$suffix-jwt-secret"
export ADMIN_PASSWORD="smoke-$suffix-admin"
export DOCUMENT_EXTRACTOR_SHARED_SECRET="smoke-$suffix-extractor"
export PUBLIC_BASE_URL="http://127.0.0.1:$api_port"
if [[ -n "$agent_vm_template_id" ]]; then
  export NEWSLY_AGENT_VM_TEMPLATE_ID="$agent_vm_template_id"
fi

compose=(
  docker compose
  --project-name "$project"
  --file "$repo_root/docker-compose.yml"
  --file "$repo_root/docker-compose.smoke.yml"
)

run_status=1
cleanup_complete=false

collect_evidence() {
  "${compose[@]}" ps --all >"$report_dir/compose-ps.txt" 2>&1 || true
  "${compose[@]}" logs --no-color --timestamps --tail 2500 >"$report_dir/containers.log" 2>&1 || true
  "${compose[@]}" run --rm --no-deps --entrypoint /usr/local/bin/newsly-admin workers --output json usage summary --window-hours 24 --group-by feature >"$report_dir/usage.json" 2>"$report_dir/usage.stderr" || true
  "${compose[@]}" run --rm --no-deps --entrypoint /usr/local/bin/newsly-admin workers --output json tasks failures --window-hours 24 --limit 100 >"$report_dir/task-failures.json" 2>"$report_dir/task-failures.stderr" || true
}

cleanup() {
  local exit_status=$?
  if [[ "$cleanup_complete" == true ]]; then
    return
  fi
  cleanup_complete=true
  collect_evidence
  if [[ "$run_status" -ne 0 && "$keep_on_failure" == true ]]; then
    {
      echo "Stack preserved after failure."
      echo "Project: $project"
      echo "Data root: $run_data_root"
    } >"$report_dir/preserved-stack.txt"
  else
    "${compose[@]}" down --volumes --remove-orphans --timeout 120 >"$report_dir/teardown.log" 2>&1 || true
    case "$run_data_root" in
      "${TMPDIR:-/tmp}"/newsly-smoke."$suffix".*)
        rm -rf -- "$run_data_root"
        ;;
      *)
        echo "refused to remove unexpected data root: $run_data_root" >>"$report_dir/teardown.log"
        ;;
    esac
  fi
  exit "$exit_status"
}
trap cleanup EXIT INT TERM

cd "$repo_root"
if [[ -n "$reused_application_image" ]]; then
  docker image inspect "$application_image" >/dev/null 2>&1 || die "application image does not exist: $application_image"
  echo "Reusing application image $application_image after an interrupted full run."
else
  echo "Building application image once for full run $run_id..."
  docker build --build-arg "NEWSLY_BUILD_SHA=$build_sha" --tag "$application_image" . 2>&1 | tee "$report_dir/application-image-build.log"
fi
if [[ -n "$reused_extractor_image" ]]; then
  docker image inspect "$extractor_image" >/dev/null 2>&1 || die "extractor image does not exist: $extractor_image"
  echo "Reusing extractor image $extractor_image after an interrupted full run."
else
  echo "Building extractor image once for full run $run_id..."
  docker build --tag "$extractor_image" python/document_extractor 2>&1 | tee "$report_dir/extractor-image-build.log"
fi

"${compose[@]}" config --quiet
"${compose[@]}" up --detach --no-build --wait --wait-timeout 300

base_url="http://127.0.0.1:$api_port"
for _ in {1..120}; do
  if curl --fail --silent --show-error "$base_url/health/ready" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
curl --fail --silent --show-error "$base_url/health/ready" >"$report_dir/readiness.json"

set +e
cargo run --locked --manifest-path "$repo_root/rust/Cargo.toml" --package newsly-smoke -- --base-url "$base_url" --source-url "$source_url" --run-id "$run_id" --report-path "$report_dir/report.json" 2>&1 | tee "$report_dir/smoke-output.log"
run_status=${PIPESTATUS[0]}
set -e

collect_evidence
if [[ "$run_status" -eq 0 ]]; then
  echo "PASS local-staging smoke: $report_dir"
else
  echo "FAIL local-staging smoke: $report_dir" >&2
fi
exit "$run_status"
