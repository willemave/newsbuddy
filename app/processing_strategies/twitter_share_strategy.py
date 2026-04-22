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
from app.services.x_api import fetch_tweet_by_url
from app.services.x_tweet_metadata import build_resolved_tweet_content, hydrate_tweet_from_metadata

logger = get_logger(__name__)


@dataclass(frozen=True)
class TweetContent:
    """Parsed tweet content payload."""

    text: str
    author: str | None
    publication_date: datetime | None


def resolve_tweet_content(*, url: str, metadata: dict[str, Any] | None = None) -> TweetContent:
    tweet_id = extract_tweet_id(url)
    hydrated_tweet = hydrate_tweet_from_metadata(metadata, tweet_id=tweet_id)
    if hydrated_tweet is not None:
        text, author, publication_date = build_resolved_tweet_content(hydrated_tweet.tweet)
        return TweetContent(text=text, author=author, publication_date=publication_date)

    fetch_result = fetch_tweet_by_url(url=url)
    if not fetch_result.success or not fetch_result.tweet:
        raise NonRetryableError(fetch_result.error or "Tweet lookup failed")

    text, author, publication_date = build_resolved_tweet_content(fetch_result.tweet)
    if not text:
        raise NonRetryableError("Tweet thread contained no text to summarize")
    return TweetContent(text=text, author=author, publication_date=publication_date)


class TwitterShareProcessorStrategy(UrlProcessorStrategy):
    """Process tweet URLs by fetching text via official X API."""

    def can_handle_url(self, url: str, response_headers: httpx.Headers | None = None) -> bool:
        return extract_tweet_id(url) is not None

    def download_content(
        self,
        url: str,
        metadata: dict[str, Any] | None = None,
    ) -> TweetContent:
        return resolve_tweet_content(url=url, metadata=metadata)

    def extract_data(
        self,
        content: TweetContent,
        url: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del context
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
