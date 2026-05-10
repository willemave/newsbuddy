"""Shared helpers for discussion fetcher tests."""

from __future__ import annotations

from app.models.contracts import ContentStatus, ContentType
from app.models.db import Content


class FakeResponse:
    """Lightweight response stub for mocked HTTP fetches."""

    def __init__(self, *, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return


class FakeAuthor:
    """Simple author model used by fake comment objects."""

    def __init__(self, name: str) -> None:
        self.name = name


class FakeComment:
    """PRAW-like comment stub."""

    def __init__(
        self,
        *,
        comment_id: str,
        body: str,
        author: str,
        created_utc: int,
        replies: list[FakeComment] | None = None,
        body_html: str | None = None,
    ) -> None:
        self.id = comment_id
        self.body = body
        self.body_html = body_html
        self.author = FakeAuthor(author)
        self.created_utc = created_utc
        self.replies = FakeCommentForest(replies or [])


class FakeCommentForest(list):
    """PRAW comment forest stub."""

    def replace_more(self, limit: int = 0) -> None:
        return None


class FakeSubmission:
    """PRAW submission stub."""

    def __init__(self, *, title: str, num_comments: int, comments: list[FakeComment]) -> None:
        self.title = title
        self.num_comments = num_comments
        self.comment_sort: str | None = None
        self.comments = FakeCommentForest(comments)


class FakeRedditClient:
    """PRAW client stub with call tracing."""

    def __init__(self, submission: FakeSubmission) -> None:
        self._submission = submission
        self.requested_ids: list[str] = []

    def submission(self, *, id: str):  # noqa: A002 - mimic praw API
        self.requested_ids.append(id)
        return self._submission


def create_news_content(db_session, *, metadata: dict[str, object]) -> Content:
    """Insert and return a news content row for discussion tests."""
    content = Content(
        content_type=ContentType.NEWS.value,
        url="https://example.com/story",
        title="Example Story",
        source="example.com",
        platform=str(metadata.get("platform") or ""),
        status=ContentStatus.NEW.value,
        content_metadata=metadata,
    )
    db_session.add(content)
    db_session.commit()
    db_session.refresh(content)
    return content
