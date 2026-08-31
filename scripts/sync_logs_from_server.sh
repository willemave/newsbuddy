#!/usr/bin/env bash
set -euo pipefail

remote_target="${NEWSLY_REMOTE_TARGET:-news-app-server}"
remote_logs_dir="${NEWSLY_REMOTE_LOGS_DIR:-/data/logs}"
local_logs_dir="${NEWSLY_LOCAL_LOGS_DIR:-./logs_from_server}"

mkdir -p "$local_logs_dir"
rsync --archive --compress --progress \
  "${remote_target}:${remote_logs_dir%/}/" \
  "${local_logs_dir%/}/"

echo "Rust application logs synced to ${local_logs_dir}."
