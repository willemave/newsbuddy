"""Tests for duplicate news item cleanup script."""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path


def _load_module():
    module_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "cleanup_news_item_duplicates.py"
    )
    spec = importlib.util.spec_from_file_location("cleanup_news_item_duplicates", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE news_items (
            id INTEGER PRIMARY KEY,
            visibility_scope TEXT NOT NULL,
            owner_user_id INTEGER,
            platform TEXT,
            source_external_id TEXT,
            canonical_item_url TEXT,
            discussion_url TEXT,
            canonical_story_url TEXT,
            article_title TEXT
        );

        CREATE TABLE news_digest_bullets (
            id INTEGER PRIMARY KEY,
            source_count INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE news_digest_bullet_sources (
            id INTEGER PRIMARY KEY,
            bullet_id INTEGER NOT NULL,
            news_item_id INTEGER NOT NULL,
            position INTEGER NOT NULL
        );

        CREATE TABLE news_item_digest_coverage (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            news_item_id INTEGER NOT NULL,
            digest_id INTEGER NOT NULL
        );
        """
    )


def test_find_duplicate_news_item_families_connects_shared_stable_identity() -> None:
    module = _load_module()
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _create_schema(conn)

    conn.executemany(
        """
        INSERT INTO news_items (
            id,
            visibility_scope,
            owner_user_id,
            platform,
            source_external_id,
            canonical_item_url,
            discussion_url,
            canonical_story_url,
            article_title
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                1,
                "global",
                None,
                "hackernews",
                "123",
                "https://news.ycombinator.com/item?id=123",
                "https://news.ycombinator.com/item?id=123",
                "https://example.com/story",
                "Example Story",
            ),
            (
                2,
                "global",
                None,
                "hackernews",
                "123",
                "https://news.ycombinator.com/item?id=123",
                "https://news.ycombinator.com/item?id=123",
                "https://example.com/story",
                "Example Story",
            ),
            (
                3,
                "global",
                None,
                None,
                None,
                "https://news.ycombinator.com/item?id=123",
                "https://news.ycombinator.com/item?id=123",
                "https://example.com/story",
                "Example Story",
            ),
            (
                4,
                "global",
                None,
                "hackernews",
                "999",
                "https://news.ycombinator.com/item?id=999",
                "https://news.ycombinator.com/item?id=999",
                "https://example.com/other",
                "Other Story",
            ),
        ],
    )

    families = module.find_duplicate_news_item_families(conn)

    assert len(families) == 1
    assert families[0].winner_id == 1
    assert families[0].loser_ids == (2, 3)


def test_cleanup_duplicate_news_items_rewrites_references_and_deletes_losers() -> None:
    module = _load_module()
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _create_schema(conn)

    conn.executemany(
        """
        INSERT INTO news_items (
            id,
            visibility_scope,
            owner_user_id,
            platform,
            source_external_id,
            canonical_item_url,
            discussion_url,
            canonical_story_url,
            article_title
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                10,
                "global",
                None,
                "hackernews",
                "47561496",
                "https://news.ycombinator.com/item?id=47561496",
                "https://news.ycombinator.com/item?id=47561496",
                "https://github.com/1st1/lat.md",
                "Lat.md",
            ),
            (
                11,
                "global",
                None,
                "hackernews",
                "47561496",
                "https://news.ycombinator.com/item?id=47561496",
                "https://news.ycombinator.com/item?id=47561496",
                "https://github.com/1st1/lat.md",
                "Lat.md",
            ),
            (
                12,
                "global",
                None,
                "hackernews",
                "47561496",
                "https://news.ycombinator.com/item?id=47561496",
                "https://news.ycombinator.com/item?id=47561496",
                "https://github.com/1st1/lat.md",
                "Lat.md",
            ),
            (
                20,
                "global",
                None,
                "hackernews",
                "777",
                "https://news.ycombinator.com/item?id=777",
                "https://news.ycombinator.com/item?id=777",
                "https://example.com/other",
                "Other",
            ),
        ],
    )
    conn.executemany(
        "INSERT INTO news_digest_bullets (id, source_count) VALUES (?, ?)",
        [(100, 3), (101, 2)],
    )
    conn.executemany(
        """
        INSERT INTO news_digest_bullet_sources (id, bullet_id, news_item_id, position)
        VALUES (?, ?, ?, ?)
        """,
        [
            (1000, 100, 10, 1),
            (1001, 100, 11, 2),
            (1002, 100, 20, 3),
            (1003, 101, 12, 1),
            (1004, 101, 20, 2),
        ],
    )
    conn.executemany(
        """
        INSERT INTO news_item_digest_coverage (id, user_id, news_item_id, digest_id)
        VALUES (?, ?, ?, ?)
        """,
        [
            (2000, 1, 10, 500),
            (2001, 1, 11, 501),
            (2002, 2, 12, 502),
        ],
    )

    families, stats = module.cleanup_duplicate_news_items(conn, apply_changes=True)

    assert len(families) == 1
    assert stats.family_count == 1
    assert stats.duplicate_row_count == 2
    assert stats.deleted_news_items == 2
    assert stats.deleted_coverages == 1
    assert stats.updated_coverages == 1
    assert stats.deleted_bullet_sources == 1
    assert stats.updated_bullet_sources == 1
    assert stats.reindexed_bullets == 2

    remaining_item_ids = [
        row["id"] for row in conn.execute("SELECT id FROM news_items ORDER BY id ASC").fetchall()
    ]
    assert remaining_item_ids == [10, 20]

    coverage_rows = conn.execute(
        """
        SELECT user_id, news_item_id, digest_id
        FROM news_item_digest_coverage
        ORDER BY user_id ASC, digest_id ASC
        """
    ).fetchall()
    assert [
        (row["user_id"], row["news_item_id"], row["digest_id"]) for row in coverage_rows
    ] == [
        (1, 10, 500),
        (2, 10, 502),
    ]

    bullet_source_rows = conn.execute(
        """
        SELECT bullet_id, news_item_id, position
        FROM news_digest_bullet_sources
        ORDER BY bullet_id ASC, position ASC
        """
    ).fetchall()
    assert [
        (row["bullet_id"], row["news_item_id"], row["position"]) for row in bullet_source_rows
    ] == [
        (100, 10, 1),
        (100, 20, 2),
        (101, 10, 1),
        (101, 20, 2),
    ]

    bullet_counts = conn.execute(
        "SELECT id, source_count FROM news_digest_bullets ORDER BY id ASC"
    ).fetchall()
    assert [(row["id"], row["source_count"]) for row in bullet_counts] == [(100, 2), (101, 2)]
