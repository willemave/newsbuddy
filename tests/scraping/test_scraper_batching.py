from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.models.contracts import ContentType, TaskType
from app.models.db import Content, ContentStatusEntry, NewsItem, NewsItemDiscussion, ProcessingTask
from app.scraping.base import BaseScraper
from app.services.queue import QueueService
from app.services.queue_enqueue import TaskEnqueueRequest


class _BatchScraper(BaseScraper):
    def __init__(self, items: list[dict[str, Any]]) -> None:
        super().__init__("batch-test")
        self._items = items

    def scrape(self) -> list[dict[str, Any]]:
        return self._items


class _RecordingQueue:
    def __init__(self) -> None:
        self.batches: list[list[TaskEnqueueRequest]] = []

    def enqueue_many_in_session(
        self,
        _db: Session,
        requests: list[TaskEnqueueRequest],
    ) -> list[int]:
        batch = list(requests)
        self.batches.append(batch)
        return list(range(1, len(batch) + 1))


def _patch_scraper_db(
    monkeypatch: Any,
    db_session_factory: sessionmaker[Session],
) -> None:
    @contextmanager
    def db_context() -> Iterator[Session]:
        session = db_session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    monkeypatch.setattr("app.scraping.base.get_db", db_context)


def _article_item(index: int, *, user_id: int) -> dict[str, Any]:
    return {
        "url": f"https://example.com/article-{index}",
        "title": f"Article {index}",
        "content_type": ContentType.ARTICLE,
        "user_id": user_id,
        "metadata": {"platform": "atom", "source": "Example"},
    }


def _news_item(index: int, *, user_id: int) -> dict[str, Any]:
    external_id = f"post-{index}"
    discussion_url = f"https://reddit.com/r/example/comments/{external_id}/story/"
    article_url = f"https://example.com/news-{index}"
    return {
        "url": article_url,
        "title": f"News {index}",
        "content_type": ContentType.NEWS,
        "owner_user_id": user_id,
        "visibility_scope": "user",
        "metadata": {
            "platform": "reddit",
            "source": "example",
            "source_type": "reddit",
            "source_label": "example",
            "summary": {
                "title": f"News {index}",
                "summary": "Ready summary",
                "key_points": ["Ready point"],
            },
            "article": {
                "url": article_url,
                "title": f"News {index}",
                "source_domain": "example.com",
            },
            "aggregator": {
                "name": "Reddit",
                "external_id": external_id,
                "metadata": {"comments_count": 2},
            },
            "discussion_url": discussion_url,
        },
    }


def test_scraper_skips_persistence_when_no_items_are_prepared(monkeypatch) -> None:
    def unexpected_db_context():
        raise AssertionError("Empty scraper results must not open a DB session")

    monkeypatch.setattr("app.scraping.base.get_db", unexpected_db_context)

    stats = _BatchScraper([]).run_with_stats()

    assert stats.scraped == 0
    assert stats.saved == 0
    assert stats.errors == 0


def test_scraper_batches_content_identity_commit_and_queue(
    db_session,
    db_session_factory,
    test_user,
    monkeypatch,
):
    """Three items should resolve identity once and leave through one queue batch."""
    queue = _RecordingQueue()
    statements: list[str] = []
    commits = 0

    def record_statement(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    def record_commit(session) -> None:
        nonlocal commits
        if session.bind is db_session.bind:
            commits += 1

    event.listen(db_session.bind, "before_cursor_execute", record_statement)
    event.listen(db_session_factory.class_, "after_commit", record_commit)

    _patch_scraper_db(monkeypatch, db_session_factory)
    scraper = _BatchScraper([_article_item(index, user_id=test_user.id) for index in range(3)])
    scraper.queue_service = queue

    try:
        stats = scraper.run_with_stats()
    finally:
        event.remove(db_session.bind, "before_cursor_execute", record_statement)
        event.remove(db_session_factory.class_, "after_commit", record_commit)

    identity_selects = [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith("SELECT") and "FROM contents" in statement
    ]
    selects = [
        statement for statement in statements if statement.lstrip().upper().startswith("SELECT")
    ]
    assert stats.saved == 3
    assert stats.errors == 0
    assert len(identity_selects) == 1
    assert len(selects) == 2
    assert len(statements) == 4
    assert commits == 1
    assert len(queue.batches) == 1
    assert [request.task_type for request in queue.batches[0]] == [
        TaskType.PROCESS_CONTENT,
        TaskType.PROCESS_CONTENT,
        TaskType.PROCESS_CONTENT,
    ]
    assert db_session.query(Content).count() == 3


def test_scraper_batches_news_identity_discussions_and_queue(
    db_session,
    db_session_factory,
    test_user,
    monkeypatch,
):
    queue = _RecordingQueue()
    statements: list[str] = []
    commits = 0

    def record_statement(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    def record_commit(session) -> None:
        nonlocal commits
        if session.bind is db_session.bind:
            commits += 1

    event.listen(db_session.bind, "before_cursor_execute", record_statement)
    event.listen(db_session_factory.class_, "after_commit", record_commit)

    _patch_scraper_db(monkeypatch, db_session_factory)
    scraper = _BatchScraper([_news_item(index, user_id=test_user.id) for index in range(3)])
    scraper.queue_service = queue

    try:
        stats = scraper.run_with_stats()
    finally:
        event.remove(db_session.bind, "before_cursor_execute", record_statement)
        event.remove(db_session_factory.class_, "after_commit", record_commit)

    selects = [
        statement for statement in statements if statement.lstrip().upper().startswith("SELECT")
    ]
    assert stats.saved == 3
    assert stats.errors == 0
    assert len(selects) == 3
    assert len(statements) == 5
    assert commits == 1
    assert len(queue.batches) == 1
    assert [request.task_type for request in queue.batches[0]] == [
        TaskType.FETCH_NEWS_ITEM_DISCUSSION,
        TaskType.FETCH_NEWS_ITEM_DISCUSSION,
        TaskType.FETCH_NEWS_ITEM_DISCUSSION,
    ]


def test_scraper_persists_content_and_tasks_in_one_transaction(
    db_session,
    db_session_factory,
    test_user,
    monkeypatch,
):
    commits = 0

    def record_commit(session) -> None:
        nonlocal commits
        if session.bind is db_session.bind:
            commits += 1

    event.listen(db_session_factory.class_, "after_commit", record_commit)

    _patch_scraper_db(monkeypatch, db_session_factory)
    scraper = _BatchScraper([_article_item(index, user_id=test_user.id) for index in range(3)])
    scraper.queue_service = QueueService()

    try:
        stats = scraper.run_with_stats()
    finally:
        event.remove(db_session_factory.class_, "after_commit", record_commit)

    assert stats.saved == 3
    assert commits == 1
    assert db_session.query(Content).count() == 3
    assert db_session.query(ProcessingTask).count() == 3


def test_scraper_dedupes_repeated_items_within_one_batch(
    db_session,
    db_session_factory,
    test_user,
    monkeypatch,
):
    _patch_scraper_db(monkeypatch, db_session_factory)

    article = _article_item(1, user_id=test_user.id)
    article_scraper = _BatchScraper([article, dict(article)])
    article_scraper.queue_service = QueueService()
    article_stats = article_scraper.run_with_stats()

    news = _news_item(1, user_id=test_user.id)
    news_scraper = _BatchScraper([news, dict(news)])
    news_scraper.queue_service = QueueService()
    news_stats = news_scraper.run_with_stats()

    assert (article_stats.saved, article_stats.duplicates, article_stats.errors) == (1, 1, 0)
    assert (news_stats.saved, news_stats.duplicates, news_stats.errors) == (1, 1, 0)
    assert db_session.query(Content).count() == 1
    assert db_session.query(NewsItem).count() == 1
    assert db_session.query(NewsItemDiscussion).count() == 1
    assert db_session.query(ProcessingTask).count() == 2


def test_scraper_falls_back_to_isolated_writes_when_one_item_breaks_the_batch(
    db_session,
    db_session_factory,
    test_user,
    monkeypatch,
):
    _patch_scraper_db(monkeypatch, db_session_factory)
    broken = _article_item(2, user_id=test_user.id)
    broken["metadata"] = {"platform": "atom", "unsupported": object()}
    scraper = _BatchScraper(
        [
            _article_item(1, user_id=test_user.id),
            broken,
            _article_item(3, user_id=test_user.id),
        ]
    )
    scraper.queue_service = QueueService()

    stats = scraper.run_with_stats()

    assert (stats.saved, stats.duplicates, stats.errors) == (2, 0, 1)
    assert stats.error_details == [
        "Error saving https://example.com/article-2: Object of type object is not JSON serializable"
    ]
    assert [row.url for row in db_session.query(Content).order_by(Content.url)] == [
        "https://example.com/article-1",
        "https://example.com/article-3",
    ]
    assert db_session.query(ProcessingTask).count() == 2


def test_scraper_batch_conflict_finalizes_winner_and_remaining_items(
    db_session,
    db_session_factory,
    test_user,
    monkeypatch,
):
    """A concurrent insert winner must not discard the rest of the feed batch."""
    _patch_scraper_db(monkeypatch, db_session_factory)
    scraper = _BatchScraper(
        [
            _article_item(1, user_id=test_user.id),
            _article_item(2, user_id=test_user.id),
        ]
    )
    scraper.queue_service = QueueService()
    persist_batch = scraper._persist_prepared_batch
    first_attempt = True

    def _persist_with_race(news_entries, content_entries):  # noqa: ANN001
        nonlocal first_attempt
        if first_attempt:
            first_attempt = False
            with db_session_factory() as competing_db:
                competing_db.add(
                    Content(
                        url="https://example.com/article-1",
                        source_url="https://example.com/article-1",
                        content_type=ContentType.ARTICLE.value,
                        status="new",
                        source="Example",
                        platform="atom",
                        content_metadata={},
                    )
                )
                competing_db.commit()
            raise IntegrityError("INSERT INTO contents", {}, Exception("duplicate key"))
        return persist_batch(news_entries, content_entries)

    monkeypatch.setattr(scraper, "_persist_prepared_batch", _persist_with_race)

    stats = scraper.run_with_stats()

    assert (stats.saved, stats.duplicates, stats.errors) == (1, 1, 0)
    rows = db_session.query(Content).order_by(Content.url).all()
    assert [row.url for row in rows] == [
        "https://example.com/article-1",
        "https://example.com/article-2",
    ]
    assert (
        db_session.query(ContentStatusEntry)
        .filter_by(user_id=test_user.id, content_id=rows[0].id)
        .one_or_none()
        is not None
    )
