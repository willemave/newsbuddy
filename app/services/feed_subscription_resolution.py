"""Resolve feed subscription targets from shared URLs."""

from __future__ import annotations

import html as html_lib
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from app.constants import SELF_SUBMISSION_SOURCE
from app.core.logging import get_logger
from app.models.db import Content
from app.services.apple_podcasts import resolve_apple_podcast_episode
from app.services.content_submission import normalize_url
from app.services.feed_detection import FeedDetector, detect_feeds_from_html
from app.services.gateways.http_gateway import get_http_gateway
from app.services.tweet_target_resolution import (
    TweetTargetResolver,
    normalize_tweet_external_urls,
)
from app.services.twitter_share import canonical_tweet_url, extract_tweet_id
from app.services.x_api import (
    XTweet,
    build_tweet_processing_text,
    fetch_tweet_by_id,
    fetch_tweets_by_ids,
)
from app.services.x_integration import get_x_user_access_token
from app.services.x_tweet_metadata import (
    hydrate_included_tweets_from_metadata,
    hydrate_tweet_from_metadata,
)

logger = get_logger(__name__)

FEED_LIKE_URL_HINTS = ("rss", "atom", "feed", ".xml")
MAX_FEED_SUBSCRIPTION_CANDIDATES = 12
YOUTUBE_HOSTS = {"youtube.com", "m.youtube.com", "youtu.be"}
HREF_RE = re.compile(
    r"<a\b[^>]*\shref=(?P<quote>[\"'])(?P<href>.*?)(?P=quote)",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class FeedSubscriptionCandidate:
    """URL candidate to inspect for a feed subscription request."""

    url: str
    source: str
    title: str | None = None
    detected_feed: dict[str, Any] | None = None


@dataclass(frozen=True)
class FeedCandidateDetection:
    """Feed detection result for one candidate URL."""

    detected_feed: dict[str, Any] | None
    all_detected_feeds: Any | None
    fetch_status: str
    html_content: str | None = None


@dataclass(frozen=True)
class CandidateBuildResult:
    """Initial candidate bundle from platform-specific URL resolvers."""

    candidates: list[FeedSubscriptionCandidate]
    metadata_updates: dict[str, Any]
    failure_status: str | None = None


@dataclass(frozen=True)
class FeedSubscriptionResolution:
    """Resolved feed-subscription target and metadata side effects."""

    detected_feed: dict[str, Any] | None
    all_detected_feeds: Any | None
    status: str
    metadata_updates: dict[str, Any]
    source: str | None = None
    source_url: str | None = None


def _normalize_hostname(hostname: str | None) -> str:
    normalized = (hostname or "").strip().lower()
    if normalized.startswith("www."):
        return normalized[4:]
    return normalized


def _looks_like_feed_url(url: str) -> bool:
    lowered = url.lower()
    return any(hint in lowered for hint in FEED_LIKE_URL_HINTS)


def _is_youtube_url(url: str) -> bool:
    parsed = urlparse(url)
    return _normalize_hostname(parsed.hostname) in YOUTUBE_HOSTS


def _is_youtube_subscribable_url(url: str) -> bool:
    parsed = urlparse(url)
    hostname = _normalize_hostname(parsed.hostname)
    if hostname not in YOUTUBE_HOSTS:
        return False
    if hostname == "youtu.be":
        return False

    path = parsed.path.strip("/")
    if not path:
        return False
    if path.startswith(("@", "channel/", "c/", "user/")):
        return True
    return path == "playlist" and bool(parse_qs(parsed.query).get("list", [None])[0])


class FeedSubscriptionResolver:
    """Resolve the best feed-like target for an Add Feed share-sheet submission."""

    def __init__(self, *, tweet_target_resolver: TweetTargetResolver | None = None) -> None:
        self._tweet_target_resolver = tweet_target_resolver or TweetTargetResolver()

    def resolve(
        self,
        *,
        db,
        content: Content,
        metadata: dict[str, Any],
        url: str,
    ) -> FeedSubscriptionResolution:
        """Resolve a submitted URL into a detected feed, if possible."""
        fetch_status = "no_feed_found"
        detected_feed: dict[str, Any] | None = None
        all_detected_feeds: Any | None = None
        selected_candidate: FeedSubscriptionCandidate | None = None
        build_result = self._build_initial_candidates(
            db=db,
            content=content,
            metadata=metadata,
            url=url,
        )
        if build_result.failure_status:
            fetch_status = build_result.failure_status
        candidates = list(build_result.candidates)
        seen_candidate_urls = {candidate.url for candidate in candidates}
        candidate_index = 0
        evaluated_candidates = 0

        while (
            candidate_index < len(candidates)
            and evaluated_candidates < MAX_FEED_SUBSCRIPTION_CANDIDATES
        ):
            candidate = candidates[candidate_index]
            candidate_index += 1
            evaluated_candidates += 1
            detection = self._detect_candidate(
                db=db,
                content=content,
                candidate=candidate,
            )
            fetch_status = detection.fetch_status
            if detection.detected_feed:
                detected_feed = detection.detected_feed
                all_detected_feeds = detection.all_detected_feeds
                selected_candidate = candidate
                break

            if not detection.html_content:
                continue

            for linked_candidate in self._extract_link_candidates(
                html_content=detection.html_content,
                page_url=candidate.url,
                content_id=content.id or 0,
            ):
                if len(candidates) >= MAX_FEED_SUBSCRIPTION_CANDIDATES:
                    break
                if linked_candidate.url in seen_candidate_urls:
                    continue
                seen_candidate_urls.add(linked_candidate.url)
                candidates.append(linked_candidate)

        return FeedSubscriptionResolution(
            detected_feed=detected_feed,
            all_detected_feeds=all_detected_feeds,
            status=fetch_status,
            metadata_updates=build_result.metadata_updates,
            source=selected_candidate.source if selected_candidate else None,
            source_url=selected_candidate.url if selected_candidate else None,
        )

    def _detect_direct_feed_url(
        self,
        url: str,
        page_title: str | None,
        *,
        db,
        content_id: int | None = None,
    ) -> dict[str, Any] | None:
        detector = FeedDetector(use_exa_search=False)
        validated_feed = detector.validate_feed_url(url)
        if not validated_feed:
            return None

        classification = detector.classify_feed_type(
            feed_url=url,
            page_url=url,
            page_title=page_title or validated_feed.get("title"),
            html_content=None,
            db=db,
            usage_persist={
                "feature": "feed_detection",
                "operation": "feed_detection.classify_feed_type",
                "source": "queue",
                "content_id": content_id,
                "metadata": {"page_url": url},
            },
        )
        title_value = validated_feed.get("title")
        resolved_title = (
            title_value if isinstance(title_value, str) and title_value else (page_title or "")
        )
        return {
            "url": url,
            "type": classification.feed_type,
            "title": resolved_title,
            "format": validated_feed.get("feed_format", "rss"),
        }

    def _detect_feeds_from_html(
        self,
        *,
        html_content: str,
        page_url: str,
        page_title: str | None,
        db,
        content: Content,
    ) -> dict[str, Any] | None:
        return detect_feeds_from_html(
            html_content,
            page_url,
            page_title=page_title,
            source=SELF_SUBMISSION_SOURCE,
            content_type=content.content_type,
            force_detect=True,
            use_exa_search=False,
            db=db,
            usage_persist={
                "feature": "feed_detection",
                "operation": "feed_detection.classify_feed_type",
                "source": "queue",
                "content_id": content.id,
                "metadata": {"page_url": page_url},
            },
        )

    def _fetch_html_for_feed_detection(self, *, url: str, content: Content) -> str | None:
        body, _headers = get_http_gateway().fetch_content(url)
        if isinstance(body, str):
            return body
        if isinstance(body, bytes):
            return body.decode("utf-8", errors="ignore")
        logger.warning(
            "Fetched feed-detection candidate had unsupported body type",
            extra={
                "component": "sequential_task_processor",
                "operation": "feed_detect_fetch",
                "item_id": content.id,
                "context_data": {"url": url, "body_type": type(body).__name__},
            },
        )
        return None

    def _detect_candidate(
        self,
        *,
        db,
        content: Content,
        candidate: FeedSubscriptionCandidate,
    ) -> FeedCandidateDetection:
        if candidate.detected_feed:
            return FeedCandidateDetection(
                detected_feed=dict(candidate.detected_feed),
                all_detected_feeds=None,
                fetch_status="detected",
                html_content=None,
            )

        detected_feed = self._detect_direct_feed_url(
            candidate.url,
            candidate.title or content.title,
            db=db,
            content_id=content.id,
        )
        if detected_feed:
            return FeedCandidateDetection(
                detected_feed=detected_feed,
                all_detected_feeds=None,
                fetch_status="detected",
                html_content=None,
            )

        try:
            html_content = self._fetch_html_for_feed_detection(
                url=candidate.url,
                content=content,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Failed to fetch URL for feed detection: %s",
                exc,
                extra={
                    "component": "sequential_task_processor",
                    "operation": "feed_detect_fetch",
                    "item_id": content.id,
                    "context_data": {"url": candidate.url, "error": str(exc)},
                },
            )
            return FeedCandidateDetection(
                detected_feed=None,
                all_detected_feeds=None,
                fetch_status="fetch_failed",
                html_content=None,
            )

        if not html_content:
            return FeedCandidateDetection(
                detected_feed=None,
                all_detected_feeds=None,
                fetch_status="no_feed_found",
                html_content=None,
            )

        feed_data = self._detect_feeds_from_html(
            html_content=html_content,
            page_url=candidate.url,
            page_title=candidate.title or content.title,
            db=db,
            content=content,
        )
        if feed_data:
            return FeedCandidateDetection(
                detected_feed=feed_data.get("detected_feed"),
                all_detected_feeds=feed_data.get("all_detected_feeds"),
                fetch_status="detected",
                html_content=html_content,
            )

        return FeedCandidateDetection(
            detected_feed=None,
            all_detected_feeds=None,
            fetch_status="no_feed_found",
            html_content=html_content,
        )

    def _append_candidate(
        self,
        candidates: list[FeedSubscriptionCandidate],
        seen_urls: set[str],
        candidate: FeedSubscriptionCandidate,
        *,
        content_id: int,
    ) -> None:
        try:
            normalized_url = normalize_url(candidate.url)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Skipping invalid feed subscription candidate URL: %s",
                candidate.url,
                extra={
                    "component": "feed_subscription",
                    "operation": "normalize_candidate_url",
                    "item_id": content_id,
                },
            )
            return

        if normalized_url in seen_urls:
            return
        seen_urls.add(normalized_url)
        candidates.append(
            FeedSubscriptionCandidate(
                url=normalized_url,
                source=candidate.source,
                title=candidate.title,
                detected_feed=candidate.detected_feed,
            )
        )

    def _sort_candidates(
        self,
        candidates: list[FeedSubscriptionCandidate],
    ) -> list[FeedSubscriptionCandidate]:
        indexed = list(enumerate(candidates))

        def _priority(item: tuple[int, FeedSubscriptionCandidate]) -> tuple[int, int]:
            index, candidate = item
            if candidate.detected_feed:
                return (0, index)
            if _looks_like_feed_url(candidate.url):
                return (1, index)
            return (2, index)

        return [candidate for _index, candidate in sorted(indexed, key=_priority)]

    def _extract_link_candidates(
        self,
        *,
        html_content: str,
        page_url: str,
        content_id: int,
    ) -> list[FeedSubscriptionCandidate]:
        candidates: list[FeedSubscriptionCandidate] = []
        seen_urls: set[str] = set()

        for match in HREF_RE.finditer(html_content):
            href = html_lib.unescape((match.group("href") or "").strip())
            if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue

            absolute_url = urljoin(page_url, href)
            parsed = urlparse(absolute_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                continue

            self._append_candidate(
                candidates,
                seen_urls,
                FeedSubscriptionCandidate(url=absolute_url, source="linked_page"),
                content_id=content_id,
            )

        return self._sort_candidates(candidates)

    def _build_youtube_candidate(
        self,
        *,
        url: str,
        page_title: str | None,
        content: Content,
    ) -> FeedSubscriptionCandidate | None:
        if not _is_youtube_url(url):
            return None
        if _is_youtube_subscribable_url(url):
            title = page_title or content.title or "YouTube"
            return FeedSubscriptionCandidate(
                url=url,
                source="youtube",
                title=title,
                detected_feed={
                    "url": url,
                    "type": "youtube",
                    "title": title,
                    "format": "youtube",
                },
            )

        author_url, author_name = self._resolve_youtube_author_url(url, content=content)
        if not author_url:
            return None
        title = author_name or page_title or content.title or "YouTube"
        return FeedSubscriptionCandidate(
            url=author_url,
            source="youtube_oembed_author",
            title=title,
            detected_feed={
                "url": author_url,
                "type": "youtube",
                "title": title,
                "format": "youtube",
            },
        )

    def _resolve_youtube_author_url(
        self,
        url: str,
        *,
        content: Content,
    ) -> tuple[str | None, str | None]:
        oembed_url = "https://www.youtube.com/oembed?" + urlencode({"url": url, "format": "json"})
        try:
            body, _headers = get_http_gateway().fetch_content(oembed_url)
            if isinstance(body, bytes):
                body = body.decode("utf-8", errors="ignore")
            if not isinstance(body, str):
                return None, None
            payload = json.loads(body)
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "YouTube oEmbed author lookup failed for feed subscription: %s",
                exc,
                extra={
                    "component": "feed_subscription",
                    "operation": "youtube_oembed_author_lookup",
                    "item_id": content.id,
                    "context_data": {"url": url, "error": str(exc)},
                },
            )
            return None, None

        author_url = payload.get("author_url")
        author_name = payload.get("author_name")
        if not isinstance(author_url, str) or not author_url.strip():
            return None, None
        if not _is_youtube_subscribable_url(author_url):
            return None, None
        return author_url.strip(), author_name if isinstance(author_name, str) else None

    def _build_apple_podcast_candidate(
        self,
        *,
        url: str,
        content: Content,
    ) -> FeedSubscriptionCandidate | None:
        host = _normalize_hostname(urlparse(url).hostname)
        if host not in {"podcasts.apple.com", "itunes.apple.com", "music.apple.com"}:
            return None
        try:
            resolution = resolve_apple_podcast_episode(url)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Apple Podcasts feed resolution failed for feed subscription: %s",
                exc,
                extra={
                    "component": "feed_subscription",
                    "operation": "apple_podcast_resolution",
                    "item_id": content.id,
                    "context_data": {"url": url, "error": str(exc)},
                },
            )
            return None

        if not resolution.feed_url:
            return None
        return FeedSubscriptionCandidate(
            url=resolution.feed_url,
            source="apple_podcasts",
            title=resolution.episode_title or content.title,
        )

    def _build_x_tweet_candidates(
        self,
        *,
        db,
        content: Content,
        metadata: dict[str, Any],
        url: str,
    ) -> CandidateBuildResult:
        tweet_id = extract_tweet_id(str(url))
        if not tweet_id:
            return CandidateBuildResult(candidates=[], metadata_updates={})

        submitter_id = metadata.get("submitted_by_user_id")
        access_token = None
        if isinstance(submitter_id, int):
            access_token = get_x_user_access_token(db, user_id=submitter_id)

        tweet_url = canonical_tweet_url(tweet_id)
        hydrated_tweet = hydrate_tweet_from_metadata(metadata, tweet_id=tweet_id)
        tweet = hydrated_tweet.tweet if hydrated_tweet is not None else None
        metadata_updates: dict[str, Any] = {
            "platform": "twitter",
            "discussion_url": tweet_url,
            "tweet_id": tweet_id,
            "tweet_url": tweet_url,
            "tweet_lookup_source": hydrated_tweet.source if hydrated_tweet else "x_api",
        }
        if tweet is None:
            fetch_result = fetch_tweet_by_id(
                tweet_id=tweet_id,
                access_token=access_token,
                telemetry={
                    "feature": "analyze_url",
                    "operation": "analyze_url.fetch_tweet_for_feed_subscription",
                    "content_id": content.id,
                    "user_id": submitter_id if isinstance(submitter_id, int) else None,
                },
            )
            if not fetch_result.success or not fetch_result.tweet:
                metadata_updates["tweet_enrichment"] = {
                    "status": "failed",
                    "reason": "tweet_lookup_failed",
                    "error": fetch_result.error or "Tweet lookup failed",
                }
                return CandidateBuildResult(
                    candidates=[],
                    metadata_updates=metadata_updates,
                    failure_status="tweet_lookup_failed",
                )
            tweet = fetch_result.tweet

        content_id = content.id or 0
        external_urls = normalize_tweet_external_urls(
            tweet.external_urls,
            content_id=content_id,
        )
        linked_tweet_ids = list(tweet.linked_tweet_ids)
        metadata_updates.update(
            {
                "tweet_author": tweet.author_name,
                "tweet_author_username": tweet.author_username,
                "tweet_created_at": tweet.created_at,
                "tweet_like_count": tweet.like_count,
                "tweet_retweet_count": tweet.retweet_count,
                "tweet_reply_count": tweet.reply_count,
                "tweet_text": tweet.text,
                "tweet_processing_text": build_tweet_processing_text(tweet),
                "tweet_external_urls": external_urls,
                "tweet_linked_tweet_ids": linked_tweet_ids,
                "has_video": tweet.has_video,
                "video_duration_ms": tweet.video_duration_ms,
            }
        )

        candidates: list[FeedSubscriptionCandidate] = [
            FeedSubscriptionCandidate(url=candidate_url, source="x_tweet_external_url")
            for candidate_url in external_urls
        ]
        included_tweets_by_id = dict(hydrate_included_tweets_from_metadata(metadata))
        linked_tweets: list[XTweet] = [
            linked_tweet
            for linked_tweet_id in linked_tweet_ids
            if (linked_tweet := included_tweets_by_id.get(linked_tweet_id)) is not None
        ]
        remaining_linked_tweet_ids = [
            linked_tweet_id
            for linked_tweet_id in linked_tweet_ids
            if linked_tweet_id not in included_tweets_by_id
        ]
        if remaining_linked_tweet_ids:
            try:
                fetched_linked_tweets = fetch_tweets_by_ids(
                    tweet_ids=remaining_linked_tweet_ids,
                    access_token=access_token,
                    telemetry={
                        "feature": "analyze_url",
                        "operation": "analyze_url.fetch_linked_tweets_for_feed_subscription",
                        "content_id": content.id,
                        "user_id": submitter_id if isinstance(submitter_id, int) else None,
                    },
                )
                linked_tweets.extend(fetched_linked_tweets)
                included_tweets_by_id.update(
                    {linked_tweet.id: linked_tweet for linked_tweet in fetched_linked_tweets}
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Linked tweet lookup failed for feed subscription %s",
                    tweet.id,
                    extra={
                        "component": "feed_subscription",
                        "operation": "linked_tweet_lookup",
                        "item_id": content.id,
                    },
                )

        for linked_tweet in linked_tweets:
            linked_urls = normalize_tweet_external_urls(
                linked_tweet.external_urls,
                content_id=content_id,
            )
            candidates.extend(
                FeedSubscriptionCandidate(url=linked_url, source="x_linked_tweet_external_url")
                for linked_url in linked_urls
            )

        if candidates:
            return CandidateBuildResult(
                candidates=self._sort_candidates(candidates),
                metadata_updates=metadata_updates,
            )

        resolution = self._tweet_target_resolver.resolve_tweet_target(
            root_tweet=tweet,
            access_token=access_token,
            content_id=content_id,
            included_tweets_by_id=included_tweets_by_id,
        )
        metadata_updates.update(
            {
                "tweet_thread_text": resolution.thread_text,
                "tweet_resolution_source": resolution.resolution_source,
                "tweet_resolution_tweet_id": resolution.resolution_tweet_id,
                "tweet_thread_lookup_status": resolution.thread_lookup_status,
            }
        )
        if not resolution.selected_article_url:
            return CandidateBuildResult(candidates=[], metadata_updates=metadata_updates)

        source = (
            "x_thread_external_url"
            if resolution.resolution_source == "thread_reply"
            else "x_tweet_external_url"
        )
        return CandidateBuildResult(
            candidates=[
                FeedSubscriptionCandidate(
                    url=resolution.selected_article_url,
                    source=source,
                )
            ],
            metadata_updates=metadata_updates,
        )

    def _build_initial_candidates(
        self,
        *,
        db,
        content: Content,
        metadata: dict[str, Any],
        url: str,
    ) -> CandidateBuildResult:
        candidates: list[FeedSubscriptionCandidate] = []
        seen_urls: set[str] = set()
        content_id = content.id or 0
        metadata_updates: dict[str, Any] = {}

        youtube_candidate = self._build_youtube_candidate(
            url=url,
            page_title=content.title,
            content=content,
        )
        if youtube_candidate:
            self._append_candidate(candidates, seen_urls, youtube_candidate, content_id=content_id)

        apple_candidate = self._build_apple_podcast_candidate(url=url, content=content)
        if apple_candidate:
            self._append_candidate(candidates, seen_urls, apple_candidate, content_id=content_id)

        x_result = self._build_x_tweet_candidates(
            db=db,
            content=content,
            metadata=metadata,
            url=url,
        )
        metadata_updates.update(x_result.metadata_updates)
        for candidate in x_result.candidates:
            self._append_candidate(candidates, seen_urls, candidate, content_id=content_id)

        if x_result.failure_status:
            return CandidateBuildResult(
                candidates=self._sort_candidates(candidates),
                metadata_updates=metadata_updates,
                failure_status=x_result.failure_status,
            )

        self._append_candidate(
            candidates,
            seen_urls,
            FeedSubscriptionCandidate(
                url=url,
                source="submitted_url",
                title=content.title,
            ),
            content_id=content_id,
        )
        return CandidateBuildResult(
            candidates=self._sort_candidates(candidates),
            metadata_updates=metadata_updates,
        )
