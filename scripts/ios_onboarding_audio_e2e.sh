#!/usr/bin/env bash
set -euo pipefail

# End-to-end onboarding run on the iOS simulator against the local Rust stack, with real
# audio. A spoken clip is synthesized with macOS `say` and handed to the app's DEBUG audio
# fixture: the real recorder, silence auto-stop, upload, backend transcription, discovery and
# completion all run; only the microphone input is replaced by the clip.
#
# Requires the local stack (scripts/dev.sh all --env-file .env --local-e2e), a booted
# simulator with a DEBUG build installed, and axe >= 1.8.
#
# Usage:
#   scripts/ios_onboarding_audio_e2e.sh
#   scripts/ios_onboarding_audio_e2e.sh --phrase "I read about chess and space launches"
#   scripts/ios_onboarding_audio_e2e.sh --voice Daniel --udid <SIM_UDID>

BUNDLE_ID="org.willemaw.newsly"
UDID=""
API_HOST="127.0.0.1"
API_PORT="8000"
VOICE="Samantha"
PHRASE="I mostly read about artificial intelligence research, climate technology, and Formula One racing. I also like long interviews with startup founders."
OUTPUT_DIR="${OUTPUT_DIR:-/tmp/newsly_onboarding_audio_e2e_$(date +%Y%m%d_%H%M%S)}"
DISCOVERY_TIMEOUT_SECONDS=240

usage() {
  cat <<'EOF'
Usage: scripts/ios_onboarding_audio_e2e.sh [options]

Options:
  --udid <SIM_UDID>    Simulator UDID (default: the booted simulator).
  --phrase <TEXT>      What the synthesized user says on the voice step.
  --voice <NAME>       macOS `say` voice (default: Samantha).
  --api-port <PORT>    Local Rust API port (default: 8000).
  --output-dir <DIR>   Screenshots, UI dumps, and the clip (default: /tmp/...).
  -h, --help           Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --udid) UDID="${2:?}"; shift 2 ;;
    --phrase) PHRASE="${2:?}"; shift 2 ;;
    --voice) VOICE="${2:?}"; shift 2 ;;
    --api-port) API_PORT="${2:?}"; shift 2 ;;
    --output-dir) OUTPUT_DIR="${2:?}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

export DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"

if [[ -z "${UDID}" ]]; then
  UDID="$(xcrun simctl list devices booted | grep -Eo '[0-9A-F-]{36}' | head -1 || true)"
fi
if [[ -z "${UDID}" ]]; then
  echo "No booted simulator; boot one or pass --udid." >&2
  exit 1
fi
if ! curl -sf "http://${API_HOST}:${API_PORT}/health" >/dev/null; then
  echo "Local API is not answering on ${API_HOST}:${API_PORT}; start scripts/dev.sh first." >&2
  exit 1
fi

mkdir -p "${OUTPUT_DIR}"
step=0

shot() {
  step=$((step + 1))
  local name
  name="$(printf '%02d_%s' "${step}" "$1")"
  # Let the step transition settle so the capture shows one screen, not a crossfade.
  sleep 1
  xcrun simctl io "${UDID}" screenshot "${OUTPUT_DIR}/${name}.png" >/dev/null 2>&1
  axe describe-ui --udid "${UDID}" > "${OUTPUT_DIR}/${name}.json" 2>/dev/null || true
  echo "  captured ${name}"
}

# Waits until an element with the accessibility identifier is on screen.
wait_for() {
  local identifier="$1" timeout="$2" waited=0 tree
  # Capture the whole tree first: piping into `grep -q` closes the pipe early on large screens,
  # and under pipefail that broken pipe reads as "not found".
  until tree="$(axe describe-ui --udid "${UDID}" 2>/dev/null)" \
    && grep -q "\"AXUniqueId\" : \"${identifier}\"" <<<"${tree}"; do
    if (( waited >= timeout )); then
      echo "Timed out after ${timeout}s waiting for ${identifier}" >&2
      shot "timeout_${identifier//./_}"
      exit 1
    fi
    sleep 1
    waited=$((waited + 1))
  done
}

tap() {
  axe tap --id "$1" --wait-timeout 15 --udid "${UDID}" >/dev/null
}

echo "Synthesizing the spoken answer (${VOICE}): ${PHRASE}"
clip="${OUTPUT_DIR}/onboarding_answer.m4a"
say -v "${VOICE}" -o "${clip}" --file-format=m4af --data-format=aac@16000 "${PHRASE}"

# The app reads the clip from its own container so simulator sandboxing never matters.
container="$(xcrun simctl get_app_container "${UDID}" "${BUNDLE_ID}" data)"
mkdir -p "${container}/tmp"
cp "${clip}" "${container}/tmp/onboarding_answer.m4a"

xcrun simctl privacy "${UDID}" grant microphone "${BUNDLE_ID}" >/dev/null 2>&1 || true
xcrun simctl terminate "${UDID}" "${BUNDLE_ID}" >/dev/null 2>&1 || true

echo "Launching a fresh debug user against http://${API_HOST}:${API_PORT}"
xcrun simctl launch "${UDID}" "${BUNDLE_ID}" \
  -newslyE2EEnabled YES \
  -newslyE2EAutoLogin YES \
  -newslyE2EServerHost "${API_HOST}" \
  -newslyE2EServerPort "${API_PORT}" \
  -newslyE2EUseHTTPS NO \
  -newslyE2ECompleteOnboarding NO \
  -newslyE2ECompleteTutorial YES \
  -newslyE2EAudioFixture "${container}/tmp/onboarding_answer.m4a" >/dev/null

wait_for onboarding.intro.continue 60
shot intro
tap onboarding.intro.continue

wait_for onboarding.choice.personalized 15
shot choice
tap onboarding.choice.personalized

wait_for onboarding.audio.state.recording 20
shot recording
echo "Recording; the clip plays as speech, then silence triggers the real auto-stop."

wait_for onboarding.loading.screen 90
shot matching
echo "Discovery running (up to ${DISCOVERY_TIMEOUT_SECONDS}s)."

wait_for onboarding.suggestions.screen "${DISCOVERY_TIMEOUT_SECONDS}"
shot picks
tap onboarding.suggestions.continue

wait_for onboarding.aggregators.continue 15
shot aggregators
tap onboarding.aggregators.continue

wait_for onboarding.complete 15
shot reddit
tap onboarding.complete

wait_for briefing.screen 90
shot briefing

echo "Onboarding completed end to end. Artifacts: ${OUTPUT_DIR}"
