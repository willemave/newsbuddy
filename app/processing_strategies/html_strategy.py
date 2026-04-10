"""
This module defines the strategy for processing standard HTML web pages using crawl4ai.
"""

import asyncio
import logging
import re
from html import unescape
from typing import Any
from urllib.parse import urlparse

import httpx
import trafilatura
from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CacheMode,
    CrawlerRunConfig,
    LLMConfig,
    LLMTableExtraction,
)
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.http_client.robust_http_client import RobustHttpClient
from app.processing_strategies.base_strategy import UrlProcessorStrategy
from app.services.exa_client import ExaClientError, exa_get_contents
from app.utils.dates import parse_date_with_tz
from app.utils.title_utils import clean_title

logger = get_logger(__name__)

ACCESS_GATE_TITLE_MARKERS: tuple[str, ...] = (
    "just a moment",
    "checking your browser",
    "enable javascript",
    "verify you are human",
)
ACCESS_GATE_TEXT_MARKERS: tuple[str, ...] = (
    "this site requires javascript to run correctly",
    "enable javascript and cookies to continue",
    "turn on javascript",
    "or unblock scripts",
    "checking your browser",
    "verify you are human",
    "please wait while we verify",
    "ray id",
)
ACCESS_GATE_HTML_MARKERS: tuple[str, ...] = (
    "cf-challenge",
    "challenge-error-text",
    "cf-turnstile",
    "performance & security by cloudflare",
)
NEWSPAPER_FALLBACK_DOMAINS: frozenset[str] = frozenset({"ft.com", "www.ft.com"})
PAYWALL_TEXT_MARKERS: tuple[str, ...] = (
    "subscribe to read",
    "subscribe to continue reading",
    "sign in to continue reading",
    "this article is for subscribers",
)
ACCESS_GATE_MAX_TEXT_LENGTH = 2500
DISCUSSION_ONLY_MAX_TEXT_LENGTH = 8000
DISCUSSION_LEDE_MARKERS: tuple[str, ...] = (
    "#### discussion about this post",
    "discussion about this post",
    "commentsrestacks",
)
DISCUSSION_TAIL_MARKERS: tuple[str, ...] = (
    "\n#### Discussion about this post",
    "\n### Discussion about this post",
    " #### Discussion about this post",
)


class HtmlProcessorStrategy(UrlProcessorStrategy):
    """
    Strategy for processing standard HTML web pages.
    It downloads HTML content using crawl4ai with optimized content extraction,
    and prepares it for further processing.
    """

    def __init__(self, http_client: RobustHttpClient):
        super().__init__(http_client)
        self.settings = get_settings()

    @staticmethod
    def _host_for_url(url: str) -> str:
        """Return a normalized hostname for a URL."""

        try:
            return (urlparse(url).netloc or "").lower()
        except Exception:
            return ""

    def _detect_source(self, url: str) -> str:
        """Detect the source type from URL."""
        if "pubmed.ncbi.nlm.nih.gov" in url or "pmc.ncbi.nlm.nih.gov" in url:
            return "PubMed"
        elif "arxiv.org" in url:
            return "Arxiv"
        elif "substack.com" in url:
            return "Substack"
        elif "medium.com" in url:
            return "Medium"
        elif "chinatalk.media" in url:
            return "ChinaTalk"
        else:
            return "web"

    def _map_platform(self, source: str, url: str) -> str | None:
        """Map platform from the detected source or URL.

        Keeps platform taxonomy consistent with scrapers (substack, medium,
        arxiv, pubmed, youtube, etc.). Returns None for generic web.
        """
        s = (source or "").lower()
        if s == "substack":
            return "substack"
        if s == "medium":
            return "medium"
        if s == "arxiv":
            return "arxiv"
        if s in ("pubmed", "pmc"):
            return "pubmed"
        # Some Substack publications use custom domains (e.g., chinatalk.media)
        if any(h in url for h in (".substack.com", "chinatalk.media")):
            return "substack"
        # Fallback: no clear platform
        return None

    def preprocess_url(self, url: str) -> str:
        """
        Preprocess URLs to ensure we get the full content.
        - Transform PubMed URLs to PMC full-text URLs
        - Transform ArXiv abstract URLs to PDF URLs
        """
        # Handle PubMed URLs - transform to PMC full-text if available
        pubmed_match = re.match(r"https?://pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", url)
        if pubmed_match:
            pmid = pubmed_match.group(1)
            pmc_url = f"https://pmc.ncbi.nlm.nih.gov/articles/pmid/{pmid}/"
            logger.debug("HtmlStrategy: Transforming PubMed URL %s to PMC URL %s", url, pmc_url)
            return pmc_url

        # Handle ArXiv URLs - transform abstract to PDF
        if "arxiv.org/abs/" in url:
            logger.debug("HtmlStrategy: Transforming arXiv URL %s", url)
            return url.replace("/abs/", "/pdf/")

        logger.debug(
            "HtmlStrategy: preprocess_url called for %s, no transformation applied.",
            url,
        )
        return url

    def can_handle_url(self, url: str, response_headers: httpx.Headers | None = None) -> bool:
        """
        Determines if this strategy can handle the given URL.
        Checks for 'text/html' in Content-Type or common HTML file extensions.
        """
        if response_headers:
            content_type = response_headers.get("content-type", "").lower()
            if "text/html" in content_type:
                logger.debug(
                    "HtmlStrategy can handle %s based on Content-Type: %s",
                    url,
                    content_type,
                )
                return True

        # Fallback: check URL pattern if no headers (e.g. direct call without HEAD)
        if not url.lower().endswith((".pdf", ".xml", ".json", ".txt")) and url.lower().startswith(
            ("http://", "https://")
        ):
            # ArXiv PDF URLs are handled by ArxivStrategy or PdfStrategy.
            # This check ensures HtmlStrategy doesn't mistakenly claim them.
            if "arxiv.org/pdf/" in url.lower():
                logger.debug(
                    f"HtmlStrategy: URL {url} appears to be an arXiv PDF, "
                    "deferring to other strategies."
                )
                return False
            logger.debug(
                f"HtmlStrategy attempting to handle {url} based on URL pattern "
                "(not PDF/XML/JSON/TXT)."
            )
            return True  # A bit of a catch-all if no other strategy matches

        logger.debug(f"HtmlStrategy cannot handle {url} based on current checks.")
        return False

    def download_content(self, url: str) -> str:
        """
        Downloads HTML content from the given URL.
        For crawl4ai, we'll use the extract_data method directly since it handles downloading.
        This method remains for compatibility with the base class.
        """
        logger.info(f"HtmlStrategy: download_content called for {url}")
        # We'll actually download in extract_data using crawl4ai
        return url  # Return the URL itself as a placeholder

    def _get_source_specific_config(self, source: str) -> dict[str, Any]:
        """Get source-specific configuration for crawl4ai."""
        # Base configuration
        config = {
            "word_count_threshold": 20,
            "excluded_tags": ["script", "style", "nav", "footer", "header"],
            "exclude_external_links": True,
            "remove_overlay_elements": True,
            "page_timeout_ms": 90_000,
            "wait_for_timeout_ms": 90_000,
            "wait_until": "domcontentloaded",
            "wait_for": "body",
            "max_crawl_attempts": 1,
            "crawl_retry_delay_seconds": 1.5,
        }

        # Source-specific adjustments
        if source == "Substack":
            config["excluded_tags"].extend(["form", "aside"])
            config["excluded_selector"] = (
                ".subscribe-widget, .footer-wrap, .subscription-form-wrapper"
            )
            config["target_elements"] = [".post", ".post-content", "article"]
            config["max_crawl_attempts"] = 2
            config["page_timeout_ms"] = 120_000
            config["wait_for_timeout_ms"] = 120_000
        elif source == "Medium":
            config["excluded_selector"] = ".metabar, .js-postActions, .js-stickyFooter"
            config["target_elements"] = ["article", ".postArticle", ".section-content"]
        elif source in ["PubMed", "PMC"]:
            # Keep more scientific content
            config["excluded_tags"] = ["script", "style", "nav", "footer"]
            config["target_elements"] = [".article", ".abstract", ".body", ".content", "main"]
            config["word_count_threshold"] = 10  # Lower threshold for scientific content
        elif source == "ChinaTalk":
            config["target_elements"] = [".post-content", ".post", "article"]
            config["excluded_selector"] = ".subscribe-widget, .comments-section"
            config["max_crawl_attempts"] = 2
            config["page_timeout_ms"] = 120_000
            config["wait_for_timeout_ms"] = 120_000
        elif source == "Arxiv":
            # ArXiv PDFs need special handling
            config["pdf"] = True

        return config

    @staticmethod
    def _get_domain_overrides(url: str) -> dict[str, Any]:
        """Return per-domain crawl4ai overrides."""

        host = HtmlProcessorStrategy._host_for_url(url)

        overrides: dict[str, Any] = {}
        if host.endswith("screenrant.com"):
            overrides.update(
                {
                    "page_timeout_ms": 45_000,
                    "wait_for_timeout_ms": 30_000,
                    "wait_for": None,
                }
            )
        if host.endswith("redbook.io"):
            overrides.update(
                {
                    "page_timeout_ms": 45_000,
                    "wait_for_timeout_ms": 30_000,
                    "wait_for": None,
                }
            )
        if host.endswith("dashboard.congress.ccc.de"):
            overrides.update(
                {
                    "page_timeout_ms": 30_000,
                    "wait_for_timeout_ms": 20_000,
                    "wait_for": None,
                }
            )
        return overrides

    def _resolve_llm_api_token(self, provider: str) -> str | None:
        """Resolve the API token to use for the configured LLM provider."""
        provider_name = provider.split("/", 1)[0].lower()
        if provider_name == "openai":
            return self.settings.openai_api_key
        if provider_name == "google":
            return self.settings.google_api_key
        if provider_name in {"anthropic", "claude"}:
            return self.settings.anthropic_api_key
        return None

    def _build_table_extraction_strategy(self) -> LLMTableExtraction | None:
        """Create an optional table extraction strategy for crawl4ai."""
        if not getattr(self.settings, "crawl4ai_enable_table_extraction", False):
            return None

        provider = getattr(self.settings, "crawl4ai_table_provider", None)
        if not provider:
            logger.debug("crawl4ai table extraction enabled but provider not configured")
            return None

        api_token = self._resolve_llm_api_token(provider)
        llm_config_kwargs: dict[str, Any] = {"provider": provider}
        if api_token:
            llm_config_kwargs["api_token"] = api_token

        css_selector = getattr(self.settings, "crawl4ai_table_css_selector", None)
        if css_selector:
            css_selector = css_selector.strip() or None

        try:
            return LLMTableExtraction(
                llm_config=LLMConfig(**llm_config_kwargs),
                css_selector=css_selector,
                enable_chunking=getattr(self.settings, "crawl4ai_table_enable_chunking", True),
                chunk_token_threshold=getattr(
                    self.settings, "crawl4ai_table_chunk_token_threshold", 3000
                ),
                min_rows_per_chunk=getattr(self.settings, "crawl4ai_table_min_rows_per_chunk", 10),
                max_parallel_chunks=getattr(self.settings, "crawl4ai_table_max_parallel_chunks", 5),
                verbose=getattr(self.settings, "crawl4ai_table_verbose", False),
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning("Failed to initialize crawl4ai table extraction strategy: %s", exc)
            return None

    @staticmethod
    def _is_retryable_crawl_error(error: Exception) -> bool:
        """Return True when the crawl error looks transient and merits a retry."""

        message = str(error).lower()
        transient_tokens = [
            "net::err_timed_out",
            "timeout",
            "wait condition failed",
            "selector 'body'",
            "net::err_connection_refused",
            "net::err_cert_verifier_changed",
            "net::err_connection_reset",
            "net::err_failed",
        ]
        return any(token in message for token in transient_tokens)

    @staticmethod
    def _should_use_httpx_fallback(error: Exception) -> bool:
        """Return True when a lightweight fetch/parse fallback is worth trying."""

        message = str(error).lower()
        fallback_tokens = [
            "net::err_connection_refused",
            "net::err_http2_protocol_error",
            "net::err_cert_verifier_changed",
            "wait condition failed",
            "timeout after",
            "net::err_name_not_resolved",
        ]
        return any(token in message for token in fallback_tokens)

    @staticmethod
    def _is_non_retryable_extraction_error(error: Exception) -> bool:
        """Return True when the extraction error should stop retrying."""

        message = str(error).lower()
        non_retryable_patterns = [
            r"\b401\b",
            r"\b403\b",
            r"\b404\b",
        ]
        non_retryable_tokens = [
            "blocked",
            "forbidden",
            "access denied",
            "not found",
            "paywall",
            "err_http_response_code_failure",
            "err_http2_protocol_error",
            "err_ssl_protocol_error",
            "err_connection_refused",
            "err_cert_",
            "timeout",
            "wait condition failed",
        ]
        if any(re.search(pattern, message) for pattern in non_retryable_patterns):
            return True
        return any(token in message for token in non_retryable_tokens)

    @staticmethod
    def _extract_title_from_html(html_content: str) -> str | None:
        """Extract a page title from raw HTML."""

        patterns = [
            r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+name=["\']twitter:title["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+name=["\']title["\'][^>]+content=["\']([^"\']+)["\']',
            r"<title[^>]*>(.*?)</title>",
            r"<h1[^>]*>(.*?)</h1>",
        ]
        for pattern in patterns:
            match = re.search(pattern, html_content, re.IGNORECASE | re.DOTALL)
            if not match:
                continue
            title = unescape(match.group(1)).strip()
            title = re.sub(r"\s+", " ", title) if title else None
            cleaned = clean_title(title)
            if cleaned:
                return cleaned
            if title:
                return title
        return None

    @staticmethod
    def _extract_text_from_html(html_content: str) -> str:
        """Lightweight HTML to text extraction for fallback."""

        without_scripts = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html_content)
        without_tags = re.sub(r"(?is)<[^>]+>", " ", without_scripts)
        text = unescape(without_tags)
        return re.sub(r"\s+", " ", text).strip()

    @classmethod
    def _detect_access_gate(
        cls,
        *,
        title: str | None,
        text_content: str | None,
        html_content: str | None,
    ) -> str | None:
        """Detect access-gate/challenge pages that are not real article content."""

        normalized_title = re.sub(r"\s+", " ", title or "").strip().lower()
        normalized_text = re.sub(r"\s+", " ", text_content or "").strip().lower()
        normalized_html = (html_content or "").lower()
        text_len = len(normalized_text)
        short_payload = 0 < text_len <= ACCESS_GATE_MAX_TEXT_LENGTH

        title_marker_hit = any(marker in normalized_title for marker in ACCESS_GATE_TITLE_MARKERS)
        text_marker_hit = any(marker in normalized_text for marker in ACCESS_GATE_TEXT_MARKERS)
        html_marker_hit = any(marker in normalized_html for marker in ACCESS_GATE_HTML_MARKERS)

        if title_marker_hit and (text_marker_hit or html_marker_hit or short_payload):
            return "access gate detected: challenge/JS wall title"
        if text_marker_hit and short_payload:
            return "access gate detected: challenge/JS wall content"
        if html_marker_hit and short_payload:
            return "access gate detected: challenge/JS wall html markers"
        return None

    @staticmethod
    def _detect_placeholder_title_issue(
        *,
        title: str | None,
        text_content: str | None,
    ) -> str | None:
        """Detect paywall/blocked pages that expose only a placeholder title."""

        normalized_title = re.sub(r"\s+", " ", title or "").strip()
        if not normalized_title:
            return None
        if clean_title(normalized_title):
            return None

        normalized_text = re.sub(r"\s+", " ", text_content or "").strip().lower()
        short_payload = 0 < len(normalized_text) <= ACCESS_GATE_MAX_TEXT_LENGTH
        paywall_text_hit = any(marker in normalized_text for marker in PAYWALL_TEXT_MARKERS)
        if short_payload or paywall_text_hit:
            return "blocked/paywalled placeholder title"
        return None

    @staticmethod
    def _looks_like_discussion_url(url: str) -> bool:
        """Return True when the submitted URL explicitly targets a discussion page."""

        normalized_url = url.lower().rstrip("/")
        return "/comment/" in normalized_url or normalized_url.endswith("/comments")

    @classmethod
    def _detect_discussion_only_extraction(
        cls,
        *,
        url: str,
        text_content: str | None,
    ) -> str | None:
        """Detect when extraction captured a comment thread instead of the article body."""

        if cls._looks_like_discussion_url(url):
            return None

        normalized_text = re.sub(r"\s+", " ", text_content or "").strip().lower()
        if not normalized_text:
            return None

        starts_with_discussion = any(
            normalized_text.startswith(marker) for marker in DISCUSSION_LEDE_MARKERS
        )
        if not starts_with_discussion:
            return None

        javascript_wall_hit = any(marker in normalized_text for marker in ACCESS_GATE_TEXT_MARKERS)
        if javascript_wall_hit:
            return "malformed extraction: discussion/comments block with javascript wall"
        if len(normalized_text) <= DISCUSSION_ONLY_MAX_TEXT_LENGTH:
            return "malformed extraction: discussion/comments block without article body"
        return None

    @classmethod
    def _detect_extraction_issue(
        cls,
        *,
        url: str,
        title: str | None,
        text_content: str | None,
        html_content: str | None,
    ) -> str | None:
        """Return a reason when extracted content looks malformed."""

        gate_reason = cls._detect_access_gate(
            title=title,
            text_content=text_content,
            html_content=html_content,
        )
        if gate_reason:
            return gate_reason

        placeholder_title_reason = cls._detect_placeholder_title_issue(
            title=title,
            text_content=text_content,
        )
        if placeholder_title_reason:
            return placeholder_title_reason

        return cls._detect_discussion_only_extraction(
            url=url,
            text_content=text_content,
        )

    @classmethod
    def _trim_discussion_tail(cls, url: str, text_content: str | None) -> str:
        """Remove trailing discussion sections from article text when possible."""

        if cls._looks_like_discussion_url(url) or not text_content:
            return text_content or ""

        trimmed_text = text_content
        for marker in DISCUSSION_TAIL_MARKERS:
            marker_index = trimmed_text.find(marker)
            if marker_index != -1:
                trimmed_text = trimmed_text[:marker_index].rstrip()
                break

        return trimmed_text

    def _exa_fallback_fetch(self, url: str, source: str) -> dict[str, Any] | None:
        """Use Exa contents as the first fallback after crawl4ai extraction fails."""

        try:
            results = exa_get_contents(
                [url],
                max_characters=None,
                livecrawl="always",
                raise_on_error=True,
            )
        except ExaClientError as exc:
            logger.error(
                "HtmlStrategy: Exa fallback request failed for %s: %s",
                url,
                exc,
            )
            return None
        if not results:
            return None

        result = results[0]
        final_url = result.url or url
        title = result.title or "Untitled"
        text_content = self._trim_discussion_tail(final_url, result.text)
        extraction_issue = self._detect_extraction_issue(
            url=final_url,
            title=title,
            text_content=text_content,
            html_content=None,
        )
        if extraction_issue:
            logger.warning(
                "HtmlStrategy: Exa fallback content still appears malformed for %s (%s)",
                final_url,
                extraction_issue,
            )
            return None

        host = self._host_for_url(final_url) or source

        logger.info(
            "HtmlStrategy: Exa fallback extraction succeeded for %s (text_length=%s)",
            final_url,
            len(text_content),
        )
        return {
            "title": title,
            "author": None,
            "publication_date": parse_date_with_tz(result.published_date)
            if result.published_date
            else None,
            "text_content": text_content,
            "content_type": "html",
            "source": host,
            "final_url_after_redirects": final_url,
            "table_markdown": None,
            "gate_page_detected": False,
            "extraction_error": None,
        }

    def _http_fallback_fetch(self, url: str, source: str) -> dict[str, Any] | None:
        """Use httpx + trafilatura when crawl and Exa extraction fail."""

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        }
        try:
            response = self.http_client.get(url, headers=headers, timeout=20.0)
        except Exception as exc:  # noqa: BLE001
            logger.error("HtmlStrategy fallback fetch failed for %s: %s", url, exc)
            return None

        html_content = response.text
        title = self._extract_title_from_html(html_content) or "Untitled"
        text_content = trafilatura.extract(html_content, include_links=True) or ""
        if not text_content:
            text_content = self._extract_text_from_html(html_content)

        extraction_issue = self._detect_extraction_issue(
            url=str(response.url),
            title=title,
            text_content=text_content,
            html_content=html_content,
        )
        if extraction_issue:
            logger.warning(
                "HtmlStrategy: Fallback content still appears malformed for %s (%s)",
                response.url,
                extraction_issue,
            )
        logger.info(
            "HtmlStrategy: Fallback extraction succeeded for %s (text_length=%s)",
            response.url,
            len(text_content),
        )
        host = self._host_for_url(str(response.url)) or source
        return {
            "title": title,
            "author": None,
            "publication_date": None,
            "text_content": text_content,
            "content_type": "html",
            "source": host,
            "final_url_after_redirects": str(response.url),
            "table_markdown": None,
            "gate_page_detected": bool(
                extraction_issue and extraction_issue.startswith("access gate detected")
            ),
            "extraction_error": extraction_issue,
        }

    def _fallback_fetch(self, url: str, source: str) -> dict[str, Any] | None:
        """Try domain-specific recovery first, then generic fallbacks."""

        newspaper_data = self._newspaper_fallback_fetch(url)
        if newspaper_data:
            return newspaper_data

        exa_data = self._exa_fallback_fetch(url, source)
        if exa_data:
            return exa_data

        return self._http_fallback_fetch(url, source)

    def _newspaper_fallback_fetch(self, url: str) -> dict[str, Any] | None:
        """Use newspaper4k on a small blocked-domain allowlist before generic fallbacks."""

        host = self._host_for_url(url)
        if host not in NEWSPAPER_FALLBACK_DOMAINS:
            return None

        try:
            import newspaper
        except ImportError:
            logger.debug("HtmlStrategy: newspaper4k not installed; skipping newspaper fallback")
            return None

        try:
            article = newspaper.article(url)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "HtmlStrategy: newspaper fallback failed for %s: %s",
                url,
                exc,
            )
            return None

        title = clean_title(getattr(article, "title", None))
        text_content = re.sub(r"\s+", " ", getattr(article, "text", "") or "").strip()
        if not text_content:
            return None

        extraction_issue = self._detect_extraction_issue(
            url=url,
            title=title,
            text_content=text_content,
            html_content=getattr(article, "article_html", None),
        )
        if extraction_issue:
            logger.warning(
                "HtmlStrategy: newspaper fallback content still appears malformed for %s (%s)",
                url,
                extraction_issue,
            )
            return None

        publication_date = getattr(article, "publish_date", None)
        authors = getattr(article, "authors", None)
        author = (
            ", ".join(author for author in authors if author)
            if isinstance(authors, list)
            else None
        )

        logger.info(
            "HtmlStrategy: newspaper fallback extraction succeeded for %s (text_length=%s)",
            url,
            len(text_content),
        )
        return {
            "title": title or "Untitled",
            "author": author or None,
            "publication_date": publication_date,
            "text_content": text_content,
            "content_type": "html",
            "source": host,
            "final_url_after_redirects": url,
            "table_markdown": None,
            "gate_page_detected": False,
            "extraction_error": None,
            "used_newspaper_fallback": True,
        }

    def extract_data(self, content: str, url: str) -> dict[str, Any]:
        """
        Extracts data from HTML content using crawl4ai.
        'content' parameter is ignored as crawl4ai handles downloading.
        'url' here is the final URL after any preprocessing.
        """
        logger.info(f"HtmlStrategy: Extracting data from {url}")

        # Detect source for metadata
        source = self._detect_source(url)
        logger.debug("HtmlStrategy: Starting extraction (url=%s, source=%s)", url, source)
        table_strategy = self._build_table_extraction_strategy()

        try:
            # Configure browser
            browser_config = BrowserConfig(
                headless=True,
                viewport_width=1920,
                viewport_height=1080,
                text_mode=False,
                light_mode=True,
                ignore_https_errors=True,
                java_script_enabled=True,
                extra_args=["--disable-blink-features=AutomationControlled"],
                verbose=False,
            )

            # Get source-specific configuration
            source_config = self._get_source_specific_config(source)
            source_config.update(self._get_domain_overrides(url))
            page_timeout_ms = int(source_config.get("page_timeout_ms", 90_000))
            wait_for_timeout_ms = int(source_config.get("wait_for_timeout_ms", page_timeout_ms))
            max_crawl_attempts = max(1, int(source_config.get("max_crawl_attempts", 3)))
            retry_delay_seconds = float(source_config.get("crawl_retry_delay_seconds", 1.5))

            # Configure crawler run
            run_config = CrawlerRunConfig(
                # Content filtering
                word_count_threshold=source_config.get("word_count_threshold", 20),
                excluded_tags=source_config.get("excluded_tags", []),
                excluded_selector=source_config.get("excluded_selector"),
                target_elements=source_config.get("target_elements"),
                exclude_external_links=source_config.get("exclude_external_links", True),
                # Content processing
                process_iframes=False,
                remove_overlay_elements=source_config.get("remove_overlay_elements", True),
                remove_forms=True,
                keep_data_attributes=False,
                # Page handling
                wait_until=source_config.get("wait_until", "domcontentloaded"),
                wait_for=source_config.get("wait_for"),
                delay_before_return_html=1.0,
                page_timeout=page_timeout_ms,
                wait_for_timeout=wait_for_timeout_ms,
                adjust_viewport_to_content=True,
                # Performance
                cache_mode=CacheMode.BYPASS,
                verbose=False,
                # Link filtering
                exclude_social_media_links=True,
                exclude_domains=["facebook.com", "twitter.com", "instagram.com", "linkedin.com"],
                # Special handling
                pdf=source_config.get("pdf", False),
                check_robots_txt=False,
                table_extraction=table_strategy,
                markdown_generator=DefaultMarkdownGenerator(
                    content_source="raw_html",
                    options={
                        "ignore_links": False,
                        "ignore_images": True,  # Avoid Base64 data URIs bloating content
                        "escape_html": False,
                        "body_width": 0,
                    },
                ),
            )
            logger.debug(
                "HtmlStrategy: Crawl config prepared "
                "(url=%s, word_count_threshold=%s, target_elements=%s)",
                url,
                run_config.word_count_threshold,
                run_config.target_elements,
            )

            # Use AsyncWebCrawler with asyncio.run
            async def run_crawl_with_retries():
                crawl4ai_logger = logging.getLogger("crawl4ai")
                original_level = crawl4ai_logger.level
                crawl4ai_logger.setLevel(logging.WARNING)
                try:
                    last_error: Exception | None = None
                    for attempt in range(1, max_crawl_attempts + 1):
                        crawler = None
                        should_retry = False
                        try:
                            crawler = AsyncWebCrawler(config=browser_config)
                            await crawler.__aenter__()
                            result = await crawler.arun(url=url, config=run_config)
                            logger.debug(
                                "HtmlStrategy: Crawl finished "
                                "(url=%s, success=%s, status=%s, redirected=%s)",
                                url,
                                getattr(result, "success", None),
                                getattr(result, "status_code", None),
                                getattr(result, "redirected_url", None),
                            )
                            return result
                        except Exception as exc:  # noqa: BLE001
                            last_error = exc
                            logger.debug(
                                "HtmlStrategy: Crawl attempt %s/%s failed for %s: %s",
                                attempt,
                                max_crawl_attempts,
                                url,
                                exc,
                            )
                            if self._is_retryable_crawl_error(exc) and attempt < max_crawl_attempts:
                                should_retry = True
                                logger.warning(
                                    "HtmlStrategy: Retrying crawl for %s after timeout "
                                    "(attempt %s/%s)",
                                    url,
                                    attempt + 1,
                                    max_crawl_attempts,
                                )
                            else:
                                raise
                        finally:
                            if crawler:
                                try:
                                    await crawler.__aexit__(None, None, None)
                                except Exception as close_error:  # noqa: BLE001
                                    # Log browser close errors but don't fail the extraction
                                    logger.debug(
                                        "Error closing browser (non-critical): %s", close_error
                                    )
                        if should_retry:
                            await asyncio.sleep(retry_delay_seconds)

                    if last_error is not None:
                        raise last_error
                    raise RuntimeError("Crawl4ai retry loop exited without result")
                finally:
                    crawl4ai_logger.setLevel(original_level)

            try:
                result = asyncio.run(run_crawl_with_retries())
            except Exception as crawl_exc:  # noqa: BLE001
                if self._should_use_httpx_fallback(crawl_exc):
                    fallback_data = self._fallback_fetch(url, source)
                    if fallback_data:
                        return fallback_data
                raise

            # Check if result is None
            if result is None:
                error_msg = "Crawl4ai extraction returned None - possible timeout or network issue"
                logger.warning(f"{error_msg} for URL: {url}")
                raise Exception(error_msg)

            if not result.success:
                error_detail = getattr(result, "error_message", None) or getattr(
                    result, "error", None
                )

                if not error_detail:
                    # Some crawl4ai failures surface an `errors` list
                    errors = getattr(result, "errors", None)
                    if errors:
                        error_detail = "; ".join(str(e) for e in errors if e)

                if not error_detail:
                    error_detail = "Unknown error"

                status_code = getattr(result, "status_code", None)
                if status_code:
                    error_detail = f"{error_detail} (status_code={status_code})"

                redirected_url = getattr(result, "redirected_url", None)
                if redirected_url and redirected_url != url:
                    error_detail = f"{error_detail} [redirected to {redirected_url}]"

                error_msg = f"Crawl4ai extraction failed: {error_detail}"
                logger.warning(f"{error_msg} for URL: {url}")

                if self._should_use_httpx_fallback(RuntimeError(error_detail)):
                    fallback_data = self._fallback_fetch(url, source)
                    if fallback_data:
                        return fallback_data

                raise Exception(error_msg)

            # Extract metadata from content if not provided
            extracted_text = result.markdown.raw_markdown if result.markdown else ""
            if not extracted_text:
                raise Exception("No content extracted from the page")
            logger.debug(
                "HtmlStrategy: Extracted markdown length=%s cleaned_html_length=%s",
                len(extracted_text),
                len(result.cleaned_html or ""),
            )
            logger.debug(
                "HtmlStrategy: Markdown preview: %s",
                (extracted_text[:200] + "...") if len(extracted_text) > 200 else extracted_text,
            )
            if result.cleaned_html:
                logger.debug(
                    "HtmlStrategy: Cleaned HTML preview: %s",
                    (
                        result.cleaned_html[:200].replace("\n", " ") + "..."
                        if len(result.cleaned_html) > 200
                        else result.cleaned_html.replace("\n", " ")
                    ),
                )
            if result.metadata:
                logger.debug("HtmlStrategy: Raw metadata keys=%s", list(result.metadata.keys()))

            title = (result.metadata.get("title") if result.metadata else None) or "Untitled"
            author = None
            publication_date = None
            table_markdown: list[str] = []

            if table_strategy and getattr(result, "tables", None):
                for table in result.tables or []:
                    table_md = getattr(table, "markdown", None)
                    if table_md:
                        table_markdown.append(table_md.strip())

            # Try to extract metadata from the content
            if extracted_text:
                # Simple pattern matching for common metadata patterns
                # Author patterns
                author_patterns = [
                    r"(?:By|Author|Written by)[:\s]+([^\n]+)",
                    r"<meta[^>]+name=[\"']author[\"'][^>]+content=[\"']([^\"']+)[\"']",
                ]

                # First check cleaned HTML for meta tags
                cleaned_html = result.cleaned_html if hasattr(result, "cleaned_html") else ""
                if cleaned_html:
                    for pattern in author_patterns[1:]:  # Meta tag patterns
                        match = re.search(pattern, cleaned_html, re.IGNORECASE)
                        if match:
                            author = match.group(1).strip()
                            break

                # Then check markdown content
                if not author:
                    for pattern in author_patterns[:1]:  # Text patterns
                        match = re.search(pattern, extracted_text, re.IGNORECASE)
                        if match:
                            author = match.group(1).strip()
                            # Clean up author if it contains extra content
                            if len(author) > 100:  # Likely grabbed too much
                                author = None
                            break

                # Date patterns
                date_patterns = [
                    r"(?:Published|Date|Posted)[:\s]+([^\n]+\d{4}[^\n]*)",
                    r"(\d{1,2}[-/]\d{1,2}[-/]\d{2,4})",
                    r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})",
                ]
                for pattern in date_patterns:
                    match = re.search(pattern, extracted_text, re.IGNORECASE)
                    if match:
                        date_str = match.group(1).strip()
                        publication_date = parse_date_with_tz(date_str)
                        if publication_date:
                            break

            logger.info(
                f"HtmlStrategy: Successfully extracted data for {url}. "
                f"Title: {title[:50] if title else 'None'}... Source: {source}"
            )
            # Map source to full domain name of final URL
            try:
                from urllib.parse import urlparse

                final_url = result.url if hasattr(result, "url") and result.url else url
                host = urlparse(final_url).netloc or ""
            except Exception:
                final_url = url
                host = ""
            logger.debug(
                "HtmlStrategy: Extraction metadata (final_url=%s, publication_date=%s, author=%s)",
                final_url,
                publication_date,
                author,
            )
            extraction_issue = self._detect_extraction_issue(
                url=final_url,
                title=title,
                text_content=extracted_text,
                html_content=result.cleaned_html,
            )
            if extraction_issue:
                logger.warning(
                    "HtmlStrategy: Suspect extraction detected for %s (%s)",
                    final_url,
                    extraction_issue,
                )
                fallback_data = self._fallback_fetch(final_url, source)
                if fallback_data and not fallback_data.get("extraction_error"):
                    logger.info(
                        "HtmlStrategy: Using fallback extraction for %s "
                        "after malformed crawl4ai output",
                        final_url,
                    )
                    return fallback_data

            # Extract feed links from HTML for potential feed detection
            feed_links = None
            if result.cleaned_html:
                from app.services.feed_detection import extract_feed_links

                feed_links = extract_feed_links(result.cleaned_html, final_url)
                if feed_links:
                    logger.debug(
                        "HtmlStrategy: Found %d feed link(s) in HTML",
                        len(feed_links),
                    )

            return {
                "title": title,
                "author": author,
                "publication_date": publication_date,
                "text_content": extracted_text,
                "content_type": "html",
                # Source should be full domain name, leave platform to the scraper convention
                "source": host,
                "final_url_after_redirects": final_url,
                "table_markdown": table_markdown or None,
                "feed_links": feed_links,  # For feed detection in worker
                "gate_page_detected": bool(
                    extraction_issue and extraction_issue.startswith("access gate detected")
                ),
                "extraction_error": extraction_issue,
            }

        except Exception as e:
            import traceback

            from app.services.http import NonRetryableError

            if self._should_use_httpx_fallback(e):
                fallback_data = self._fallback_fetch(url, source)
                if fallback_data:
                    logger.warning(
                        "HtmlStrategy: Using fallback extraction for %s after error: %s", url, e
                    )
                    return fallback_data

            error_msg = f"Content extraction failed for {url}: {str(e)}"
            traceback_str = traceback.format_exc()

            # Log the error
            logger.exception(
                "HtmlStrategy: %s",
                error_msg,
                extra={
                    "component": "html_strategy",
                    "operation": "html_content_extraction",
                    "item_id": url,
                    "context_data": {
                        "url": url,
                        "strategy": "html",
                        "source": source,
                        "method": "crawl4ai",
                        "error_type": type(e).__name__,
                        "crawl4ai_config": {
                            "page_timeout_ms": int(source_config.get("page_timeout_ms", 90_000))
                            if "source_config" in locals()
                            else None,
                            "wait_for_timeout_ms": int(
                                source_config.get("wait_for_timeout_ms", 90_000)
                            )
                            if "source_config" in locals()
                            else None,
                            "wait_until": source_config.get("wait_until", "domcontentloaded")
                            if "source_config" in locals()
                            else None,
                            "wait_for": source_config.get("wait_for")
                            if "source_config" in locals()
                            else None,
                            "max_crawl_attempts": int(source_config.get("max_crawl_attempts", 1))
                            if "source_config" in locals()
                            else None,
                        },
                        "traceback": traceback_str,
                    },
                },
            )

            # Check if this is a non-retryable error
            if self._is_non_retryable_extraction_error(e):
                # Raise NonRetryableError to prevent infinite retries
                raise NonRetryableError(f"Non-retryable error: {error_msg}") from e

            # For other errors, return a minimal response to allow processing to continue
            # with fallback content
            # Failure path: still try to emit domain for source
            try:
                from urllib.parse import urlparse

                host = urlparse(url).netloc or ""
            except Exception:
                host = ""
            return {
                "title": f"Content from {url}",
                "text_content": f"Failed to extract content from {url}. Error: {str(e)}",
                "content_type": "html",
                "source": host,
                "final_url_after_redirects": url,
                "extraction_error": str(e),
            }

    def prepare_for_llm(self, extracted_data: dict[str, Any]) -> dict[str, Any]:
        """
        Prepares extracted HTML data for LLM processing.
        """
        logger.info(
            f"HtmlStrategy: Preparing data for LLM for URL: "
            f"{extracted_data.get('final_url_after_redirects')}"
        )
        text_content = extracted_data.get("text_content", "") or ""
        logger.debug("HtmlStrategy: LLM preparation payload length=%s", len(text_content))

        table_markdown = extracted_data.get("table_markdown")
        if table_markdown:
            if isinstance(table_markdown, list):
                combined_tables = "\n\n".join(
                    table for table in table_markdown if isinstance(table, str) and table
                )
            else:
                combined_tables = str(table_markdown)

            if combined_tables:
                text_content = (
                    f"{text_content}\n\n## Extracted Tables\n{combined_tables}"
                    if text_content
                    else f"## Extracted Tables\n{combined_tables}"
                )

        # Based on app.llm.py, filter_article and summarize_article take the content string.
        return {
            "content_to_filter": text_content,
            "content_to_summarize": text_content,
            "is_pdf": False,
        }

    def extract_internal_urls(self, content: str, original_url: str) -> list[str]:
        """
        Extracts internal URLs from HTML content for logging.
        This is a basic implementation; more sophisticated parsing might be needed.
        """
        # This is a placeholder. A more robust implementation would use BeautifulSoup
        # or a regex designed for URLs, and properly resolve relative URLs.
        logger.info(
            f"HtmlStrategy: extract_internal_urls called for {original_url}. "
            "(Placeholder - returning empty list)"
        )
        return []
