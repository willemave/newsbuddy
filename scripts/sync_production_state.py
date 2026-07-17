#!/usr/bin/env python3
"""Copy production runtime state into the local development environment.

This is the one-stop production snapshot command:

1. Pull a production Postgres dump.
2. Restore it into the local app database and rewrite the selected env file.
3. Sync recent file-backed runtime assets from production.
4. Restart the local API server without starting workers.

Asset sync is intentionally time-bounded. A full production image/media/body
copy can be many gigabytes; by default this pulls files modified in the last
30 days, which is enough for current inbox and briefing work without mirroring
the whole server.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import signal
import subprocess
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse, urlunparse

try:
    from datetime import UTC
except ImportError:  # pragma: no cover - compatibility for the system Python 3.9.
    from datetime import timezone

    UTC = timezone.utc  # noqa: UP017
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REMOTE_HOST = os.environ.get("REMOTE_HOST", "news-app-server")
DEFAULT_REMOTE_CONTAINER = os.environ.get("REMOTE_CONTAINER", "newsly-workers")
DEFAULT_REMOTE_DATA_ROOT = "/data"
DEFAULT_DATABASE_URL = "postgresql+psycopg://newsly:root@127.0.0.1:5432/newsly"
DEFAULT_ASSET_DAYS = 30
DEFAULT_ASSET_DIRS = ("images", "media", "content_bodies", "personal_markdown")
DATABASE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
TOOL_FALLBACK_DIRS = (Path("/opt/homebrew/bin"), Path("/usr/local/bin"))
ASSET_DESTINATION_FIELDS = {
    "images": ("IMAGES_BASE_DIR", PROJECT_ROOT / "data" / "images"),
    "media": ("MEDIA_BASE_DIR", PROJECT_ROOT / "data" / "media"),
    "content_bodies": ("CONTENT_BODY_LOCAL_ROOT", PROJECT_ROOT / "data" / "content_bodies"),
    "personal_markdown": ("PERSONAL_MARKDOWN_ROOT", PROJECT_ROOT / "data" / "personal_markdown"),
}
LOCAL_API_SCREEN_NAME = "newsly-local-api"
LOCAL_API_LOG = PROJECT_ROOT / "logs" / "local-api.log"


@dataclass(frozen=True)
class AssetPlan:
    name: str
    remote_path: str
    local_path: Path
    recent_file_count: int


def _tool(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    for directory in TOOL_FALLBACK_DIRS:
        candidate = directory / name
        if candidate.exists():
            return str(candidate)
    raise SystemExit(f"ERROR: required command {name!r} was not found")


def _run(
    cmd: list[str],
    *,
    cwd: Path = PROJECT_ROOT,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, check=check, text=True)


def _remote_docker_cmd(args: argparse.Namespace, script: str) -> list[str]:
    docker_command = (
        f"sudo docker exec {shlex.quote(args.remote_container)} bash -lc {shlex.quote(script)}"
    )
    return ["ssh", args.remote_host, docker_command]


def _resolve_env_file(path: Path | None) -> Path:
    if path is not None:
        return path.expanduser().resolve()
    return Path(os.environ.get("NEWSLY_ENV_FILE", PROJECT_ROOT / ".env")).expanduser().resolve()


def _read_env_value(env_file: Path, key: str) -> str | None:
    if key in os.environ:
        return os.environ[key]
    if not env_file.exists():
        return None
    for line in env_file.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        raw_key, raw_value = stripped.split("=", 1)
        if raw_key.strip() != key:
            continue
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        return value
    return None


def _read_database_url(env_file: Path) -> str:
    if "DATABASE_URL" in os.environ:
        return os.environ["DATABASE_URL"]
    value = _read_env_value(env_file, "DATABASE_URL")
    return value or DEFAULT_DATABASE_URL


def _database_name_from_url(raw_url: str) -> str:
    parsed = urlparse(raw_url.replace("postgresql+psycopg", "postgresql"))
    name = parsed.path.lstrip("/")
    return name or "newsly"


def _validate_database_name(name: str) -> None:
    if not DATABASE_NAME_RE.match(name):
        raise SystemExit(f"ERROR: unsupported database name {name!r}")


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sql_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _replace_database_url(raw_url: str, target_db: str) -> str:
    parsed = urlparse(raw_url)
    return urlunparse(parsed._replace(path=f"/{target_db}"))


def _write_database_url(env_file: Path, database_url: str) -> None:
    env_file.parent.mkdir(parents=True, exist_ok=True)
    lines = env_file.read_text().splitlines() if env_file.exists() else []
    output: list[str] = []
    replaced = False
    for line in lines:
        if line.startswith("DATABASE_URL="):
            output.append(f"DATABASE_URL={database_url}")
            replaced = True
        else:
            output.append(line)
    if not replaced:
        if output and output[-1].strip():
            output.append("")
        output.append(f"DATABASE_URL={database_url}")
    env_file.write_text("\n".join(output) + "\n")


def _resolve_local_storage_path(raw_value: str | None, default_path: Path) -> Path:
    if not raw_value:
        return default_path.resolve()

    path = Path(raw_value).expanduser()
    container_root = Path(DEFAULT_REMOTE_DATA_ROOT)
    if path.is_absolute():
        if path == container_root or container_root in path.parents:
            return (PROJECT_ROOT / "data" / path.relative_to(container_root)).resolve()
        return path.resolve()

    return (PROJECT_ROOT / path).resolve()


def _local_asset_paths(env_file: Path) -> dict[str, Path]:
    return {
        asset_name: _resolve_local_storage_path(_read_env_value(env_file, env_key), default_path)
        for asset_name, (env_key, default_path) in ASSET_DESTINATION_FIELDS.items()
    }


def _dump_path_from_args(args: argparse.Namespace) -> Path:
    if args.dump_path is not None:
        return args.dump_path.expanduser().resolve()
    if args.reuse_dump:
        latest = _latest_dump()
        if latest is not None:
            return latest
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return PROJECT_ROOT / ".local_dumps" / f"newsly_prod_{timestamp}.dump"


def _latest_dump() -> Path | None:
    dump_dir = PROJECT_ROOT / ".local_dumps"
    if not dump_dir.exists():
        return None
    dumps = sorted(dump_dir.glob("newsly_prod_*.dump"))
    return dumps[-1] if dumps else None


def _human_size(path: Path) -> str:
    size = path.stat().st_size
    units = ("B", "KB", "MB", "GB", "TB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{size} B"
        value /= 1024
    return f"{size} B"


def _pull_database(args: argparse.Namespace, dump_path: Path) -> None:
    dump_path.parent.mkdir(parents=True, exist_ok=True)
    if args.reuse_dump:
        if not dump_path.exists():
            raise SystemExit(f"ERROR: --reuse-dump was set but dump does not exist: {dump_path}")
        print(f"Reusing production dump: {dump_path}")
        return

    print(
        f"Dumping production Postgres from {args.remote_host} (container: {args.remote_container})"
    )
    print(f"Output: {dump_path}")
    remote_script = """
set -euo pipefail
PGPASSWORD="${POSTGRES_PASSWORD:?}" \
  pg_dump \
    --format=custom \
    --compress=6 \
    --no-owner \
    --no-privileges \
    -h 127.0.0.1 \
    -U "${POSTGRES_USER:?}" \
    -d "${POSTGRES_DB:?}"
"""
    with dump_path.open("wb") as output:
        subprocess.run(
            _remote_docker_cmd(args, remote_script),
            cwd=PROJECT_ROOT,
            stdout=output,
            check=True,
        )
    print(f"Dump complete: {dump_path} ({_human_size(dump_path)})")


def _restore_database(args: argparse.Namespace, dump_path: Path, env_file: Path) -> None:
    source_database_url = _read_database_url(env_file)
    target_db = args.target_db or _database_name_from_url(source_database_url)
    _validate_database_name(target_db)
    target_database_url = _replace_database_url(source_database_url, target_db)
    admin_dsn, target_dsn, env = _admin_dsn(target_db, database_url=target_database_url)

    existing = _psql(
        admin_dsn,
        f"SELECT 1 FROM pg_database WHERE datname = {_sql_literal(target_db)}",
        env,
    )
    if existing:
        if args.no_force:
            raise SystemExit(
                f"ERROR: database {target_db!r} already exists. "
                "Omit --no-force to drop and recreate it."
            )
        print(f"Dropping existing database {target_db!r}")
        _psql(
            admin_dsn,
            f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname = {_sql_literal(target_db)} AND pid <> pg_backend_pid()",
            env,
        )
        _psql(admin_dsn, f"DROP DATABASE {_sql_identifier(target_db)}", env)

    print(f"Creating database {target_db!r}")
    _psql(admin_dsn, f"CREATE DATABASE {_sql_identifier(target_db)}", env)

    print(f"Restoring {dump_path.name} into {target_db!r}")
    subprocess.run(
        [
            _tool("pg_restore"),
            "--no-owner",
            "--no-privileges",
            "--dbname",
            target_dsn,
            "--jobs",
            "4",
            str(dump_path),
        ],
        check=True,
        env=env,
    )

    _write_database_url(env_file, target_database_url)

    users = _psql(target_dsn, "SELECT count(*) FROM users", env)
    contents = _psql(target_dsn, "SELECT count(*) FROM contents", env)
    knowledge_saves = _psql(
        target_dsn,
        "SELECT count(*) FROM content_knowledge_saves WHERE user_id = 1",
        env,
    )
    knowledge_content = _psql(
        target_dsn,
        "SELECT count(*) FROM contents c JOIN content_knowledge_saves k ON k.content_id = c.id "
        "WHERE k.user_id = 1",
        env,
    )

    print()
    print(f"Restored into database: {target_db}")
    print(f"Updated env file: {env_file}")
    print(f"  users: {users}")
    print(f"  contents: {contents}")
    print(f"  user=1 knowledge saves: {knowledge_saves}")
    print(f"  user=1 knowledge-linked contents: {knowledge_content}")


def _admin_dsn(target_db: str, *, database_url: str) -> tuple[str, str, dict[str, str]]:
    parsed = urlparse(database_url.replace("postgresql+psycopg", "postgresql"))
    admin = parsed._replace(path="/postgres")
    target = parsed._replace(path=f"/{target_db}")
    env = os.environ.copy()
    if parsed.password:
        env["PGPASSWORD"] = parsed.password
    return urlunparse(admin), urlunparse(target), env


def _psql(dsn: str, sql: str, env: dict[str, str]) -> str:
    result = subprocess.run(
        [_tool("psql"), dsn, "-v", "ON_ERROR_STOP=1", "-X", "-At", "-c", sql],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return result.stdout.strip()


def _count_recent_remote_files(args: argparse.Namespace, remote_path: str) -> int:
    script = f"""
set -euo pipefail
remote_path={shlex.quote(remote_path)}
asset_days={int(args.asset_days)}
if [[ ! -d "${{remote_path}}" ]]; then
  printf '0\\n'
  exit 0
fi
find "${{remote_path}}" -type f -mtime "-${{asset_days}}" -printf '.\\n' 2>/dev/null |
  wc -l |
  tr -d ' '
"""
    result = subprocess.run(
        _remote_docker_cmd(args, script),
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    raw_count = result.stdout.strip()
    return int(raw_count or "0")


def _build_asset_plans(args: argparse.Namespace, env_file: Path) -> list[AssetPlan]:
    selected_assets = tuple(args.asset_dir or DEFAULT_ASSET_DIRS)
    local_paths = _local_asset_paths(env_file)
    plans: list[AssetPlan] = []
    for asset_name in selected_assets:
        remote_path = f"{args.remote_data_root.rstrip('/')}/{asset_name}"
        recent_count = _count_recent_remote_files(args, remote_path)
        plans.append(
            AssetPlan(
                name=asset_name,
                remote_path=remote_path,
                local_path=local_paths[asset_name],
                recent_file_count=recent_count,
            )
        )
    return plans


def _sync_asset_dir(args: argparse.Namespace, plan: AssetPlan) -> None:
    print(
        f"Syncing {plan.name}: {plan.recent_file_count} files from "
        f"{plan.remote_path} modified in the last {args.asset_days} days"
    )
    if plan.recent_file_count == 0:
        return

    plan.local_path.mkdir(parents=True, exist_ok=True)
    remote_script = f"""
set -euo pipefail
remote_path={shlex.quote(plan.remote_path)}
asset_days={int(args.asset_days)}
cd "${{remote_path}}"
find . -type f -mtime "-${{asset_days}}" -print0 2>/dev/null |
  tar --null --files-from - --create --file -
"""
    ssh_proc = subprocess.Popen(
        _remote_docker_cmd(args, remote_script),
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        text=False,
    )
    assert ssh_proc.stdout is not None
    tar_proc = subprocess.run(
        [_tool("tar"), "-xf", "-", "-C", str(plan.local_path)],
        cwd=PROJECT_ROOT,
        stdin=ssh_proc.stdout,
        check=False,
    )
    ssh_proc.stdout.close()
    ssh_status = ssh_proc.wait()
    if ssh_status != 0:
        raise SystemExit(f"ERROR: remote tar stream failed for {plan.name}")
    if tar_proc.returncode != 0:
        raise SystemExit(f"ERROR: local tar extraction failed for {plan.name}")


def _sync_assets(args: argparse.Namespace, env_file: Path) -> None:
    plans = _build_asset_plans(args, env_file)
    for plan in plans:
        _sync_asset_dir(args, plan)


def _stop_screen_server(screen_name: str) -> None:
    if shutil.which("screen") is None:
        raise SystemExit("ERROR: screen is required for local API restart")
    subprocess.run(
        ["screen", "-S", screen_name, "-X", "quit"],
        cwd=PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def _listener_pids(port: int) -> list[int]:
    lsof = shutil.which("lsof")
    if lsof is None:
        raise SystemExit("ERROR: lsof is required to restart the local API safely")

    result = subprocess.run(
        [lsof, f"-tiTCP:{port}", "-sTCP:LISTEN"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in {0, 1}:
        raise SystemExit(f"ERROR: failed to inspect port {port}: {result.stderr.strip()}")
    return [int(line) for line in result.stdout.splitlines() if line.strip().isdigit()]


def _process_command(pid: int) -> str:
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _is_newsly_api_command(command: str) -> bool:
    return str(PROJECT_ROOT) in command and (
        "uvicorn app.main:app" in command or "scripts/start_services.sh" in command
    )


def _wait_for_port_release(port: int, timeout_seconds: int) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not _listener_pids(port):
            return True
        time.sleep(0.25)
    return not _listener_pids(port)


def _stop_local_api(args: argparse.Namespace) -> None:
    _stop_screen_server(args.screen_name)
    port = int(args.server_port)
    safe_pids: list[int] = []
    unsafe_processes: list[str] = []
    for pid in _listener_pids(port):
        command = _process_command(pid)
        if _is_newsly_api_command(command):
            safe_pids.append(pid)
        else:
            unsafe_processes.append(f"{pid}: {command}")

    if unsafe_processes:
        details = "\n".join(unsafe_processes)
        raise SystemExit(f"ERROR: port {port} is occupied by a non-Newsly process:\n{details}")

    for pid in safe_pids:
        print(f"Stopping existing local Newsly API listener pid={pid}")
        with suppress(ProcessLookupError):
            os.kill(pid, signal.SIGTERM)

    if safe_pids and not _wait_for_port_release(port, timeout_seconds=10):
        for pid in safe_pids:
            with suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        if not _wait_for_port_release(port, timeout_seconds=5):
            raise SystemExit(f"ERROR: port {port} is still occupied after stopping Newsly API")


def _restart_local_api(args: argparse.Namespace, env_file: Path) -> None:
    _stop_local_api(args)
    LOCAL_API_LOG.parent.mkdir(parents=True, exist_ok=True)
    command = (
        f"cd {shlex.quote(str(PROJECT_ROOT))} && "
        f"exec {shlex.quote(str(PROJECT_ROOT / 'scripts' / 'start_services.sh'))} "
        f"server --env-file {shlex.quote(str(env_file))} "
        f"--port {int(args.server_port)} --no-reload "
        f"> {shlex.quote(str(LOCAL_API_LOG))} 2>&1"
    )
    _run(["screen", "-dmS", args.screen_name, "/bin/zsh", "-lc", command])
    print(f"Started local API in screen session {args.screen_name!r}")
    print(f"API log: {LOCAL_API_LOG}")

    health_url = f"http://127.0.0.1:{int(args.server_port)}/health"
    curl = shutil.which("curl")
    if curl is None:
        return
    for _attempt in range(30):
        result = subprocess.run(
            [curl, "-fsS", health_url],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            print(f"Health check passed: {health_url}")
            return
        time.sleep(1)
    print(f"WARNING: local API did not pass health check within 30s: {health_url}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Copy production DB and recent assets locally")
    parser.add_argument("--remote-host", default=DEFAULT_REMOTE_HOST)
    parser.add_argument("--remote-container", default=DEFAULT_REMOTE_CONTAINER)
    parser.add_argument("--remote-data-root", default=DEFAULT_REMOTE_DATA_ROOT)
    parser.add_argument("--env-file", type=Path, default=None)
    parser.add_argument("--target-db", default=None)
    parser.add_argument("--dump-path", type=Path, default=None)
    parser.add_argument("--reuse-dump", action="store_true", help="restore from --dump-path")
    parser.add_argument("--skip-db", action="store_true", help="only sync recent assets")
    parser.add_argument("--skip-assets", action="store_true", help="only sync the database")
    parser.add_argument(
        "--asset-days",
        type=int,
        default=DEFAULT_ASSET_DAYS,
        help="Sync files modified in the last N days. Defaults to 30.",
    )
    parser.add_argument(
        "--asset-dir",
        action="append",
        choices=sorted(DEFAULT_ASSET_DIRS),
        help=(
            "Asset directory to sync. Repeatable. Defaults to images, media, "
            "content_bodies, and personal_markdown."
        ),
    )
    parser.add_argument(
        "--no-force",
        action="store_true",
        help="Fail instead of dropping an existing local DB during restore.",
    )
    parser.add_argument(
        "--no-restart-server",
        dest="restart_server",
        action="store_false",
        help="Do not restart the local API after copying state.",
    )
    parser.add_argument("--server-port", type=int, default=8000)
    parser.add_argument("--screen-name", default=LOCAL_API_SCREEN_NAME)
    parser.set_defaults(restart_server=True)
    args = parser.parse_args(argv)
    if args.asset_days < 1:
        parser.error("--asset-days must be >= 1")
    if args.skip_db and args.skip_assets:
        parser.error("--skip-db and --skip-assets cannot both be set")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    env_file = _resolve_env_file(args.env_file)
    dump_path = _dump_path_from_args(args)

    if args.restart_server:
        _stop_local_api(args)

    if not args.skip_db:
        _pull_database(args, dump_path)
        _restore_database(args, dump_path, env_file)

    if not args.skip_assets:
        _sync_assets(args, env_file)

    if args.restart_server:
        _restart_local_api(args, env_file)

    print("Production state sync complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
