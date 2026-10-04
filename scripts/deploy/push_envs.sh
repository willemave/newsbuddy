#!/usr/bin/env bash
set -euo pipefail

# Explicit env-only host sync. Production app deploys are handled by GitHub Actions.

REMOTE_HOST="news-app-server"
REMOTE_DIR="/opt/news_app"
OWNER_GROUP="willem:willem"
SOURCE_ENV_FILE=".env.racknerd"
REMOTE_PORT="22"

usage() {
  cat <<'USAGE'
Usage: scripts/deploy/push_envs.sh [--host user@host] [--port 22] [--dir /remote/path] [--owner user:group] [--source .env.racknerd]

Validates the production compose keys, then atomically installs .env.racknerd on the host.
USAGE
}

require_value() {
  [[ -n "${2:-}" && "$2" != -* ]] || { echo "Option $1 requires a value" >&2; exit 2; }
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--host)
      require_value "$1" "${2:-}"
      REMOTE_HOST="$2"
      shift 2
      ;;
    -d|--dir)
      require_value "$1" "${2:-}"
      REMOTE_DIR="$2"
      shift 2
      ;;
    -o|--owner)
      require_value "$1" "${2:-}"
      OWNER_GROUP="$2"
      shift 2
      ;;
    -p|--port)
      require_value "$1" "${2:-}"
      REMOTE_PORT="$2"
      shift 2
      ;;
    -s|--source)
      require_value "$1" "${2:-}"
      SOURCE_ENV_FILE="$2"
      shift 2
      ;;
    --help|-\?)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

[[ -f "$SOURCE_ENV_FILE" ]] || { echo "Local source env file not found: $SOURCE_ENV_FILE" >&2; exit 1; }
[[ "$REMOTE_HOST" =~ ^([A-Za-z0-9._-]+@)?[A-Za-z0-9._-]+$ ]] || { echo "Invalid remote host" >&2; exit 2; }
[[ "$REMOTE_PORT" =~ ^[0-9]+$ ]] || { echo "Invalid remote port" >&2; exit 2; }
[[ "$REMOTE_DIR" =~ ^/[A-Za-z0-9._/-]+$ ]] || { echo "Invalid remote directory" >&2; exit 2; }
[[ "$OWNER_GROUP" =~ ^[A-Za-z_][A-Za-z0-9_-]*:[A-Za-z_][A-Za-z0-9_-]*$ ]] || { echo "Owner must be user:group" >&2; exit 2; }

seen_keys=$'\n'
while IFS= read -r line || [[ -n "$line" ]]; do
  [[ "$line" =~ ^[[:space:]]*$ || "$line" =~ ^[[:space:]]*# ]] && continue
  [[ "$line" =~ ^([A-Z][A-Z0-9_]*)= ]] || { echo "Invalid dotenv assignment" >&2; exit 1; }
  key="${BASH_REMATCH[1]}"
  value="${line#*=}"
  case "$key" in
    POSTGRES_PASSWORD|NEWSLY_DATABASE_URL|DOCUMENT_EXTRACTOR_SHARED_SECRET)
      [[ -n "$value" && "$value" != "''" && "$value" != '""' ]] || { echo "Required dotenv key is empty: $key" >&2; exit 1; }
      ;;
  esac
  [[ "$seen_keys" != *$'\n'"$key"$'\n'* ]] || { echo "Duplicate dotenv key: $key" >&2; exit 1; }
  seen_keys+="$key"$'\n'
done <"$SOURCE_ENV_FILE"
for key in POSTGRES_PASSWORD NEWSLY_DATABASE_URL DOCUMENT_EXTRACTOR_SHARED_SECRET; do
  [[ "$seen_keys" == *$'\n'"$key"$'\n'* ]] || { echo "Missing required dotenv key: $key" >&2; exit 1; }
done

service_user="${OWNER_GROUP%%:*}"
service_group="${OWNER_GROUP##*:}"
remote_command="set -euo pipefail; sudo mkdir -p '$REMOTE_DIR'; temporary=\$(sudo mktemp '$REMOTE_DIR/.env.racknerd.XXXXXX'); trap 'sudo rm -f \"\$temporary\"' EXIT; sudo install -m 600 -o '$service_user' -g '$service_group' /dev/stdin \"\$temporary\"; sudo mv -f \"\$temporary\" '$REMOTE_DIR/.env.racknerd'; trap - EXIT"

ssh -p "$REMOTE_PORT" -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
  "$REMOTE_HOST" "$remote_command" <"$SOURCE_ENV_FILE"

echo "✅ Remote .env.racknerd updated"
