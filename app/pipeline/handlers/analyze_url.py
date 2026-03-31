"""Analyze URL task handler."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.constants import (
    DEFAULT_INITIAL_FEED_ARTICLE_DOWNLOAD_COUNT,
    SELF_SUBMISSION_SOURCE,
)
from app.core.logging import get_logger
from app.models.metadata import ContentStatus, ContentType
from app.models.metadata_state import normalize_metadata_shape, update_processing_state
from app.models.schema import Content, ProcessingTask
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope, TaskResult
from app.pipeline.workflows.analyze_url_workflow import AnalyzeUrlWorkflow
from app.services.apple_podcasts import resolve_apple_podcast_episode
from app.services.content_analyzer import AnalysisError
from app.services.content_metadata_merge import refresh_merge_content_metadata
from app.services.content_submission import normalize_url
from app.services.feed_backfill import FeedBackfillRequest, backfill_feed_for_config
from app.services.feed_detection import FeedDetector, detect_feeds_from_html
from app.services.feed_subscription import subscribe_to_detected_feed_result
from app.services.gateways.http_gateway import get_http_gateway
from app.services.gateways.llm_gateway import get_llm_gateway
from app.services.instruction_links import create_contents_from_instruction_links
from app.services.long_form_images import enqueue_visible_long_form_image_if_needed
from app.services.queue import TaskType
from app.services.scraper_configs import ensure_inbox_status
from app.services.twitter_share import (
    canonical_tweet_url,
    extract_tweet_id,
)
from app.services.url_detection import (
    infer_content_type_and_platform,
    should_use_llm_analysis,
)
from app.services.x_api import (
    XTweet,
    build_tweet_processing_text,
    fetch_tweet_by_id,
    fetch_tweets_by_ids,
    fetch_user_tweets,
    search_recent_tweets,
)
from app.services.x_integration import get_x_user_access_token

logger = get_logger(__name__)


def _build_analysis_instruction(
    instruction: str | None,
    crawl_links: bool,
) -> str | None:
    """Build the instruction string to send to the content analyzer."""
    cleaned = instruction.strip() if instruction else None
    if cleaned:
        return cleaned
    if not crawl_links:
        return None
    return "Extract relevant links from the submitted page."


def _build_thread_text(tweet_texts: list[str]) -> str:
    """Join tweet/thread text into a single body."""
    cleaned = [text.strip() for text in tweet_texts if isinstance(text, str) and text.strip()]
    return "\n\n".join(cleaned)


def _parse_x_created_at(value: str | None) -> datetime | None:
    """Parse X API timestamps into timezone-aware datetimes."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _is_nonfatal_tweet_lookup_error(error_message: str) -> bool:
    """Return True when tweet lookup failures should degrade gracefully."""
    lowered = error_message.lower()
    return "x_app_bearer_token is required" in lowered


def _build_x_app_auth_error(error_message: str) -> str:
    """Build a clear operator-facing error when X app auth is missing."""
    return (
        "X app-authenticated tweet lookup is unavailable. Configure "
        "X_APP_BEARER_TOKEN (or TWITTER_AUTH_TOKEN) in the runtime environment. "
        f"Details: {error_message}"
    )


@dataclass(frozen=True)
class FlowOutcome:
    """Result for optional analyze-url flows."""

    handled: bool
    success: bool
    error_message: str | None = None
    retryable: bool = True


@dataclass(frozen=True)
class TweetArticleResolution:
    """Resolved article target and thread context for an X share."""

    selected_article_url: str | None
    resolution_source: str
    resolution_tweet_id: str
    thread_text: str
    linked_tweet_ids: list[str]
    thread_lookup_status: str


class FeedSubscriptionFlow:
    """Handle feed subscription requests during URL analysis."""

    def _run_initial_feed_download(
        self,
        *,
        user_id: Any,
        subscription_status: str,
        config_id: int | None,
    ) -> dict[str, object]:
        """Run the one-time initial feed backfill for newly created subscriptions."""
        initial_download: dict[str, object] = {
            "requested_count": DEFAULT_INITIAL_FEED_ARTICLE_DOWNLOAD_COUNT,
            "ran": False,
            "status": "skipped",
            "reason": subscription_status,
        }
        if subscription_status != "created":
            return initial_download
        if not isinstance(user_id, int):
            initial_download["reason"] = "missing_user"
            return initial_download
        if not isinstance(config_id, int):
            initial_download["reason"] = "missing_config_id"
            return initial_download

        initial_download["ran"] = True
        try:
            result = backfill_feed_for_config(
                FeedBackfillRequest(
                    user_id=user_id,
                    config_id=config_id,
                    count=DEFAULT_INITIAL_FEED_ARTICLE_DOWNLOAD_COUNT,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Initial feed download failed for config %s",
                config_id,
                extra={
                    "component": "feed_subscription",
                    "operation": "initial_download",
                    "item_id": config_id,
                    "context_data": {
                        "user_id": user_id,
                        "config_id": config_id,
                        "error": str(exc),
                    },
                },
            )
            initial_download["status"] = "failed"
            initial_download["error"] = str(exc)
            return initial_download

        initial_download.update(
            {
                "status": "completed",
                "config_id": result.config_id,
                "base_limit": result.base_limit,
                "target_limit": result.target_limit,
                "scraped": result.scraped,
                "saved": result.saved,
                "duplicates": result.duplicates,
                "errors": result.errors,
            }
        )
        return initial_download

    def _detect_direct_feed_url(
        self,
        url: str,
        page_title: str | None,
    ) -> dict[str, str] | None:
        """Return detected feed metadata when the submitted URL is already a feed."""
        detector = FeedDetector(use_exa_search=False)
        validated_feed = detector.validate_feed_url(url)
        if not validated_feed:
            return None

        classification = detector.classify_feed_type(
            feed_url=url,
            page_url=url,
            page_title=page_title or validated_feed.get("title"),
            html_content=None,
        )
        return {
            "url": url,
            "type": classification.feed_type,
            "title": validated_feed.get("title") or page_title,
            "format": validated_feed.get("feed_format", "rss"),
        }

    def run(
        self,
        db,
        content: Content,
        metadata: dict[str, Any],
        url: str,
        subscribe_to_feed: bool,
    ) -> FlowOutcome:
        """Process feed subscription and short-circuit if requested."""
        base_metadata = normalize_metadata_shape(metadata)
        metadata = dict(base_metadata)
        if not subscribe_to_feed:
            return FlowOutcome(handled=False, success=True)

        fetch_status = "no_feed_found"
        detected_feed = self._detect_direct_feed_url(url, content.title)
        all_detected_feeds = None
        if not detected_feed:
            html_content: str | None = None
            try:
                http_gateway = get_http_gateway()
                body, _headers = http_gateway.fetch_content(url)
                if isinstance(body, str):
                    html_content = body
            except Exception as exc:  # noqa: BLE001
                fetch_status = "fetch_failed"
                logger.error(
                    "Failed to fetch URL for feed detection: %s",
                    exc,
                    extra={
                        "component": "sequential_task_processor",
                        "operation": "feed_detect_fetch",
                        "item_id": content.id,
                        "context_data": {"url": url, "error": str(exc)},
                    },
                )

            if html_content:
                feed_data = detect_feeds_from_html(
                    html_content,
                    str(url),
                    page_title=content.title,
                    source=SELF_SUBMISSION_SOURCE,
                    content_type=content.content_type,
                )
                if feed_data:
                    detected_feed = feed_data.get("detected_feed")
                    all_detected_feeds = feed_data.get("all_detected_feeds")

        if detected_feed:
            fetch_status = "detected"
        if detected_feed:
            detected_title = detected_feed.get("title")
            resolved_display_name = (
                detected_title.strip()
                if isinstance(detected_title, str) and detected_title.strip()
                else content.title
            )
            processing_updates: dict[str, object] = {"subscribe_to_feed": True}
            processing_updates["detected_feed"] = detected_feed
            if all_detected_feeds:
                processing_updates["all_detected_feeds"] = all_detected_feeds

            subscription_result = subscribe_to_detected_feed_result(
                db,
                metadata.get("submitted_by_user_id"),
                detected_feed,
                display_name=resolved_display_name,
            )
            fetch_status = subscription_result.status
            processing_updates["feed_subscription"] = {
                "status": fetch_status,
                "feed_url": detected_feed.get("url"),
                "feed_type": detected_feed.get("type"),
                "created": subscription_result.created,
                "config_id": subscription_result.config_id,
                "initial_download": self._run_initial_feed_download(
                    user_id=metadata.get("submitted_by_user_id"),
                    subscription_status=subscription_result.status,
                    config_id=subscription_result.config_id,
                ),
            }
        else:
            processing_updates = {"subscribe_to_feed": True}
            processing_updates["feed_subscription"] = {"status": fetch_status}
        metadata = update_processing_state(metadata, **processing_updates)

        content.content_metadata = refresh_merge_content_metadata(
            db,
            content_id=content.id,
            base_metadata=base_metadata,
            updated_metadata=metadata,
        )
        content.status = ContentStatus.SKIPPED.value
        content.processed_at = datetime.now(UTC)
        db.commit()

        logger.info(
            "Feed subscription flow completed for content %s (status=%s)",
            content.id,
            metadata.get("feed_subscription", {}).get("status"),
        )
        return FlowOutcome(handled=True, success=True)


class TwitterShareFlow:
    """Handle tweet URL fanout and metadata enrichment."""

    _THREAD_PAGE_LIMIT = 10
    _THREAD_TWEET_LIMIT = 1000

    def _ensure_existing_article_visible(
        self,
        db,
        *,
        existing: Content,
        submitter_id: int | None,
    ) -> None:
        """Attach an existing article row to the submitting user's inbox when possible."""
        if not submitter_id:
            return

        status_created = ensure_inbox_status(
            db,
            submitter_id,
            existing.id,
            content_type=existing.content_type,
        )
        db.commit()
        if status_created:
            enqueue_visible_long_form_image_if_needed(db, existing)

    def _normalize_candidate_urls(self, urls: list[str], *, content_id: int) -> list[str]:
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
                        "component": "twitter_share",
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
            created_at = _parse_x_created_at(tweet.created_at) or datetime.min.replace(tzinfo=UTC)
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
        root_created_at = _parse_x_created_at(root_tweet.created_at)
        can_use_recent_search = bool(
            root_created_at and root_created_at >= cutoff and root_tweet.author_username
        )
        collected: list[XTweet] = [root_tweet]

        if can_use_recent_search:
            query = (
                f"conversation_id:{root_tweet.conversation_id} "
                f"from:{root_tweet.author_username}"
            )
            try:
                page = search_recent_tweets(query=query, access_token=access_token, max_results=100)
                collected.extend(page.tweets)
                thread_tweets = self._build_same_author_thread(root_tweet, collected)
                for tweet in thread_tweets:
                    normalized_urls = self._normalize_candidate_urls(
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
                        "component": "twitter_share",
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
                normalized_urls = self._normalize_candidate_urls(
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

    def _resolve_article_target(
        self,
        *,
        root_tweet: XTweet,
        access_token: str | None,
        content_id: int,
    ) -> TweetArticleResolution:
        root_urls = self._normalize_candidate_urls(root_tweet.external_urls, content_id=content_id)
        linked_tweet_ids = list(root_tweet.linked_tweet_ids)
        if root_urls:
            return TweetArticleResolution(
                selected_article_url=root_urls[0],
                resolution_source="root_tweet",
                resolution_tweet_id=root_tweet.id,
                thread_text=_build_thread_text([build_tweet_processing_text(root_tweet)]),
                linked_tweet_ids=linked_tweet_ids,
                thread_lookup_status="not_needed",
            )

        if linked_tweet_ids:
            try:
                linked_tweets = fetch_tweets_by_ids(
                    tweet_ids=linked_tweet_ids,
                    access_token=access_token,
                )
                for linked_tweet in linked_tweets:
                    linked_urls = self._normalize_candidate_urls(
                        linked_tweet.external_urls,
                        content_id=content_id,
                    )
                    if linked_urls:
                        return TweetArticleResolution(
                            selected_article_url=linked_urls[0],
                            resolution_source="linked_tweet",
                            resolution_tweet_id=linked_tweet.id,
                            thread_text=_build_thread_text([build_tweet_processing_text(root_tweet)]),
                            linked_tweet_ids=linked_tweet_ids,
                            thread_lookup_status="not_needed",
                        )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Linked tweet lookup failed for tweet %s",
                    root_tweet.id,
                    extra={
                        "component": "twitter_share",
                        "operation": "linked_tweet_lookup",
                        "item_id": content_id,
                    },
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
                    "component": "twitter_share",
                    "operation": "thread_lookup",
                    "item_id": content_id,
                },
            )
            selected_article_url = None
            thread_lookup_status = "unavailable"
            resolution_tweet_id = root_tweet.id
            thread_tweets = [root_tweet]
        thread_text = _build_thread_text(
            [build_tweet_processing_text(tweet) for tweet in thread_tweets]
        )
        if selected_article_url:
            return TweetArticleResolution(
                selected_article_url=selected_article_url,
                resolution_source="thread_reply",
                resolution_tweet_id=resolution_tweet_id,
                thread_text=thread_text,
                linked_tweet_ids=linked_tweet_ids,
                thread_lookup_status=thread_lookup_status,
            )

        return TweetArticleResolution(
            selected_article_url=None,
            resolution_source="tweet_only",
            resolution_tweet_id=root_tweet.id,
            thread_text=thread_text
            or _build_thread_text([build_tweet_processing_text(root_tweet)]),
            linked_tweet_ids=linked_tweet_ids,
            thread_lookup_status=thread_lookup_status,
        )

    def run(
        self,
        db,
        content: Content,
        metadata: dict[str, Any],
        url: str,
        task_queue_gateway,
    ) -> FlowOutcome:
        """Process tweet URLs and enrich the original content row."""
        base_metadata = normalize_metadata_shape(metadata)
        metadata = dict(base_metadata)
        tweet_id = extract_tweet_id(str(url))
        is_self_submission = content.source == SELF_SUBMISSION_SOURCE or bool(
            metadata.get("submitted_by_user_id")
        )
        if not tweet_id or not is_self_submission:
            return FlowOutcome(handled=False, success=True)

        tweet_url = canonical_tweet_url(tweet_id)
        submitter_id = metadata.get("submitted_by_user_id")
        access_token = None
        if isinstance(submitter_id, int):
            access_token = get_x_user_access_token(db, user_id=submitter_id)

        fetch_result = fetch_tweet_by_id(tweet_id=tweet_id, access_token=access_token)
        if not fetch_result.success or not fetch_result.tweet:
            error_message = fetch_result.error or "Tweet lookup failed"
            if _is_nonfatal_tweet_lookup_error(error_message):
                setup_error = _build_x_app_auth_error(error_message)
                logger.warning(
                    "Twitter share enrichment failed due to missing app auth token",
                    extra={
                        "component": "twitter_share",
                        "operation": "fetch_tweet",
                        "item_id": content.id,
                        "context_data": {"error": setup_error},
                    },
                )
                metadata = update_processing_state(
                    metadata,
                    tweet_enrichment={
                        "status": "failed",
                        "reason": "x_app_auth_unavailable",
                        "error": setup_error,
                    },
                )
                content.content_metadata = refresh_merge_content_metadata(
                    db,
                    content_id=content.id,
                    base_metadata=base_metadata,
                    updated_metadata=metadata,
                )
                content.status = ContentStatus.FAILED.value
                content.error_message = setup_error
                content.processed_at = datetime.now(UTC)
                db.commit()
                return FlowOutcome(
                    handled=True,
                    success=False,
                    error_message=setup_error,
                    retryable=False,
                )

            logger.error(
                "Twitter share fetch failed: %s",
                error_message,
                extra={
                    "component": "twitter_share",
                    "operation": "fetch_tweet",
                    "item_id": content.id,
                },
            )
            content.status = ContentStatus.FAILED.value
            content.error_message = error_message
            db.commit()
            return FlowOutcome(handled=True, success=False, error_message=error_message)

        tweet = fetch_result.tweet
        processing_text = build_tweet_processing_text(tweet)
        resolution = self._resolve_article_target(
            root_tweet=tweet,
            access_token=access_token,
            content_id=content.id,
        )
        external_urls = self._normalize_candidate_urls(tweet.external_urls, content_id=content.id)

        metadata.update(
            {
                "platform": "twitter",
                "discussion_url": tweet_url,
                "tweet_id": tweet_id,
                "tweet_url": tweet_url,
                "tweet_author": tweet.author_name,
                "tweet_author_username": tweet.author_username,
                "tweet_created_at": tweet.created_at,
                "tweet_like_count": tweet.like_count,
                "tweet_retweet_count": tweet.retweet_count,
                "tweet_reply_count": tweet.reply_count,
                "tweet_text": tweet.text,
                "tweet_thread_text": resolution.thread_text,
                "tweet_processing_text": processing_text,
                "tweet_external_urls": external_urls,
                "tweet_linked_tweet_ids": resolution.linked_tweet_ids,
                "tweet_resolution_source": resolution.resolution_source,
                "tweet_resolution_tweet_id": resolution.resolution_tweet_id,
                "tweet_thread_lookup_status": resolution.thread_lookup_status,
            }
        )
        if tweet.article_title:
            metadata["tweet_article_title"] = tweet.article_title
            if not content.title:
                content.title = tweet.article_title[:500]
        if tweet.article_text:
            metadata["tweet_article_text"] = tweet.article_text
        if tweet.note_tweet_text:
            metadata["tweet_note_tweet_text"] = tweet.note_tweet_text

        content.content_type = ContentType.ARTICLE.value
        content.platform = "twitter"
        if not content.source_url:
            content.source_url = tweet_url

        primary_external_url = resolution.selected_article_url
        existing_primary_article: Content | None = None
        if primary_external_url:
            existing_primary_article = (
                db.query(Content)
                .filter(
                    Content.url == primary_external_url,
                    Content.content_type == ContentType.ARTICLE.value,
                )
                .first()
            )
            if existing_primary_article:
                self._ensure_existing_article_visible(
                    db,
                    existing=existing_primary_article,
                    submitter_id=submitter_id if isinstance(submitter_id, int) else None,
                )
                metadata["canonical_content_id"] = existing_primary_article.id
                content.url = tweet_url
                content.status = ContentStatus.SKIPPED.value
                content.error_message = "Canonical URL conflicts with existing content"
                content.processed_at = datetime.now(UTC)
            else:
                content.url = primary_external_url
        else:
            content.url = tweet_url
            if not tweet.article_text and not tweet.note_tweet_text:
                metadata = update_processing_state(metadata, tweet_only=True)

        content.content_metadata = refresh_merge_content_metadata(
            db,
            content_id=content.id,
            base_metadata=base_metadata,
            updated_metadata=metadata,
        )
        db.commit()

        logger.info(
            "Twitter share processed for content %s (external_urls=%s)",
            content.id,
            len(external_urls),
            extra={
                "component": "twitter_share",
                "operation": "analyze_url",
                "item_id": content.id,
            },
        )

        return FlowOutcome(handled=True, success=True)


class UrlAnalysisFlow:
    """Handle platform and content type analysis."""

    def run(
        self,
        db,
        content: Content,
        metadata: dict[str, Any],
        url: str,
        analysis_instruction: str | None,
    ) -> Any | None:
        """Perform URL analysis with pattern matching or LLM analysis."""
        base_metadata = normalize_metadata_shape(metadata)
        metadata = dict(base_metadata)
        platform_hint = metadata.get("platform_hint")
        if not isinstance(platform_hint, str):
            platform_hint = None
        use_llm = should_use_llm_analysis(url) or bool(analysis_instruction)
        if not use_llm:
            detected_type, platform = infer_content_type_and_platform(url, None, platform_hint)
            logger.info(
                "Pattern-based detection for %s: type=%s, platform=%s",
                content.id,
                detected_type.value,
                platform,
            )

            content.content_type = detected_type.value
            if platform:
                content.platform = platform
                metadata["platform"] = platform
            if platform == "apple_podcasts":
                resolution = resolve_apple_podcast_episode(url)
                if resolution.feed_url:
                    metadata.setdefault("feed_url", resolution.feed_url)
                if resolution.episode_title:
                    metadata.setdefault("episode_title", resolution.episode_title)
                    if not content.title:
                        content.title = resolution.episode_title
                if resolution.audio_url:
                    metadata.setdefault("audio_url", resolution.audio_url)
            if platform == "youtube" and detected_type == ContentType.PODCAST:
                metadata.setdefault("audio_url", url)
                metadata.setdefault("video_url", url)
                metadata.setdefault("youtube_video", True)

            content.content_metadata = refresh_merge_content_metadata(
                db,
                content_id=content.id,
                base_metadata=base_metadata,
                updated_metadata=metadata,
            )
            db.commit()
            return None

        llm_gateway = get_llm_gateway()
        result = llm_gateway.analyze_url(url, instruction=analysis_instruction)

        if isinstance(result, AnalysisError):
            logger.warning(
                "LLM analysis failed for %s, using pattern detection: %s",
                content.id,
                result.message,
            )
            detected_type, platform = infer_content_type_and_platform(url, None, platform_hint)
            content.content_type = detected_type.value
            if platform:
                content.platform = platform
                metadata["platform"] = platform
            if platform == "youtube" and detected_type == ContentType.PODCAST:
                metadata.setdefault("audio_url", url)
                metadata.setdefault("video_url", url)
                metadata.setdefault("youtube_video", True)
        else:
            analysis = result.analysis
            if analysis.content_type == "article":
                content.content_type = ContentType.ARTICLE.value
            elif analysis.content_type in ("podcast", "video"):
                content.content_type = ContentType.PODCAST.value
            else:
                content.content_type = ContentType.ARTICLE.value

            if analysis.platform:
                content.platform = analysis.platform
                metadata["platform"] = analysis.platform
            if analysis.media_url:
                metadata["audio_url"] = analysis.media_url
            if analysis.media_format:
                metadata["media_format"] = analysis.media_format
            if analysis.title:
                metadata["extracted_title"] = analysis.title
                if not content.title:
                    content.title = analysis.title
            if analysis.description:
                metadata["extracted_description"] = analysis.description
            if analysis.duration_seconds:
                metadata["duration"] = analysis.duration_seconds
            if analysis.content_type == "video":
                metadata["is_video"] = True
                metadata["video_url"] = url
            if (
                analysis.platform == "youtube"
                and content.content_type == ContentType.PODCAST.value
                and "audio_url" not in metadata
            ):
                metadata["audio_url"] = url
                metadata.setdefault("video_url", url)
                metadata.setdefault("youtube_video", True)

            logger.info(
                "LLM analysis complete for %s: type=%s, platform=%s",
                content.id,
                content.content_type,
                content.platform,
            )

        content.content_metadata = refresh_merge_content_metadata(
            db,
            content_id=content.id,
            base_metadata=base_metadata,
            updated_metadata=metadata,
        )
        db.commit()
        return result if not isinstance(result, AnalysisError) else None


class InstructionLinkFanout:
    """Create follow-up content from instruction links."""

    def run(self, db, content: Content, analysis_result: Any) -> None:
        """Create content records from instruction links."""
        created_ids = create_contents_from_instruction_links(
            db,
            content,
            analysis_result.instruction.links,
        )
        if created_ids:
            logger.info(
                "Created %d content records from instruction links for %s",
                len(created_ids),
                content.id,
            )


class InstructionPayloadCleaner:
    """Remove instruction payload from the task after processing."""

    def run(self, db, task_id: int) -> None:
        """Clear instruction data from processing task payload."""
        task = db.query(ProcessingTask).filter(ProcessingTask.id == int(task_id)).first()
        if task and isinstance(task.payload, dict) and "instruction" in task.payload:
            updated_payload = dict(task.payload)
            updated_payload.pop("instruction", None)
            task.payload = updated_payload
            db.commit()


class AnalyzeUrlHandler:
    """Handle URL analysis tasks."""

    task_type = TaskType.ANALYZE_URL

    def __init__(self) -> None:
        self._feed_flow = FeedSubscriptionFlow()
        self._twitter_flow = TwitterShareFlow()
        self._analysis_flow = UrlAnalysisFlow()
        self._instruction_fanout = InstructionLinkFanout()
        self._payload_cleaner = InstructionPayloadCleaner()
        self._workflow = AnalyzeUrlWorkflow(
            feed_flow=self._feed_flow,
            twitter_flow=self._twitter_flow,
            analysis_flow=self._analysis_flow,
            instruction_fanout=self._instruction_fanout,
            payload_cleaner=self._payload_cleaner,
        )

    def handle(self, task: TaskEnvelope, context: TaskContext) -> TaskResult:
        """Analyze URL to determine content type, then enqueue processing."""
        content_id = task.content_id or task.payload.get("content_id")
        if not content_id:
            logger.error("No content_id provided for analyze_url task")
            return TaskResult.fail("No content_id provided")

        content_id = int(content_id)
        logger.info("Analyzing URL for content %s", content_id)

        try:
            payload = task.payload or {}
            instruction = payload.get("instruction")
            crawl_links = bool(payload.get("crawl_links"))
            subscribe_to_feed = bool(payload.get("subscribe_to_feed"))
            analysis_instruction = _build_analysis_instruction(instruction, crawl_links)

            return self._workflow.run(
                task=task,
                context=context,
                analysis_instruction=analysis_instruction,
                instruction=instruction,
                crawl_links=crawl_links,
                subscribe_to_feed=subscribe_to_feed,
            )

        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "URL analysis error for content_id %s: %s",
                content_id,
                exc,
                extra={
                    "component": "sequential_task_processor",
                    "operation": "analyze_url",
                    "item_id": content_id,
                    "context_data": {"error": str(exc)},
                },
            )
            return TaskResult.fail(str(exc))
