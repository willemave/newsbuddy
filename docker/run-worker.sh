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

# Thread counts come from settings, so WORKER_THREADS_CONTENT=1 and friends are
# already picked up straight from the container environment.
exec python scripts/run_workers.py \
  --queue "${queue_name}" \
  --worker-slot "${worker_slot}" \
  --stats-interval "${WORKER_STATS_INTERVAL:-60}"
