"""Shared X/Twitter target resolution helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.core.logging import get_logger
from app.services.content_submission import normalize_url
from app.services.x_api import (
    XTweet,
    build_tweet_processing_text,
    fetch_tweets_by_ids,
    fetch_user_tweets,
    search_recent_tweets,
)
from app.services.x_tweet_metadata import parse_x_created_at

logger = get_logger(__name__)


@dataclass(frozen=True)
class TweetTargetResolution:
    """Resolved canonical target and thread context for a submitted tweet URL."""

    selected_article_url: str | None
    resolution_source: str
    resolution_tweet_id: str
    thread_text: str
    linked_tweet_ids: list[str]
    thread_lookup_status: str


def build_thread_text(tweet_texts: list[str]) -> str:
    """Join tweet/thread text into a single body."""
    cleaned = [text.strip() for text in tweet_texts if isinstance(text, str) and text.strip()]
    return "\n\n".join(cleaned)


def normalize_tweet_external_urls(urls: list[str], *, content_id: int) -> list[str]:
    """Normalize and deduplicate external URLs from tweet payloads."""
    normalized_urls: list[str] = []
    seen: set[str] = set()
    for raw_url in urls:
        try:
            normalized = normalize_url(raw_url)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Skipping invalid tweet external URL: %s",
                raw_url,
                extra={
                    "component": "tweet_resolution",
                    "operation": "normalize_external_url",
                    "item_id": content_id,
                },
            )
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        normalized_urls.append(normalized)
    return normalized_urls


class TweetTargetResolver:
    """Resolve a tweet into the best canonical article URL, if one exists."""

    _THREAD_PAGE_LIMIT = 10
    _THREAD_TWEET_LIMIT = 1000
    _THREAD_SIGNAL_MARKERS = ("1/", "thread", "🧵", "part 1", "1 of")

    def _build_same_author_thread(self, root_tweet: XTweet, tweets: list[XTweet]) -> list[XTweet]:
        by_id: dict[str, XTweet] = {root_tweet.id: root_tweet}
        for tweet in tweets:
            if tweet.id == root_tweet.id:
                continue
            if tweet.author_id and root_tweet.author_id and tweet.author_id != root_tweet.author_id:
                continue
            if tweet.conversation_id != root_tweet.conversation_id:
                continue
            by_id[tweet.id] = tweet

        def _sort_key(tweet: XTweet) -> tuple[datetime, str]:
            created_at = parse_x_created_at(tweet.created_at) or datetime.min.replace(tzinfo=UTC)
            return created_at, tweet.id

        return sorted(by_id.values(), key=_sort_key)

    def _resolve_from_thread(
        self,
        *,
        root_tweet: XTweet,
        access_token: str | None,
    ) -> tuple[str | None, str, str, list[XTweet]]:
        if not root_tweet.author_id or not root_tweet.conversation_id:
            return None, "unavailable", root_tweet.id, [root_tweet]

        cutoff = datetime.now(UTC) - timedelta(days=7)
        root_created_at = parse_x_created_at(root_tweet.created_at)
        can_use_recent_search = bool(
            root_created_at and root_created_at >= cutoff and root_tweet.author_username
        )
        collected: list[XTweet] = [root_tweet]

        if can_use_recent_search:
            query = (
                f"conversation_id:{root_tweet.conversation_id} from:{root_tweet.author_username}"
            )
            try:
                page = search_recent_tweets(query=query, access_token=access_token, max_results=100)
                collected.extend(page.tweets)
                thread_tweets = self._build_same_author_thread(root_tweet, collected)
                for tweet in thread_tweets:
                    normalized_urls = normalize_tweet_external_urls(
                        tweet.external_urls,
                        content_id=0,
                    )
                    if normalized_urls:
                        return normalized_urls[0], "found", tweet.id, thread_tweets
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Recent search thread lookup failed for tweet %s",
                    root_tweet.id,
                    extra={
                        "component": "tweet_resolution",
                        "operation": "recent_search_thread_lookup",
                    },
                )

        scanned = len([tweet for tweet in collected if tweet.id != root_tweet.id])
        pages = 0
        pagination_token: str | None = None
        while pages < self._THREAD_PAGE_LIMIT and scanned < self._THREAD_TWEET_LIMIT:
            page = fetch_user_tweets(
                user_id=root_tweet.author_id,
                access_token=access_token,
                pagination_token=pagination_token,
                max_results=min(100, self._THREAD_TWEET_LIMIT - scanned),
            )
            pages += 1
            scanned += len(page.tweets)
            collected.extend(page.tweets)
            thread_tweets = self._build_same_author_thread(root_tweet, collected)
            for tweet in thread_tweets:
                normalized_urls = normalize_tweet_external_urls(
                    tweet.external_urls,
                    content_id=0,
                )
                if normalized_urls:
                    return normalized_urls[0], "found", tweet.id, thread_tweets
            if not page.next_token:
                return None, "not_found", root_tweet.id, thread_tweets
            pagination_token = page.next_token

        thread_tweets = self._build_same_author_thread(root_tweet, collected)
        return None, "capped", root_tweet.id, thread_tweets

    def _should_attempt_thread_lookup(self, root_tweet: XTweet) -> bool:
        if not root_tweet.author_id or not root_tweet.conversation_id:
            return False
        if root_tweet.article_text or root_tweet.note_tweet_text:
            return False
        if root_tweet.external_urls:
            return False
        lowered = root_tweet.text.lower()
        if any(marker in lowered for marker in self._THREAD_SIGNAL_MARKERS):
            return True
        return (root_tweet.reply_count or 0) >= 3

    def resolve_tweet_target(
        self,
        *,
        root_tweet: XTweet,
        access_token: str | None,
        content_id: int,
        included_tweets_by_id: dict[str, XTweet] | None = None,
    ) -> TweetTargetResolution:
        """Resolve the first suitable article URL from a tweet, linked tweet, or thread."""
        root_urls = normalize_tweet_external_urls(root_tweet.external_urls, content_id=content_id)
        linked_tweet_ids = list(root_tweet.linked_tweet_ids)
        if root_urls:
            return TweetTargetResolution(
                selected_article_url=root_urls[0],
                resolution_source="root_tweet",
                resolution_tweet_id=root_tweet.id,
                thread_text=build_thread_text([build_tweet_processing_text(root_tweet)]),
                linked_tweet_ids=linked_tweet_ids,
                thread_lookup_status="not_needed",
            )

        if root_tweet.article_text or root_tweet.note_tweet_text:
            return TweetTargetResolution(
                selected_article_url=None,
                resolution_source="root_tweet",
                resolution_tweet_id=root_tweet.id,
                thread_text=build_thread_text([build_tweet_processing_text(root_tweet)]),
                linked_tweet_ids=linked_tweet_ids,
                thread_lookup_status="not_needed",
            )

        included_lookup = included_tweets_by_id or {}
        if linked_tweet_ids:
            for linked_tweet_id in linked_tweet_ids:
                linked_tweet = included_lookup.get(linked_tweet_id)
                if linked_tweet is None:
                    continue
                linked_urls = normalize_tweet_external_urls(
                    linked_tweet.external_urls,
                    content_id=content_id,
                )
                if linked_urls:
                    return TweetTargetResolution(
                        selected_article_url=linked_urls[0],
                        resolution_source="linked_tweet",
                        resolution_tweet_id=linked_tweet.id,
                        thread_text=build_thread_text([build_tweet_processing_text(root_tweet)]),
                        linked_tweet_ids=linked_tweet_ids,
                        thread_lookup_status="not_needed",
                    )

        remaining_linked_tweet_ids = [
            linked_tweet_id
            for linked_tweet_id in linked_tweet_ids
            if linked_tweet_id not in included_lookup
        ]

        if remaining_linked_tweet_ids:
            try:
                linked_tweets = fetch_tweets_by_ids(
                    tweet_ids=remaining_linked_tweet_ids,
                    access_token=access_token,
                )
                for linked_tweet in linked_tweets:
                    linked_urls = normalize_tweet_external_urls(
                        linked_tweet.external_urls,
                        content_id=content_id,
                    )
                    if linked_urls:
                        return TweetTargetResolution(
                            selected_article_url=linked_urls[0],
                            resolution_source="linked_tweet",
                            resolution_tweet_id=linked_tweet.id,
                            thread_text=build_thread_text(
                                [build_tweet_processing_text(root_tweet)]
                            ),
                            linked_tweet_ids=linked_tweet_ids,
                            thread_lookup_status="not_needed",
                        )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Linked tweet lookup failed for tweet %s",
                    root_tweet.id,
                    extra={
                        "component": "tweet_resolution",
                        "operation": "linked_tweet_lookup",
                        "item_id": content_id,
                    },
                )

        if not self._should_attempt_thread_lookup(root_tweet):
            return TweetTargetResolution(
                selected_article_url=None,
                resolution_source="tweet_only",
                resolution_tweet_id=root_tweet.id,
                thread_text=build_thread_text([build_tweet_processing_text(root_tweet)]),
                linked_tweet_ids=linked_tweet_ids,
                thread_lookup_status="not_attempted",
            )

        try:
            selected_article_url, thread_lookup_status, resolution_tweet_id, thread_tweets = (
                self._resolve_from_thread(root_tweet=root_tweet, access_token=access_token)
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "Thread lookup failed for tweet %s",
                root_tweet.id,
                extra={
                    "component": "tweet_resolution",
                    "operation": "thread_lookup",
                    "item_id": content_id,
                },
            )
            selected_article_url = None
            thread_lookup_status = "unavailable"
            resolution_tweet_id = root_tweet.id
            thread_tweets = [root_tweet]
        thread_text = build_thread_text(
            [build_tweet_processing_text(tweet) for tweet in thread_tweets]
        )
        if selected_article_url:
            return TweetTargetResolution(
                selected_article_url=selected_article_url,
                resolution_source="thread_reply",
                resolution_tweet_id=resolution_tweet_id,
                thread_text=thread_text,
                linked_tweet_ids=linked_tweet_ids,
                thread_lookup_status=thread_lookup_status,
            )

        return TweetTargetResolution(
            selected_article_url=None,
            resolution_source="tweet_only",
            resolution_tweet_id=root_tweet.id,
            thread_text=thread_text or build_thread_text([build_tweet_processing_text(root_tweet)]),
            linked_tweet_ids=linked_tweet_ids,
            thread_lookup_status=thread_lookup_status,
        )
