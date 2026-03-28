"""Tweet-only processing strategy for share-sheet ingestion."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from app.core.logging import get_logger
from app.processing_strategies.base_strategy import UrlProcessorStrategy
from app.services.http import NonRetryableError
from app.services.twitter_share import extract_tweet_id
from app.services.x_api import build_tweet_processing_text, fetch_tweet_by_url

logger = get_logger(__name__)


@dataclass(frozen=True)
class TweetContent:
    """Parsed tweet content payload."""

    text: str
    author: str | None
    publication_date: datetime | None


def _parse_tweet_date(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    try:
        iso_value = date_str.replace("Z", "+00:00")
        return datetime.fromisoformat(iso_value)
    except Exception:
        pass
    try:
        return datetime.strptime(date_str, "%a %b %d %H:%M:%S %z %Y")
    except Exception:
        return None


def _build_thread_text(thread: list[str]) -> str:
    cleaned = [text.strip() for text in thread if isinstance(text, str) and text.strip()]
    return "\n\n".join(cleaned)


class TwitterShareProcessorStrategy(UrlProcessorStrategy):
    """Process tweet URLs by fetching text via official X API."""

    def can_handle_url(self, url: str, response_headers: httpx.Headers | None = None) -> bool:
        return extract_tweet_id(url) is not None

    def download_content(self, url: str) -> TweetContent:
        fetch_result = fetch_tweet_by_url(url=url)

        if not fetch_result.success or not fetch_result.tweet:
            raise NonRetryableError(fetch_result.error or "Tweet lookup failed")

        tweet = fetch_result.tweet
        thread_text = _build_thread_text([build_tweet_processing_text(tweet)])
        if not thread_text:
            raise NonRetryableError("Tweet thread contained no text to summarize")

        author = None
        if tweet.author_username:
            author = f"@{tweet.author_username}"
        publication_date = _parse_tweet_date(tweet.created_at)

        return TweetContent(text=thread_text, author=author, publication_date=publication_date)

    def extract_data(self, content: TweetContent, url: str) -> dict[str, Any]:
        title = content.text.split("\n", 1)[0].strip() if content.text else "Tweet"

        return {
            "title": title[:280] if title else "Tweet",
            "author": content.author,
            "publication_date": content.publication_date,
            "text_content": content.text,
            "content_type": "text",
            "final_url_after_redirects": url,
        }

    def prepare_for_llm(self, extracted_data: dict[str, Any]) -> dict[str, Any]:
        text_content = (extracted_data.get("text_content") or "").strip()
        return {
            "content_to_filter": text_content,
            "content_to_summarize": text_content,
            "is_pdf": False,
        }
