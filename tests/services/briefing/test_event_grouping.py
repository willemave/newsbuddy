from __future__ import annotations

import numpy as np
import pytest

from app.services.briefing import event_grouping
from app.services.briefing.composer import plan_windows
from app.services.briefing.sources import BriefingSource

# Unit vectors: Pixel angles share a direction, unrelated stories point elsewhere.
_PIXEL = np.array([1.0, 0.0, 0.0])
_PIXEL_ANGLE = np.array([0.9, 0.436, 0.0])  # cos ≈ 0.90 to _PIXEL
_UBER = np.array([0.0, 1.0, 0.0])
_SAMSUNG_FOLD = np.array([0.7, 0.0, 0.714])  # cos ≈ 0.70 to _PIXEL


def _source(news_id: int, title: str) -> BriefingSource:
    return BriefingSource(
        source_key=f"news:{news_id}",
        kind="news",
        id=news_id,
        tier="news",
        lens_key="tech",
        title=title,
        summary=None,
        key_points=[],
        url=None,
        image_url=None,
        thumbnail_url=None,
        published_at=None,
        content_type=None,
    )


PIXEL_TITLES = [
    "Google unveils Pixel 11 Pro Fold with Tensor G6",
    "Google launches Pixel 11 Pro Fold with thinner design",
    "Google raises Pixel 11 prices $100",
    "Google Pixel 11 introduces Camera Looks",
    "Google cuts Pixel 11 AI Pro trial to 6 months",
]


def _fake_encoder(vectors_by_title: dict[str, np.ndarray]):
    def encode(texts: list[str], **_kwargs: object) -> np.ndarray:
        return np.array([vectors_by_title[text.split("\n")[0]] for text in texts])

    return encode


def test_group_news_events_keeps_one_event_together(monkeypatch: pytest.MonkeyPatch) -> None:
    titles = [*PIXEL_TITLES, "Uber sells stake in Serve Robotics"]
    sources = [_source(index, title) for index, title in enumerate(titles, start=1)]
    vectors = {
        title: _PIXEL if index == 0 else _PIXEL_ANGLE for index, title in enumerate(PIXEL_TITLES)
    }
    vectors["Uber sells stake in Serve Robotics"] = _UBER
    monkeypatch.setattr(event_grouping, "encode_texts_with_embedding_model", _fake_encoder(vectors))

    events = event_grouping.group_news_events(sources, source_of=lambda source: source)

    assert [[source.id for source in event] for event in events] == [[1, 2, 3, 4, 5], [6]]


def test_group_news_events_requires_shared_title_token(monkeypatch: pytest.MonkeyPatch) -> None:
    sources = [
        _source(1, "Google unveils Pixel 11 Pro Fold with Tensor G6"),
        _source(2, "Apple announces new chips"),
    ]
    monkeypatch.setattr(
        event_grouping,
        "encode_texts_with_embedding_model",
        _fake_encoder({sources[0].title: _PIXEL, sources[1].title: _PIXEL}),
    )

    events = event_grouping.group_news_events(sources, source_of=lambda source: source)

    assert [[source.id for source in event] for event in events] == [[1], [2]]


def test_group_news_events_rejects_below_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    sources = [
        _source(1, "Google unveils Pixel 11 Pro Fold with Tensor G6"),
        _source(2, "Samsung Galaxy Z Fold 8 review: the thinnest foldable yet"),
    ]
    monkeypatch.setattr(
        event_grouping,
        "encode_texts_with_embedding_model",
        _fake_encoder({sources[0].title: _PIXEL, sources[1].title: _SAMSUNG_FOLD}),
    )

    events = event_grouping.group_news_events(sources, source_of=lambda source: source)

    assert [[source.id for source in event] for event in events] == [[1], [2]]


def test_group_news_events_falls_back_to_arrival_on_embedding_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = [_source(index, title) for index, title in enumerate(PIXEL_TITLES, start=1)]

    def failing_encode(texts: list[str], **_kwargs: object) -> np.ndarray:
        raise RuntimeError("embedding provider down")

    monkeypatch.setattr(event_grouping, "encode_texts_with_embedding_model", failing_encode)

    events = event_grouping.group_news_events(sources, source_of=lambda source: source)

    assert [[source.id for source in event] for event in events] == [[1], [2], [3], [4], [5]]


def test_plan_windows_counts_events_not_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    """Five Pixel angles plus three unrelated stories are four events: one window."""
    other_titles = [
        "Uber sells stake in Serve Robotics",
        "Live map of webcams for the 2026 solar eclipse",
        "OpenAI ethics lead departs",
    ]
    titles = [*PIXEL_TITLES[:2], other_titles[0], *PIXEL_TITLES[2:], *other_titles[1:]]
    sources = [_source(index, title) for index, title in enumerate(titles, start=1)]
    vectors: dict[str, np.ndarray] = {}
    for index, title in enumerate(PIXEL_TITLES):
        vectors[title] = _PIXEL if index == 0 else _PIXEL_ANGLE
    for index, title in enumerate(other_titles):
        unit = np.zeros(3)
        unit[1 + index % 2] = 1.0
        vectors[title] = unit if index < 2 else np.array([0.0, 0.707, 0.707])
    monkeypatch.setattr(event_grouping, "encode_texts_with_embedding_model", _fake_encoder(vectors))
    settings = event_grouping.get_settings()
    monkeypatch.setattr(settings, "briefing_news_window_max", 4)

    windows = plan_windows(
        sources,
        tier="news",
        settings=settings,
        source_of=lambda source: source,
    )

    assert len(windows) == 1
    assert [source.id for source in windows[0]] == [1, 2, 4, 5, 6, 3, 7, 8]


def test_plan_windows_without_source_of_treats_each_source_as_an_event() -> None:
    windows = plan_windows(list(range(8)), tier="news")

    assert [len(window) for window in windows] == [4, 4]
