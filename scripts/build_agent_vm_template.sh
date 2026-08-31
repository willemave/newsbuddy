#!/usr/bin/env bash
set -euo pipefail

readonly template_name="newsly-agent"
readonly cli_package="@e2b/cli"
readonly cli_version="2.18.0"
readonly cli_integrity="sha512-SOSnh/sgcVaPnsQ4coYImqiFXpVslKgiFTE9ZJEfqJJ0Ngl+3tUGZlFFaEhxTX6340kAmzQz5QOwymdTBvb2RQ=="
readonly sdk_package="e2b"
readonly sdk_version="2.46.1"
readonly sdk_integrity="sha512-OqYovS2oFrt4mk737CgfW/RoMadBYK84l5qjKpvbEoOB9KKxaZIXm7YUwOKSRTlijrrwDRX7oZlyPoVXiCpyTw=="

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
dockerfile_path="$repo_root/e2b.Dockerfile"
cpu_count=2
memory_mb=2048
mode=""
no_cache=false
receipt_path=""

usage() {
  cat <<'EOF'
Usage: scripts/build_agent_vm_template.sh (--check | --dry-run | --publish) [options]

Validate or publish the canonical Newsly E2B template with the pinned official CLI.

Modes:
  --check                 Validate local inputs without network access.
  --dry-run               Validate and print the exact publication command.
  --publish               Rebuild the canonical newsly-agent alias in E2B.

Options:
  --cpu-count COUNT       Template vCPU count (default: 2).
  --memory-mb MEBIBYTES   Even template memory size, at least 512 (default: 2048).
  --no-cache              Disable the E2B build cache for publication.
  --receipt PATH          Write the post-build JSON receipt to PATH.
  -h, --help              Show this help.

Publication requires E2B_API_KEY (or LLM_TASK_SANDBOX_E2B_API_KEY). It refuses
dirty e2b.Dockerfile or rust/ inputs so the receipt identifies an exact Git commit.
EOF
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

set_mode() {
  if [[ -n "$mode" ]]; then
    die "choose exactly one of --check, --dry-run, or --publish"
  fi
  mode="$1"
}

require_value() {
  local option="$1"
  local value="${2:-}"
  [[ -n "$value" ]] || die "$option requires a value"
}

while (($# > 0)); do
  case "$1" in
    --check)
      set_mode "check"
      shift
      ;;
    --dry-run)
      set_mode "dry-run"
      shift
      ;;
    --publish)
      set_mode "publish"
      shift
      ;;
    --cpu-count)
      require_value "$1" "${2:-}"
      cpu_count="$2"
      shift 2
      ;;
    --memory-mb)
      require_value "$1" "${2:-}"
      memory_mb="$2"
      shift 2
      ;;
    --no-cache)
      no_cache=true
      shift
      ;;
    --receipt)
      require_value "$1" "${2:-}"
      receipt_path="$2"
      shift 2
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

[[ -n "$mode" ]] || die "choose one of --check, --dry-run, or --publish"
[[ "$cpu_count" =~ ^[1-9][0-9]*$ ]] || die "--cpu-count must be a positive integer"
[[ "$memory_mb" =~ ^[0-9]+$ ]] || die "--memory-mb must be an integer"
((memory_mb >= 512)) || die "--memory-mb must be at least 512"
((memory_mb % 2 == 0)) || die "--memory-mb must be even"
if [[ "$mode" != "publish" && -n "$receipt_path" ]]; then
  die "--receipt is only valid with --publish"
fi

required_inputs=(
  "$dockerfile_path"
  "$repo_root/rust/Cargo.toml"
  "$repo_root/rust/Cargo.lock"
  "$repo_root/rust/crates/newsly-vm-bootstrap/Cargo.toml"
  "$repo_root/rust/crates/newsly-vm-bootstrap/src/lib.rs"
  "$repo_root/rust/crates/newsly-vm-bootstrap/src/main.rs"
)
for input_path in "${required_inputs[@]}"; do
  [[ -f "$input_path" ]] || die "missing required template input: ${input_path#"$repo_root"/}"
done

base_image="$(sed -n '1s/^FROM[[:space:]]\{1,\}//p' "$dockerfile_path")"
[[ "$base_image" =~ ^e2bdev/code-interpreter@sha256:[0-9a-f]{64}$ ]] ||
  die "e2b.Dockerfile must pin the E2B base image by sha256 digest"

required_dockerfile_fragments=(
  'COPY rust/Cargo.toml rust/Cargo.lock /opt/newsly-vm-bootstrap-build/'
  'COPY rust/crates /opt/newsly-vm-bootstrap-build/crates'
  'cargo build --locked --release --package newsly-vm-bootstrap'
  'target/release/newsly-vm-bootstrap /usr/local/bin/newsly-vm-bootstrap'
  'newsly-vm-bootstrap --help >/dev/null'
  'USER user'
  'WORKDIR /data/workspace'
)
for fragment in "${required_dockerfile_fragments[@]}"; do
  grep -Fq "$fragment" "$dockerfile_path" ||
    die "e2b.Dockerfile is missing required fragment: $fragment"
done

command -v cargo >/dev/null || die "cargo is required to validate the Rust helper package"
metadata="$({ cargo metadata \
  --manifest-path "$repo_root/rust/Cargo.toml" \
  --format-version 1 \
  --locked \
  --offline \
  --no-deps; } 2>/dev/null)" || die "Rust workspace metadata is invalid"
command -v jq >/dev/null || die "jq is required"
jq -e '.packages | any(.name == "newsly-vm-bootstrap")' >/dev/null <<<"$metadata" ||
  die "newsly-vm-bootstrap is not a Rust workspace package"

if command -v sha256sum >/dev/null; then
  dockerfile_sha256="$(sha256sum "$dockerfile_path" | awk '{print $1}')"
elif command -v shasum >/dev/null; then
  dockerfile_sha256="$(shasum -a 256 "$dockerfile_path" | awk '{print $1}')"
else
  die "sha256sum or shasum is required"
fi
source_sha="$(git -C "$repo_root" rev-parse HEAD)"
[[ "$source_sha" =~ ^[0-9a-f]{40}$ ]] || die "repository HEAD is not a full Git SHA"
dirty_inputs="$(git -C "$repo_root" status --porcelain --untracked-files=all -- e2b.Dockerfile rust)"
template_inputs_clean=true
if [[ -n "$dirty_inputs" ]]; then
  template_inputs_clean=false
fi

e2b_cli=(
  npm exec --yes
  --package "${cli_package}@${cli_version}"
  --package "${sdk_package}@${sdk_version}"
  --
  e2b
)
publication_command=(
  "${e2b_cli[@]}"
  template create "$template_name"
  --path "$repo_root"
  --dockerfile "$(basename "$dockerfile_path")"
  --cpu-count "$cpu_count"
  --memory-mb "$memory_mb"
)
if [[ "$no_cache" == true ]]; then
  publication_command+=(--no-cache)
fi

print_command() {
  printf 'E2B_API_KEY=[REDACTED]'
  printf ' %q' "${publication_command[@]}"
  printf '\n'
}

if [[ "$mode" == "check" ]]; then
  jq -n \
    --arg template_name "$template_name" \
    --arg cli "${cli_package}@${cli_version}" \
    --arg sdk "${sdk_package}@${sdk_version}" \
    --arg source_sha "$source_sha" \
    --arg dockerfile_sha256 "$dockerfile_sha256" \
    --argjson template_inputs_clean "$template_inputs_clean" \
    '{status:"valid", network_used:false, template_inputs_clean:$template_inputs_clean, template_name:$template_name, cli:$cli, sdk:$sdk, source_sha:$source_sha, dockerfile_sha256:$dockerfile_sha256}'
  exit 0
fi

if [[ "$mode" == "dry-run" ]]; then
  if [[ "$template_inputs_clean" != true ]]; then
    printf 'warning: e2b.Dockerfile or rust/ has uncommitted inputs; --publish would refuse this tree\n' >&2
  fi
  print_command
  exit 0
fi

publisher_api_key="${E2B_API_KEY:-${LLM_TASK_SANDBOX_E2B_API_KEY:-}}"
[[ -n "$publisher_api_key" ]] ||
  die "E2B_API_KEY or LLM_TASK_SANDBOX_E2B_API_KEY is required for --publish"
export E2B_API_KEY="$publisher_api_key"
command -v npm >/dev/null || die "npm is required for --publish"

[[ -z "$dirty_inputs" ]] ||
  die "refusing to publish dirty template inputs; commit and validate the exact SHA first"

registry_integrity="$(npm view "${cli_package}@${cli_version}" dist.integrity)"
[[ "$registry_integrity" == "$cli_integrity" ]] ||
  die "${cli_package}@${cli_version} registry integrity did not match the repository pin"
registry_sdk_integrity="$(npm view "${sdk_package}@${sdk_version}" dist.integrity)"
[[ "$registry_sdk_integrity" == "$sdk_integrity" ]] ||
  die "${sdk_package}@${sdk_version} registry integrity did not match the repository pin"

export CI="${CI:-1}"
resolved_cli_version="$("${e2b_cli[@]}" --version)"
[[ "$resolved_cli_version" == "$cli_version" ]] ||
  die "expected E2B CLI $cli_version, got $resolved_cli_version"

"${publication_command[@]}"

templates_json="$("${e2b_cli[@]}" template list --format json)"
template_json="$(jq -ce --arg alias "$template_name" '
  [.[] | select((.aliases // []) | index($alias))]
  | if length == 1 then .[0]
    elif length == 0 then error("published alias was not returned by E2B")
    else error("published alias resolved to multiple E2B templates")
    end
' <<<"$templates_json")"
jq -e '.templateID | type == "string" and length > 0' >/dev/null <<<"$template_json" ||
  die "E2B did not return a template ID for the published alias"

receipt="$(jq -cn \
  --arg template_name "$template_name" \
  --arg cli "${cli_package}@${cli_version}" \
  --arg sdk "${sdk_package}@${sdk_version}" \
  --arg source_sha "$source_sha" \
  --arg dockerfile_sha256 "$dockerfile_sha256" \
  --argjson template "$template_json" \
  '{status:"published", template_name:$template_name, template_id:$template.templateID, cli:$cli, sdk:$sdk, source_sha:$source_sha, dockerfile_sha256:$dockerfile_sha256, e2b:{cpu_count:$template.cpuCount, memory_mb:$template.memoryMB, envd_version:$template.envdVersion, created_at:$template.createdAt}}')"

printf '%s\n' "$receipt"
if [[ -n "$receipt_path" ]]; then
  receipt_directory="$(dirname "$receipt_path")"
  [[ -d "$receipt_directory" ]] || die "receipt directory does not exist: $receipt_directory"
  printf '%s\n' "$receipt" >"$receipt_path"
fi
