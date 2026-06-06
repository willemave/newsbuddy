# admin/

Source folder: `admin`

## Purpose
Local production-operator CLI for inspecting and repairing the remote Newsly runtime over SSH.

## Runtime behavior
- `python -m admin ...` builds an argparse CLI with JSON or text envelopes for automation-friendly output.
- Commands route through SSH helpers and remote module calls rather than importing the production app locally.
- Docker-runtime log tailing uses the unified container stream; DB/log commands can still sync or query legacy direct-access deployments when configured.
- SQL execution is guarded by `sql_guard.py`; unsafe or mutating operations require explicit repair commands instead of raw ad hoc SQL.

## Important files
| Path | Purpose |
|---|---|
| `admin/__main__.py` | Module entrypoint for `uv run -m admin ...`. |
| `admin/cli.py` | Argument parser, command dispatch, normalized result/error envelopes. |
| `admin/config.py` | Remote target, app directory, log paths, local sync paths, and env-file resolution. |
| `admin/ssh.py` | SSH, remote Python, remote Docker logs, and rsync helpers. |
| `admin/remote.py` | Remote command entrypoint executed on the server. |
| `admin/remote_ops.py` | Server-side health, logs, usage, DB, event, debug, and repair operations. |
| `admin/log_parsing.py` | Structured and plain log parsing helpers. |
| `admin/output.py` | JSON/text output envelope formatting. |
| `admin/sql_guard.py` | Read-only SQL validation for operator queries. |
| `admin/.env.example` | Template for local operator configuration. |

## Integration points
- Production debugging workflows should prefer this CLI over direct SSH command construction.
- Tests live in `tests/admin`.
- The repo AGENTS guide calls out `uv run -m admin logs exceptions --limit 20` and `uv run -m admin logs tail --limit 200` as the first production-debug commands.

## Excluded local files
Local synced DBs, pycache files, and downloaded logs under `admin/` are runtime artifacts and are not part of the source contract.
