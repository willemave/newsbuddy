"""
This module defines the strategy for processing PubMed article pages.
Its primary role is to extract the full-text link and delegate further processing.
"""

import asyncio
import re
from typing import Any

import httpx  # For type hinting httpx.Headers
from bs4 import BeautifulSoup
from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

from app.core.logging import get_logger
from app.http_client.robust_http_client import RobustHttpClient
from app.processing_strategies.base_strategy import UrlProcessorStrategy

logger = get_logger(__name__)


class PubMedProcessorStrategy(UrlProcessorStrategy):
    """
    Strategy for processing PubMed article pages.
    It downloads the PubMed page, extracts the link to the full-text article,
    and then signals for delegation to another strategy (HTML or PDF)
    to process the actual full-text content.
    """

    def __init__(self, http_client: RobustHttpClient):
        super().__init__(http_client)

    def can_handle_url(self, url: str, response_headers: httpx.Headers | None = None) -> bool:
        """
        Determines if this strategy can handle the given URL.
        Checks if the URL is a PubMed article page.
        """
        url_lower = url.lower()
        is_pubmed_page = "pubmed.ncbi.nlm.nih.gov" in url_lower
        has_html_extension = url_lower.endswith((".pdf", ".html", ".htm"))
        matches_pubmed_pattern = bool(re.search(r"pubmed\.ncbi\.nlm\.nih\.gov/\d+/?$", url_lower))

        if is_pubmed_page and not has_html_extension and matches_pubmed_pattern:
            logger.debug("PubMedStrategy can handle PubMed article page: %s", url)
            return True

        logger.debug(
            "PubMedStrategy cannot handle URL: %s (not a typical PubMed article page URL)",
            url,
        )
        return False

    def download_content(self, url: str) -> str:
        """
        Downloads the HTML content of the PubMed article page using crawl4ai.
        This avoids 403 errors that regular HTTP requests encounter.
        """
        logger.info(f"PubMedStrategy: download_content called for {url}")
        # Return URL as placeholder - actual download happens in extract_data with crawl4ai
        return url

    def _extract_full_text_link_from_html(
        self, pubmed_page_html: str, pubmed_url: str
    ) -> str | None:
        """
        Helper to extract the full text link from PubMed page HTML.
        This logic is similar to the one previously in processor.py.
        """
        try:
            soup = BeautifulSoup(pubmed_page_html, "html.parser")

            # Look for full text links section (multiple selectors for robustness)
            full_text_section = soup.find("div", {"class": "full-text-links-list"})
            if not full_text_section:
                full_text_section = soup.find("aside", {"id": "full-text-links"})
            if not full_text_section:
                # Try finding by heading text then parent
                heading = None
                heading_pattern = re.compile(r"Full.*text.*links", re.IGNORECASE)
                for tag_name in ("h3", "h4", "strong"):
                    for candidate in soup.find_all(tag_name):
                        heading_text = candidate.get_text(" ", strip=True)
                        if heading_pattern.search(heading_text):
                            heading = candidate
                            break
                    if heading is not None:
                        break
                if heading:
                    full_text_section = heading.find_parent("div")  # Common parent

            if full_text_section:
                links = full_text_section.find_all("a", href=True)

                # Prioritize PMC links
                pmc_link = None
                first_link = None

                for link_tag in links:
                    href = link_tag.get("href")
                    if not isinstance(href, str) or not href:
                        continue

                    # Resolve relative URLs
                    if href.startswith("//"):
                        href = "https:" + href
                    elif href.startswith("/"):
                        # Ensure base is correct for pubmed or ncbi domain
                        base_domain = (
                            "https://www.ncbi.nlm.nih.gov"
                            if "ncbi.nlm.nih.gov" in pubmed_url
                            else "https://pubmed.ncbi.nlm.nih.gov"
                        )
                        href = base_domain + href

                    if not first_link:  # Keep track of the very first valid link
                        first_link = href

                    if "pmc" in href.lower() and (
                        "article" in href.lower() or href.endswith(".pdf")
                    ):
                        pmc_link = href
                        logger.info("PubMedStrategy: Found PMC link: %s", pmc_link)
                        return pmc_link  # Prioritize and return immediately

                if first_link:  # If no PMC link, return the first one found
                    logger.info(
                        "PubMedStrategy: No PMC link found, returning first available link: %s",
                        first_link,
                    )
                    return first_link

            logger.warning(
                (
                    "PubMedStrategy: Could not find 'full-text-links' section or any links "
                    "within it for %s"
                ),
                pubmed_url,
            )
            return None

        except Exception as e:
            logger.error(
                "PubMedStrategy: Error parsing PubMed HTML for full text link from %s: %s",
                pubmed_url,
                e,
                exc_info=True,
            )
            return None

    def extract_data(
        self,
        content: str,
        url: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Extracts the full-text link from the PubMed page using crawl4ai.
        Returns a special dictionary indicating the next URL to process.
        'content' parameter is ignored as crawl4ai handles downloading.
        'url' here is the final URL of the PubMed page itself.
        """
        del content, context
        logger.info("PubMedStrategy: Extracting full-text link from PubMed page: %s", url)

        try:
            # Configure browser for crawl4ai
            browser_config = BrowserConfig(
                headless=True,
                viewport_width=1920,
                viewport_height=1080,
                text_mode=False,
                light_mode=True,
                ignore_https_errors=True,
                java_script_enabled=True,
                extra_args=["--disable-blink-features=AutomationControlled"],
            )

            # Configure crawler run
            run_config = CrawlerRunConfig(
                cache_mode=CacheMode.ENABLED,
                verbose=False,
                wait_until="domcontentloaded",
                wait_for="body",
                delay_before_return_html=1.0,
            )

            # Use AsyncWebCrawler with asyncio.run
            async def crawl():
                async with AsyncWebCrawler(config=browser_config) as crawler:
                    return await crawler.arun(url=url, config=run_config)

            result = asyncio.run(crawl())

            if not result or not result.success:
                raise Exception(f"Crawl4ai extraction failed for PubMed page: {url}")

            # Get the HTML content
            pubmed_page_html = result.html

            full_text_url = self._extract_full_text_link_from_html(pubmed_page_html, url)

        except Exception as err:
            logger.error("PubMedStrategy: Error using crawl4ai for %s: %s", url, err)
            # Fall back to returning an error
            return {
                "title": f"PubMed Page Access Failed for {url.split('/')[-1]}",
                "text_content": f"Could not access PubMed page: {err}",
                "content_type": "error_pubmed_extraction",
                "final_url_after_redirects": url,
            }

        if full_text_url:
            logger.info(
                (
                    "PubMedStrategy: Extracted full-text URL '%s' from PubMed page %s. "
                    "Delegating processing."
                ),
                full_text_url,
                url,
            )
            return {
                "next_url_to_process": full_text_url,
                "original_pubmed_url": url,  # The URL of the PubMed page itself
                "content_type": "pubmed_delegation",  # Special type to signal delegation
                "final_url_after_redirects": url,  # For this strategy, it's the pubmed page URL
            }
        else:
            logger.warning(
                "PubMedStrategy: Could not extract any full-text link from %s. Cannot delegate.",
                url,
            )
            # This is an extraction failure for this strategy.
            return {
                "title": f"PubMed Full-Text Link Extraction Failed for {url.split('/')[-1]}",
                "text_content": "Could not find a usable full-text link on the PubMed page.",
                "content_type": "error_pubmed_extraction",  # Special error type
                "final_url_after_redirects": url,
            }

    def prepare_for_llm(self, extracted_data: dict[str, Any]) -> dict[str, Any]:
        """
        This method should not be called directly if delegation occurs.
        If called (e.g., due to an extraction failure), it indicates no LLM processing.
        """
        logger.info("PubMedStrategy: prepare_for_llm called. Data: %s", extracted_data)
        # If 'next_url_to_process' is present, this strategy's job is done.
        # If not, it means extraction failed, so no LLM processing for this step.
        return {  # Indicates no content for LLM from this PubMed *page* itself
            "content_to_filter": None,
            "content_to_summarize": None,
            "is_pdf": False,  # Irrelevant as we are delegating or failed
        }

    def extract_internal_urls(self, content: str, original_url: str) -> list[str]:
        """
        Extracts internal URLs from the PubMed page for logging.
        Could log other links found on the PubMed page if desired.
        """
        # Placeholder, similar to HtmlStrategy.
        # Could parse 'content' (PubMed page HTML) for other links if needed.
        logger.info(
            (
                "PubMedStrategy: extract_internal_urls called for %s. "
                "(Placeholder - returning empty list)"
            ),
            original_url,
        )
        return []
