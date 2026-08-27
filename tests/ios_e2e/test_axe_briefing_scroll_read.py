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
SEGMENT_COUNT = 8
SOURCES_PER_SEGMENT = 2
SOURCE_COUNT = SEGMENT_COUNT * SOURCES_PER_SEGMENT


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
) -> list[frozenset[int]]:
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
    source_groups: list[frozenset[int]] = []
    segments: list[BriefingSegment] = []
    for segment_index in range(SEGMENT_COUNT):
        group_item_ids: set[int] = set()
        source_keys: list[str] = []
        source_paragraphs: list[dict[str, Any]] = []
        for source_index in range(SOURCES_PER_SEGMENT):
            story_number = segment_index * SOURCES_PER_SEGMENT + source_index + 1
            item = news_item_factory(
                ingest_key=f"axe-scroll-read-{story_number}",
                visibility_scope="user",
                owner_user_id=user_id,
                summary_title=f"Scroll Read Story {story_number}",
                summary_text=(
                    f"Scroll Read Story {story_number} explains the concrete evidence, "
                    "tradeoffs, and operational context a reader needs before moving on."
                ),
            )
            assert item.id is not None
            item_id = int(item.id)
            item_ids.add(item_id)
            group_item_ids.add(item_id)
            source_key = build_source_key("news", item_id)
            source_keys.append(source_key)
            source_paragraphs.append(
                {
                    "runs": [
                        {
                            "kind": "source_link",
                            "text": f"Scroll Read Story {story_number}",
                            "source_key": source_key,
                        }
                    ]
                }
            )
        paragraphs = source_paragraphs + [
            {
                "runs": [
                    {
                        "kind": "text",
                        "text": (
                            "This deliberately substantial paragraph keeps the grouped segment "
                            "on screen long enough for an actual upward swipe to carry its "
                            "complete body beyond the pinned reading boundary."
                        ),
                    }
                ]
            }
            for _ in range(2)
        ]
        source_groups.append(frozenset(group_item_ids))
        segments.append(
            BriefingSegment(
                lens_id=int(lens.id),
                user_id=user_id,
                blocks=[{"type": "passage", "weight": "lead", "paragraphs": paragraphs}],
                markdown_raw=f"Grouped scroll-read segment {segment_index + 1}.",
                narration_text=f"Scroll read narration {segment_index + 1}.",
                source_keys=source_keys,
                status="active",
                model="axe-fixture",
                prompt_version="test",
                created_at=now - timedelta(minutes=segment_index),
                updated_at=now - timedelta(minutes=segment_index),
            )
        )

    db_session.add_all(segments)
    db_session.commit()
    assert item_ids == set().union(*source_groups)
    return source_groups


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


def _assert_source_groups_are_atomic(
    read_ids: set[int],
    source_groups: list[frozenset[int]],
) -> None:
    partial_groups = [
        sorted(group)
        for group in source_groups
        if read_ids.intersection(group) and not group.issubset(read_ids)
    ]
    assert partial_groups == [], f"Partially read grouped segments: {partial_groups}"


@pytest.mark.parametrize(
    ("swipe_distance_fraction", "swipe_duration_seconds"),
    [(0.55, 0.35), (0.18, 0.9)],
    ids=("standard-swipe", "incremental-drag"),
)
def test_scrolling_news_marks_read_before_any_refresh(
    briefing_refresh_requests: list[int],
    axe_runner: AxeRunner,
    live_server: Any,
    db_session: Any,
    test_user: Any,
    news_item_factory: Any,
    swipe_distance_fraction: float,
    swipe_duration_seconds: float,
) -> None:
    """A real upward scroll must update both the live count and persisted read rows."""
    assert test_user.id is not None
    user_id = int(test_user.id)
    test_user.reading_experience = "briefing"
    source_groups = _seed_news_briefing(
        db_session,
        user_id=user_id,
        news_item_factory=news_item_factory,
    )
    seeded_item_ids = set().union(*source_groups)

    launched = axe_runner.launch(
        arguments=_launch_arguments(live_server=live_server, user_id=user_id),
        expectation=AxeStateExpectation(
            ids=("briefing.screen", "briefing.lens_pager"),
            texts=(f"{NEWS_LABEL}, {SOURCE_COUNT} unread sources",),
        ),
        timeout_seconds=20,
        name="scroll_read_launch",
    )
    initial_unread_count = _news_unread_count(launched.tree)
    assert initial_unread_count == SOURCE_COUNT

    # Let the launch settle past the read-mark debounce. Merely appearing in the
    # viewport must not mark a segment; only a subsequent passage beyond the
    # pinned boundary may do so.
    time.sleep(0.6)
    assert _read_news_item_ids(db_session, user_id=user_id) == set()
    assert briefing_refresh_requests == []

    observed_unread_count = initial_unread_count
    observed_read_ids: set[int] = set()
    maximum_swipes = 12 if swipe_distance_fraction < 0.3 else 6
    for swipe_index in range(maximum_swipes):
        axe_runner.swipe_up(
            name=f"scroll_read_swipe_{swipe_index + 1}",
            expectation=AxeStateExpectation(
                ids=("briefing.tier.news", "briefing.lens_pager"),
            ),
            timeout_seconds=8,
            distance_fraction=swipe_distance_fraction,
            duration_seconds=swipe_duration_seconds,
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
    _assert_source_groups_are_atomic(observed_read_ids, source_groups)
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
    _assert_source_groups_are_atomic(captured_read_ids, source_groups)
    assert captured_read_ids <= seeded_item_ids
    assert briefing_refresh_requests == []

    # The first optimistic mark replaces the selected lens render model. A
    # second independent passage proves that replacement did not detach the
    # page-level tracker from subsequent scroll updates.
    deadline = time.monotonic() + 4
    baseline_unread_count = captured_unread_count
    baseline_read_ids = captured_read_ids
    baseline_synchronized = False
    while time.monotonic() < deadline:
        current_count = _news_unread_count(axe_runner.describe_ui())
        current_read_ids = _read_news_item_ids(db_session, user_id=user_id)
        if current_count is not None and current_count == initial_unread_count - len(
            current_read_ids
        ):
            baseline_unread_count = current_count
            baseline_read_ids = current_read_ids
            baseline_synchronized = True
            break
        time.sleep(0.1)
    assert baseline_synchronized
    assert baseline_unread_count > 0

    continued_unread_count = baseline_unread_count
    continued_read_ids = baseline_read_ids
    for swipe_index in range(4):
        axe_runner.swipe_up(
            name=f"scroll_read_continued_swipe_{swipe_index + 1}",
            expectation=AxeStateExpectation(
                ids=("briefing.tier.news", "briefing.lens_pager"),
            ),
            timeout_seconds=8,
            distance_fraction=swipe_distance_fraction,
            duration_seconds=swipe_duration_seconds,
        )
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            current_count = _news_unread_count(axe_runner.describe_ui())
            current_read_ids = _read_news_item_ids(db_session, user_id=user_id)
            if (
                current_count is not None
                and current_count < baseline_unread_count
                and current_read_ids > baseline_read_ids
                and current_count == initial_unread_count - len(current_read_ids)
            ):
                continued_unread_count = current_count
                continued_read_ids = current_read_ids
                break
            time.sleep(0.1)
        if continued_read_ids > baseline_read_ids:
            break

    assert continued_unread_count < baseline_unread_count
    assert continued_read_ids > baseline_read_ids
    _assert_source_groups_are_atomic(continued_read_ids, source_groups)
    assert continued_read_ids <= seeded_item_ids
    assert briefing_refresh_requests == []
