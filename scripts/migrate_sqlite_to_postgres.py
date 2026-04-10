#!/usr/bin/env python3
"""Copy application data from a SQLite backup into a PostgreSQL database."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus
from uuid import UUID

from dateutil import parser as date_parser
from dotenv import load_dotenv
from sqlalchemy import MetaData, create_engine, func, insert, select, text
from sqlalchemy.engine import URL, Connection, Engine, make_url
from sqlalchemy.sql.schema import Table
from sqlalchemy.sql.sqltypes import JSON, Boolean, Date, DateTime, Numeric, String, Time

DEFAULT_SKIP_TABLES = {"alembic_version"}


@dataclass(frozen=True)
class TablePlan:
    name: str
    source_table: Table
    target_table: Table
    common_columns: tuple[str, ...]


class MigrationValueError(RuntimeError):
    """Raised when a source value cannot be represented in PostgreSQL."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Copy rows from a SQLite backup into a PostgreSQL database.",
    )
    parser.add_argument(
        "--sqlite-path",
        required=True,
        help="Path to the SQLite backup file.",
    )
    parser.add_argument(
        "--postgres-url",
        help="Target PostgreSQL URL. Defaults to DATABASE_URL from the environment.",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=1000,
        help="Rows to insert per batch (default: 1000).",
    )
    parser.add_argument(
        "--only-table",
        action="append",
        default=[],
        help="Restrict migration to this table name. May be passed multiple times.",
    )
    parser.add_argument(
        "--skip-table",
        action="append",
        default=[],
        help="Skip this table name. May be passed multiple times.",
    )
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="Truncate the selected PostgreSQL tables before copying rows.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect and count rows without modifying PostgreSQL.",
    )
    parser.add_argument(
        "--truncate-strings",
        action="store_true",
        help="Truncate overlong VARCHAR values to the current PostgreSQL column limit.",
    )
    return parser


def require_postgres_url(raw_url: str | None) -> str:
    resolved = raw_url or os.environ.get("DATABASE_URL")
    if resolved and "change-me" in resolved:
        resolved = None
    if not resolved:
        resolved = build_postgres_url_from_env()
    if not resolved:
        raise SystemExit("DATABASE_URL is not set. Pass --postgres-url or export DATABASE_URL.")
    parsed = make_url(resolved)
    if not parsed.drivername.startswith("postgresql"):
        raise SystemExit(f"Target database must be PostgreSQL, got {parsed.drivername!r}.")
    return parsed.render_as_string(hide_password=False)


def require_sqlite_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser().resolve()
    if not path.exists():
        raise SystemExit(f"SQLite backup not found: {path}")
    if not path.is_file():
        raise SystemExit(f"SQLite path is not a file: {path}")
    return path


def load_selected_env_file() -> None:
    env_path = Path(os.environ.get("NEWSLY_ENV_FILE", ".env")).expanduser()
    if env_path.exists():
        load_dotenv(env_path, override=False)


def build_postgres_url_from_env() -> str | None:
    user = os.environ.get("POSTGRES_USER")
    password = os.environ.get("POSTGRES_PASSWORD")
    database = os.environ.get("POSTGRES_DB")
    if not user or not password or not database:
        return None

    host = os.environ.get("POSTGRES_HOST", "127.0.0.1")
    port = os.environ.get("POSTGRES_PORT", "5432")
    return (
        "postgresql+psycopg://"
        f"{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/{quote_plus(database)}"
    )


def quote_table_name(table: Table) -> str:
    if table.schema:
        return f'"{table.schema}"."{table.name}"'
    return f'"{table.name}"'


def load_metadata(engine: Engine) -> MetaData:
    metadata = MetaData()
    metadata.reflect(bind=engine)
    return metadata


def build_table_plans(
    source_metadata: MetaData,
    target_metadata: MetaData,
    *,
    only_tables: set[str],
    skip_tables: set[str],
) -> list[TablePlan]:
    source_tables = {
        table.name: table
        for table in source_metadata.tables.values()
        if not table.name.startswith("sqlite_")
    }
    target_tables = {
        table.name: table
        for table in target_metadata.tables.values()
        if table.name not in skip_tables
    }
    selected_names = set(source_tables) & set(target_tables)
    if only_tables:
        selected_names &= only_tables
    selected_names -= skip_tables

    ordered_names = [
        table.name for table in target_metadata.sorted_tables if table.name in selected_names
    ]
    remaining_names = sorted(selected_names - set(ordered_names))
    ordered_names.extend(remaining_names)

    plans: list[TablePlan] = []
    for table_name in ordered_names:
        source_table = source_tables[table_name]
        target_table = target_tables[table_name]
        common_columns = tuple(
            column.name for column in target_table.columns if column.name in source_table.columns
        )
        if not common_columns:
            continue
        plans.append(
            TablePlan(
                name=table_name,
                source_table=source_table,
                target_table=target_table,
                common_columns=common_columns,
            )
        )
    return plans


def _is_json_type(type_: Any) -> bool:
    return isinstance(type_, JSON) or "json" in type(type_).__name__.lower()


def _is_uuid_type(type_: Any) -> bool:
    return "uuid" in type(type_).__name__.lower()


def coerce_value(
    value: Any,
    target_column,
    *,
    table_name: str,
    row_identifier: Any,
    truncate_strings: bool,
) -> Any:
    if value is None:
        return None

    type_ = target_column.type
    if isinstance(value, memoryview):
        return bytes(value)

    if (
        isinstance(type_, String)
        and type_.length
        and isinstance(value, str)
        and len(value) > type_.length
    ):
        if truncate_strings:
            print(
                f"  truncating {table_name}.{target_column.name} for row {row_identifier}: "
                f"{len(value)} -> {type_.length}"
            )
            return value[: type_.length]
        raise MigrationValueError(
            f"{table_name}.{target_column.name} for row {row_identifier} exceeds "
            f"VARCHAR({type_.length}) with length {len(value)}"
        )

    if _is_json_type(type_):
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                return value
        return value

    if isinstance(type_, Boolean):
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "t", "yes", "y", "on"}:
                return True
            if normalized in {"0", "false", "f", "no", "n", "off"}:
                return False
        if isinstance(value, (int, float)):
            return bool(value)
        return value

    if isinstance(type_, DateTime):
        if isinstance(value, str):
            return date_parser.parse(value)
        return value

    if isinstance(type_, Date):
        if isinstance(value, str):
            parsed = date_parser.parse(value)
            return parsed.date()
        if isinstance(value, datetime):
            return value.date()
        return value

    if isinstance(type_, Time):
        if isinstance(value, str):
            parsed = date_parser.parse(value)
            return parsed.timetz() if parsed.tzinfo else parsed.time()
        if isinstance(value, datetime):
            return value.timetz() if value.tzinfo else value.time()
        return value

    if isinstance(type_, Numeric) and isinstance(value, str):
        return Decimal(value)

    if _is_uuid_type(type_) and isinstance(value, str):
        return UUID(value)

    return value


def count_rows(connection: Connection, table: Table) -> int:
    return int(connection.execute(select(func.count()).select_from(table)).scalar_one())


def truncate_tables(connection: Connection, tables: list[Table]) -> None:
    if not tables:
        return
    rendered = ", ".join(quote_table_name(table) for table in tables)
    connection.execute(text(f"TRUNCATE TABLE {rendered} RESTART IDENTITY CASCADE"))


def reset_sequences(connection: Connection, tables: list[Table]) -> None:
    for table in tables:
        integer_pk_columns = []
        for column in table.primary_key.columns:
            try:
                python_type = column.type.python_type
            except NotImplementedError:
                continue
            if python_type is int:
                integer_pk_columns.append(column)
        for column in integer_pk_columns:
            sequence_name = connection.execute(
                text("SELECT pg_get_serial_sequence(:table_name, :column_name)"),
                {
                    "table_name": table.fullname,
                    "column_name": column.name,
                },
            ).scalar_one_or_none()
            if not sequence_name:
                continue
            max_value = connection.execute(
                select(func.max(column)).select_from(table)
            ).scalar_one_or_none()
            connection.execute(
                text("SELECT setval(CAST(:sequence_name AS regclass), :value, true)"),
                {
                    "sequence_name": sequence_name,
                    "value": max_value or 1,
                },
            )


def print_plan_summary(
    source_metadata: MetaData,
    target_metadata: MetaData,
    plans: list[TablePlan],
) -> None:
    source_names = {
        table.name
        for table in source_metadata.tables.values()
        if not table.name.startswith("sqlite_")
    }
    target_names = {table.name for table in target_metadata.tables.values()}
    planned_names = {plan.name for plan in plans}

    source_only = sorted(source_names - target_names)
    target_only = sorted(target_names - source_names)

    print(f"Source tables: {len(source_names)}")
    print(f"Target tables: {len(target_names)}")
    print(f"Selected tables: {len(plans)}")
    if source_only:
        print(f"Source-only tables skipped: {', '.join(source_only)}")
    if target_only:
        print(f"Target-only tables skipped: {', '.join(target_only)}")
    if planned_names:
        print(f"Copy order: {', '.join(plan.name for plan in plans)}")


def copy_table_rows(
    source_connection: Connection,
    target_connection: Connection,
    plan: TablePlan,
    *,
    chunk_size: int,
    dry_run: bool,
    truncate_strings: bool,
) -> tuple[int, int]:
    source_count = count_rows(source_connection, plan.source_table)
    if source_count == 0:
        print(f"{plan.name}: source rows=0")
        return 0, 0

    print(f"{plan.name}: source rows={source_count} columns={len(plan.common_columns)}")
    rows_copied = 0
    result = source_connection.execute(
        select(*(plan.source_table.c[column_name] for column_name in plan.common_columns))
    )

    while True:
        batch = result.fetchmany(chunk_size)
        if not batch:
            break

        prepared_rows = [
            {
                column_name: coerce_value(
                    row._mapping[column_name],
                    plan.target_table.c[column_name],
                    table_name=plan.name,
                    row_identifier=row._mapping.get("id", "<no id>"),
                    truncate_strings=truncate_strings,
                )
                for column_name in plan.common_columns
            }
            for row in batch
        ]
        if not dry_run:
            target_connection.execute(insert(plan.target_table), prepared_rows)
        rows_copied += len(prepared_rows)
        print(f"  copied {rows_copied}/{source_count}")

    return source_count, rows_copied


def main() -> int:
    args = build_parser().parse_args()
    sqlite_path = require_sqlite_path(args.sqlite_path)
    load_selected_env_file()
    postgres_url = require_postgres_url(args.postgres_url)
    if args.chunk_size <= 0:
        raise SystemExit("--chunk-size must be greater than zero.")

    sqlite_url = URL.create("sqlite", database=str(sqlite_path))
    source_engine = create_engine(sqlite_url.render_as_string(hide_password=False))
    target_engine = create_engine(postgres_url, pool_pre_ping=True)

    try:
        source_metadata = load_metadata(source_engine)
        target_metadata = load_metadata(target_engine)
        plans = build_table_plans(
            source_metadata,
            target_metadata,
            only_tables=set(args.only_table),
            skip_tables=DEFAULT_SKIP_TABLES | set(args.skip_table),
        )
        if not plans:
            raise SystemExit(
                "No overlapping tables found between the SQLite backup and PostgreSQL."
            )

        print(f"SQLite backup: {sqlite_path}")
        print(f"Target PostgreSQL: {make_url(postgres_url).render_as_string(hide_password=True)}")
        print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")
        print(f"Chunk size: {args.chunk_size}")
        print_plan_summary(source_metadata, target_metadata, plans)

        total_source_rows = 0
        total_copied_rows = 0

        with source_engine.connect() as source_connection:
            target_context = target_engine.connect() if args.dry_run else target_engine.begin()
            with target_context as target_connection:
                if args.truncate:
                    print("Truncating selected PostgreSQL tables")
                    if not args.dry_run:
                        truncate_tables(target_connection, [plan.target_table for plan in plans])

                for plan in plans:
                    source_rows, copied_rows = copy_table_rows(
                        source_connection,
                        target_connection,
                        plan,
                        chunk_size=args.chunk_size,
                        dry_run=args.dry_run,
                        truncate_strings=args.truncate_strings,
                    )
                    total_source_rows += source_rows
                    total_copied_rows += copied_rows

                if not args.dry_run:
                    print("Resetting PostgreSQL sequences")
                    reset_sequences(target_connection, [plan.target_table for plan in plans])

        print(f"Complete: source rows={total_source_rows} copied rows={total_copied_rows}")
    finally:
        source_engine.dispose()
        target_engine.dispose()

    return 0


if __name__ == "__main__":
    sys.exit(main())
