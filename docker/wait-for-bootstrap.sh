#!/usr/bin/env bash
set -euo pipefail

bootstrap_ready_file="${NEWSLY_BOOTSTRAP_READY_FILE:-/tmp/newsly-bootstrap.ready}"

until [[ -f "${bootstrap_ready_file}" ]]; do
  echo "Waiting for Newsly bootstrap to complete..." >&2
  sleep 1
done
