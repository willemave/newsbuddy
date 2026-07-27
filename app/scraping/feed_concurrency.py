"""Bounded structured concurrency for independent configured feeds."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed


def run_feed_jobs[FeedT, ItemT](
    feeds: Sequence[FeedT],
    scrape_feed: Callable[[FeedT], list[ItemT]],
    *,
    max_workers: int = 4,
    on_error: Callable[[FeedT, Exception], None] | None = None,
) -> list[ItemT]:
    """Scrape independent feeds concurrently while preserving configured order."""
    if not feeds:
        return []

    worker_count = min(max(max_workers, 1), len(feeds))
    results_by_index: dict[int, list[ItemT]] = {}
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="feed-scrape") as executor:
        future_to_entry = {
            executor.submit(scrape_feed, feed): (index, feed) for index, feed in enumerate(feeds)
        }
        for future in as_completed(future_to_entry):
            index, feed = future_to_entry[future]
            try:
                results_by_index[index] = future.result()
            except Exception as exc:  # noqa: BLE001 - isolates one remote feed
                results_by_index[index] = []
                if on_error is not None:
                    on_error(feed, exc)

    return [item for index in range(len(feeds)) for item in results_by_index[index]]
