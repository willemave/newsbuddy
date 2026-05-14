import json
from datetime import UTC, datetime
from types import SimpleNamespace

from app.models.db import NewsItem
from app.testing.postgres_harness import create_temporary_postgres_harness
from scripts import export_news_items_raw_snapshot as exporter


def test_normalize_news_item_statuses_defaults_to_ready() -> None:
    assert exporter.normalize_news_item_statuses(None) == ["ready"]


def test_normalize_news_item_statuses_accepts_all() -> None:
    assert exporter.normalize_news_item_statuses(["ready", "all"]) == []


def test_normalize_news_item_statuses_rejects_unknown() -> None:
    try:
        exporter.normalize_news_item_statuses(["ready,unknown"])
    except ValueError as exc:
        assert "Unknown news item statuses: unknown" in str(exc)
    else:
        raise AssertionError("Expected unknown status to raise")


def test_build_news_items_raw_snapshot_resolves_article_body(monkeypatch) -> None:
    harness = create_temporary_postgres_harness(
        schema_prefix="newsly_raw_snapshot",
        tables=[NewsItem.__table__],
    )

    class FakeArticleBodyResolver:
        def resolve(self, session, *, news_item):  # noqa: ANN001
            del session
            return SimpleNamespace(
                source="storage",
                text=f"Full article body for news item {news_item.id}.",
                updated_at=datetime(2026, 5, 12, 12, 30, tzinfo=UTC).replace(tzinfo=None),
            )

    try:
        with harness.session_factory() as session:
            session.add_all(
                [
                    NewsItem(
                        id=501,
                        ingest_key="techmeme:test:501",
                        visibility_scope="global",
                        platform="techmeme",
                        source_type="aggregator",
                        source_label="Example",
                        article_url="https://example.com/story",
                        article_domain="example.com",
                        summary_key_points=["Existing live point."],
                        summary_text="Existing live summary.",
                        raw_metadata={
                            "article": {"title": "Example story"},
                            "summary": {"title": "Example summary"},
                        },
                        status="ready",
                        ingested_at=datetime(2026, 5, 12, 12, 0, tzinfo=UTC).replace(tzinfo=None),
                    ),
                    NewsItem(
                        id=502,
                        ingest_key="techmeme:test:502",
                        visibility_scope="global",
                        platform="techmeme",
                        source_type="aggregator",
                        source_label="Example",
                        raw_metadata={},
                        status="failed",
                        ingested_at=datetime(2026, 5, 12, 13, 0, tzinfo=UTC).replace(tzinfo=None),
                    ),
                ]
            )
            session.commit()

        monkeypatch.setattr(
            exporter,
            "get_news_item_article_body_resolver",
            lambda: FakeArticleBodyResolver(),
        )
        result = exporter.build_news_items_raw_snapshot(
            database_url=harness.database_url,
            limit=150,
            statuses=["ready"],
        )

        assert result["row_count"] == 1
        assert result["statuses"] == ["ready"]
        assert result["body_stats"] == {"resolved": 1, "missing": 0, "errors": 0}
        row = result["rows"][0]
        assert row["id"] == 501
        assert row["summary_key_points"] == ["Existing live point."]
        assert row["summary_text"] == "Existing live summary."
        assert row["article_body_text"] == "Full article body for news item 501."
        assert row["article_body_source"] == "storage"
        assert row["article_body_updated_at"] == "2026-05-12T12:30:00"
        assert row["article_title"] == "Example story"
        assert row["summary_title"] == "Example summary"
    finally:
        harness.close()


def test_write_envelope_writes_raw_snapshot_json(tmp_path) -> None:
    output_file = tmp_path / "news_snapshot.json"
    exporter.write_envelope(
        exporter.build_envelope({"rows": [{"id": 1}], "row_count": 1}),
        output_file,
    )

    payload = json.loads(output_file.read_text(encoding="utf-8"))
    assert payload == {
        "ok": True,
        "command": "export.news-items-raw-snapshot",
        "data": {"rows": [{"id": 1}], "row_count": 1},
    }
