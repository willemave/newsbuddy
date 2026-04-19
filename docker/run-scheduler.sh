#!/usr/bin/env bash
set -euo pipefail

/app/docker/wait-for-bootstrap.sh

cd /app

exec python /app/docker/supercronic.py /app/docker/crontab
