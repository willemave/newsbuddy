#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
script_dir="${repo_root}/scripts"
# shellcheck source=scripts/lib/rust_runtime.sh
source "${script_dir}/lib/rust_runtime.sh"

env_file_option=""
api_base_url="${NEWSLY_RELEASE_API_BASE_URL:-http://127.0.0.1:8010}"
simulator_udid="${NEWSLY_RELEASE_SIMULATOR_UDID:-}"
with_live_smoke=false
allow_live_provider_costs=false
service_pid=""
gate_database_name=""
maintenance_database_url=""

usage() {
  cat <<'EOF'
Usage: scripts/release_gate.sh [options]

Run the complete deterministic Newsly release gate locally against one clean
commit: Rust/SQLx/contracts, both Python islands, native iOS tests, and AXe.
The script creates one disposable PostgreSQL database, starts one local Rust
API for the iOS/AXe phase, and does not build Docker images.

Options:
  --env-file PATH              Local runtime environment (defaults to normal lookup).
  --api-base-url URL           Loopback API origin for iOS/AXe (default: 127.0.0.1:8010).
  --simulator-udid UDID        Simulator to boot/use (defaults to a booted iPhone).
  --with-live-smoke            Also run the production-shaped Docker/API/LLM smoke.
  --allow-live-provider-costs  Required with --with-live-smoke.
  -h, --help                   Show this help.

The live smoke builds each Docker image once for the full run, then reuses that
same disposable stack for every API, queue, LLM, E2B, deck, chat, and share-sheet
scenario.
EOF
}

die() {
  echo "release_gate: $*" >&2
  exit 1
}

while (($#)); do
  case "$1" in
    --env-file)
      (($# >= 2)) || die "--env-file requires a path"
      env_file_option="$2"
      shift 2
      ;;
    --api-base-url)
      (($# >= 2)) || die "--api-base-url requires a URL"
      api_base_url="$2"
      shift 2
      ;;
    --simulator-udid)
      (($# >= 2)) || die "--simulator-udid requires a UDID"
      simulator_udid="$2"
      shift 2
      ;;
    --with-live-smoke)
      with_live_smoke=true
      shift
      ;;
    --allow-live-provider-costs)
      allow_live_provider_costs=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

if [[ "$with_live_smoke" == true && "$allow_live_provider_costs" != true ]]; then
  die "--with-live-smoke requires --allow-live-provider-costs"
fi

for command_name in cargo createdb curl docker dropdb git jq uv xcodebuild xcrun; do
  command -v "$command_name" >/dev/null 2>&1 || die "missing required command: $command_name"
done
command -v axe >/dev/null 2>&1 || die "missing required command: axe"
command -v sqlx >/dev/null 2>&1 || die "missing required command: sqlx (install sqlx-cli 0.9.0)"

cd "$repo_root"
release_sha="$(git rev-parse HEAD)"
[[ "$release_sha" =~ ^[0-9a-f]{40}$ ]] || die "HEAD is not a full commit SHA"
[[ -z "$(git status --porcelain)" ]] || die "commit all changes before running the release gate"

env_file="$(newsly_resolve_env_file "$repo_root" "$env_file_option")"
[[ -n "$env_file" ]] || die "no local environment file found; pass --env-file"
[[ "$env_file" = /* ]] || env_file="$repo_root/$env_file"
newsly_load_dotenv "$env_file"
newsly_normalize_database_environment
newsly_require_database_url
maintenance_database_url="$DATABASE_URL"
gate_database_name="newsly_gate_${release_sha:0:12}_$$"
createdb --maintenance-db="$maintenance_database_url" "$gate_database_name"
database_url_without_query="${maintenance_database_url%%\?*}"
database_url_query=""
if [[ "$maintenance_database_url" == *\?* ]]; then
  database_url_query="?${maintenance_database_url#*\?}"
fi
export DATABASE_URL="${database_url_without_query%/*}/${gate_database_name}${database_url_query}"
export NEWSLY_DATABASE_URL="$DATABASE_URL"

IFS=$'\t' read -r api_scheme api_host api_port api_use_https \
  < <(newsly_parse_api_base_url "$api_base_url")
[[ "$api_scheme" == http ]] || die "the local release API must use http"
case "$api_host" in
  127.0.0.1|localhost) ;;
  *) die "the local release API must use a loopback host" ;;
esac
api_base_url="http://${api_host}:${api_port}"

result_root="$repo_root/test-results/release-gate/$release_sha"
derived_data="$result_root/DerivedData"
xcresult_path="$result_root/newsly-tests.xcresult"
mkdir -p "$result_root"

cleanup() {
  if [[ -n "$service_pid" ]]; then
    kill -TERM "$service_pid" >/dev/null 2>&1 || true
    wait "$service_pid" 2>/dev/null || true
    service_pid=""
  fi
  if [[ -n "$gate_database_name" ]]; then
    dropdb --if-exists --force \
      --maintenance-db="$maintenance_database_url" \
      "$gate_database_name" >/dev/null 2>&1 || true
    gate_database_name=""
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

echo "Release SHA: $release_sha"
echo "Evidence: $result_root"

echo "== Rust, SQLx, architecture, and contracts =="
NEWSLY_ENV_FILE="$env_file" scripts/run_sqlx_migrations.sh
scripts/architecture_guard.sh
(
  cd rust
  cargo clippy --workspace --all-targets --locked -- -D warnings
  cargo test --workspace --locked
  SQLX_OFFLINE=true cargo check --workspace --all-targets --locked --offline
  cargo sqlx prepare --workspace --check
)
NEWSLY_BUILD_SHA="$release_sha" NEWSLY_ENV_FILE=/dev/null \
  docker compose --file docker-compose.yml config --quiet

echo "== Database-free Python islands =="
uv sync --project python/evals --frozen --group dev
env -u DATABASE_URL -u NEWSLY_DATABASE_URL \
  uv run --project python/evals pytest -q python/evals/tests
uv run --project python/evals ruff check \
  python/evals/src python/evals/scripts python/evals/tests
uv run --project python/evals ruff format --check \
  python/evals/src python/evals/scripts python/evals/tests
uv run --project python/evals mypy --config-file python/evals/pyproject.toml \
  python/evals/src python/evals/scripts python/evals/tests
env -u DATABASE_URL -u NEWSLY_DATABASE_URL \
  uv build --project python/evals --out-dir "$result_root/evals-dist"

uv sync --project python/document_extractor --frozen --group dev
env -u DATABASE_URL -u NEWSLY_DATABASE_URL \
  uv run --project python/document_extractor \
  pytest -q python/document_extractor/tests
env -u DATABASE_URL -u NEWSLY_DATABASE_URL \
  uv run --project python/document_extractor ruff check \
  python/document_extractor/newsly_document_extractor \
  python/document_extractor/tests
env -u DATABASE_URL -u NEWSLY_DATABASE_URL \
  uv run --project python/document_extractor ruff format --check \
  python/document_extractor/newsly_document_extractor \
  python/document_extractor/tests
env -u DATABASE_URL -u NEWSLY_DATABASE_URL \
  uv run --project python/document_extractor mypy \
  --config-file python/document_extractor/pyproject.toml \
  python/document_extractor/newsly_document_extractor
env -u DATABASE_URL -u NEWSLY_DATABASE_URL \
  uv build --project python/document_extractor \
  --out-dir "$result_root/document-extractor-dist"

echo "== Local Rust API for authenticated iOS tests =="
(
  cd rust
  cargo build --locked --package newsly-api --bin newsly-api
)
ENVIRONMENT=development \
  NEWSLY_RUST_BIND_ADDR="0.0.0.0:${api_port}" \
  rust/target/debug/newsly-api \
  >"$result_root/local-api.log" 2>&1 &
service_pid="$!"
for _ in {1..240}; do
  if curl --fail --silent --show-error "$api_base_url/health/ready" >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "$service_pid" >/dev/null 2>&1; then
    tail -200 "$result_root/local-api.log" >&2
    die "local Rust API exited before becoming ready"
  fi
  sleep 1
done
curl --fail --silent --show-error "$api_base_url/health/ready" >/dev/null || {
  tail -200 "$result_root/local-api.log" >&2
  die "local Rust API did not become ready at $api_base_url"
}

if [[ -z "$simulator_udid" ]]; then
  simulator_udid="$(
    xcrun simctl list devices available -j |
      jq -r '[.devices[][] | select(.state == "Booted" and (.name | startswith("iPhone")))][0].udid // empty'
  )"
fi
if [[ -z "$simulator_udid" ]]; then
  simulator_udid="$(
    xcrun simctl list devices available -j |
      jq -r '[.devices[][] | select(.name == "iPhone 17")][0].udid // [.devices[][] | select(.name | startswith("iPhone"))][0].udid // empty'
  )"
fi
[[ -n "$simulator_udid" ]] || die "no available iPhone Simulator found"
xcrun simctl boot "$simulator_udid" >/dev/null 2>&1 || true
xcrun simctl bootstatus "$simulator_udid" -b
# The disposable database restarts user IDs at one, while Simulator app data can retain
# per-user navigation and credentials from an earlier release run. Remove only the test app so
# the native lifecycle assertions start from the same clean client state as the fresh database.
xcrun simctl uninstall "$simulator_udid" org.willemaw.newsly >/dev/null 2>&1 || true

echo "== Native iOS tests on $simulator_udid =="
rm -rf "$xcresult_path"
set -o pipefail
xcodebuild test \
  -project client/newsly/newsly.xcodeproj \
  -scheme newsly \
  -destination "platform=iOS Simulator,id=$simulator_udid" \
  -derivedDataPath "$derived_data" \
  -resultBundlePath "$xcresult_path" \
  -parallel-testing-enabled NO \
  -maximum-parallel-testing-workers 1 \
  COMPILER_INDEX_STORE_ENABLE=NO \
  NEWSLY_E2E_SERVER_PORT="$api_port" \
  2>&1 | tee "$result_root/xcodebuild.log"

for authenticated_test in \
  testWarmResumeReturnsForegroundWithoutRelaunch \
  testProcessReclaimedRelaunchReturnsForeground
do
  if ! rg -q "${authenticated_test}.*passed" "$result_root/xcodebuild.log"; then
    die "authenticated UI test did not pass: $authenticated_test"
  fi
done
if rg -q "Authenticated lifecycle UI tests require" "$result_root/xcodebuild.log"; then
  die "authenticated UI tests skipped their local API setup"
fi
xcrun xcresulttool get test-results summary \
  --path "$xcresult_path" --compact >"$result_root/xcode-summary.json"

echo "== AXe Simulator smoke =="
APP_BUNDLE_PATH="$derived_data/Build/Products/Debug-iphonesimulator/newsly.app" \
  scripts/axe_simulator_smoke.sh \
  --udid "$simulator_udid" \
  --api-base-url "$api_base_url" \
  --output-dir "$result_root/axe"
if [[ -n "$service_pid" ]]; then
  kill -TERM "$service_pid" >/dev/null 2>&1 || true
  wait "$service_pid" 2>/dev/null || true
  service_pid=""
fi

if [[ "$with_live_smoke" == true ]]; then
  echo "== Production-shaped live API/LLM/E2B smoke =="
  scripts/smoke_local_staging.sh \
    --allow-live-provider-costs \
    --env-file "$env_file" \
    --report-dir "$result_root/local-staging-smoke"
fi

[[ "$(git rev-parse HEAD)" == "$release_sha" ]] || die "HEAD changed during the release gate"
[[ -z "$(git status --porcelain)" ]] || die "the release gate changed tracked or untracked files"

printf '%s\n' "$release_sha" >"$result_root/tested-sha.txt"
echo "Local release gate passed for $release_sha"
