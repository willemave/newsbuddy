"""
HackerNews processing strategy that handles HN discussion pages,
fetches comments, and generates comment summaries.
"""

import asyncio
import contextlib
import re
import threading
from collections.abc import Coroutine
from datetime import datetime
from typing import Any

import httpx

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.http_client.robust_http_client import RobustHttpClient
from app.processing_strategies.base_strategy import UrlProcessorStrategy
from app.processing_strategies.html_strategy import HtmlProcessorStrategy
from app.processing_strategies.pdf_strategy import PdfProcessorStrategy

logger = get_logger(__name__)


def _run_coro_sync[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine from sync code, even if an event loop is already active."""

    def _run_on_new_loop() -> T:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(coro)
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.run_until_complete(loop.shutdown_default_executor())
            return result
        finally:
            asyncio.set_event_loop(None)
            loop.close()

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _run_on_new_loop()

    result: T | None = None
    error: BaseException | None = None

    def _runner() -> None:
        nonlocal result, error
        try:
            result = _run_on_new_loop()
        except BaseException as exc:  # noqa: BLE001
            error = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()

    if error is not None:
        raise error
    if result is None:
        raise RuntimeError("Coroutine runner returned without a result")
    return result


class HackerNewsProcessorStrategy(UrlProcessorStrategy):
    """
    Strategy for processing HackerNews discussion pages.
    Handles HN item URLs, fetches comments, and includes them in the summary.
    """

    def __init__(self, http_client: RobustHttpClient):
        super().__init__(http_client)
        self.settings = get_settings()
        self.hn_api_base = "https://hacker-news.firebaseio.com/v0"

        # Initialize delegate strategies for processing linked content
        self.html_strategy = HtmlProcessorStrategy(http_client)
        self.pdf_strategy = PdfProcessorStrategy(http_client)

    def preprocess_url(self, url: str) -> str:
        """No preprocessing needed for HN URLs."""
        return url

    def can_handle_url(self, url: str, response_headers: httpx.Headers | None = None) -> bool:
        """Check if this is a HackerNews item URL."""
        hn_patterns = [
            r"https?://news\.ycombinator\.com/item\?id=\d+",
            r"https?://hacker-news\.firebaseio\.com/v0/item/\d+",
        ]

        for pattern in hn_patterns:
            if re.match(pattern, url):
                logger.debug(f"HackerNewsStrategy can handle {url}")
                return True

        return False

    def _extract_item_id(self, url: str) -> str | None:
        """Extract HN item ID from URL."""
        # Match HN item URLs
        match = re.search(r"item\?id=(\d+)", url)
        if match:
            return match.group(1)

        # Match Firebase API URLs
        match = re.search(r"/item/(\d+)", url)
        if match:
            return match.group(1)

        return None

    async def _fetch_item_data(self, item_id: str) -> dict[str, Any] | None:
        """Fetch item data from HN Firebase API."""
        try:
            url = f"{self.hn_api_base}/item/{item_id}.json"

            async with httpx.AsyncClient() as client:
                response = await client.get(url, timeout=30.0)
                response.raise_for_status()
                return response.json()

        except Exception as e:
            logger.error(f"Failed to fetch HN item {item_id}: {e}")
            return None

    async def _fetch_comment(self, comment_id: int, depth: int = 0) -> dict[str, Any] | None:
        """Fetch a single comment by ID."""
        if depth > 2:  # Limit depth to avoid too deep recursion
            return None

        try:
            url = f"{self.hn_api_base}/item/{comment_id}.json"

            async with httpx.AsyncClient() as client:
                response = await client.get(url, timeout=10.0)
                response.raise_for_status()
                data = response.json()

                if data and data.get("type") == "comment" and not data.get("deleted"):
                    return {
                        "id": data.get("id"),
                        "author": data.get("by", "unknown"),
                        "text": data.get("text", ""),
                        "time": data.get("time"),
                        "kids": data.get("kids", []),
                        "depth": depth,
                    }

        except Exception as e:
            logger.error(f"Failed to fetch comment {comment_id}: {e}")

        return None

    async def _fetch_comments(
        self, item_data: dict[str, Any], max_comments: int = 30
    ) -> list[dict[str, Any]]:
        """Fetch top-level comments for an item."""
        comments: list[dict[str, Any]] = []
        comment_ids = item_data.get("kids", [])[:max_comments]

        if not comment_ids:
            return comments

        # Fetch comments concurrently
        tasks = [self._fetch_comment(cid) for cid in comment_ids]
        results = await asyncio.gather(*tasks)

        # Filter out None results
        comments = [c for c in results if c is not None]

        # Sort by score approximation (we don't have score, so use position as proxy)
        return comments

    def _clean_html_text(self, html_text: str) -> str:
        """Clean HTML from HN comments."""
        if not html_text:
            return ""

        # Basic HTML tag removal
        text = re.sub(r"<p>", "\n\n", html_text)
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"&lt;", "<", text)
        text = re.sub(r"&gt;", ">", text)
        text = re.sub(r"&amp;", "&", text)
        text = re.sub(r"&quot;", '"', text)
        text = re.sub(r"&#x27;", "'", text)

        # Clean up whitespace
        text = re.sub(r"\n{3,}", "\n\n", text)

        return text.strip()

    def _format_comments_for_summary(self, comments: list[dict[str, Any]]) -> str:
        """Format comments into text for LLM summarization."""
        if not comments:
            return "No comments available."

        formatted_comments = []
        for i, comment in enumerate(comments[:20]):  # Limit to top 20 comments
            author = comment.get("author", "unknown")
            text = self._clean_html_text(comment.get("text", ""))

            if text:
                formatted_comments.append(f"Comment {i + 1} by {author}:\n{text}\n")

        return "\n---\n".join(formatted_comments)

    def download_content(self, url: str) -> str:
        """Download content - for HN we just return the URL."""
        logger.info(f"HackerNewsStrategy: download_content called for {url}")
        return url

    def extract_data(
        self,
        content: str,
        url: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Extract data from HackerNews item page."""
        del context
        logger.info(f"HackerNewsStrategy: Extracting data from {url}")

        item_id = self._extract_item_id(url)
        if not item_id:
            logger.error(f"Could not extract item ID from URL: {url}")
            return {
                "title": "Invalid HackerNews URL",
                "text_content": "",
                "content_type": "html",
                "source": "HackerNews",
                "final_url_after_redirects": url,
            }

        # Fetch item data and comments asynchronously
        async def fetch_all_data():
            item_data = await self._fetch_item_data(item_id)
            if not item_data:
                return None, []

            comments = await self._fetch_comments(item_data)
            return item_data, comments

        item_data, comments = _run_coro_sync(fetch_all_data())

        if not item_data:
            logger.error(f"Could not fetch HN item data for ID: {item_id}")
            return {
                "title": "Failed to fetch HackerNews data",
                "text_content": "",
                "content_type": "html",
                "platform": "hackernews",
                "source": "hackernews:HackerNews",
                "final_url_after_redirects": url,
            }

        # Extract metadata
        title = item_data.get("title", "Untitled")
        author = item_data.get("by", "unknown")
        score = item_data.get("score", 0)
        num_comments = item_data.get("descendants", 0)
        time_posted = item_data.get("time")

        # Parse publication date
        publication_date = None
        if time_posted:
            with contextlib.suppress(ValueError, TypeError):
                publication_date = datetime.fromtimestamp(time_posted)

        # Get the linked URL if it exists
        linked_url = item_data.get("url")
        item_type = item_data.get("type", "story")

        # For Ask HN, Show HN, etc., the text is in the item itself
        text_content = ""
        if item_data.get("text"):
            text_content = self._clean_html_text(item_data.get("text"))

        # Format comments for summarization
        comments_text = self._format_comments_for_summary(comments)

        # Build metadata
        metadata = {
            "title": title,
            "author": author,
            "publication_date": publication_date,
            "content_type": "html",
            "platform": "hackernews",  # Platform identifier
            "source": "hackernews:HackerNews",  # Standardized format: platform:source
            "final_url_after_redirects": url,
            # HN-specific metadata
            "hn_score": score,
            "hn_comments_count": num_comments,
            "hn_submitter": author,
            "hn_discussion_url": f"https://news.ycombinator.com/item?id={item_id}",
            "hn_item_type": item_type,
            "hn_linked_url": linked_url,
            "is_hn_text_post": bool(text_content),
            # Comments summary will be added during LLM processing
            "hn_comments_raw": comments_text,
        }

        # If there's a linked URL and no text content, we need to fetch the linked content
        if linked_url and not text_content:
            metadata["text_content"] = f"[Linked article: {linked_url}]\n\n"
            metadata["requires_content_fetch"] = True
            metadata["content_url_to_fetch"] = linked_url
        else:
            # For text posts (Ask HN, etc.) or if no linked URL
            metadata["text_content"] = text_content or "[No content]"
            metadata["requires_content_fetch"] = False

        return metadata

    def prepare_for_llm(self, extracted_data: dict[str, Any]) -> dict[str, Any]:
        """Prepare data for LLM processing, including comments."""
        logger.info("HackerNewsStrategy: Preparing data for LLM")

        # If we need to fetch linked content first
        if extracted_data.get("requires_content_fetch"):
            linked_url = extracted_data.get("content_url_to_fetch")
            if linked_url:
                # Determine which strategy to use for the linked content
                response_headers = None
                try:
                    # Make a HEAD request to determine content type
                    response = self.http_client.head(linked_url)
                    response_headers = response.headers
                except Exception as e:
                    logger.warning(f"HEAD request failed for {linked_url}: {e}")

                # Try PDF strategy first
                if self.pdf_strategy.can_handle_url(linked_url, response_headers):
                    logger.info(f"Using PDF strategy for linked content: {linked_url}")
                    pdf_content = self.pdf_strategy.download_content(linked_url)
                    pdf_data = self.pdf_strategy.extract_data(pdf_content, linked_url)
                    linked_content = pdf_data.get("text_content", "")
                else:
                    # Fall back to HTML strategy
                    logger.info(f"Using HTML strategy for linked content: {linked_url}")
                    html_data = self.html_strategy.extract_data("", linked_url)
                    linked_content = html_data.get("text_content", "")

                # Update the text content with linked article content
                extracted_data["text_content"] = linked_content

        # Combine article content with HN discussion context
        text_content = extracted_data.get("text_content", "")
        comments_text = extracted_data.get("hn_comments_raw", "")

        # Create enhanced content for summarization that includes HN context
        hn_metadata = f"""
HackerNews Discussion Context:
- Title: {extracted_data.get("title")}
- Score: {extracted_data.get("hn_score", 0)} points
- Comments: {extracted_data.get("hn_comments_count", 0)}
- Submitted by: {extracted_data.get("hn_submitter", "unknown")}
- Discussion URL: {extracted_data.get("hn_discussion_url")}
        """.strip()

        # For text posts, include the text content
        if extracted_data.get("is_hn_text_post"):
            content_for_summary = f"{hn_metadata}\n\n--- POST CONTENT ---\n{text_content}"
        else:
            # For linked articles, include the article content
            content_for_summary = f"{hn_metadata}\n\n--- ARTICLE CONTENT ---\n{text_content}"

        # Add comments section
        if comments_text and comments_text != "No comments available.":
            content_for_summary += f"\n\n--- HACKERNEWS COMMENTS ---\n{comments_text}"

        return {
            "content_to_filter": text_content,  # Original content for filtering
            "content_to_summarize": content_for_summary,  # Enhanced content with HN context
            "is_pdf": False,
            "content_type": "hackernews",  # Special type for HN content
            "hn_metadata": {
                "score": extracted_data.get("hn_score", 0),
                "comments_count": extracted_data.get("hn_comments_count", 0),
                "has_comments": bool(comments_text and comments_text != "No comments available."),
            },
        }

    def extract_internal_urls(self, content: str, original_url: str) -> list[str]:
        """Extract any relevant URLs from the HN discussion."""
        # For now, we'll just return the linked URL if any
        urls: list[str] = []

        # This would be populated from the extract_data method
        # but for now return empty list
        return urls
