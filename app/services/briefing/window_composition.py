from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Protocol

from app.core.logging import get_logger
from app.core.settings import Settings
from app.services.briefing.composer import ComposedSegment, compose_window
from app.services.briefing.sources import BriefingSource

logger = get_logger(__name__)


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


def compose_windows[WindowT: CompositionWindow](
    prepared_windows: list[WindowT],
    *,
    user_id: int,
    task_id: int | None,
    use_llm: bool,
    settings: Settings,
) -> list[ComposedWindow[WindowT]]:
    def compose_one(window: WindowT) -> ComposedWindow[WindowT]:
        segment = compose_window(
            list(window.sources),
            lens_key=window.lens_key,
            lens_title=window.lens_title,
            tier=window.tier,
            window_index=window.window_index,
            task_id=task_id,
            user_id=user_id,
            use_llm=use_llm,
            settings=settings,
        )
        return ComposedWindow(prepared=window, segment=segment)

    max_workers = min(max(settings.briefing_compose_parallelism, 1), len(prepared_windows))
    if max_workers <= 1:
        return [compose_one(window) for window in prepared_windows]

    composed: list[ComposedWindow[WindowT]] = []
    for start in range(0, len(prepared_windows), max_workers):
        batch = prepared_windows[start : start + max_workers]
        logger.info(
            "Briefing composition batch started",
            extra={
                "component": "briefing",
                "operation": "compose_batch",
                "item_id": user_id,
                "task_id": task_id,
                "context_data": {
                    "batch_start": start,
                    "batch_size": len(batch),
                    "window_count": len(prepared_windows),
                },
            },
        )
        with ThreadPoolExecutor(max_workers=len(batch)) as executor:
            futures = {executor.submit(compose_one, window): window for window in batch}
            batch_results = [future.result() for future in as_completed(futures)]
            result_by_key = {
                (result.prepared.lens_id, result.prepared.window_index): result
                for result in batch_results
            }
            composed.extend(
                result_by_key[(window.lens_id, window.window_index)] for window in batch
            )
        logger.info(
            "Briefing composition batch completed",
            extra={
                "component": "briefing",
                "operation": "compose_batch",
                "item_id": user_id,
                "task_id": task_id,
                "context_data": {
                    "batch_start": start,
                    "batch_size": len(batch),
                    "window_count": len(prepared_windows),
                },
            },
        )
    return composed
