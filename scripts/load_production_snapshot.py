"""Restore a production Postgres dump into a local database.

Default target DB is ``newsly_prod`` so the existing dev ``newsly`` DB is left
alone. Use ``--target-db newsly --force`` to overwrite the dev DB instead.

Prints row counts for user 1 after restore so we can sanity-check the load.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _latest_dump() -> Path | None:
    dump_dir = PROJECT_ROOT / ".local_dumps"
    if not dump_dir.exists():
        return None
    dumps = sorted(dump_dir.glob("newsly_prod_*.dump"))
    return dumps[-1] if dumps else None


def _admin_dsn(target_db: str) -> tuple[str, dict[str, str]]:
    """Return (admin DSN against 'postgres' DB, env for pg_restore against target).

    Honors DATABASE_URL from the environment. Falls back to a sensible local
    default matching scripts/setup_local_postgres.sh.
    """
    raw = os.environ.get("DATABASE_URL", "postgresql://newsly:root@127.0.0.1:5432/newsly")
    parsed = urlparse(raw.replace("postgresql+psycopg", "postgresql"))
    admin = parsed._replace(path="/postgres")
    target = parsed._replace(path=f"/{target_db}")
    env = os.environ.copy()
    if parsed.password:
        env["PGPASSWORD"] = parsed.password
    return urlunparse(admin), {"TARGET_DSN": urlunparse(target), **env}


def _psql(dsn: str, sql: str, env: dict[str, str]) -> str:
    result = subprocess.run(
        ["psql", dsn, "-v", "ON_ERROR_STOP=1", "-X", "-At", "-c", sql],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore a prod Postgres dump locally")
    parser.add_argument("--dump-path", type=Path, default=None)
    parser.add_argument("--target-db", default="newsly_prod")
    parser.add_argument("--force", action="store_true", help="drop target DB if it exists")
    args = parser.parse_args()

    dump_path = args.dump_path or _latest_dump()
    if dump_path is None or not dump_path.exists():
        print(
            "ERROR: no dump file found. Run scripts/pull_production_db.sh first.",
            file=sys.stderr,
        )
        return 1

    admin_dsn, env = _admin_dsn(args.target_db)
    target_dsn = env.pop("TARGET_DSN")

    existing = _psql(
        admin_dsn,
        f"SELECT 1 FROM pg_database WHERE datname = '{args.target_db}'",
        env,
    )
    if existing:
        if not args.force:
            print(
                f"ERROR: database {args.target_db!r} already exists. "
                f"Pass --force to drop and recreate.",
                file=sys.stderr,
            )
            return 1
        print(f"Dropping existing database {args.target_db!r}")
        _psql(
            admin_dsn,
            f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname = '{args.target_db}' AND pid <> pg_backend_pid()",
            env,
        )
        _psql(admin_dsn, f'DROP DATABASE "{args.target_db}"', env)

    print(f"Creating database {args.target_db!r}")
    _psql(admin_dsn, f'CREATE DATABASE "{args.target_db}"', env)

    print(f"Restoring {dump_path.name} into {args.target_db!r}")
    subprocess.run(
        [
            "pg_restore",
            "--no-owner",
            "--no-privileges",
            "--dbname",
            target_dsn,
            "--jobs",
            "4",
            str(dump_path),
        ],
        check=False,
        env=env,
    )

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
    print(f"Restored into database: {args.target_db}")
    print(f"  users: {users}")
    print(f"  contents: {contents}")
    print(f"  user=1 knowledge saves: {knowledge_saves}")
    print(f"  user=1 knowledge-linked contents: {knowledge_content}")
    print()
    print(f"To point dev at this DB, set DATABASE_URL to point at '{args.target_db}'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
