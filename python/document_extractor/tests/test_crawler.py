import asyncio
import os
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

import pytest

import newsly_document_extractor.crawler as crawler_module
from newsly_document_extractor.crawler import WarmCrawler
from newsly_document_extractor.models import ExtractionProfile
from newsly_document_extractor.settings import ExtractorSettings


def test_crawl4ai_cannot_reload_the_application_dotenv() -> None:
    assert os.environ["PYTHON_DOTENV_DISABLED"] == "1"


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


class ObservedLock(asyncio.Lock):
    """Expose a waiter reaching admission without relying on scheduling sleeps."""

    def __init__(self) -> None:
        super().__init__()
        self.waiting = asyncio.Event()

    async def acquire(self) -> bool:
        if self.locked():
            self.waiting.set()
        return await super().acquire()


class ControlledCrawler(FakeCrawler):
    instances: list["ControlledCrawler"] = []
    created: asyncio.Queue["ControlledCrawler"]

    def __init__(self, *, config: Any) -> None:
        super().__init__(config=config)
        self.running = asyncio.Event()
        self.release = asyncio.Event()
        self.finished = False
        self.created.put_nowait(self)

    async def arun(self, **_kwargs: Any) -> Any:
        self.running.set()
        await self.release.wait()
        assert not self.closed
        self.finished = True
        return SimpleNamespace(success=True)


@pytest.fixture
def controlled_crawler(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[WarmCrawler, ObservedLock, list[asyncio.Timeout]]:
    ControlledCrawler.instances = []
    ControlledCrawler.created = asyncio.Queue()
    monkeypatch.setattr(crawler_module, "AsyncWebCrawler", ControlledCrawler)
    timeout = asyncio.timeout
    deadlines: list[asyncio.Timeout] = []

    def controlled_timeout(delay: float | None) -> asyncio.Timeout:
        if delay == 30:
            deadline = timeout(None)
            deadlines.append(deadline)
            return deadline
        return timeout(delay)

    monkeypatch.setattr(crawler_module.asyncio, "timeout", controlled_timeout)
    crawler = WarmCrawler(ExtractorSettings(environment="test", shared_secret="test-secret"))
    lock = ObservedLock()
    crawler._lock = lock
    return crawler, lock, deadlines


async def wait_for_browser() -> ControlledCrawler:
    browser = await asyncio.wait_for(ControlledCrawler.created.get(), timeout=1)
    await asyncio.wait_for(browser.running.wait(), timeout=1)
    return browser


def start_crawl(crawler: WarmCrawler) -> asyncio.Task[Any]:
    return asyncio.create_task(
        crawler.crawl(
            url="https://example.com/article",
            profile=ExtractionProfile.ARTICLE,
            timeout_seconds=30,
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("interruption", ["timeout", "cancelled"])
async def test_interrupted_waiter_does_not_close_the_active_browser(
    controlled_crawler: tuple[WarmCrawler, ObservedLock, list[asyncio.Timeout]],
    caplog: pytest.LogCaptureFixture,
    interruption: str,
) -> None:
    crawler, lock, deadlines = controlled_crawler
    caplog.set_level("INFO", logger=crawler_module.__name__)
    owner = start_crawl(crawler)
    browser = await wait_for_browser()
    waiter = start_crawl(crawler)
    await asyncio.wait_for(lock.waiting.wait(), timeout=1)

    if interruption == "timeout":
        deadlines[1].reschedule(asyncio.get_running_loop().time() - 1)
        expected_exception: type[BaseException] = TimeoutError
    else:
        waiter.cancel()
        expected_exception = asyncio.CancelledError
    with pytest.raises(expected_exception):
        await asyncio.wait_for(waiter, timeout=1)

    assert not owner.done()
    assert not browser.closed
    assert not browser.finished
    assert lock.locked()
    record = caplog.records[-1]
    assert record.browser_outcome == interruption
    assert record.browser_wait_ms >= 0
    assert record.browser_run_ms == 0

    browser.release.set()
    assert (await asyncio.wait_for(owner, timeout=1)).success
    assert browser.finished
    assert (await asyncio.wait_for(start_crawl(crawler), timeout=1)).success
    assert len(ControlledCrawler.instances) == 1
    assert not browser.closed
    assert caplog.records[-1].browser_outcome == "completed"
    assert caplog.records[-1].browser_run_ms >= 0
    await crawler.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("interruption", ["timeout", "cancelled"])
async def test_interrupted_owner_recycles_before_admitting_the_waiter(
    controlled_crawler: tuple[WarmCrawler, ObservedLock, list[asyncio.Timeout]],
    interruption: str,
) -> None:
    crawler, lock, deadlines = controlled_crawler
    owner = start_crawl(crawler)
    browser = await wait_for_browser()
    waiter = start_crawl(crawler)
    await asyncio.wait_for(lock.waiting.wait(), timeout=1)

    if interruption == "timeout":
        deadlines[0].reschedule(asyncio.get_running_loop().time() - 1)
        expected_exception: type[BaseException] = TimeoutError
    else:
        owner.cancel()
        expected_exception = asyncio.CancelledError
    with pytest.raises(expected_exception):
        await asyncio.wait_for(owner, timeout=1)

    replacement = await wait_for_browser()
    assert browser.closed
    assert replacement is not browser
    assert not replacement.closed
    assert len(ControlledCrawler.instances) == 2
    replacement.release.set()
    assert (await asyncio.wait_for(waiter, timeout=1)).success
    assert (await asyncio.wait_for(start_crawl(crawler), timeout=1)).success
    assert len(ControlledCrawler.instances) == 2
    await crawler.close()


@pytest.mark.asyncio
async def test_waiter_keeps_its_original_deadline_after_acquiring_the_browser(
    controlled_crawler: tuple[WarmCrawler, ObservedLock, list[asyncio.Timeout]],
) -> None:
    crawler, lock, deadlines = controlled_crawler
    await lock.acquire()
    waiter = start_crawl(crawler)
    await asyncio.wait_for(lock.waiting.wait(), timeout=1)
    waiter_deadline = deadlines[0]

    lock.release()
    browser = await wait_for_browser()
    assert len(deadlines) == 1
    waiter_deadline.reschedule(asyncio.get_running_loop().time() - 1)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(waiter, timeout=1)
    assert browser.closed
    await crawler.close()
