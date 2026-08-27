"""AXe regression for scroll-driven News Briefing read persistence."""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import pytest

import app.routers.api.briefing as briefing_router
from app.models.db import (
    BriefingLens,
    BriefingSegment,
    BriefingState,
    NewsItemReadStatus,
)
from app.services.briefing.source_keys import build_source_key
from tests.ios_e2e.axe_harness import AxeRunner, AxeStateExpectation, tree_text

pytestmark = [pytest.mark.integration, pytest.mark.ios_e2e]

LENS_KEY = "scroll-read-regression"
LENS_TITLE = "Scroll Regression"
NEWS_LABEL = "News"
SEGMENT_COUNT = 6


@pytest.fixture
def briefing_refresh_requests(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Record explicit Briefing refreshes without changing endpoint behavior."""
    requests: list[int] = []
    enqueue = briefing_router.enqueue_briefing_refresh_task

    def recording_enqueue(*args: Any, **kwargs: Any) -> bool:
        requests.append(int(kwargs["user_id"]))
        return enqueue(*args, **kwargs)

    monkeypatch.setattr(briefing_router, "enqueue_briefing_refresh_task", recording_enqueue)
    return requests


def _launch_arguments(*, live_server: Any, user_id: int) -> dict[str, str | int | bool]:
    parsed = urlparse(live_server.base_url)
    return {
        "newslyE2EEnabled": True,
        "newslyE2EAutoLogin": True,
        "newslyE2EServerHost": parsed.hostname or "127.0.0.1",
        "newslyE2EServerPort": parsed.port or 80,
        "newslyE2EUseHTTPS": False,
        "newslyE2EUserId": user_id,
        "newslyE2ECompleteOnboarding": True,
        "newslyE2ECompleteTutorial": True,
    }


def _seed_news_briefing(
    db_session: Any,
    *,
    user_id: int,
    news_item_factory: Any,
) -> set[int]:
    now = datetime.now(UTC).replace(tzinfo=None)
    db_session.add(
        BriefingState(
            user_id=user_id,
            version=1,
            masthead_title="Briefing",
            masthead_deck="A deterministic edition for scroll read tracking.",
            last_append_at=now,
        )
    )
    lens = BriefingLens(
        user_id=user_id,
        key=LENS_KEY,
        tier="news",
        title=LENS_TITLE,
        deck="Stories tall enough to require real scrolling.",
        position=10,
        status="active",
    )
    db_session.add(lens)
    db_session.flush()
    assert lens.id is not None

    item_ids: set[int] = set()
    segments: list[BriefingSegment] = []
    for index in range(SEGMENT_COUNT):
        item = news_item_factory(
            ingest_key=f"axe-scroll-read-{index}",
            visibility_scope="user",
            owner_user_id=user_id,
            summary_title=f"Scroll Read Story {index + 1}",
            summary_text=(
                f"Scroll Read Story {index + 1} explains the concrete evidence, tradeoffs, "
                "and operational context a reader needs before moving to the next story."
            ),
        )
        assert item.id is not None
        item_id = int(item.id)
        item_ids.add(item_id)
        source_key = build_source_key("news", item_id)
        paragraphs = [
            {
                "runs": [
                    {
                        "kind": "source_link" if paragraph_index == 0 else "text",
                        "text": (
                            f"Scroll Read Story {index + 1}"
                            if paragraph_index == 0
                            else (
                                "This deliberately substantial paragraph keeps the segment "
                                "on screen long enough for an actual upward swipe to carry its "
                                "complete body beyond the pinned reading boundary."
                            )
                        ),
                        **({"source_key": source_key} if paragraph_index == 0 else {}),
                    }
                ]
            }
            for paragraph_index in range(3)
        ]
        segments.append(
            BriefingSegment(
                lens_id=int(lens.id),
                user_id=user_id,
                blocks=[{"type": "passage", "weight": "lead", "paragraphs": paragraphs}],
                markdown_raw=f"[Scroll Read Story {index + 1}](newsly://briefing/news/{item_id})",
                narration_text=f"Scroll read narration {index + 1}.",
                source_keys=[source_key],
                status="active",
                model="axe-fixture",
                prompt_version="test",
                created_at=now - timedelta(minutes=index),
                updated_at=now - timedelta(minutes=index),
            )
        )

    db_session.add_all(segments)
    db_session.commit()
    return item_ids


def _news_unread_count(tree: Any) -> int | None:
    matches = {
        int(match.group(1))
        for match in re.finditer(
            rf"{re.escape(NEWS_LABEL)}, (\d+) unread sources",
            tree_text(tree),
        )
    }
    if not matches:
        return None
    assert len(matches) == 1, f"Conflicting News unread counts in AX tree: {sorted(matches)}"
    return matches.pop()


def _read_news_item_ids(db_session: Any, *, user_id: int) -> set[int]:
    db_session.expire_all()
    return {
        int(item_id)
        for (item_id,) in db_session.query(NewsItemReadStatus.news_item_id)
        .filter(NewsItemReadStatus.user_id == user_id)
        .all()
    }


def test_scrolling_news_marks_read_before_any_refresh(
    briefing_refresh_requests: list[int],
    axe_runner: AxeRunner,
    live_server: Any,
    db_session: Any,
    test_user: Any,
    news_item_factory: Any,
) -> None:
    """A real upward scroll must update both the live count and persisted read rows."""
    assert test_user.id is not None
    user_id = int(test_user.id)
    test_user.reading_experience = "briefing"
    seeded_item_ids = _seed_news_briefing(
        db_session,
        user_id=user_id,
        news_item_factory=news_item_factory,
    )

    launched = axe_runner.launch(
        arguments=_launch_arguments(live_server=live_server, user_id=user_id),
        expectation=AxeStateExpectation(
            ids=("briefing.screen", "briefing.lens_pager"),
            texts=(f"{NEWS_LABEL}, {SEGMENT_COUNT} unread sources",),
        ),
        timeout_seconds=20,
        name="scroll_read_launch",
    )
    initial_unread_count = _news_unread_count(launched.tree)
    assert initial_unread_count == SEGMENT_COUNT

    # Let the launch settle past the read-mark debounce. Merely appearing in the
    # viewport must not mark a segment; only a subsequent passage beyond the
    # pinned boundary may do so.
    time.sleep(0.6)
    assert _read_news_item_ids(db_session, user_id=user_id) == set()
    assert briefing_refresh_requests == []

    observed_unread_count = initial_unread_count
    observed_read_ids: set[int] = set()
    for swipe_index in range(6):
        axe_runner.swipe_up(
            name=f"scroll_read_swipe_{swipe_index + 1}",
            expectation=AxeStateExpectation(
                ids=("briefing.tier.news", "briefing.lens_pager"),
            ),
            timeout_seconds=8,
        )

        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            current_count = _news_unread_count(axe_runner.describe_ui())
            current_read_ids = _read_news_item_ids(db_session, user_id=user_id)
            if (
                current_count is not None
                and current_count < initial_unread_count
                and current_count == initial_unread_count - len(current_read_ids)
            ):
                observed_unread_count = current_count
                observed_read_ids = current_read_ids
                break
            time.sleep(0.1)
        if observed_read_ids:
            break

    assert observed_unread_count < initial_unread_count, (
        "Upward scrolling never reduced the visible News unread count"
    )
    assert observed_read_ids, "Upward scrolling never persisted a News read row"
    assert observed_read_ids <= seeded_item_ids
    assert briefing_refresh_requests == [], (
        "The scroll assertion was contaminated by an explicit Briefing refresh"
    )

    captured = axe_runner.capture_until(
        name="scroll_read_persisted_without_refresh",
        expectation=AxeStateExpectation(
            ids=("briefing.tier.news", "briefing.lens_pager"),
        ),
        timeout_seconds=5,
    )
    captured_unread_count = _news_unread_count(captured.tree)
    assert captured_unread_count is not None
    assert captured_unread_count <= observed_unread_count

    # Scroll inertia may carry another segment across the boundary between the
    # first matching UI/DB observation and this evidence capture. Let that
    # optimistic state persist instead of freezing an already-stale count.
    expected_read_count = initial_unread_count - captured_unread_count
    deadline = time.monotonic() + 4
    captured_read_ids = _read_news_item_ids(db_session, user_id=user_id)
    while len(captured_read_ids) < expected_read_count and time.monotonic() < deadline:
        time.sleep(0.1)
        captured_read_ids = _read_news_item_ids(db_session, user_id=user_id)
    assert len(captured_read_ids) >= expected_read_count
    assert captured_read_ids <= seeded_item_ids
    assert briefing_refresh_requests == []
