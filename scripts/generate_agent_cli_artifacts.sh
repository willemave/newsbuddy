#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENT_OPENAPI_OUTPUT="${AGENT_OPENAPI_OUTPUT:-$REPO_ROOT/cli/openapi/agent-openapi.json}"
GO_TARGET_DIR="${GO_TARGET_DIR:-$REPO_ROOT/cli/internal/api}"

cd "$REPO_ROOT"

PYTHONPATH="$REPO_ROOT" uv run python "$REPO_ROOT/scripts/export_agent_openapi_schema.py" \
  --output "$AGENT_OPENAPI_OUTPUT"

cd "$REPO_ROOT/cli"
go run github.com/ogen-go/ogen/cmd/ogen@v1.20.1 \
  --clean \
  --target "$GO_TARGET_DIR" \
  --package api \
  "$AGENT_OPENAPI_OUTPUT"

uv run python - "$GO_TARGET_DIR/oas_json_gen.go" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

path = Path(sys.argv[1])
source = path.read_text(encoding="utf-8")

# Keep CLI decoding tolerant of the timestamp shapes currently emitted by the
# API, including naive ISO-like datetimes from local fixtures and snapshots.
source = source.replace("json.DecodeDateTime", "decodeFlexibleDateTime")

null_reset = """	if d.Next() == jx.Null {
		if err := d.Null(); err != nil {
			return err
		}
		o.Reset()
		return nil
	}
"""

for opt_type in (
    "OptDetectedFeed",
    "OptInt",
    "OptOnboardingFastDiscoverResponse",
    "OptSummaryKind",
    "OptSummaryVersion",
):
    marker = f"""func (o *{opt_type}) Decode(d *jx.Decoder) error {{
	if o == nil {{
		return errors.New("invalid: unable to decode {opt_type} to nil")
	}}
"""
    replacement = marker + null_reset
    if marker in source and replacement not in source:
        source = source.replace(marker, replacement, 1)

path.write_text(source, encoding="utf-8")
PY

gofmt -w "$GO_TARGET_DIR"
