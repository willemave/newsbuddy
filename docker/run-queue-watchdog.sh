#!/usr/bin/env bash
set -euo pipefail

/app/docker/wait-for-bootstrap.sh

cd /app

exec python /app/scripts/watchdog_queue_recovery.py --loop --interval-seconds 300
