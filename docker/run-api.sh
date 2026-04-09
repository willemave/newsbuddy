#!/usr/bin/env bash
set -euo pipefail

cd /app

exec python -m uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --no-access-log
