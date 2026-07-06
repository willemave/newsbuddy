#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

export NEWSLY_PERF_TRACE_OUTPUT_DIR="${NEWSLY_PERF_TRACE_OUTPUT_DIR:-$REPO_ROOT/tmp/ios-performance-traces/$(date +%Y%m%d-%H%M%S)}"
export NEWSLY_MAESTRO_TEST_OUTPUT_DIR="${NEWSLY_MAESTRO_TEST_OUTPUT_DIR:-$NEWSLY_PERF_TRACE_OUTPUT_DIR/maestro}"

mkdir -p "$NEWSLY_PERF_TRACE_OUTPUT_DIR" "$NEWSLY_MAESTRO_TEST_OUTPUT_DIR"

echo "Writing Instruments traces to $NEWSLY_PERF_TRACE_OUTPUT_DIR"
exec "$REPO_ROOT/tests/scripts/ios_maestro.sh" -m ios_performance "$@"
