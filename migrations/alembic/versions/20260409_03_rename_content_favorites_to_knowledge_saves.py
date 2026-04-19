"""Rename content favorites storage to knowledge saves.

Revision ID: 20260409_03
Revises: 20260409_02
Create Date: 2026-04-09
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import inspect

revision: str = "20260409_03"
down_revision: str | None = "20260409_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_TABLE = "content_favorites"
NEW_TABLE = "content_knowledge_saves"
OLD_COLUMN = "favorited_at"
NEW_COLUMN = "saved_at"

OLD_INDEXES = (
    "ix_content_favorites_content_id",
    "ix_content_favorites_user_id",
)
OLD_UNIQUE_CONSTRAINT = "uq_content_favorites_user_content"
NEW_INDEXES = (
    ("ix_content_knowledge_saves_content_id", ["content_id"], False),
    ("ix_content_knowledge_saves_user_id", ["user_id"], False),
)
NEW_UNIQUE_CONSTRAINT = "uq_content_knowledge_saves_user_content"


def _table_exists(inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _index_names(inspector, table_name: str) -> set[str]:
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _unique_constraint_names(inspector, table_name: str) -> set[str]:
    return {constraint["name"] for constraint in inspector.get_unique_constraints(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if _table_exists(inspector, NEW_TABLE):
        return

    if not _table_exists(inspector, OLD_TABLE):
        return

    old_columns = {column["name"] for column in inspector.get_columns(OLD_TABLE)}
    if OLD_COLUMN in old_columns:
        with op.batch_alter_table(OLD_TABLE) as batch_op:
            batch_op.alter_column(OLD_COLUMN, new_column_name=NEW_COLUMN)

    op.rename_table(OLD_TABLE, NEW_TABLE)

    inspector = inspect(bind)
    existing_indexes = _index_names(inspector, NEW_TABLE)
    for index_name in OLD_INDEXES:
        if index_name in existing_indexes:
            op.drop_index(index_name, table_name=NEW_TABLE)

    for index_name, columns, unique in NEW_INDEXES:
        op.create_index(index_name, NEW_TABLE, columns, unique=unique)

    unique_constraints = _unique_constraint_names(inspector, NEW_TABLE)
    if OLD_UNIQUE_CONSTRAINT in unique_constraints:
        with op.batch_alter_table(NEW_TABLE) as batch_op:
            batch_op.drop_constraint(OLD_UNIQUE_CONSTRAINT, type_="unique")
            batch_op.create_unique_constraint(NEW_UNIQUE_CONSTRAINT, ["user_id", "content_id"])
    elif NEW_UNIQUE_CONSTRAINT not in unique_constraints:
        with op.batch_alter_table(NEW_TABLE) as batch_op:
            batch_op.create_unique_constraint(NEW_UNIQUE_CONSTRAINT, ["user_id", "content_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if _table_exists(inspector, OLD_TABLE):
        return

    if not _table_exists(inspector, NEW_TABLE):
        return

    unique_constraints = _unique_constraint_names(inspector, NEW_TABLE)
    if NEW_UNIQUE_CONSTRAINT in unique_constraints:
        with op.batch_alter_table(NEW_TABLE) as batch_op:
            batch_op.drop_constraint(NEW_UNIQUE_CONSTRAINT, type_="unique")
            batch_op.create_unique_constraint(OLD_UNIQUE_CONSTRAINT, ["user_id", "content_id"])

    existing_indexes = _index_names(inspector, NEW_TABLE)
    for index_name, _columns, _unique in NEW_INDEXES:
        if index_name in existing_indexes:
            op.drop_index(index_name, table_name=NEW_TABLE)

    op.rename_table(NEW_TABLE, OLD_TABLE)

    old_columns = {column["name"] for column in inspect(bind).get_columns(OLD_TABLE)}
    if NEW_COLUMN in old_columns:
        with op.batch_alter_table(OLD_TABLE) as batch_op:
            batch_op.alter_column(NEW_COLUMN, new_column_name=OLD_COLUMN)

    inspector = inspect(bind)
    existing_indexes = _index_names(inspector, OLD_TABLE)
    for index_name, columns, unique in (
        ("ix_content_favorites_content_id", ["content_id"], False),
        ("ix_content_favorites_user_id", ["user_id"], False),
    ):
        if index_name not in existing_indexes:
            op.create_index(index_name, OLD_TABLE, columns, unique=unique)
