from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import Mock

from app.models.contracts import TaskType
from app.scraping.discussion_comments import DiscussionCommentsScraper


def test_discussion_comments_scraper_enqueues_refresh_candidates(db_session, monkeypatch):
    queue_service = Mock()

    @contextmanager
    def _db_context():
        yield db_session

    sync_calls: list[int] = []

    def _sync_missing(db, *, limit: int) -> int:
        assert db is db_session
        sync_calls.append(limit)
        return 2

    def _list_candidates(db, *, limit: int) -> list[int]:
        assert db is db_session
        assert limit == 25
        return [101, 202]

    monkeypatch.setattr("app.scraping.discussion_comments.get_db", lambda: _db_context())
    monkeypatch.setattr(
        "app.scraping.discussion_comments.sync_missing_visible_news_item_discussions",
        _sync_missing,
    )
    monkeypatch.setattr(
        "app.scraping.discussion_comments.list_due_news_item_discussion_refresh_candidates",
        _list_candidates,
    )
    monkeypatch.setattr(
        "app.scraping.discussion_comments.get_queue_service",
        lambda: queue_service,
    )

    stats = DiscussionCommentsScraper(limit=25).run_with_stats()

    assert sync_calls == [50]
    assert stats.scraped == 2
    assert stats.saved == 2
    assert stats.errors == 0
    assert queue_service.enqueue.call_args_list[0].args == (TaskType.FETCH_NEWS_ITEM_DISCUSSION,)
    assert queue_service.enqueue.call_args_list[0].kwargs == {"payload": {"news_item_id": 101}}
    assert queue_service.enqueue.call_args_list[1].kwargs == {"payload": {"news_item_id": 202}}
