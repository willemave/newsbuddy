#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "usage: run-worker.sh <queue> <worker-slot>" >&2
  exit 1
fi

cd /app

queue_name="$1"
worker_slot="$2"

/app/docker/wait-for-bootstrap.sh

worker_args=(
  --queue "${queue_name}"
  --worker-slot "${worker_slot}"
  --stats-interval "${WORKER_STATS_INTERVAL:-60}"
)

# Per-queue override, e.g. WORKER_THREADS_CONTENT=1 to roll back to one claim
# loop. Unset means the per-queue default from app/core/settings.py.
queue_threads_var="WORKER_THREADS_$(echo "${queue_name}" | tr '[:lower:]' '[:upper:]')"
queue_threads="${!queue_threads_var:-${WORKER_THREADS:-}}"
if [[ -n "${queue_threads}" ]]; then
  worker_args+=(--threads "${queue_threads}")
fi

exec python scripts/run_workers.py "${worker_args[@]}"
