#!/usr/bin/env bash
set -euo pipefail

# Run the state-verifying Newsly AXe interaction matrix on one explicit simulator.
# The app must already be built; this keeps build ownership separate from runtime proof.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UDID="${NEWSLY_AXE_SIMULATOR_ID:-}"
SIMULATOR_NAME="${NEWSLY_AXE_SIMULATOR_NAME:-}"
APP_PATH="${NEWSLY_AXE_APP_PATH:-/tmp/newsly_codex_build/Build/Products/Debug-iphonesimulator/newsly.app}"
ARTIFACT_ROOT="${NEWSLY_AXE_ARTIFACT_ROOT:-/tmp/newsly_axe_e2e_$(date +%Y%m%d_%H%M%S)}"
TEST_FILTER="${NEWSLY_AXE_TEST_FILTER:-}"

usage() {
  cat <<'EOF'
Usage: scripts/axe_interaction_matrix.sh [options]

Options:
  --udid <SIMULATOR_UDID>  Explicit booted simulator target.
  --app-path <NEWSLY.APP>   Current Debug simulator app bundle.
  --output-dir <DIRECTORY>  AX tree and screenshot artifact root.
  --filter <PYTEST_EXPR>    Optional pytest -k expression.
  -h, --help                Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --udid)
      UDID="${2:-}"
      shift 2
      ;;
    --app-path)
      APP_PATH="${2:-}"
      shift 2
      ;;
    --output-dir)
      ARTIFACT_ROOT="${2:-}"
      shift 2
      ;;
    --filter)
      TEST_FILTER="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

for command in axe python3 xcrun uv; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "Missing required command: $command" >&2
    exit 2
  fi
done

selector_args=()
if [[ -n "$UDID" ]]; then
  selector_args+=(--udid "$UDID")
elif [[ -n "$SIMULATOR_NAME" ]]; then
  selector_args+=(--name "$SIMULATOR_NAME")
fi
UDID="$(python3 "$REPO_ROOT/scripts/select_ios_simulator.py" "${selector_args[@]}")"

if [[ ! -d "$APP_PATH" ]]; then
  echo "Current Newsly app bundle not found: $APP_PATH" >&2
  exit 2
fi

if ! axe list-simulators | awk -F '|' -v udid="$UDID" '
  $1 ~ udid { found=1 }
  END { exit(found ? 0 : 1) }
'; then
  echo "Simulator is unavailable: $UDID" >&2
  exit 2
fi

if ! axe list-simulators | awk -F '|' -v udid="$UDID" '
  $1 ~ udid && $3 ~ /Booted/ { booted=1 }
  END { exit(booted ? 0 : 1) }
'; then
  echo "Booting simulator: $UDID"
  xcrun simctl boot "$UDID" >/dev/null
  xcrun simctl bootstatus "$UDID" -b >/dev/null
fi

mkdir -p "$ARTIFACT_ROOT"
export NEWSLY_AXE_SIMULATOR_ID="$UDID"
export NEWSLY_AXE_APP_PATH="$APP_PATH"
export NEWSLY_AXE_ARTIFACT_ROOT="$ARTIFACT_ROOT"

echo "AXe simulator: $UDID"
echo "App bundle: $APP_PATH"
echo "Artifacts: $ARTIFACT_ROOT"

pytest_args=(
  -vv
  -rs
  tests/ios_e2e/test_axe_interaction_matrix.py
)
if [[ -n "$TEST_FILTER" ]]; then
  pytest_args+=( -k "$TEST_FILTER" )
fi

uv run pytest "${pytest_args[@]}"
