from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Protocol

from app.core.settings import Settings
from app.services.briefing.composer import (
    ComposedSegment,
    LayoutGenerator,
    compose_window,
)
from app.services.briefing.sources import BriefingSource


class CompositionWindow(Protocol):
    @property
    def lens_id(self) -> int: ...

    @property
    def lens_key(self) -> str: ...

    @property
    def lens_title(self) -> str: ...

    @property
    def tier(self) -> str: ...

    @property
    def window_index(self) -> int: ...

    @property
    def sources(self) -> tuple[BriefingSource, ...]: ...


@dataclass(frozen=True)
class ComposedWindow[WindowT: CompositionWindow]:
    prepared: WindowT
    segment: ComposedSegment


def _compose_window[WindowT: CompositionWindow](
    window: WindowT,
    *,
    user_id: int,
    task_id: int | None,
    settings: Settings,
    layout_generator: LayoutGenerator | None,
) -> ComposedWindow[WindowT]:
    segment = compose_window(
        list(window.sources),
        lens_key=window.lens_key,
        lens_title=window.lens_title,
        tier=window.tier,
        window_index=window.window_index,
        task_id=task_id,
        user_id=user_id,
        settings=settings,
        layout_generator=layout_generator,
    )
    return ComposedWindow(prepared=window, segment=segment)


def compose_windows[WindowT: CompositionWindow](
    prepared_windows: list[WindowT],
    *,
    user_id: int,
    task_id: int | None,
    settings: Settings,
    layout_generator: LayoutGenerator | None = None,
) -> list[ComposedWindow[WindowT]]:
    def compose_one(window: WindowT) -> ComposedWindow[WindowT]:
        return _compose_window(
            window,
            user_id=user_id,
            task_id=task_id,
            settings=settings,
            layout_generator=layout_generator,
        )

    max_workers = min(max(settings.briefing_compose_parallelism, 1), len(prepared_windows))
    if max_workers <= 1:
        return [compose_one(window) for window in prepared_windows]

    with ThreadPoolExecutor(max_workers=max_workers) as owned_executor:
        futures = [owned_executor.submit(compose_one, window) for window in prepared_windows]
        return [future.result() for future in futures]


def compose_window_groups[
    FirstWindowT: CompositionWindow,
    SecondWindowT: CompositionWindow,
](
    first_windows: list[FirstWindowT],
    second_windows: list[SecondWindowT],
    *,
    user_id: int,
    task_id: int | None,
    settings: Settings,
    layout_generator: LayoutGenerator | None = None,
) -> tuple[list[ComposedWindow[FirstWindowT]], list[ComposedWindow[SecondWindowT]]]:
    total_count = len(first_windows) + len(second_windows)
    max_workers = min(max(settings.briefing_compose_parallelism, 1), total_count)
    if max_workers <= 1:
        return (
            compose_windows(
                first_windows,
                user_id=user_id,
                task_id=task_id,
                settings=settings,
                layout_generator=layout_generator,
            ),
            compose_windows(
                second_windows,
                user_id=user_id,
                task_id=task_id,
                settings=settings,
                layout_generator=layout_generator,
            ),
        )

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        first_futures = [
            executor.submit(
                _compose_window,
                window,
                user_id=user_id,
                task_id=task_id,
                settings=settings,
                layout_generator=layout_generator,
            )
            for window in first_windows
        ]
        second_futures = [
            executor.submit(
                _compose_window,
                window,
                user_id=user_id,
                task_id=task_id,
                settings=settings,
                layout_generator=layout_generator,
            )
            for window in second_windows
        ]
        return (
            [future.result() for future in first_futures],
            [future.result() for future in second_futures],
        )
