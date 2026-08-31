#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd "${script_dir}/.." && pwd)"
logs_dir="${repository_root}/logs"
log_file="${logs_dir}/dev.log"
pid_file="${logs_dir}/dev.pids"

usage() {
  cat <<'EOF'
Usage: scripts/dev.sh [--kill|--status] [start-services command and options]

Without a command, starts the complete Rust runtime plus the isolated Crawl4AI extractor.
The wrapper manages one canonical start_services.sh invocation and writes to logs/dev.log.

Examples:
  scripts/dev.sh
  scripts/dev.sh all --env-file .env --local-e2e --port 8010
  scripts/dev.sh server --env-file .env --port 8010
EOF
}

stop_services() {
  if [[ ! -f "${pid_file}" ]]; then
    echo "No tracked development services."
    return
  fi
  while read -r pid name; do
    [[ "${pid}" =~ ^[0-9]+$ ]] || continue
    if kill -0 "${pid}" 2>/dev/null; then
      echo "stopping ${name} (${pid})"
      kill -TERM "${pid}" 2>/dev/null || true
    fi
  done < "${pid_file}"
  rm -f "${pid_file}"
}

show_status() {
  if [[ ! -f "${pid_file}" ]]; then
    echo "No tracked development services."
    return
  fi
  while read -r pid name; do
    if kill -0 "${pid}" 2>/dev/null; then
      echo "running  ${name} (${pid})"
    else
      echo "stopped  ${name} (${pid})"
    fi
  done < "${pid_file}"
}

case "${1:-}" in
  -k|--kill)
    stop_services
    exit 0
    ;;
  -s|--status)
    show_status
    exit 0
    ;;
  -h|--help)
    usage
    exit 0
    ;;
esac

runtime_args=("$@")
if [[ ${#runtime_args[@]} -eq 0 ]]; then
  runtime_args=(all)
fi

mkdir -p "${logs_dir}"
stop_services
: > "${log_file}"
: > "${pid_file}"

echo "starting: scripts/start_services.sh ${runtime_args[*]}"
(
  exec "${script_dir}/start_services.sh" "${runtime_args[@]}"
) >> "${log_file}" 2>&1 &
printf '%s %s\n' "$!" "runtime" >> "${pid_file}"

echo "Development services started. Logs: ${log_file}"
echo "Use scripts/dev.sh --status or scripts/dev.sh --kill."
tail -f "${log_file}"
