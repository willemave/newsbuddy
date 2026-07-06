#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROJECT_PATH="$REPO_ROOT/client/newsly/newsly.xcodeproj"
SCHEME="newsly"
APP_ID="${NEWSLY_MAESTRO_APP_ID:-org.willemaw.newsly}"
DERIVED_DATA_PATH="${NEWSLY_MAESTRO_DERIVED_DATA:-$REPO_ROOT/.derived-data/maestro}"

export PATH="$HOME/.maestro/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

if ! java -version >/dev/null 2>&1; then
  for java_bin_dir in /opt/homebrew/opt/openjdk@21/bin /usr/local/opt/openjdk@21/bin; do
    if [[ -x "$java_bin_dir/java" ]]; then
      export PATH="$java_bin_dir:$PATH"
      break
    fi
  done
fi

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

specified_name = os.environ.get("NEWSLY_MAESTRO_SIMULATOR_NAME")

def load(*args: str) -> dict:
    return json.loads(subprocess.check_output(["xcrun", "simctl", "list", *args, "-j"], text=True))

if specified_name:
    for device_sets in (load("devices", "booted"), load("devices", "available")):
        for runtime_devices in device_sets.get("devices", {}).values():
            for device in runtime_devices:
                if (
                    device.get("name") == specified_name
                    and device.get("isAvailable", True)
                ):
                    print(device["udid"])
                    raise SystemExit
    sys.exit(f"No available simulator named {specified_name!r} found")

booted = load("devices", "booted")
for runtime_devices in booted.get("devices", {}).values():
    for device in runtime_devices:
        if device.get("state") == "Booted" and device.get("isAvailable", True):
            print(device["udid"])
            raise SystemExit

available = load("devices", "available")
preferred_names = ["iPhone 17 Pro", "iPhone 17", "iPhone 16 Pro", "iPhone 16", "iPhone 15 Pro", "iPhone 15"]
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

if [[ -n "${NEWSLY_MAESTRO_APPEARANCE:-}" ]]; then
  xcrun simctl ui "$SIMULATOR_ID" appearance "$NEWSLY_MAESTRO_APPEARANCE"
fi

if [[ "${NEWSLY_MAESTRO_FREEZE_STATUS_BAR:-1}" != "0" ]]; then
  xcrun simctl status_bar "$SIMULATOR_ID" override \
    --time "${NEWSLY_MAESTRO_STATUS_BAR_TIME:-9:41}" \
    --dataNetwork 5g \
    --cellularMode active \
    --cellularBars 4 \
    --wifiMode active \
    --wifiBars 3 \
    --batteryState charged \
    --batteryLevel 100
fi

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
export NEWSLY_MAESTRO_SIMULATOR_ID="$SIMULATOR_ID"

cd "$REPO_ROOT"
uv run pytest tests/ios_e2e "$@"
