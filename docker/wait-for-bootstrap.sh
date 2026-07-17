#!/usr/bin/env bash
set -euo pipefail

if [[ "${NEWSLY_WAIT_FOR_BOOTSTRAP:-true}" == "false" ]]; then
  exit 0
fi

bootstrap_ready_file="${NEWSLY_BOOTSTRAP_READY_FILE:-/tmp/newsly-bootstrap.ready}"

until [[ -f "${bootstrap_ready_file}" ]]; do
  echo "Waiting for Newsly bootstrap to complete..." >&2
  sleep 1
done
