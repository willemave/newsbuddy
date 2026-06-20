"""Analyze URL task handler."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from app.constants import (
    DEFAULT_INITIAL_FEED_ARTICLE_DOWNLOAD_COUNT,
    SELF_SUBMISSION_SOURCE,
)
from app.core.logging import get_logger
from app.models.contracts import ContentStatus, ContentType
from app.models.db import Content, ProcessingTask
from app.models.internal.feed_backfill import FeedBackfillRequest
from app.models.metadata.state import normalize_metadata_shape, update_processing_state
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope, TaskResult
from app.pipeline.workflows.analyze_url_workflow import AnalyzeUrlWorkflow
from app.services import knowledge as knowledge_service
from app.services.apple_podcasts import resolve_apple_podcast_episode
from app.services.content_analyzer import AnalysisError
from app.services.content_metadata_merge import refresh_merge_content_metadata
from app.services.feed_backfill import backfill_feed_for_config
from app.services.feed_subscription import subscribe_to_detected_feed_result
from app.services.feed_subscription_resolution import FeedSubscriptionResolver
from app.services.gateways.llm_gateway import get_llm_gateway
from app.services.instruction_links import create_contents_from_instruction_links
from app.services.queue import TaskType
from app.services.tweet_target_resolution import (
    TweetTargetResolver,
    normalize_tweet_external_urls,
)
from app.services.twitter_share import (
    canonical_tweet_url,
    extract_tweet_id,
)
from app.services.url_detection import (
    PODCAST_HOST_PLATFORMS,
    infer_content_type_and_platform,
    should_use_llm_analysis,
)
from app.services.x_api import (
    build_tweet_processing_text,
    fetch_tweet_by_id,
)
from app.services.x_integration import get_x_user_access_token
from app.services.x_tweet_metadata import (
    hydrate_included_tweets_from_metadata,
    hydrate_tweet_from_metadata,
)

logger = get_logger(__name__)

FEED_BACKFILL_SUPPORTED_TYPES = {"substack", "atom", "podcast_rss"}


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


def _is_nonfatal_tweet_lookup_error(error_message: str) -> bool:
    """Return True when tweet lookup failures should degrade gracefully."""
    lowered = error_message.lower()
    return "x_app_bearer_token is required" in lowered


def _is_spend_cap_tweet_lookup_error(error_message: str) -> bool:
    """Return True when X lookup is blocked by provider quota exhaustion."""
    lowered = error_message.lower()
    return "spendcapreached" in lowered or "spend cap reached" in lowered


def _build_x_app_auth_error(error_message: str) -> str:
    """Build a clear operator-facing error when X app auth is missing."""
    return (
        "X app-authenticated tweet lookup is unavailable. Configure "
        "X_APP_BEARER_TOKEN (or TWITTER_AUTH_TOKEN) in the runtime environment. "
        f"Details: {error_message}"
    )


def _build_x_spend_cap_error(error_message: str) -> str:
    """Build a clear operator-facing error when X API spend is exhausted."""
    return (
        "X tweet lookup is temporarily unavailable because the configured "
        "X account reached its spend cap. "
        f"Details: {error_message}"
    )


@dataclass(frozen=True)
class FlowOutcome:
    """Result for optional analyze-url flows."""

    handled: bool
    success: bool
    error_message: str | None = None
    retryable: bool = True


class FeedSubscriptionFlow:
    """Handle feed subscription requests during URL analysis."""

    def __init__(self, *, resolver: FeedSubscriptionResolver | None = None) -> None:
        self._resolver = resolver or FeedSubscriptionResolver()

    def _run_initial_feed_download(
        self,
        *,
        user_id: Any,
        subscription_status: str,
        config_id: int | None,
        feed_type: str | None,
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
        if feed_type not in FEED_BACKFILL_SUPPORTED_TYPES:
            initial_download["reason"] = f"unsupported_scraper_type:{feed_type}"
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

        resolution = self._resolver.resolve(
            db=db,
            content=content,
            metadata=metadata,
            url=url,
        )
        metadata.update(resolution.metadata_updates)
        if resolution.detected_feed:
            detected_feed = resolution.detected_feed
            detected_title = detected_feed.get("title")
            resolved_display_name = (
                detected_title.strip()
                if isinstance(detected_title, str) and detected_title.strip()
                else content.title
            )
            processing_updates: dict[str, object] = {
                "subscribe_to_feed": True,
                "detected_feed": detected_feed,
            }
            if resolution.source:
                processing_updates["feed_resolution_source"] = resolution.source
            if resolution.source_url:
                processing_updates["feed_resolution_source_url"] = resolution.source_url
            if resolution.all_detected_feeds:
                processing_updates["all_detected_feeds"] = resolution.all_detected_feeds

            subscription_result = subscribe_to_detected_feed_result(
                db,
                metadata.get("submitted_by_user_id"),
                detected_feed,
                display_name=resolved_display_name,
            )
            feed_type = detected_feed.get("type")
            processing_updates["feed_subscription"] = {
                "status": subscription_result.status,
                "feed_url": detected_feed.get("url"),
                "feed_type": feed_type,
                "created": subscription_result.created,
                "config_id": subscription_result.config_id,
                "initial_download": self._run_initial_feed_download(
                    user_id=metadata.get("submitted_by_user_id"),
                    subscription_status=subscription_result.status,
                    config_id=subscription_result.config_id,
                    feed_type=feed_type if isinstance(feed_type, str) else None,
                ),
            }
        else:
            processing_updates = {
                "subscribe_to_feed": True,
                "feed_subscription": {"status": resolution.status},
            }
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


class TweetResolutionFlow:
    """Resolve submitted tweet URLs into canonical long-form content."""

    def __init__(self, *, target_resolver: TweetTargetResolver | None = None) -> None:
        self._target_resolver = target_resolver or TweetTargetResolver()

    def _resolve_target_type(
        self,
        *,
        resolved_url: str | None,
    ) -> tuple[str, str | None]:
        if not resolved_url:
            return ContentType.ARTICLE.value, "twitter"
        hostname = (urlparse(resolved_url).hostname or "").strip().lower()
        if hostname.startswith("www."):
            hostname = hostname[4:]
        podcast_platform = PODCAST_HOST_PLATFORMS.get(hostname)
        if podcast_platform:
            return ContentType.PODCAST.value, podcast_platform
        detected_type, detected_platform = infer_content_type_and_platform(
            resolved_url,
            None,
            None,
        )
        return detected_type.value, detected_platform

    def _save_bookmark_target_for_user(
        self,
        db,
        *,
        content: Content,
        submitter_id: int | None,
    ) -> None:
        """Save a bookmark target without adding it to the long-form inbox."""
        if not submitter_id:
            return

        content_id = content.id
        if content_id is None:
            return
        knowledge_service.save_to_knowledge(db, content_id, submitter_id)

    def run(
        self,
        db,
        content: Content,
        metadata: dict[str, Any],
        url: str,
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
        submitted_via = str(metadata.get("submitted_via") or "").strip().lower()
        is_bookmark_submission = submitted_via == "x_bookmarks"
        access_token = None
        if isinstance(submitter_id, int):
            access_token = get_x_user_access_token(db, user_id=submitter_id)

        hydrated_tweet = hydrate_tweet_from_metadata(metadata, tweet_id=tweet_id)
        tweet = hydrated_tweet.tweet if hydrated_tweet is not None else None
        metadata["tweet_lookup_source"] = hydrated_tweet.source if hydrated_tweet else "x_api"
        if tweet is None:
            fetch_result = fetch_tweet_by_id(
                tweet_id=tweet_id,
                access_token=access_token,
                telemetry={
                    "feature": "analyze_url",
                    "operation": "analyze_url.fetch_tweet",
                    "content_id": content.id,
                    "user_id": submitter_id if isinstance(submitter_id, int) else None,
                },
            )
            if not fetch_result.success or not fetch_result.tweet:
                error_message = fetch_result.error or "Tweet lookup failed"
                setup_error: str | None = None
                enrichment_status = "failed"
                enrichment_reason: str | None = None
                if _is_nonfatal_tweet_lookup_error(error_message):
                    setup_error = _build_x_app_auth_error(error_message)
                    enrichment_reason = "x_app_auth_unavailable"
                elif _is_spend_cap_tweet_lookup_error(error_message):
                    setup_error = _build_x_spend_cap_error(error_message)
                    enrichment_status = "deferred"
                    enrichment_reason = "x_spend_cap_reached"
                if setup_error is not None and enrichment_reason is not None:
                    logger.warning(
                        "Tweet resolution failed before lookup could proceed",
                        extra={
                            "component": "tweet_resolution",
                            "operation": "fetch_tweet",
                            "item_id": content.id,
                            "context_data": {"error": setup_error},
                        },
                    )
                    metadata = update_processing_state(
                        metadata,
                        tweet_enrichment={
                            "status": enrichment_status,
                            "reason": enrichment_reason,
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

                logger.warning(
                    "Tweet resolution failed before lookup could proceed",
                    extra={
                        "component": "tweet_resolution",
                        "operation": "fetch_tweet",
                        "item_id": content.id,
                        "context_data": {"error": error_message},
                    },
                )
                content.status = ContentStatus.FAILED.value
                content.error_message = error_message
                db.commit()
                return FlowOutcome(handled=True, success=False, error_message=error_message)

            tweet = fetch_result.tweet
        processing_text = build_tweet_processing_text(tweet)
        content_id = content.id
        if content_id is None:
            raise ValueError("Tweet resolution flow requires persisted content")
        included_tweets_by_id = hydrate_included_tweets_from_metadata(metadata)
        resolution = self._target_resolver.resolve_tweet_target(
            root_tweet=tweet,
            access_token=access_token,
            content_id=content_id,
            included_tweets_by_id=included_tweets_by_id,
        )
        external_urls = normalize_tweet_external_urls(
            tweet.external_urls,
            content_id=content_id,
        )

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
                "has_video": tweet.has_video,
                "video_duration_ms": tweet.video_duration_ms,
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

        if not content.source_url:
            content.source_url = tweet_url

        primary_external_url = resolution.selected_article_url
        existing_target: Content | None = None
        target_content_type, target_platform = self._resolve_target_type(
            resolved_url=primary_external_url,
        )
        content.content_type = target_content_type
        content.platform = target_platform or "twitter"
        if primary_external_url:
            existing_target = (
                db.query(Content)
                .filter(
                    Content.url == primary_external_url,
                    Content.content_type == target_content_type,
                )
                .first()
            )
            if existing_target:
                self._save_bookmark_target_for_user(
                    db,
                    content=existing_target,
                    submitter_id=submitter_id if is_bookmark_submission else None,
                )
                metadata["canonical_content_id"] = existing_target.id
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
        if is_bookmark_submission:
            self._save_bookmark_target_for_user(
                db,
                content=content,
                submitter_id=submitter_id if isinstance(submitter_id, int) else None,
            )

        logger.info(
            "Tweet URL resolved for content %s (external_urls=%s)",
            content.id,
            len(external_urls),
            extra={
                "component": "tweet_resolution",
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
        result = llm_gateway.analyze_url(
            url,
            instruction=analysis_instruction,
            db=db,
            usage_persist={
                "feature": "content_analyzer",
                "operation": "content_analyzer.analyze_url",
                "source": "queue",
                "content_id": content.id,
                "metadata": {"url": str(url)},
            },
        )

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
        self._tweet_resolution_flow = TweetResolutionFlow()
        self._analysis_flow = UrlAnalysisFlow()
        self._instruction_fanout = InstructionLinkFanout()
        self._payload_cleaner = InstructionPayloadCleaner()
        self._workflow = AnalyzeUrlWorkflow(
            feed_flow=self._feed_flow,
            tweet_resolution_flow=self._tweet_resolution_flow,
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
