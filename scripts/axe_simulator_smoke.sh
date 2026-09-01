#!/usr/bin/env bash
set -euo pipefail

# Smoke test helper for iOS simulator + axe CLI.
#
# Usage:
#   scripts/axe_simulator_smoke.sh
#   scripts/axe_simulator_smoke.sh --udid <SIM_UDID> --bundle-id org.willemaw.newsly
#   scripts/axe_simulator_smoke.sh --record-video --capture-logs
#
# Environment overrides:
#   OUTPUT_DIR=/tmp/axe_newsly_smoke
#   BUILD_BEFORE_RUN=1
#   XCODE_PROJECT=client/newsly/newsly.xcodeproj
#   XCODE_SCHEME=newsly
#   NEWSLY_AXE_API_BASE_URL=http://127.0.0.1:8000

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/rust_runtime.sh
source "${script_dir}/lib/rust_runtime.sh"

BUNDLE_ID="org.willemaw.newsly"
UDID=""
OUTPUT_DIR="${OUTPUT_DIR:-/tmp/axe_newsly_smoke_$(date +%Y%m%d_%H%M%S)}"
BUILD_BEFORE_RUN="${BUILD_BEFORE_RUN:-0}"
XCODE_PROJECT="${XCODE_PROJECT:-client/newsly/newsly.xcodeproj}"
XCODE_SCHEME="${XCODE_SCHEME:-newsly}"
APP_BUNDLE_PATH="${APP_BUNDLE_PATH:-/tmp/newsly_codex_build/Build/Products/Debug-iphonesimulator/newsly.app}"
API_BASE_URL="${NEWSLY_AXE_API_BASE_URL:-${NEWSLY_LOCAL_API_BASE_URL:-http://127.0.0.1:8000}}"
INSTALL_APP="${INSTALL_APP:-1}"
RECORD_VIDEO=0
CAPTURE_LOGS=0

usage() {
  cat <<'EOF'
Usage: scripts/axe_simulator_smoke.sh [options]

Options:
  --udid <SIM_UDID>       Simulator UDID. If omitted, auto-selects booted sim.
  --bundle-id <BUNDLE_ID> App bundle id (default: org.willemaw.newsly).
  --api-base-url <URL>    Local Rust API origin (default: http://127.0.0.1:8000).
  --output-dir <DIR>      Output folder for artifacts.
  --build                 Build app before launch.
  --no-install            Skip simulator app install before launch.
  --app-path <PATH>       App bundle path to install (default: /tmp/newsly_codex_build/.../newsly.app).
  --record-video          Record simulator video during smoke run.
  --capture-logs          Capture simulator logs during smoke run.
  -h, --help              Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --udid)
      UDID="${2:-}"
      shift 2
      ;;
    --bundle-id)
      BUNDLE_ID="${2:-}"
      shift 2
      ;;
    --api-base-url)
      if [[ -z "${2:-}" ]]; then
        echo "--api-base-url requires a value" >&2
        exit 2
      fi
      API_BASE_URL="${2:-}"
      shift 2
      ;;
    --output-dir)
      OUTPUT_DIR="${2:-}"
      shift 2
      ;;
    --build)
      BUILD_BEFORE_RUN=1
      shift
      ;;
    --no-install)
      INSTALL_APP=0
      shift
      ;;
    --app-path)
      APP_BUNDLE_PATH="${2:-}"
      shift 2
      ;;
    --record-video)
      RECORD_VIDEO=1
      shift
      ;;
    --capture-logs)
      CAPTURE_LOGS=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

IFS=$'\t' read -r api_scheme api_host api_port api_use_https \
  < <(newsly_parse_api_base_url "${API_BASE_URL}")
API_BASE_URL="${api_scheme}://${api_host}:${api_port}"

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

require_cmd axe
require_cmd curl
require_cmd xcrun
require_cmd jq

if [[ -z "$UDID" ]]; then
  UDID="$(axe list-simulators | awk -F '|' '$3 ~ /Booted/ {gsub(/^[[:space:]]+|[[:space:]]+$/, "", $1); print $1; exit}')"
fi

if [[ -z "$UDID" ]]; then
  echo "No booted simulator found. Boot one first, or pass --udid." >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"
echo "Using simulator: $UDID"
echo "Output dir: $OUTPUT_DIR"

if [[ "$BUILD_BEFORE_RUN" == "1" ]]; then
  echo "Building app..."
  xcodebuild \
    -project "$XCODE_PROJECT" \
    -scheme "$XCODE_SCHEME" \
    -sdk iphonesimulator \
    -configuration Debug \
    -derivedDataPath /tmp/newsly_codex_build \
    build >/dev/null
fi

echo "Ensuring simulator is booted..."
xcrun simctl bootstatus "$UDID" -b >/dev/null

if [[ "$INSTALL_APP" == "1" ]]; then
  if [[ -d "$APP_BUNDLE_PATH" ]]; then
    echo "Installing app bundle: $APP_BUNDLE_PATH"
    xcrun simctl install "$UDID" "$APP_BUNDLE_PATH" >/dev/null
  else
    echo "Skipping install; app bundle not found at $APP_BUNDLE_PATH"
  fi
fi

LOG_PID=""
if [[ "$CAPTURE_LOGS" == "1" ]]; then
  echo "Starting simulator log capture..."
  xcrun simctl spawn "$UDID" log stream --style compact \
    --predicate "processImagePath CONTAINS 'newsly'" \
    > "$OUTPUT_DIR/sim_logs.txt" 2>&1 &
  LOG_PID="$!"
fi

VIDEO_PID=""
if [[ "$RECORD_VIDEO" == "1" ]]; then
  echo "Starting video recording..."
  axe record-video --udid "$UDID" --output "$OUTPUT_DIR/smoke.mp4" >/dev/null 2>&1 &
  VIDEO_PID="$!"
fi

cleanup() {
  if [[ -n "$VIDEO_PID" ]]; then
    kill "$VIDEO_PID" >/dev/null 2>&1 || true
  fi
  if [[ -n "$LOG_PID" ]]; then
    kill "$LOG_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

echo "Launching app: $BUNDLE_ID"
debug_user_id="$(
  curl --fail --silent --show-error \
    --request POST \
    --header 'Content-Type: application/json' \
    --data '{"has_completed_onboarding":true,"has_completed_new_user_tutorial":true}' \
    "${API_BASE_URL%/}/auth/debug/new-user" |
    jq -er '.user.id'
)"

launch_authenticated_app() {
  xcrun simctl launch "$UDID" "$BUNDLE_ID" \
    -newslyE2EEnabled true \
    -newslyE2EAutoLogin true \
    -newslyE2EServerHost "${api_host}" \
    -newslyE2EServerPort "${api_port}" \
    -newslyE2EUseHTTPS "${api_use_https}" \
    -newslyE2EUserId "$debug_user_id" \
    -newslyE2ECompleteOnboarding true \
    -newslyE2ECompleteTutorial true >/dev/null
}

capture_nonempty_ui_tree() {
  local output_path="$1"
  local attempt
  for attempt in {1..5}; do
    axe describe-ui --udid "$UDID" >"$output_path"
    if ! jq -e '
      length == 1
      and .[0].type == "Application"
      and ((.[0].children // []) | length == 0)
      and ((.[0].frame.width // 0) == 0)
      and ((.[0].frame.height // 0) == 0)
    ' "$output_path" >/dev/null; then
      return 0
    fi
    sleep 1
  done
  return 1
}

assert_id() {
  local identifier="$1"
  local ui_file="$2"
  if ! jq -e --arg identifier "$identifier" \
    '.. | objects | select(.AXUniqueId == $identifier)' "$ui_file" >/dev/null; then
    echo "Required accessibility identifier not found: $identifier" >&2
    exit 1
  fi
}

launch_authenticated_app
sleep 1

echo "Capturing launch artifacts..."
if ! capture_nonempty_ui_tree "$OUTPUT_DIR/00_launch_ui.json"; then
  echo "AXe returned an empty zero-size application tree; rebooting the Simulator bridge once..."
  xcrun simctl terminate "$UDID" "$BUNDLE_ID" >/dev/null 2>&1 || true
  xcrun simctl shutdown "$UDID"
  xcrun simctl boot "$UDID"
  xcrun simctl bootstatus "$UDID" -b >/dev/null
  launch_authenticated_app
  sleep 1
  if ! capture_nonempty_ui_tree "$OUTPUT_DIR/00_launch_ui.json"; then
    echo "AXe accessibility tree remained empty after one Simulator reboot." >&2
    exit 1
  fi
fi

if ! jq -e \
  '.. | objects | select(.AXUniqueId == "briefing.screen")' \
  "$OUTPUT_DIR/00_launch_ui.json" >/dev/null; then
  assert_id "tab.briefing" "$OUTPUT_DIR/00_launch_ui.json"
  echo "Selecting Briefing tab to normalize persisted root-tab state..."
  axe tap --id "tab.briefing" --udid "$UDID" --wait-timeout 5 --post-delay 1 >/dev/null
  axe describe-ui --udid "$UDID" >"$OUTPUT_DIR/00_launch_ui.json"
fi
axe screenshot --udid "$UDID" --output "$OUTPUT_DIR/00_launch.png" >/dev/null
assert_id "briefing.screen" "$OUTPUT_DIR/00_launch_ui.json"

echo "Navigating to Knowledge tab..."
axe tap --id "tab.knowledge" --udid "$UDID" --wait-timeout 5 --post-delay 1 >/dev/null
axe describe-ui --udid "$UDID" > "$OUTPUT_DIR/01_knowledge_ui.json"
axe screenshot --udid "$UDID" --output "$OUTPUT_DIR/01_knowledge.png" >/dev/null
assert_id "knowledge.screen" "$OUTPUT_DIR/01_knowledge_ui.json"

echo "Opening More sheet..."
axe tap --id "knowledge.more_menu" --udid "$UDID" --wait-timeout 5 --post-delay 1 >/dev/null
axe describe-ui --udid "$UDID" > "$OUTPUT_DIR/02_more_ui.json"
axe screenshot --udid "$UDID" --output "$OUTPUT_DIR/02_more.png" >/dev/null
assert_id "more.screen" "$OUTPUT_DIR/02_more_ui.json"

echo "Opening Search from More..."
axe tap --id "more.search" --udid "$UDID" --wait-timeout 5 --post-delay 1 >/dev/null
axe describe-ui --udid "$UDID" > "$OUTPUT_DIR/03_search_ui.json"
axe screenshot --udid "$UDID" --output "$OUTPUT_DIR/03_search.png" >/dev/null
assert_id "search.input" "$OUTPUT_DIR/03_search_ui.json"

echo "Done. Artifacts written to:"
echo "  $OUTPUT_DIR"
ls -1 "$OUTPUT_DIR" | sed 's/^/  - /'
