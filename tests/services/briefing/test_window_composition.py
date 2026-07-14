from concurrent.futures import Future
from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.settings import get_settings
from app.models.contracts import ContentType
from app.services.briefing.composer import ComposedSegment
from app.services.briefing.sources import BriefingSource
from app.services.briefing.window_composition import compose_window_groups, compose_windows


@dataclass(frozen=True)
class _Window:
    lens_id: int
    lens_key: str
    lens_title: str
    tier: str
    window_index: int
    sources: tuple[BriefingSource, ...]


def test_parallel_compose_uses_context_managed_executor(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "briefing_compose_parallelism", 2)
    windows = [
        _Window(
            lens_id=1,
            lens_key="articles",
            lens_title="Articles",
            tier="longform",
            window_index=0,
            sources=(_briefing_source(1),),
        ),
        _Window(
            lens_id=1,
            lens_key="articles",
            lens_title="Articles",
            tier="longform",
            window_index=1,
            sources=(_briefing_source(2),),
        ),
        _Window(
            lens_id=1,
            lens_key="articles",
            lens_title="Articles",
            tier="longform",
            window_index=2,
            sources=(_briefing_source(3),),
        ),
        _Window(
            lens_id=1,
            lens_key="articles",
            lens_title="Articles",
            tier="longform",
            window_index=3,
            sources=(_briefing_source(4),),
        ),
        _Window(
            lens_id=1,
            lens_key="articles",
            lens_title="Articles",
            tier="longform",
            window_index=4,
            sources=(_briefing_source(5),),
        ),
    ]

    executors = []

    class FakeExecutor:
        def __init__(self, *, max_workers: int) -> None:
            self.max_workers = max_workers
            self.did_exit = False
            executors.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:  # noqa: ANN002
            self.did_exit = True

        def submit(self, fn, window):  # noqa: ANN001
            future: Future = Future()
            future.set_result(fn(window))
            return future

    monkeypatch.setattr("app.services.briefing.window_composition.ThreadPoolExecutor", FakeExecutor)
    monkeypatch.setattr(
        "app.services.briefing.window_composition.compose_window", _fake_compose_window
    )

    composed = compose_windows(
        windows,
        user_id=1,
        task_id=99,
        use_llm=True,
        settings=settings,
    )

    assert [result.prepared.window_index for result in composed] == [0, 1, 2, 3, 4]
    assert len(executors) == 1
    assert executors[0].max_workers == 2
    assert executors[0].did_exit


def test_grouped_compose_submits_append_and_compaction_before_waiting(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "briefing_compose_parallelism", 2)
    first = [_window(1), _window(2)]
    second = [_window(3)]
    submitted: list[_Window] = []

    class DeferredFuture:
        def __init__(self, fn, window, kwargs) -> None:  # noqa: ANN001
            self.fn = fn
            self.window = window
            self.kwargs = kwargs

        def result(self):
            assert len(submitted) == 3
            return self.fn(self.window, **self.kwargs)

    class FakeExecutor:
        def __init__(self, *, max_workers: int) -> None:
            assert max_workers == 2

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:  # noqa: ANN002
            pass

        def submit(self, fn, window, **kwargs):  # noqa: ANN001
            submitted.append(window)
            return DeferredFuture(fn, window, kwargs)

    monkeypatch.setattr("app.services.briefing.window_composition.ThreadPoolExecutor", FakeExecutor)
    monkeypatch.setattr(
        "app.services.briefing.window_composition.compose_window",
        _fake_compose_window,
    )

    composed_first, composed_second = compose_window_groups(
        first,
        second,
        user_id=1,
        task_id=99,
        use_llm=True,
        settings=settings,
    )

    assert [result.prepared.window_index for result in composed_first] == [1, 2]
    assert [result.prepared.window_index for result in composed_second] == [3]


def _window(index: int) -> _Window:
    return _Window(
        lens_id=1,
        lens_key="articles",
        lens_title="Articles",
        tier="longform",
        window_index=index,
        sources=(_briefing_source(index),),
    )


def _fake_compose_window(sources, **_kwargs):  # noqa: ANN001, ANN003
    source = sources[0]
    return ComposedSegment(
        blocks=[{"type": "passage", "source_keys": [source.source_key]}],
        markdown_raw=source.title,
        narration_text=source.title,
        status="active",
        model="deterministic",
        prompt_version="test",
        input_tokens=None,
        output_tokens=None,
        generation_ms=1,
        warnings=[],
    )


def _briefing_source(index: int) -> BriefingSource:
    return BriefingSource(
        source_key=f"content:{index}",
        kind="content",
        id=index,
        tier="longform",
        lens_key="articles",
        title=f"Briefing source {index}",
        summary=f"Summary {index}",
        key_points=[f"Point {index}"],
        url=f"https://example.com/{index}",
        image_url=None,
        thumbnail_url=None,
        published_at=datetime.now(UTC).replace(tzinfo=None),
        content_type=ContentType.ARTICLE,
    )
