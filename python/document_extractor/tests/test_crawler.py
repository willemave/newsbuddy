import asyncio
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

import pytest

import newsly_document_extractor.crawler as crawler_module
from newsly_document_extractor.crawler import WarmCrawler
from newsly_document_extractor.models import ExtractionProfile
from newsly_document_extractor.settings import ExtractorSettings


class FakePage:
    def __init__(self) -> None:
        self.handler: Callable[[Any], Awaitable[None]] | None = None

    async def route(self, _pattern: str, handler: Callable[[Any], Awaitable[None]]) -> None:
        self.handler = handler


class FakeRoute:
    def __init__(self, url: str) -> None:
        self.request = SimpleNamespace(url=url)
        self.aborted = False
        self.continued = False

    async def abort(self, _reason: str) -> None:
        self.aborted = True

    async def continue_(self) -> None:
        self.continued = True


class FakeCrawlerStrategy:
    def __init__(self) -> None:
        self.hook: Callable[..., Awaitable[Any]] | None = None

    def set_hook(self, _name: str, hook: Callable[..., Awaitable[Any]]) -> None:
        self.hook = hook


class FakeCrawler:
    instances: list["FakeCrawler"] = []
    block_crawl = False

    def __init__(self, *, config: Any) -> None:
        self.config = config
        self.crawler_strategy = FakeCrawlerStrategy()
        self.started = False
        self.closed = False
        self.__class__.instances.append(self)

    async def start(self) -> None:
        self.started = True

    async def arun(self, **_kwargs: Any) -> Any:
        if self.block_crawl:
            await asyncio.Event().wait()
        return SimpleNamespace(success=True)

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_browser_route_guard_aborts_private_subrequests() -> None:
    page = FakePage()
    await WarmCrawler._install_public_network_guard(page)
    assert page.handler is not None
    route = FakeRoute("http://169.254.169.254/latest/meta-data")

    await page.handler(route)

    assert route.aborted
    assert not route.continued


@pytest.mark.asyncio
async def test_warm_crawler_recycles_after_the_configured_crawl_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeCrawler.instances = []
    FakeCrawler.block_crawl = False
    monkeypatch.setattr(crawler_module, "AsyncWebCrawler", FakeCrawler)
    crawler = WarmCrawler(
        ExtractorSettings(
            environment="test",
            shared_secret="test-secret",
            crawler_max_crawls=1,
        )
    )

    await crawler.crawl(
        url="https://example.com/one",
        profile=ExtractionProfile.ARTICLE,
        timeout_seconds=1,
    )
    await crawler.crawl(
        url="https://example.com/two",
        profile=ExtractionProfile.ARTICLE,
        timeout_seconds=1,
    )
    await crawler.close()

    assert len(FakeCrawler.instances) == 2
    assert FakeCrawler.instances[0].closed
    assert FakeCrawler.instances[1].closed


@pytest.mark.asyncio
async def test_warm_crawler_discards_a_browser_after_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeCrawler.instances = []
    FakeCrawler.block_crawl = True
    monkeypatch.setattr(crawler_module, "AsyncWebCrawler", FakeCrawler)
    crawler = WarmCrawler(ExtractorSettings(environment="test", shared_secret="test-secret"))

    with pytest.raises(TimeoutError):
        await crawler.crawl(
            url="https://example.com/slow",
            profile=ExtractionProfile.ARTICLE,
            timeout_seconds=0.01,
        )

    assert len(FakeCrawler.instances) == 1
    assert FakeCrawler.instances[0].closed
