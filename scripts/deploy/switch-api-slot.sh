#!/usr/bin/env bash
set -euo pipefail

slot="${1:-}"
case "${slot}" in
  blue)
    port=8001
    ;;
  green)
    port=8002
    ;;
  *)
    echo "usage: switch-api-slot.sh blue|green" >&2
    exit 2
    ;;
esac

upstream_file="/etc/nginx/newsly-active-upstream.conf"
state_dir="/opt/newsly/state"
candidate="$(mktemp)"
backup="$(mktemp)"
had_upstream=false

cleanup() {
  rm -f "${candidate}" "${backup}"
}
trap cleanup EXIT

restore_upstream() {
  if [[ "${had_upstream}" == true ]]; then
    install -m 644 "${backup}" "${upstream_file}"
  else
    rm -f "${upstream_file}"
  fi
}

rollback_upstream() {
  restore_upstream
  nginx -t >/dev/null 2>&1 || true
  systemctl reload nginx >/dev/null 2>&1 || true
}

curl --fail --silent --show-error --max-time 10 \
  "http://127.0.0.1:${port}/health" >/dev/null

printf 'server 127.0.0.1:%s;\n' "${port}" > "${candidate}"
if [[ -f "${upstream_file}" ]]; then
  cp "${upstream_file}" "${backup}"
  had_upstream=true
fi
install -m 644 "${candidate}" "${upstream_file}"

if ! nginx -t; then
  restore_upstream
  exit 1
fi

if ! systemctl reload nginx; then
  rollback_upstream
  exit 1
fi
active_upstream_healthy=false
for attempt in $(seq 1 10); do
  if curl --fail --silent --show-error --max-time 10 \
    http://127.0.0.1/health >/dev/null; then
    active_upstream_healthy=true
    break
  fi
  if [[ "${attempt}" -lt 10 ]]; then
    sleep 1
  fi
done
if [[ "${active_upstream_healthy}" != true ]]; then
  rollback_upstream
  exit 1
fi
mkdir -p "${state_dir}"
printf '%s\n' "${slot}" > "${state_dir}/active-api-slot"
echo "active API slot: ${slot} (127.0.0.1:${port})"
