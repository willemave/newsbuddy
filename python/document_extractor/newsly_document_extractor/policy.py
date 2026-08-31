"""Newsly document extraction policy retained with Crawl4AI."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit

import trafilatura
from bs4 import BeautifulSoup
from dateutil.parser import ParserError
from dateutil.parser import parse as parse_date
from pydantic import HttpUrl

from newsly_document_extractor.crawler import WarmCrawler
from newsly_document_extractor.models import (
    SCHEMA_VERSION,
    ExtractIntent,
    ExtractionFailure,
    ExtractionFailureCode,
    ExtractionFallbackRequired,
    ExtractionMethod,
    ExtractionProfile,
    ExtractionSuccess,
    ExtractionTiming,
    ExtractRequest,
    ExtractResult,
    FallbackKind,
    PubMedDelegation,
    UsageEvent,
)
from newsly_document_extractor.settings import ExtractorSettings
from newsly_document_extractor.url_safety import (
    PublicFetch,
    UrlSafetyError,
    decode_document,
    fetch_public_document,
    require_public_url,
)

ACCESS_GATE_TITLE_MARKERS = (
    "just a quick check",
    "just a moment",
    "checking your browser",
    "enable javascript",
    "verify you are human",
)
ACCESS_GATE_TEXT_MARKERS = (
    "403 forbidden",
    "this site requires javascript to run correctly",
    "enable javascript and cookies to continue",
    "please enable js and disable any ad blocker",
    "checking your browser",
    "verify you are human",
    "not a robot",
    "detected unusual activity",
    "please wait while we verify",
    "ray id",
    "routing loop detected",
)
ACCESS_GATE_HTML_MARKERS = (
    "cf-challenge",
    "challenge-error-text",
    "cf-turnstile",
    "performance & security by cloudflare",
)
PAYWALL_TEXT_MARKERS = (
    "subscribe to read",
    "subscribe to continue reading",
    "subscribe to unlock",
    "sign in to continue reading",
    "this article is for subscribers",
)
DISCUSSION_TAIL_MARKERS = (
    "\n#### Discussion about this post",
    "\n### Discussion about this post",
    " #### Discussion about this post",
)
DISCUSSION_LEDE_MARKERS = (
    "#### discussion about this post",
    "discussion about this post",
    "commentsrestacks",
)
DISCUSSION_ONLY_MAX_TEXT_LENGTH = 8_000
PUBLISHER_TAIL_MARKERS = {
    "reuters.com": (
        " Our Standards:",
        "\nOur Standards:",
        " ## Read Next",
        "\n## Read Next",
        " Read Next / Editor's Picks",
        "\nRead Next / Editor's Picks",
        " - X - Facebook - Linkedin",
    ),
    "wsj.com": (
        " Copyright ©2026 Dow Jones & Company",
        " Copyright ©2025 Dow Jones & Company",
        " ## Up Next",
        "\n## Up Next",
        " ### Further Reading",
        "\n### Further Reading",
        " ## Most Popular",
        "\n## Most Popular",
        " content frame **An error has occurred**",
    ),
}
PUBLISHER_LEADING_MARKERS = {"wsj.com": (" # ", "\n# ")}
REUTERS_DATELINE_RE = re.compile(
    r"(?:^|\s)(?:[A-Z][A-Z .'-]+,\s+)?[A-Z][a-z]+\.?\s+\d{1,2}\s+\(Reuters\)\s+-"
)
REUTERS_INLINE_NOISE_REPLACEMENTS = (
    (
        re.compile(
            r"The Reuters [^.]{0,180} newsletter[^.]{0,180}\. Sign up here\.? ?",
            flags=re.IGNORECASE,
        ),
        "",
    ),
    (
        re.compile(
            r"Make sense of [^.]{0,180} newsletter\. Sign up here\.? ?",
            flags=re.IGNORECASE,
        ),
        "",
    ),
    (re.compile(r"Advertisement · Scroll to continue ?", flags=re.IGNORECASE), ""),
)
CHROME_HEAVY_TEXT_MARKERS = (
    "skip to main content",
    "exclusive news, data and analytics",
    "purchase licensing rights",
    "manage your tracker preferences",
    "look out for an alert in your inbox",
    "markets.businessinsider.com/index",
    "latest startups venture apple security ai apps events podcasts newsletters",
    "stay on the cutting edge",
    "top events nba nhl pga tour",
    "accessenablerproxy",
    "we value your privacy",
    "apnews.com/world-news",
    "subscribe for $1subscribe for $1",
)
LINK_DENSITY_HEAVY_THRESHOLD = 0.004
BLOCKED_TITLE_VALUES = (
    "access denied",
    "attention required!",
    "enable javascript and cookies to continue",
    "please verify you are a human",
    "subscribe to read",
    "untitled",
)
DIRECT_STATIC_HOST_SUFFIXES = (
    "apnews.com",
    "axios.com",
    "bbc.com",
    "bearblog.dev",
    "businessinsider.com",
    "cnbc.com",
    "espn.com",
    "eu-startups.com",
    "finance.yahoo.com",
    "fortune.com",
    "ghost.io",
    "gorillafund.org",
    "insidehighered.com",
    "medium.com",
    "news.ycombinator.com",
    "phys.org",
    "quantamagazine.org",
    "ruky.me",
    "socket.dev",
    "techcrunch.com",
    "theguardian.com",
    "theverge.com",
    "tomshardware.com",
    "wix-ux.com",
    "zdnet.com",
)
MAX_TABLES_TOTAL_BYTES = 400_000


class Crawler(Protocol):
    async def crawl(
        self,
        *,
        url: str,
        profile: ExtractionProfile,
        timeout_seconds: float,
    ) -> Any: ...

    async def close(self) -> None: ...


Fetcher = Callable[..., Awaitable[PublicFetch]]
UrlValidator = Callable[[str], Awaitable[tuple[Any, ...]]]


@dataclass(frozen=True, slots=True)
class StaticDocument:
    final_url: str
    title: str
    author: str | None
    published_at: datetime | None
    markdown: str
    feed_links: tuple[str, ...]
    html: str
    downloaded_bytes: int
    issue: str | None


def _hostname(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().rstrip(".")


def _domain_matches(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith(f".{suffix}")


def _bounded_message(message: object) -> str:
    normalized = re.sub(r"\s+", " ", str(message)).strip()
    return (normalized or "Extraction failed")[:2_000]


def _truncate_utf8(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _profile_for_url(url: str, requested: ExtractionProfile) -> ExtractionProfile:
    if requested is not ExtractionProfile.AUTOMATIC:
        return requested
    host = _hostname(url)
    if _domain_matches(host, "pubmed.ncbi.nlm.nih.gov") or _domain_matches(
        host, "pmc.ncbi.nlm.nih.gov"
    ):
        return ExtractionProfile.SCIENTIFIC
    if _domain_matches(host, "substack.com") or _domain_matches(host, "chinatalk.media"):
        return ExtractionProfile.NEWSLETTER
    return ExtractionProfile.ARTICLE


def _parse_published_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parse_date(value, fuzzy=True)
    except (ParserError, OverflowError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _metadata_value(soup: BeautifulSoup, *names: str) -> str | None:
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        if tag is not None:
            value = tag.get("content")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _clean_markdown(url: str, markdown: str) -> str:
    text = markdown.strip()
    for marker in DISCUSSION_TAIL_MARKERS:
        marker_index = text.find(marker)
        if marker_index >= 0:
            text = text[:marker_index].rstrip()
    host = _hostname(url)
    for suffix, leading_markers in PUBLISHER_LEADING_MARKERS.items():
        if not _domain_matches(host, suffix):
            continue
        marker_indexes = [index for marker in leading_markers if (index := text.find(marker)) >= 0]
        if marker_indexes:
            text = text[min(marker_indexes) :].strip()
        break
    if _domain_matches(host, "reuters.com"):
        dateline = REUTERS_DATELINE_RE.search(text)
        if dateline:
            text = text[dateline.start() :].strip()
        for pattern, replacement in REUTERS_INLINE_NOISE_REPLACEMENTS:
            text = pattern.sub(replacement, text)
        reporting_index = text.find(" Reporting by ")
        if reporting_index >= 0:
            text = text[:reporting_index].rstrip()
    for suffix, tail_markers in PUBLISHER_TAIL_MARKERS.items():
        if not _domain_matches(host, suffix):
            continue
        for marker in tail_markers:
            marker_index = text.find(marker)
            if marker_index >= 0:
                text = text[:marker_index].rstrip()
    text = re.sub(r"[ \t]{2,}", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _looks_like_discussion_url(url: str) -> bool:
    normalized = url.lower().rstrip("/")
    return "/comment/" in normalized or normalized.endswith("/comments")


def _link_density(markdown: str) -> float:
    return markdown.count("](") / max(len(markdown), 1)


def _looks_chrome_heavy(markdown: str) -> bool:
    normalized = re.sub(r"\s+", " ", markdown).strip().lower()
    return bool(normalized) and (
        any(marker in normalized for marker in CHROME_HEAVY_TEXT_MARKERS)
        or _link_density(normalized) >= LINK_DENSITY_HEAVY_THRESHOLD
    )


def _detect_issue(*, url: str, title: str, markdown: str, html: str | None) -> str | None:
    normalized_title = re.sub(r"\s+", " ", title).strip().lower()
    normalized_text = re.sub(r"\s+", " ", markdown).strip().lower()
    normalized_html = (html or "").lower()
    if any(marker in normalized_title for marker in ACCESS_GATE_TITLE_MARKERS):
        return "access_gate_title"
    if len(normalized_text) <= 2_500 and any(
        marker in normalized_text for marker in ACCESS_GATE_TEXT_MARKERS
    ):
        return "access_gate_body"
    if any(marker in normalized_html for marker in ACCESS_GATE_HTML_MARKERS):
        return "access_gate_html"
    if normalized_title in BLOCKED_TITLE_VALUES and len(normalized_text) <= 2_500:
        return "blocked_title"
    if len(normalized_text) <= 2_500 and any(
        marker in normalized_text for marker in PAYWALL_TEXT_MARKERS
    ):
        return "paywall"
    if not normalized_text:
        return "missing_body"
    if (
        not _looks_like_discussion_url(url)
        and len(normalized_text) <= DISCUSSION_ONLY_MAX_TEXT_LENGTH
        and any(normalized_text.startswith(marker) for marker in DISCUSSION_LEDE_MARKERS)
    ):
        return "discussion_only"
    return None


def _extract_feed_links(soup: BeautifulSoup, base_url: str) -> tuple[str, ...]:
    links: list[str] = []
    for link in soup.find_all("link", href=True):
        rel_value: object = link.get("rel")
        rel = (
            " ".join(str(value) for value in rel_value)
            if isinstance(rel_value, list)
            else str(rel_value or "")
        )
        media_type = str(link.get("type") or "").lower()
        if "alternate" not in rel.lower() or media_type not in {
            "application/rss+xml",
            "application/atom+xml",
            "application/feed+json",
        }:
            continue
        href = link.get("href")
        if isinstance(href, str) and href.strip():
            candidate = urljoin(base_url, href.strip())
            if candidate not in links:
                links.append(candidate)
        if len(links) >= 50:
            break
    return tuple(links)


def parse_static_document(document: PublicFetch) -> StaticDocument:
    """Extract deterministic static article data from an already bounded fetch."""

    html = decode_document(document)
    soup = BeautifulSoup(html, "html.parser")
    title = (
        _metadata_value(soup, "og:title", "twitter:title")
        or (soup.title.get_text(" ", strip=True) if soup.title else None)
        or "Untitled"
    )
    author = _metadata_value(soup, "author", "article:author")
    published_at = _parse_published_at(
        _metadata_value(
            soup,
            "article:published_time",
            "datePublished",
            "date",
            "pubdate",
        )
    )
    markdown = trafilatura.extract(
        html,
        output_format="markdown",
        include_comments=False,
        include_images=False,
        include_links=True,
        include_tables=True,
        favor_precision=True,
    )
    cleaned_markdown = _clean_markdown(document.final_url, markdown or "")
    return StaticDocument(
        final_url=document.final_url,
        title=title[:1_000],
        author=author[:500] if author else None,
        published_at=published_at,
        markdown=cleaned_markdown,
        feed_links=_extract_feed_links(soup, document.final_url),
        html=html,
        downloaded_bytes=len(document.body),
        issue=_detect_issue(
            url=document.final_url,
            title=title,
            markdown=cleaned_markdown,
            html=html,
        ),
    )


def _extract_pubmed_link(html: str, base_url: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    section = soup.find("div", {"class": "full-text-links-list"}) or soup.find(
        "aside", {"id": "full-text-links"}
    )
    if section is None:
        heading_pattern = re.compile(r"Full.*text.*links", re.IGNORECASE)
        for candidate in soup.find_all(("h3", "h4", "strong")):
            if heading_pattern.search(candidate.get_text(" ", strip=True)):
                section = candidate.find_parent(("div", "aside", "section"))
                break
    if section is None:
        return None

    first_link: str | None = None
    for link in section.find_all("a", href=True):
        href = link.get("href")
        if not isinstance(href, str) or not href.strip():
            continue
        resolved = urljoin(base_url, href.strip())
        first_link = first_link or resolved
        lowered = resolved.lower()
        if "pmc" in lowered and ("article" in lowered or lowered.endswith(".pdf")):
            return resolved
    return first_link


def _github_repo_parts(url: str) -> tuple[str, str] | None:
    parsed = urlsplit(url)
    if parsed.hostname not in {"github.com", "www.github.com"}:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2 or parts[0] in {"features", "marketplace", "orgs", "topics"}:
        return None
    owner = parts[0]
    repo = parts[1].removesuffix(".git")
    return (owner, repo) if owner and repo else None


class ExtractionPolicy:
    """Execute Newsly's static/browser/delegation policy without durable authority."""

    def __init__(
        self,
        settings: ExtractorSettings,
        *,
        crawler: Crawler | None = None,
        fetcher: Fetcher = fetch_public_document,
        url_validator: UrlValidator = require_public_url,
    ) -> None:
        self._settings = settings
        self._crawler = crawler or WarmCrawler(settings)
        self._fetcher = fetcher
        self._url_validator = url_validator
        self._admission = asyncio.Semaphore(settings.max_concurrent_extractions)

    async def close(self) -> None:
        await self._crawler.close()

    async def extract(self, request: ExtractRequest) -> ExtractResult:
        started_at = time.monotonic()
        deadline_seconds = (request.absolute_deadline - datetime.now(UTC)).total_seconds()
        if deadline_seconds <= 0:
            return self._failure(
                request,
                ExtractionFailureCode.DEADLINE_EXCEEDED,
                "Extraction deadline has already elapsed",
                retryable=True,
                started_at=started_at,
            )

        try:
            async with asyncio.timeout(deadline_seconds):
                async with self._admission:
                    await self._url_validator(str(request.url))
                    if request.intent is ExtractIntent.RESOLVE_PUBMED:
                        return await self._resolve_pubmed(request, started_at)
                    return await self._extract_document(request, started_at)
        except TimeoutError:
            return self._failure(
                request,
                ExtractionFailureCode.DEADLINE_EXCEEDED,
                "Extraction exceeded its absolute deadline",
                retryable=True,
                started_at=started_at,
            )
        except UrlSafetyError as exc:
            code = ExtractionFailureCode(exc.code)
            return self._failure(
                request,
                code,
                exc,
                retryable=exc.retryable,
                http_status=exc.http_status,
                started_at=started_at,
            )
        except Exception as exc:  # noqa: BLE001
            return self._failure(
                request,
                ExtractionFailureCode.INTERNAL_ERROR,
                exc,
                retryable=False,
                started_at=started_at,
            )

    async def _fetch_static(self, request: ExtractRequest) -> StaticDocument:
        github_repo = _github_repo_parts(str(request.url))
        if github_repo is not None:
            owner, repo = github_repo
            readme = await self._fetcher(
                f"https://api.github.com/repos/{owner}/{repo}/readme",
                max_bytes=request.options.max_download_bytes,
                timeout_seconds=min(
                    self._settings.fetch_timeout_seconds,
                    max(
                        1.0,
                        (request.absolute_deadline - datetime.now(UTC)).total_seconds(),
                    ),
                ),
                max_redirects=self._settings.max_redirects,
                request_headers={"Accept": "application/vnd.github.raw"},
            )
            markdown = decode_document(readme).strip()
            return StaticDocument(
                final_url=str(request.url),
                title=f"{owner}/{repo} README",
                author=None,
                published_at=None,
                markdown=markdown,
                feed_links=(),
                html="",
                downloaded_bytes=len(readme.body),
                issue=_detect_issue(
                    url=str(request.url),
                    title=f"{owner}/{repo} README",
                    markdown=markdown,
                    html=None,
                ),
            )
        document = await self._fetcher(
            str(request.url),
            max_bytes=request.options.max_download_bytes,
            timeout_seconds=min(
                self._settings.fetch_timeout_seconds,
                max(
                    1.0,
                    (request.absolute_deadline - datetime.now(UTC)).total_seconds(),
                ),
            ),
            max_redirects=self._settings.max_redirects,
        )
        return parse_static_document(document)

    async def _extract_document(self, request: ExtractRequest, started_at: float) -> ExtractResult:
        static_document: StaticDocument | None = None
        static_error: UrlSafetyError | None = None
        try:
            static_document = await self._fetch_static(request)
        except UrlSafetyError as exc:
            if exc.code in {"invalid_url", "response_too_large"}:
                raise
            static_error = exc

        if request.intent is ExtractIntent.STATIC_ANALYZE:
            if static_document is None:
                if static_error is not None:
                    raise static_error
                return self._failure(
                    request,
                    ExtractionFailureCode.NO_CONTENT,
                    "Static extraction returned no document",
                    retryable=False,
                    started_at=started_at,
                )
            return self._success_from_static(request, static_document, started_at)

        if static_document is not None and self._prefer_static(request, static_document):
            return self._success_from_static(request, static_document, started_at)

        if not request.options.allow_browser_fallback:
            reason = static_document.issue if static_document is not None else static_error
            return self._fallback(
                request,
                reason or "Static extraction returned no usable body",
                started_at,
                usage_events=self._static_usage_events(static_document),
            )

        profile = _profile_for_url(str(request.url), request.options.profile)
        browser_timeout = min(
            request.options.browser_timeout_ms / 1_000,
            max(0.001, (request.absolute_deadline - datetime.now(UTC)).total_seconds()),
        )
        try:
            result = await self._crawler.crawl(
                url=str(request.url),
                profile=profile,
                timeout_seconds=browser_timeout,
            )
        except Exception as exc:  # noqa: BLE001
            if static_document is not None and self._usable_static(request, static_document):
                return self._success_from_static(
                    request,
                    static_document,
                    started_at,
                    warning="Crawl4AI failed; used bounded static extraction",
                    additional_usage_events=[self._browser_usage_event()],
                )
            return self._fallback(
                request,
                exc,
                started_at,
                usage_events=[
                    *self._static_usage_events(static_document),
                    self._browser_usage_event(),
                ],
            )

        if result is None or not getattr(result, "success", False):
            reason = getattr(result, "error_message", None) or "Crawl4AI returned no usable result"
            if static_document is not None and self._usable_static(request, static_document):
                return self._success_from_static(
                    request,
                    static_document,
                    started_at,
                    warning="Crawl4AI returned a failure; used bounded static extraction",
                    additional_usage_events=[self._browser_usage_event()],
                )
            return self._fallback(
                request,
                reason,
                started_at,
                usage_events=[
                    *self._static_usage_events(static_document),
                    self._browser_usage_event(),
                ],
            )

        final_url = str(
            getattr(result, "url", None) or getattr(result, "redirected_url", None) or request.url
        )
        await self._url_validator(final_url)
        markdown_result = getattr(result, "markdown", None)
        raw_markdown = getattr(markdown_result, "raw_markdown", None) if markdown_result else None
        markdown = _clean_markdown(final_url, raw_markdown or "")
        metadata = getattr(result, "metadata", None) or {}
        title = str(metadata.get("title") or "Untitled")[:1_000]
        cleaned_html = str(getattr(result, "cleaned_html", None) or "")
        issue = _detect_issue(
            url=final_url,
            title=title,
            markdown=markdown,
            html=cleaned_html,
        )

        if issue or not markdown:
            if static_document is not None and self._usable_static(request, static_document):
                return self._success_from_static(
                    request,
                    static_document,
                    started_at,
                    warning=f"Crawl4AI output was rejected ({issue or 'missing_body'})",
                    additional_usage_events=[self._browser_usage_event()],
                )
            return self._fallback(
                request,
                issue or "Crawl4AI returned an empty body",
                started_at,
                usage_events=[
                    *self._static_usage_events(static_document),
                    self._browser_usage_event(),
                ],
            )

        if (
            static_document is not None
            and self._usable_static(request, static_document)
            and self._prefer_static_over_browser(static_document.markdown, markdown)
        ):
            return self._success_from_static(
                request,
                static_document,
                started_at,
                warning="Crawl4AI output was chrome-heavy; used bounded static extraction",
                additional_usage_events=[self._browser_usage_event()],
            )

        if len(markdown.encode("utf-8")) > request.options.max_markdown_bytes:
            markdown = _truncate_utf8(markdown, request.options.max_markdown_bytes)
            warnings = ["Extracted markdown was truncated at the configured byte limit"]
        else:
            warnings = []

        tables: list[str] = []
        table_bytes = 0
        for table in getattr(result, "tables", None) or []:
            table_markdown = getattr(table, "markdown", None)
            if isinstance(table_markdown, str) and table_markdown.strip():
                bounded_table = _truncate_utf8(table_markdown.strip(), 250_000)
                bounded_table_bytes = len(bounded_table.encode("utf-8"))
                if table_bytes + bounded_table_bytes > MAX_TABLES_TOTAL_BYTES:
                    warnings.append("Extracted tables were truncated at the response byte limit")
                    break
                tables.append(bounded_table)
                table_bytes += bounded_table_bytes
            if len(tables) >= 50:
                break

        browser_html = str(getattr(result, "html", None) or cleaned_html)
        soup = BeautifulSoup(browser_html, "html.parser")
        author = _metadata_value(soup, "author", "article:author")
        published_at = _parse_published_at(
            _metadata_value(soup, "article:published_time", "datePublished", "date")
        )
        return ExtractionSuccess(
            schema_version=SCHEMA_VERSION,
            request_id=request.request_id,
            kind="success",
            final_url=HttpUrl(final_url),
            title=title,
            author=author[:500] if author else None,
            published_at=published_at,
            markdown=markdown,
            tables=tables,
            feed_links=[HttpUrl(url) for url in _extract_feed_links(soup, final_url)]
            if request.options.discover_feeds
            else [],
            method=ExtractionMethod.CRAWL4AI,
            warnings=warnings,
            usage_events=[
                *self._static_usage_events(static_document),
                self._browser_usage_event(),
            ],
            timings=self._timings(started_at),
        )

    async def _resolve_pubmed(self, request: ExtractRequest, started_at: float) -> ExtractResult:
        host = _hostname(str(request.url))
        path = urlsplit(str(request.url)).path
        if (
            not _domain_matches(host, "pubmed.ncbi.nlm.nih.gov")
            or re.fullmatch(r"/\d+/?", path) is None
        ):
            return self._failure(
                request,
                ExtractionFailureCode.INVALID_URL,
                "resolve_pubmed accepts only pubmed.ncbi.nlm.nih.gov article URLs",
                retryable=False,
                started_at=started_at,
            )

        html: str | None = None
        usage_events: list[UsageEvent] = []
        try:
            static_document = await self._fetch_static(request)
            html = static_document.html
            usage_events.append(
                UsageEvent(
                    kind="downloaded_body",
                    quantity=static_document.downloaded_bytes,
                    unit="byte",
                )
            )
        except UrlSafetyError as exc:
            if exc.code in {"invalid_url", "response_too_large"}:
                raise

        next_url = _extract_pubmed_link(html, str(request.url)) if html else None
        if next_url is None:
            browser_timeout = min(
                request.options.browser_timeout_ms / 1_000,
                max(0.001, (request.absolute_deadline - datetime.now(UTC)).total_seconds()),
            )
            try:
                result = await self._crawler.crawl(
                    url=str(request.url),
                    profile=ExtractionProfile.SCIENTIFIC,
                    timeout_seconds=browser_timeout,
                )
            except Exception as exc:  # noqa: BLE001
                return self._failure(
                    request,
                    ExtractionFailureCode.CRAWL_FAILED,
                    exc,
                    retryable=True,
                    started_at=started_at,
                )
            if result is not None and getattr(result, "success", False):
                html = str(getattr(result, "html", None) or "")
                next_url = _extract_pubmed_link(html, str(request.url))
                usage_events.append(UsageEvent(kind="browser_page", quantity=1, unit="page"))

        if next_url is None:
            return self._failure(
                request,
                ExtractionFailureCode.NO_CONTENT,
                "PubMed page did not expose a usable full-text link",
                retryable=False,
                started_at=started_at,
            )
        await self._url_validator(next_url)
        return PubMedDelegation(
            schema_version=SCHEMA_VERSION,
            request_id=request.request_id,
            kind="delegation",
            next_url=HttpUrl(next_url),
            reason="pubmed_full_text",
            warnings=[],
            usage_events=usage_events,
            timings=self._timings(started_at),
        )

    @staticmethod
    def _usable_static(request: ExtractRequest, document: StaticDocument) -> bool:
        if _github_repo_parts(document.final_url) is not None:
            return document.issue is None and bool(document.markdown)
        return (
            document.issue is None
            and len(document.markdown) >= request.options.static_minimum_characters
        )

    @classmethod
    def _prefer_static(cls, request: ExtractRequest, document: StaticDocument) -> bool:
        if not cls._usable_static(request, document):
            return False
        host = _hostname(document.final_url)
        return (
            any(_domain_matches(host, suffix) for suffix in DIRECT_STATIC_HOST_SUFFIXES)
            or len(document.markdown) >= 1_200
        )

    @staticmethod
    def _prefer_static_over_browser(static_markdown: str, browser_markdown: str) -> bool:
        if _looks_chrome_heavy(browser_markdown) and not _looks_chrome_heavy(static_markdown):
            return True
        browser_link_density = _link_density(browser_markdown)
        return (
            browser_link_density >= LINK_DENSITY_HEAVY_THRESHOLD
            and _link_density(static_markdown) <= browser_link_density * 0.7
        )

    def _success_from_static(
        self,
        request: ExtractRequest,
        document: StaticDocument,
        started_at: float,
        *,
        warning: str | None = None,
        additional_usage_events: list[UsageEvent] | None = None,
    ) -> ExtractionSuccess:
        warnings = [warning] if warning else []
        if len(document.markdown.encode("utf-8")) > request.options.max_markdown_bytes:
            markdown = _truncate_utf8(document.markdown, request.options.max_markdown_bytes)
            warnings.append("Extracted markdown was truncated at the configured byte limit")
        else:
            markdown = document.markdown
        if not markdown:
            markdown = "No readable article body was extracted."
            warnings.append("Static extraction returned no readable article body")
        return ExtractionSuccess(
            schema_version=SCHEMA_VERSION,
            request_id=request.request_id,
            kind="success",
            final_url=HttpUrl(document.final_url),
            title=document.title,
            author=document.author,
            published_at=document.published_at,
            markdown=markdown,
            tables=[],
            feed_links=[HttpUrl(url) for url in document.feed_links]
            if request.options.discover_feeds
            else [],
            method=ExtractionMethod.STATIC_READABILITY,
            warnings=warnings,
            usage_events=[
                *self._static_usage_events(document),
                *(additional_usage_events or []),
            ],
            timings=self._timings(started_at),
        )

    def _fallback(
        self,
        request: ExtractRequest,
        reason: object,
        started_at: float,
        *,
        usage_events: list[UsageEvent],
    ) -> ExtractionFallbackRequired:
        message = _bounded_message(reason)
        retryable = any(
            token in message.lower()
            for token in ("timeout", "tempor", "connection", "network", "unavailable")
        )
        return ExtractionFallbackRequired(
            schema_version=SCHEMA_VERSION,
            request_id=request.request_id,
            kind="fallback_required",
            fallback=FallbackKind.FIRECRAWL,
            url=request.url,
            reason=message,
            retryable=retryable,
            usage_events=usage_events,
            timings=self._timings(started_at),
        )

    @staticmethod
    def _static_usage_events(document: StaticDocument | None) -> list[UsageEvent]:
        if document is None:
            return []
        return [
            UsageEvent(
                kind="downloaded_body",
                quantity=document.downloaded_bytes,
                unit="byte",
            )
        ]

    @staticmethod
    def _browser_usage_event() -> UsageEvent:
        return UsageEvent(kind="browser_page", quantity=1, unit="page")

    def _failure(
        self,
        request: ExtractRequest,
        code: ExtractionFailureCode,
        message: object,
        *,
        retryable: bool,
        started_at: float,
        http_status: int | None = None,
    ) -> ExtractionFailure:
        return ExtractionFailure(
            schema_version=SCHEMA_VERSION,
            request_id=request.request_id,
            kind="failure",
            code=code,
            retryable=retryable,
            http_status=http_status,
            message=_bounded_message(message),
            timings=self._timings(started_at),
        )

    @staticmethod
    def _timings(started_at: float) -> list[ExtractionTiming]:
        return [
            ExtractionTiming(
                name="total",
                milliseconds=max(0, round((time.monotonic() - started_at) * 1_000)),
            )
        ]
