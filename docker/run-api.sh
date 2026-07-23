#!/usr/bin/env bash
set -euo pipefail

/app/docker/wait-for-bootstrap.sh

cd /app

exec python -m uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --proxy-headers \
  --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}" \
  --no-access-log
