#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROJECT_PATH="$REPO_ROOT/client/newsly/newsly.xcodeproj"
SCHEME="newsly"
APP_ID="${NEWSLY_MAESTRO_APP_ID:-org.willemaw.newsly}"
DERIVED_DATA_PATH="${NEWSLY_MAESTRO_DERIVED_DATA:-$REPO_ROOT/.derived-data/maestro}"

export PATH="$HOME/.maestro/bin:$PATH"

if ! java -version >/dev/null 2>&1; then
  if brew --prefix openjdk@21 >/dev/null 2>&1; then
    export PATH="$(brew --prefix openjdk@21)/bin:$PATH"
  fi
fi

if ! command -v maestro >/dev/null 2>&1; then
  echo "Maestro is not installed. Run tests/scripts/install_maestro.sh first." >&2
  exit 1
fi

if ! java -version >/dev/null 2>&1; then
  echo "Java runtime not found. Run tests/scripts/install_maestro.sh first." >&2
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required to run the pytest harness." >&2
  exit 1
fi

SIMULATOR_ID="$(
python3 - <<'PY'
import json
import os
import subprocess
import sys

specified = os.environ.get("NEWSLY_MAESTRO_SIMULATOR_ID")
if specified:
    print(specified)
    raise SystemExit

def load(*args: str) -> dict:
    return json.loads(subprocess.check_output(["xcrun", "simctl", "list", *args, "-j"], text=True))

booted = load("devices", "booted")
for runtime_devices in booted.get("devices", {}).values():
    for device in runtime_devices:
        if device.get("state") == "Booted" and device.get("isAvailable", True):
            print(device["udid"])
            raise SystemExit

available = load("devices", "available")
preferred_names = ["iPhone 16 Pro", "iPhone 16", "iPhone 15 Pro", "iPhone 15"]
fallback = None
for runtime_devices in available.get("devices", {}).values():
    for device in runtime_devices:
        if not device.get("isAvailable", True):
            continue
        name = device.get("name", "")
        if "iPhone" not in name:
            continue
        if fallback is None:
            fallback = device["udid"]
        if name in preferred_names:
            print(device["udid"])
            raise SystemExit

if fallback:
    print(fallback)
    raise SystemExit

sys.exit("No available iPhone simulator found")
PY
)"

open -a Simulator
xcrun simctl boot "$SIMULATOR_ID" >/dev/null 2>&1 || true
xcrun simctl bootstatus "$SIMULATOR_ID" -b

mkdir -p "$DERIVED_DATA_PATH"

xcodebuild \
  -project "$PROJECT_PATH" \
  -scheme "$SCHEME" \
  -configuration Debug \
  -destination "id=$SIMULATOR_ID" \
  -derivedDataPath "$DERIVED_DATA_PATH" \
  build

APP_PATH="$DERIVED_DATA_PATH/Build/Products/Debug-iphonesimulator/newsly.app"
if [[ ! -d "$APP_PATH" ]]; then
  echo "Built app not found at $APP_PATH" >&2
  exit 1
fi

xcrun simctl uninstall "$SIMULATOR_ID" "$APP_ID" >/dev/null 2>&1 || true
xcrun simctl install "$SIMULATOR_ID" "$APP_PATH"

export NEWSLY_MAESTRO_APP_ID="$APP_ID"

cd "$REPO_ROOT"
uv run pytest tests/ios_e2e "$@"
