#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
flow_name="visual_main_screens.yaml"
namespace="maestro"
server_port="8000"
manifest_path=""
record_screenshots="false"

usage() {
  cat <<'EOF'
Usage: scripts/run_maestro_flow.sh [options]

Seeds the canonical local iOS fixture, reads its maestro_env contract, and runs one checked-in flow.

Options:
  --flow NAME       Flow filename under client/newsly/Maestro/flows.
  --namespace NAME  Fixture namespace passed to newsly-admin (default: maestro).
  --server-port N   Local Rust API port (default: 8000).
  --manifest PATH   Reuse an existing newsly-admin JSON success envelope instead of seeding.
  --record-screenshots
                    Record reference screenshots in flows that support it.
  -h, --help        Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --flow) flow_name="${2:?--flow requires a value}"; shift 2 ;;
    --namespace) namespace="${2:?--namespace requires a value}"; shift 2 ;;
    --server-port) server_port="${2:?--server-port requires a value}"; shift 2 ;;
    --manifest) manifest_path="${2:?--manifest requires a value}"; shift 2 ;;
    --record-screenshots) record_screenshots="true"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "${flow_name}" == */* || "${flow_name}" == .* ]]; then
  echo "--flow must name a file directly under client/newsly/Maestro/flows" >&2
  exit 2
fi
flow_path="${repo_root}/client/newsly/Maestro/flows/${flow_name}"
[[ -f "${flow_path}" ]] || { echo "Maestro flow not found: ${flow_path}" >&2; exit 2; }
command -v jq >/dev/null || { echo "jq is required" >&2; exit 127; }
command -v maestro >/dev/null || { echo "maestro is required" >&2; exit 127; }

if [[ -n "${manifest_path}" ]]; then
  manifest="$(cat "${manifest_path}")"
else
  cd "${repo_root}"
  manifest="$(cargo run --manifest-path rust/Cargo.toml -p newsly-admin --locked --offline -- \
    --output json e2e seed --namespace "${namespace}" --server-port "${server_port}" --confirm-local)"
fi

if [[ "$(jq -r '.ok // false' <<<"${manifest}")" != "true" ]]; then
  echo "newsly-admin did not return a successful fixture manifest" >&2
  exit 1
fi

required_variables="$(grep -Eo '\$\{[A-Z][A-Z0-9_]*' "${flow_path}" | sed 's/^\${//' | sort -u || true)"
maestro_args=(test)
while IFS= read -r variable; do
  [[ -n "${variable}" ]] || continue
  value="$(jq -er --arg key "${variable}" '.data.maestro_env[$key]' <<<"${manifest}")" || {
    echo "fixture manifest is missing Maestro variable ${variable}" >&2
    exit 1
  }
  if [[ "${variable}" == "RECORD_SCREENSHOTS" ]]; then
    value="${record_screenshots}"
  fi
  maestro_args+=(-e "${variable}=${value}")
done <<<"${required_variables}"
if [[ "${required_variables}" != *"RECORD_SCREENSHOTS"* ]]; then
  maestro_args+=(-e "RECORD_SCREENSHOTS=${record_screenshots}")
fi
maestro_args+=("${flow_path}")

cd "${repo_root}"
exec maestro "${maestro_args[@]}"
