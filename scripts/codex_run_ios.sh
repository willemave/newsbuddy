#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

simulator_udid="$(
  xcrun simctl list devices booted -j |
    jq -r '[.devices[][] | select(.state == "Booted" and .isAvailable != false)][0].udid // empty'
)"

if [[ -z "$simulator_udid" ]]; then
  echo "No booted iOS Simulator found. Boot one in Xcode and run this action again." >&2
  exit 1
fi

if ! curl --fail --silent --show-error http://localhost:8000/health >/dev/null; then
  echo "Newsly's local API is not healthy at http://localhost:8000." >&2
  echo "Start it with scripts/start_server.sh, then run this action again." >&2
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
uv run python scripts/dev_user.py setup --launch --simulator "$simulator_udid"

exec npx serve-sim "$simulator_udid"
