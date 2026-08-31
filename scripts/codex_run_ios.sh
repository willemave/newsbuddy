#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"
script_dir="${repo_root}/scripts"
# shellcheck source=scripts/lib/rust_runtime.sh
source "${script_dir}/lib/rust_runtime.sh"

api_base_url="${NEWSLY_CODEX_API_BASE_URL:-${NEWSLY_LOCAL_API_BASE_URL:-http://127.0.0.1:8000}}"

usage() {
  cat <<'EOF'
Usage: scripts/codex_run_ios.sh [--api-base-url URL]

Build, install, and launch Newsly against an explicit local Rust API origin.
The default is http://127.0.0.1:8000 and can also be set with
NEWSLY_CODEX_API_BASE_URL or NEWSLY_LOCAL_API_BASE_URL.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --api-base-url)
      if [[ -z "${2:-}" ]]; then
        echo "--api-base-url requires a value" >&2
        exit 2
      fi
      api_base_url="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

IFS=$'\t' read -r api_scheme api_host api_port api_use_https \
  < <(newsly_parse_api_base_url "${api_base_url}")
api_base_url="${api_scheme}://${api_host}:${api_port}"

simulator_udid="$(
  xcrun simctl list devices booted -j |
    jq -r '[.devices[][] | select(.state == "Booted" and .isAvailable != false)][0].udid // empty'
)"

if [[ -z "$simulator_udid" ]]; then
  echo "No booted iOS Simulator found. Boot one in Xcode and run this action again." >&2
  exit 1
fi

if ! curl --fail --silent --show-error "${api_base_url}/health" >/dev/null; then
  echo "Newsly's local API is not healthy at ${api_base_url}." >&2
  echo "Start it with scripts/start_services.sh server --port ${api_port}, then run this action again." >&2
  exit 1
fi

derived_data_path="${NEWSLY_CODEX_DERIVED_DATA:-/tmp/newsly_codex_build}"
app_path="$derived_data_path/Build/Products/Debug-iphonesimulator/newsly.app"

xcodebuild \
  -project client/newsly/newsly.xcodeproj \
  -scheme newsly \
  -sdk iphonesimulator \
  -configuration Debug \
  -destination "platform=iOS Simulator,id=$simulator_udid" \
  -derivedDataPath "$derived_data_path" \
  build

xcrun simctl install "$simulator_udid" "$app_path"

debug_user_id="$(
  curl --fail --silent --show-error \
    --request POST \
    --header 'Content-Type: application/json' \
    --data '{"has_completed_onboarding":true,"has_completed_new_user_tutorial":true}' \
    "${api_base_url}/auth/debug/new-user" |
    jq -er '.user.id'
)"

xcrun simctl launch "$simulator_udid" org.willemaw.newsly \
  -newslyE2EEnabled true \
  -newslyE2EAutoLogin true \
  -newslyE2EServerHost "${api_host}" \
  -newslyE2EServerPort "${api_port}" \
  -newslyE2EUseHTTPS "${api_use_https}" \
  -newslyE2EUserId "$debug_user_id" \
  -newslyE2ECompleteOnboarding true \
  -newslyE2ECompleteTutorial true

exec npx serve-sim "$simulator_udid"
