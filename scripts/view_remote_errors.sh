#!/usr/bin/env bash
set -euo pipefail

remote_target="${NEWSLY_REMOTE_TARGET:-news-app-server}"
since="${1:-24h}"
containers=(newsly-workers newsly-scheduler newsly-document-extractor)

active_slot="$(
  ssh "$remote_target" \
    "test -f /opt/newsly/state/active-api-slot && tr -d '[:space:]' </opt/newsly/state/active-api-slot"
)"
case "$active_slot" in
  blue|green) containers+=("newsly-api-${active_slot}") ;;
  *) echo "Could not resolve the active API slot." >&2; exit 1 ;;
esac

for container in "${containers[@]}"; do
  echo "===== ${container}"
  ssh "$remote_target" \
    "sudo docker logs --since '$since' '$container' 2>&1" |
    rg -i 'error|exception|failed|panic|fatal' || true
done
