#!/usr/bin/env bash
set -euo pipefail

# Lightweight helper to push local `.env.racknerd` to remote, then mirror to `.env`.
# This is env-sync only. Production app deploys are handled by GitHub Actions.

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/common.sh"

REMOTE_HOST="willem@192.3.250.10"
REMOTE_DIR="/opt/news_app"
OWNER_GROUP="newsapp:newsapp"
SOURCE_ENV_FILE=".env.racknerd"
REMOTE_STAGING_DIR="/tmp/news_app_env_sync"
REMOTE_PORT="22"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--host)
      require_option_value "$1" "${2:-}"
      REMOTE_HOST="$2"
      shift 2
      ;;
    -d|--dir)
      require_option_value "$1" "${2:-}"
      REMOTE_DIR="$2"
      shift 2
      ;;
    -o|--owner)
      require_option_value "$1" "${2:-}"
      OWNER_GROUP="$2"
      shift 2
      ;;
    -p|--port)
      require_option_value "$1" "${2:-}"
      REMOTE_PORT="$2"
      shift 2
      ;;
    -s|--source)
      require_option_value "$1" "${2:-}"
      SOURCE_ENV_FILE="$2"
      shift 2
      ;;
    --staging)
      require_option_value "$1" "${2:-}"
      REMOTE_STAGING_DIR="$2"
      shift 2
      ;;
    --help|-\?)
      cat <<'USAGE'
Usage: scripts/deploy/push_envs.sh [--host user@host] [--port 22] [--dir /remote/path] [--owner user:group] [--source .env.racknerd]

Pushes local .env.racknerd to remote and mirrors it to .env using sudo cp.
USAGE
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

deploy_sync_env_file \
  "$REMOTE_HOST" \
  "$REMOTE_DIR" \
  "$OWNER_GROUP" \
  "$SOURCE_ENV_FILE" \
  "$REMOTE_STAGING_DIR" \
  "$REMOTE_PORT"

echo "✅ Remote .env.racknerd and .env updated"
