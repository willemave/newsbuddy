#!/usr/bin/env bash
set -euo pipefail

IMAGE="${BGUTIL_PROVIDER_IMAGE:-brainicism/bgutil-ytdlp-pot-provider:1.3.1}"
CONTAINER_NAME="${BGUTIL_PROVIDER_CONTAINER_NAME:-news_app_bgutil_provider}"
HOST="${BGUTIL_PROVIDER_HOST:-127.0.0.1}"
HOST_PORT="${BGUTIL_PROVIDER_PORT:-4416}"
CONTAINER_PORT="${BGUTIL_PROVIDER_CONTAINER_PORT:-4416}"
PULL_IMAGE="${BGUTIL_PROVIDER_PULL_IMAGE:-missing}"
DOCKER_ARGS_RAW="${BGUTIL_PROVIDER_DOCKER_ARGS:-}"
SERVER_ARGS_RAW="${BGUTIL_PROVIDER_SERVER_ARGS:-}"

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker is required to run the bgutil provider" >&2
  exit 1
fi

case "$PULL_IMAGE" in
  true)
    docker pull "$IMAGE"
    ;;
  missing)
    if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
      docker pull "$IMAGE"
    fi
    ;;
  false)
    ;;
  *)
    echo "ERROR: unsupported BGUTIL_PROVIDER_PULL_IMAGE value: $PULL_IMAGE" >&2
    exit 1
    ;;
esac

docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true

docker_args=()
if [[ -n "$DOCKER_ARGS_RAW" ]]; then
  # shellcheck disable=SC2206
  docker_args=($DOCKER_ARGS_RAW)
fi

server_args=()
if [[ -n "$SERVER_ARGS_RAW" ]]; then
  # shellcheck disable=SC2206
  server_args=($SERVER_ARGS_RAW)
fi

exec docker run \
  --rm \
  --name "$CONTAINER_NAME" \
  --init \
  -p "${HOST}:${HOST_PORT}:${CONTAINER_PORT}" \
  "${docker_args[@]}" \
  "$IMAGE" \
  "${server_args[@]}"
