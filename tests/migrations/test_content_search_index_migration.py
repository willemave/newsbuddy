from __future__ import annotations

import importlib

from app.repositories.content_search_expressions import CONTENT_SEARCH_DOCUMENT_SQL


def test_content_search_migration_uses_the_canonical_document() -> None:
    migration = importlib.import_module(
        "migrations.alembic.versions.20260825_01_align_content_search_index"
    )

    assert " ".join(migration.CONTENT_SEARCH_DOCUMENT_SQL.split()) == " ".join(
        CONTENT_SEARCH_DOCUMENT_SQL.split()
    )
    assert migration.SUMMARY_TITLE_TRGM_INDEX_NAME == "idx_contents_summary_title_trgm"
    assert migration.down_revision == "20260823_01"
