"""Merge duplicate news_items rows that share stable ingest identity."""

from __future__ import annotations

import argparse
import os
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DuplicateNewsItemFamily:
    """One connected duplicate family keyed by stable ingest identity."""

    winner_id: int
    loser_ids: tuple[int, ...]
    identity_label: str
    title: str | None

    @property
    def all_ids(self) -> tuple[int, ...]:
        return (self.winner_id, *self.loser_ids)

    @property
    def duplicate_count(self) -> int:
        return len(self.loser_ids)


@dataclass(frozen=True)
class DuplicateCleanupStats:
    """Counts produced by a duplicate cleanup run."""

    family_count: int = 0
    duplicate_row_count: int = 0
    updated_coverages: int = 0
    deleted_coverages: int = 0
    updated_bullet_sources: int = 0
    deleted_bullet_sources: int = 0
    reindexed_bullets: int = 0
    deleted_news_items: int = 0

    def combine(self, other: DuplicateCleanupStats) -> DuplicateCleanupStats:
        return DuplicateCleanupStats(
            family_count=self.family_count + other.family_count,
            duplicate_row_count=self.duplicate_row_count + other.duplicate_row_count,
            updated_coverages=self.updated_coverages + other.updated_coverages,
            deleted_coverages=self.deleted_coverages + other.deleted_coverages,
            updated_bullet_sources=self.updated_bullet_sources + other.updated_bullet_sources,
            deleted_bullet_sources=self.deleted_bullet_sources + other.deleted_bullet_sources,
            reindexed_bullets=self.reindexed_bullets + other.reindexed_bullets,
            deleted_news_items=self.deleted_news_items + other.deleted_news_items,
        )


class _UnionFind:
    def __init__(self) -> None:
        self._parent: dict[int, int] = {}

    def add(self, value: int) -> None:
        self._parent.setdefault(value, value)

    def find(self, value: int) -> int:
        parent = self._parent[value]
        if parent != value:
            self._parent[value] = self.find(parent)
        return self._parent[value]

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        if left_root < right_root:
            self._parent[right_root] = left_root
        else:
            self._parent[left_root] = right_root


def _normalize_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned or None


def _identity_tokens(row: sqlite3.Row) -> list[tuple[Any, ...]]:
    scope = row["visibility_scope"]
    owner_user_id = row["owner_user_id"]
    tokens: list[tuple[Any, ...]] = []

    platform = _normalize_text(row["platform"])
    source_external_id = _normalize_text(row["source_external_id"])
    if platform and source_external_id:
        tokens.append(("external", scope, owner_user_id, platform, source_external_id))

    for identity_type, field_name in (
        ("canonical_item_url", "canonical_item_url"),
        ("discussion_url", "discussion_url"),
        ("canonical_story_url", "canonical_story_url"),
    ):
        value = _normalize_text(row[field_name])
        if value:
            tokens.append((identity_type, scope, owner_user_id, value))

    return tokens


def _preferred_identity_label(row: sqlite3.Row) -> str:
    platform = _normalize_text(row["platform"])
    source_external_id = _normalize_text(row["source_external_id"])
    if platform and source_external_id:
        return f"{platform}:{source_external_id}"
    for field_name in ("canonical_item_url", "discussion_url", "canonical_story_url"):
        value = _normalize_text(row[field_name])
        if value:
            return value
    return f"news_item:{row['id']}"


def find_duplicate_news_item_families(
    conn: sqlite3.Connection,
    *,
    max_families: int | None = None,
) -> list[DuplicateNewsItemFamily]:
    """Return duplicate news_item families connected by stable identity tokens."""
    rows = conn.execute(
        """
        SELECT
            id,
            visibility_scope,
            owner_user_id,
            platform,
            source_external_id,
            canonical_item_url,
            discussion_url,
            canonical_story_url,
            article_title
        FROM news_items
        ORDER BY id ASC
        """
    ).fetchall()
    if not rows:
        return []

    row_by_id = {int(row["id"]): row for row in rows}
    uf = _UnionFind()
    token_members: dict[tuple[Any, ...], list[int]] = defaultdict(list)

    for row in rows:
        item_id = int(row["id"])
        uf.add(item_id)
        for token in _identity_tokens(row):
            token_members[token].append(item_id)

    for members in token_members.values():
        if len(members) < 2:
            continue
        anchor = members[0]
        for member in members[1:]:
            uf.union(anchor, member)

    families: dict[int, list[int]] = defaultdict(list)
    for item_id in row_by_id:
        families[uf.find(item_id)].append(item_id)

    duplicate_families: list[DuplicateNewsItemFamily] = []
    for member_ids in families.values():
        if len(member_ids) < 2:
            continue
        sorted_ids = tuple(sorted(member_ids))
        winner_id = sorted_ids[0]
        winner_row = row_by_id[winner_id]
        duplicate_families.append(
            DuplicateNewsItemFamily(
                winner_id=winner_id,
                loser_ids=sorted_ids[1:],
                identity_label=_preferred_identity_label(winner_row),
                title=_normalize_text(winner_row["article_title"]),
            )
        )

    duplicate_families.sort(key=lambda family: family.winner_id)
    if max_families is not None:
        return duplicate_families[:max_families]
    return duplicate_families


def _group_rows(rows: list[sqlite3.Row], key_name: str) -> dict[Any, list[sqlite3.Row]]:
    grouped: dict[Any, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        grouped[row[key_name]].append(row)
    return grouped


def _plan_family_cleanup(
    conn: sqlite3.Connection,
    family: DuplicateNewsItemFamily,
) -> DuplicateCleanupStats:
    family_ids = family.all_ids
    placeholders = ",".join("?" for _ in family_ids)

    coverage_rows = conn.execute(
        f"""
        SELECT id, user_id, news_item_id
        FROM news_item_digest_coverage
        WHERE news_item_id IN ({placeholders})
        ORDER BY id ASC
        """,
        family_ids,
    ).fetchall()
    updated_coverages = 0
    deleted_coverages = 0
    for user_rows in _group_rows(coverage_rows, "user_id").values():
        keeper = min(
            user_rows,
            key=lambda row: (row["news_item_id"] != family.winner_id, row["id"]),
        )
        if keeper["news_item_id"] != family.winner_id:
            updated_coverages += 1
        deleted_coverages += len(user_rows) - 1

    bullet_source_rows = conn.execute(
        f"""
        SELECT id, bullet_id, news_item_id, position
        FROM news_digest_bullet_sources
        WHERE news_item_id IN ({placeholders})
        ORDER BY bullet_id ASC, position ASC, id ASC
        """,
        family_ids,
    ).fetchall()
    updated_bullet_sources = 0
    deleted_bullet_sources = 0
    affected_bullet_ids: set[int] = set()
    for bullet_rows in _group_rows(bullet_source_rows, "bullet_id").values():
        keeper = min(
            bullet_rows,
            key=lambda row: (
                row["news_item_id"] != family.winner_id,
                row["position"],
                row["id"],
            ),
        )
        if keeper["news_item_id"] != family.winner_id:
            updated_bullet_sources += 1
        deleted_bullet_sources += len(bullet_rows) - 1
        affected_bullet_ids.add(int(keeper["bullet_id"]))

    return DuplicateCleanupStats(
        family_count=1,
        duplicate_row_count=family.duplicate_count,
        updated_coverages=updated_coverages,
        deleted_coverages=deleted_coverages,
        updated_bullet_sources=updated_bullet_sources,
        deleted_bullet_sources=deleted_bullet_sources,
        reindexed_bullets=len(affected_bullet_ids),
        deleted_news_items=family.duplicate_count,
    )


def _reindex_bullet_sources(conn: sqlite3.Connection, bullet_id: int) -> None:
    rows = conn.execute(
        """
        SELECT id, position
        FROM news_digest_bullet_sources
        WHERE bullet_id = ?
        ORDER BY position ASC, id ASC
        """,
        (bullet_id,),
    ).fetchall()
    for new_position, row in enumerate(rows, start=1):
        if int(row["position"]) == new_position:
            continue
        conn.execute(
            "UPDATE news_digest_bullet_sources SET position = ? WHERE id = ?",
            (new_position, int(row["id"])),
        )
    conn.execute(
        "UPDATE news_digest_bullets SET source_count = ? WHERE id = ?",
        (len(rows), bullet_id),
    )


def _apply_family_cleanup(
    conn: sqlite3.Connection,
    family: DuplicateNewsItemFamily,
) -> DuplicateCleanupStats:
    family_ids = family.all_ids
    placeholders = ",".join("?" for _ in family_ids)

    coverage_rows = conn.execute(
        f"""
        SELECT id, user_id, news_item_id
        FROM news_item_digest_coverage
        WHERE news_item_id IN ({placeholders})
        ORDER BY id ASC
        """,
        family_ids,
    ).fetchall()
    updated_coverages = 0
    deleted_coverages = 0
    for user_rows in _group_rows(coverage_rows, "user_id").values():
        keeper = min(
            user_rows,
            key=lambda row: (row["news_item_id"] != family.winner_id, row["id"]),
        )
        delete_ids = [int(row["id"]) for row in user_rows if int(row["id"]) != int(keeper["id"])]
        if delete_ids:
            conn.executemany(
                "DELETE FROM news_item_digest_coverage WHERE id = ?",
                [(coverage_id,) for coverage_id in delete_ids],
            )
            deleted_coverages += len(delete_ids)
        if keeper["news_item_id"] != family.winner_id:
            conn.execute(
                "UPDATE news_item_digest_coverage SET news_item_id = ? WHERE id = ?",
                (family.winner_id, int(keeper["id"])),
            )
            updated_coverages += 1

    bullet_source_rows = conn.execute(
        f"""
        SELECT id, bullet_id, news_item_id, position
        FROM news_digest_bullet_sources
        WHERE news_item_id IN ({placeholders})
        ORDER BY bullet_id ASC, position ASC, id ASC
        """,
        family_ids,
    ).fetchall()
    updated_bullet_sources = 0
    deleted_bullet_sources = 0
    affected_bullet_ids: set[int] = set()
    for bullet_rows in _group_rows(bullet_source_rows, "bullet_id").values():
        keeper = min(
            bullet_rows,
            key=lambda row: (
                row["news_item_id"] != family.winner_id,
                row["position"],
                row["id"],
            ),
        )
        delete_ids = [int(row["id"]) for row in bullet_rows if int(row["id"]) != int(keeper["id"])]
        if delete_ids:
            conn.executemany(
                "DELETE FROM news_digest_bullet_sources WHERE id = ?",
                [(source_id,) for source_id in delete_ids],
            )
            deleted_bullet_sources += len(delete_ids)
        if keeper["news_item_id"] != family.winner_id:
            conn.execute(
                "UPDATE news_digest_bullet_sources SET news_item_id = ? WHERE id = ?",
                (family.winner_id, int(keeper["id"])),
            )
            updated_bullet_sources += 1
        affected_bullet_ids.add(int(keeper["bullet_id"]))

    conn.executemany(
        "DELETE FROM news_items WHERE id = ?",
        [(loser_id,) for loser_id in family.loser_ids],
    )
    for bullet_id in sorted(affected_bullet_ids):
        _reindex_bullet_sources(conn, bullet_id)

    return DuplicateCleanupStats(
        family_count=1,
        duplicate_row_count=family.duplicate_count,
        updated_coverages=updated_coverages,
        deleted_coverages=deleted_coverages,
        updated_bullet_sources=updated_bullet_sources,
        deleted_bullet_sources=deleted_bullet_sources,
        reindexed_bullets=len(affected_bullet_ids),
        deleted_news_items=family.duplicate_count,
    )


def cleanup_duplicate_news_items(
    conn: sqlite3.Connection,
    *,
    apply_changes: bool,
    max_families: int | None = None,
) -> tuple[list[DuplicateNewsItemFamily], DuplicateCleanupStats]:
    """Plan or apply duplicate cleanup across stable-identity news item families."""
    families = find_duplicate_news_item_families(conn, max_families=max_families)
    stats = DuplicateCleanupStats()
    if not families:
        return families, stats

    started_transaction = apply_changes and not conn.in_transaction
    if started_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        for family in families:
            family_stats = (
                _apply_family_cleanup(conn, family)
                if apply_changes
                else _plan_family_cleanup(conn, family)
            )
            stats = stats.combine(family_stats)
        if started_transaction:
            conn.commit()
    except Exception:
        if started_transaction:
            conn.rollback()
        raise

    return families, stats


def _resolve_database_path(raw_value: str) -> Path:
    if raw_value.startswith("sqlite:///"):
        return Path(raw_value.removeprefix("sqlite:///"))
    return Path(raw_value)


def _default_database_arg() -> str:
    return os.environ.get("DATABASE_URL", "news_app.db")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cleanup duplicate news_items rows.")
    parser.add_argument(
        "--database",
        default=_default_database_arg(),
        help="SQLite database path or sqlite:/// URL",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the cleanup. Dry-run by default.",
    )
    parser.add_argument(
        "--max-families",
        type=int,
        default=None,
        help="Optionally limit the number of duplicate families processed.",
    )
    parser.add_argument(
        "--show-families",
        type=int,
        default=20,
        help="Number of duplicate families to print.",
    )
    return parser.parse_args()


def _print_family_preview(families: list[DuplicateNewsItemFamily], *, limit: int) -> None:
    if not families:
        return
    print("Duplicate families:")
    for family in families[:limit]:
        print(
            f"- winner={family.winner_id} losers={list(family.loser_ids)} "
            f"rows={len(family.all_ids)} identity={family.identity_label} "
            f"title={family.title or '<missing>'}"
        )
    remaining = len(families) - min(limit, len(families))
    if remaining > 0:
        print(f"... {remaining} more families not shown")


def _print_stats(stats: DuplicateCleanupStats, *, apply_changes: bool) -> None:
    mode = "Applied" if apply_changes else "Planned"
    print(f"{mode} cleanup summary:")
    print(f"- families: {stats.family_count}")
    print(f"- duplicate_rows: {stats.duplicate_row_count}")
    print(f"- updated_coverages: {stats.updated_coverages}")
    print(f"- deleted_coverages: {stats.deleted_coverages}")
    print(f"- updated_bullet_sources: {stats.updated_bullet_sources}")
    print(f"- deleted_bullet_sources: {stats.deleted_bullet_sources}")
    print(f"- reindexed_bullets: {stats.reindexed_bullets}")
    print(f"- deleted_news_items: {stats.deleted_news_items}")


def main() -> None:
    args = _parse_args()
    database_path = _resolve_database_path(args.database)
    conn = sqlite3.connect(database_path, timeout=60.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 60000")
    try:
        families, stats = cleanup_duplicate_news_items(
            conn,
            apply_changes=args.apply,
            max_families=args.max_families,
        )
    finally:
        conn.close()

    _print_stats(stats, apply_changes=args.apply)
    _print_family_preview(families, limit=max(0, args.show_families))


if __name__ == "__main__":
    main()
