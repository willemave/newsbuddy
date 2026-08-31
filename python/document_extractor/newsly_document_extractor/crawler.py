"""Warm, single-flight Crawl4AI ownership for the extractor process."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from urllib.parse import urlsplit

from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CacheMode,
    CrawlerRunConfig,
    LLMConfig,
    LLMTableExtraction,
)
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

from newsly_document_extractor.models import ExtractionProfile
from newsly_document_extractor.settings import ExtractorSettings
from newsly_document_extractor.url_safety import UrlSafetyError, require_public_url

logger = logging.getLogger(__name__)


class WarmCrawler:
    """Own one reusable browser and recycle it after bounded use or age."""

    def __init__(self, settings: ExtractorSettings) -> None:
        self._settings = settings
        self._lock = asyncio.Lock()
        self._crawler: Any | None = None
        self._created_at = 0.0
        self._crawl_count = 0

    async def close(self) -> None:
        async with self._lock:
            await self._close_unlocked()

    async def crawl(
        self,
        *,
        url: str,
        profile: ExtractionProfile,
        timeout_seconds: float,
    ) -> Any:
        """Run one bounded crawl, including the wait for single-flight admission."""

        try:
            async with asyncio.timeout(timeout_seconds):
                async with self._lock:
                    crawler = await self._get_crawler_unlocked()
                    try:
                        result = await crawler.arun(
                            url=url,
                            config=self._run_config(profile, timeout_seconds, url),
                        )
                    except BaseException:
                        await self._close_unlocked()
                        raise
                    self._crawl_count += 1
                    return result
        except TimeoutError:
            # A timed-out browser may still be carrying a page or context. Discard it before the
            # next caller so a late result cannot leak across requests.
            await self.close()
            raise

    async def _get_crawler_unlocked(self) -> Any:
        now = time.monotonic()
        should_recycle = self._crawler is not None and (
            self._crawl_count >= self._settings.crawler_max_crawls
            or (now - self._created_at) >= self._settings.crawler_max_age_seconds
        )
        if should_recycle:
            await self._close_unlocked()

        if self._crawler is None:
            crawler = AsyncWebCrawler(
                config=BrowserConfig(
                    headless=True,
                    viewport_width=1920,
                    viewport_height=1080,
                    text_mode=False,
                    light_mode=True,
                    ignore_https_errors=True,
                    java_script_enabled=True,
                    extra_args=["--disable-blink-features=AutomationControlled"],
                    max_pages_before_recycle=1,
                    verbose=False,
                )
            )
            crawler.crawler_strategy.set_hook(
                "on_page_context_created",
                self._install_public_network_guard,
            )
            self._crawler = crawler
            self._created_at = now
            self._crawl_count = 0
            try:
                await crawler.start()
            except BaseException:
                await self._close_unlocked()
                raise
        return self._crawler

    @staticmethod
    async def _install_public_network_guard(page: Any, **_kwargs: Any) -> Any:
        """Abort browser subrequests that target anything outside the public network."""

        logged_block = False

        async def guard_route(route: Any) -> None:
            nonlocal logged_block
            request_url = str(route.request.url)
            if request_url.startswith(("about:", "blob:", "data:")):
                await route.continue_()
                return
            try:
                await require_public_url(request_url)
            except UrlSafetyError:
                if not logged_block:
                    logger.warning("Blocked one or more non-public Crawl4AI subrequests")
                    logged_block = True
                await route.abort("blockedbyclient")
                return
            await route.continue_()

        await page.route("**/*", guard_route)
        return page

    async def _close_unlocked(self) -> None:
        crawler = self._crawler
        self._crawler = None
        self._created_at = 0.0
        self._crawl_count = 0
        if crawler is None:
            return
        try:
            async with asyncio.timeout(5.0):
                await crawler.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Unable to close reusable Crawl4AI browser: %s", exc)

    def _table_strategy(self) -> LLMTableExtraction | None:
        provider = self._settings.table_extraction_provider
        if not provider:
            return None
        config: dict[str, str] = {"provider": provider}
        if self._settings.table_extraction_api_token is not None:
            config["api_token"] = self._settings.table_extraction_api_token.get_secret_value()
        return LLMTableExtraction(
            llm_config=LLMConfig(**config),
            enable_chunking=True,
            chunk_token_threshold=3_000,
            min_rows_per_chunk=10,
            max_parallel_chunks=5,
            verbose=False,
        )

    def _run_config(
        self,
        profile: ExtractionProfile,
        timeout_seconds: float,
        url: str,
    ) -> CrawlerRunConfig:
        excluded_tags = ["script", "style", "nav", "footer", "header"]
        target_elements: list[str] | None = None
        word_count_threshold = 20
        excluded_selector: str | None = None
        wait_for: str | None = "body"

        if profile is ExtractionProfile.SCIENTIFIC:
            excluded_tags = ["script", "style", "nav", "footer"]
            target_elements = [".article", ".abstract", ".body", ".content", "main"]
            word_count_threshold = 10
        elif profile is ExtractionProfile.NEWSLETTER:
            excluded_tags.extend(["form", "aside"])
            target_elements = [".post", ".post-content", "article"]
            excluded_selector = ".subscribe-widget, .footer-wrap, .subscription-form-wrapper"
        elif profile is ExtractionProfile.ARTICLE:
            target_elements = ["article", "main", "[role=main]"]

        page_timeout_ms = min(int(timeout_seconds * 1_000), 180_000)
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
        if host == "medium.com" or host.endswith(".medium.com"):
            target_elements = ["article", ".postArticle", ".section-content"]
            excluded_selector = ".metabar, .js-postActions, .js-stickyFooter"
        if any(
            host == suffix or host.endswith(f".{suffix}")
            for suffix in ("screenrant.com", "redbook.io")
        ):
            page_timeout_ms = min(page_timeout_ms, 45_000)
            wait_for = None
        elif host == "dashboard.congress.ccc.de" or host.endswith(".dashboard.congress.ccc.de"):
            page_timeout_ms = min(page_timeout_ms, 30_000)
            wait_for = None

        return CrawlerRunConfig(
            word_count_threshold=word_count_threshold,
            excluded_tags=excluded_tags,
            excluded_selector=excluded_selector,
            target_elements=target_elements,
            exclude_external_links=True,
            process_iframes=False,
            remove_overlay_elements=True,
            remove_forms=True,
            keep_data_attributes=False,
            wait_until="domcontentloaded",
            wait_for=wait_for,
            delay_before_return_html=1.0,
            page_timeout=page_timeout_ms,
            wait_for_timeout=page_timeout_ms,
            adjust_viewport_to_content=True,
            cache_mode=CacheMode.BYPASS,
            verbose=False,
            exclude_social_media_links=True,
            exclude_domains=["facebook.com", "twitter.com", "instagram.com", "linkedin.com"],
            check_robots_txt=False,
            table_extraction=self._table_strategy(),
            markdown_generator=DefaultMarkdownGenerator(
                content_source="raw_html",
                options={
                    "ignore_links": False,
                    "ignore_images": True,
                    "escape_html": False,
                    "body_width": 0,
                },
            ),
        )
