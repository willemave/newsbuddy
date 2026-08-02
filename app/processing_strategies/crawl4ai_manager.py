"""Bounded process-lifetime ownership for the reusable Crawl4AI browser."""

import asyncio
import atexit
import concurrent.futures
import threading
import time
from typing import Any

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig

from app.core.logging import get_logger

logger = get_logger(__name__)

REUSABLE_CRAWLER_MAX_CRAWLS = 50
REUSABLE_CRAWLER_MAX_AGE_SECONDS = 15 * 60
REUSABLE_CRAWLER_ASYNC_CLOSE_TIMEOUT_SECONDS = 5.0
REUSABLE_CRAWLER_SYNC_CLOSE_TIMEOUT_SECONDS = 10.0
CRAWL4AI_OPERATION_GRACE_SECONDS = 60.0
CRAWL4AI_CONTEXT_RECYCLE_PAGES = 1


class ReusableCrawlerManager:
    """Own one async Crawl4AI crawler on a persistent loop thread."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._crawler: Any | None = None
        self._crawler_key: tuple[Any, ...] | None = None
        self._created_at = 0.0
        self._crawl_count = 0

    def run(
        self,
        *,
        browser_config: BrowserConfig,
        browser_config_key: tuple[Any, ...],
        url: str,
        run_config: CrawlerRunConfig,
        timeout_seconds: float,
    ) -> Any:
        """Run one crawl within a deadline that includes shared-crawler admission."""
        timeout_seconds = max(float(timeout_seconds), 0.001)
        deadline = time.monotonic() + timeout_seconds
        acquired = self._lock.acquire(timeout=timeout_seconds)
        if not acquired:
            raise TimeoutError(
                f"Crawl4AI crawl timeout after {timeout_seconds:.1f}s waiting for crawler access"
            )

        try:
            loop = self._ensure_loop_locked()
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                raise TimeoutError(
                    "Crawl4AI crawl timeout after "
                    f"{timeout_seconds:.1f}s waiting for crawler access"
                )

            future = asyncio.run_coroutine_threadsafe(
                self._arun(
                    browser_config=browser_config,
                    browser_config_key=browser_config_key,
                    url=url,
                    run_config=run_config,
                ),
                loop,
            )
            try:
                return future.result(timeout=remaining_seconds)
            except concurrent.futures.TimeoutError as exc:
                if future.done():
                    raise

                future.cancel()
                logger.error(
                    "Crawl4AI crawl exceeded its hard deadline",
                    extra={
                        "component": "html_strategy",
                        "operation": "crawl4ai_deadline",
                        "item_id": url,
                        "context_data": {"timeout_seconds": timeout_seconds},
                    },
                )
                self._close_crawler_on_loop_locked(
                    loop,
                    error_message="Error resetting timed-out Crawl4AI crawler",
                )
                raise TimeoutError(f"Crawl4AI crawl timeout after {timeout_seconds:.1f}s") from exc
        finally:
            self._lock.release()

    def mark_broken(self) -> None:
        """Discard the current crawler after a browser-level failure."""
        with self._lock:
            loop = self._loop
            if loop is None or loop.is_closed():
                self._reset_state_locked()
                return
            self._close_crawler_on_loop_locked(
                loop,
                error_message="Error closing broken crawler (non-critical)",
            )

    def close(self) -> None:
        """Close the crawler and stop the loop thread."""
        with self._lock:
            loop = self._loop
            thread = self._thread
            if loop is None:
                self._reset_state_locked()
                return
            if not loop.is_closed():
                self._close_crawler_on_loop_locked(
                    loop,
                    error_message="Error closing reusable crawler (non-critical)",
                )
                loop.call_soon_threadsafe(loop.stop)
            if thread is not None and thread.is_alive():
                thread.join(timeout=5)
            self._reset_state_locked()

    def _close_crawler_on_loop_locked(
        self,
        loop: asyncio.AbstractEventLoop,
        *,
        error_message: str,
    ) -> None:
        """Request crawler shutdown without allowing cleanup to block a worker forever."""
        future = asyncio.run_coroutine_threadsafe(self._close_crawler(), loop)
        try:
            future.result(timeout=REUSABLE_CRAWLER_SYNC_CLOSE_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001
            future.cancel()
            logger.warning("%s: %s", error_message, exc)

    def _ensure_loop_locked(self) -> asyncio.AbstractEventLoop:
        if self._loop is not None and not self._loop.is_closed():
            return self._loop

        ready = threading.Event()

        def _run_loop() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            ready.set()
            try:
                loop.run_forever()
            finally:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.run_until_complete(loop.shutdown_default_executor())
                asyncio.set_event_loop(None)
                loop.close()

        self._thread = threading.Thread(
            target=_run_loop,
            name="crawl4ai-reusable-crawler",
            daemon=True,
        )
        self._thread.start()
        ready.wait(timeout=5)
        if self._loop is None:
            raise RuntimeError("Reusable crawler loop failed to start")
        return self._loop

    async def _arun(
        self,
        *,
        browser_config: BrowserConfig,
        browser_config_key: tuple[Any, ...],
        url: str,
        run_config: CrawlerRunConfig,
    ) -> Any:
        crawler = await self._get_crawler(browser_config, browser_config_key)
        result = await crawler.arun(url=url, config=run_config)
        self._crawl_count += 1
        return result

    async def _get_crawler(
        self,
        browser_config: BrowserConfig,
        browser_config_key: tuple[Any, ...],
    ) -> Any:
        now = time.monotonic()
        effective_config_key = (id(AsyncWebCrawler), *browser_config_key)
        should_recycle = self._crawler is not None and (
            self._crawler_key != effective_config_key
            or self._crawl_count >= REUSABLE_CRAWLER_MAX_CRAWLS
            or (now - self._created_at) >= REUSABLE_CRAWLER_MAX_AGE_SECONDS
        )
        if should_recycle:
            await self._close_crawler()

        if self._crawler is None:
            crawler = AsyncWebCrawler(config=browser_config)
            self._crawler = crawler
            self._crawler_key = effective_config_key
            self._created_at = now
            self._crawl_count = 0
            try:
                await crawler.start()
            except BaseException:
                await self._close_crawler()
                raise
        return self._crawler

    async def _close_crawler(self) -> None:
        crawler = self._crawler
        self._crawler = None
        self._crawler_key = None
        self._created_at = 0.0
        self._crawl_count = 0
        if crawler is None:
            return
        try:
            await asyncio.wait_for(
                crawler.close(),
                timeout=REUSABLE_CRAWLER_ASYNC_CLOSE_TIMEOUT_SECONDS,
            )
        except Exception as close_error:  # noqa: BLE001
            logger.warning("Error closing browser (non-critical): %s", close_error)

    def _reset_state_locked(self) -> None:
        self._loop = None
        self._thread = None
        self._crawler = None
        self._crawler_key = None
        self._created_at = 0.0
        self._crawl_count = 0


REUSABLE_CRAWLER_MANAGER = ReusableCrawlerManager()
atexit.register(REUSABLE_CRAWLER_MANAGER.close)
