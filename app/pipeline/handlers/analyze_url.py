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
from app.models.internal.feed_backfill import BACKFILL_SUPPORTED_TYPES
from app.models.metadata.state import normalize_metadata_shape, update_processing_state
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope, TaskResult
from app.pipeline.workflows.analyze_url_workflow import AnalyzeUrlWorkflow
from app.services.active_users import lock_active_user
from app.services.agent_vm_runtime import resolve_sandbox_user_id
from app.services.apple_podcasts import ApplePodcastResolution, resolve_apple_podcast_episode
from app.services.canonical_content_state import finalize_canonical_user_state
from app.services.content_analyzer import AnalysisError
from app.services.content_metadata_merge import refresh_merge_content_metadata
from app.services.feed_research_runtime import sandboxed_http_service
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
from app.services.x_bookmark_destinations import (
    reconcile_x_bookmark_destinations_for_content_in_session,
)
from app.services.x_integration import get_x_user_access_token
from app.services.x_tweet_metadata import (
    hydrate_included_tweets_from_metadata,
    hydrate_tweet_from_metadata,
)

logger = get_logger(__name__)


def _require_content_id(content: Content) -> int:
    content_id = content.id
    if content_id is None:
        raise ValueError("Analyze URL content must be persisted")
    return int(content_id)


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


def _resolve_apple_podcast_in_feed_sandbox(
    url: str,
    *,
    user_id: object,
    content_id: int | None,
) -> ApplePodcastResolution:
    sandbox_user_id = resolve_sandbox_user_id(user_id)
    with sandboxed_http_service(
        user_id=sandbox_user_id,
        execution_id=content_id,
    ) as http_service:
        return resolve_apple_podcast_episode(
            url,
            feed_fetch=http_service.fetch,
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

    def _build_initial_feed_download(
        self,
        *,
        subscription_status: str,
        config_id: int | None,
        backfill_task_id: int | None,
        feed_type: str | None,
    ) -> dict[str, object]:
        """Describe the durable initial backfill for a new subscription."""
        initial_download: dict[str, object] = {
            "requested_count": DEFAULT_INITIAL_FEED_ARTICLE_DOWNLOAD_COUNT,
            "ran": False,
            "status": "skipped",
            "reason": subscription_status,
        }
        if subscription_status not in {"created", "reactivated"}:
            return initial_download
        if feed_type not in BACKFILL_SUPPORTED_TYPES:
            initial_download["reason"] = f"unsupported_scraper_type:{feed_type}"
            return initial_download
        if not isinstance(config_id, int):
            raise RuntimeError("Activated feed subscription is missing its config id")
        if not isinstance(backfill_task_id, int):
            raise RuntimeError(
                "Activated feed subscription is missing its durable initial backfill task"
            )

        initial_download.update(
            {
                "status": "queued",
                "reason": subscription_status,
                "config_id": config_id,
                "task_id": backfill_task_id,
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
        raw_submitter_id = metadata.get("submitted_by_user_id")
        if raw_submitter_id is not None and lock_active_user(db, raw_submitter_id) is None:
            metadata = update_processing_state(
                metadata,
                subscribe_to_feed=True,
                feed_subscription={"status": "inactive_user"},
            )
            content.content_metadata = refresh_merge_content_metadata(
                db,
                content_id=content.id,
                base_metadata=base_metadata,
                updated_metadata=metadata,
            )
            content.status = ContentStatus.SKIPPED.value
            content.error_message = None
            content.processed_at = datetime.now(UTC)
            db.flush()
            return FlowOutcome(handled=True, success=True)

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
            initial_download = self._build_initial_feed_download(
                subscription_status=subscription_result.status,
                config_id=subscription_result.config_id,
                backfill_task_id=subscription_result.backfill_task_id,
                feed_type=feed_type if isinstance(feed_type, str) else None,
            )
            processing_updates["feed_subscription"] = {
                "status": subscription_result.status,
                "feed_url": detected_feed.get("url"),
                "feed_type": feed_type,
                "created": subscription_result.created,
                "config_id": subscription_result.config_id,
                "backfill_task_id": subscription_result.backfill_task_id,
                "initial_download": initial_download,
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
        subscription_error = (
            resolution.detected_feed is not None
            and subscription_result.status == "subscription_failed"
        )
        content.status = (
            ContentStatus.FAILED.value if subscription_error else ContentStatus.SKIPPED.value
        )
        content.error_message = subscription_result.error_message if subscription_error else None
        content.processed_at = datetime.now(UTC)
        db.flush()
        logger.info(
            "Feed subscription flow completed for content %s (status=%s)",
            content.id,
            metadata.get("feed_subscription", {}).get("status"),
        )
        if subscription_error:
            return FlowOutcome(
                handled=True,
                success=False,
                error_message=subscription_result.error_message or "Feed subscription failed",
                retryable=False,
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
        raw_submitter_id = metadata.get("submitted_by_user_id")
        submitter_id = raw_submitter_id if isinstance(raw_submitter_id, int) else None
        active_submitter_id = lock_active_user(db, submitter_id)
        submitted_via = str(metadata.get("submitted_via") or "").strip().lower()
        is_bookmark_submission = submitted_via == "x_bookmarks"
        if is_bookmark_submission:
            reconcile_x_bookmark_destinations_for_content_in_session(
                db,
                bookmark_content_id=_require_content_id(content),
                fallback_user_id=active_submitter_id,
            )
        access_token = None
        if active_submitter_id is not None:
            access_token = get_x_user_access_token(db, user_id=active_submitter_id)

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
                    "user_id": active_submitter_id,
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
                    db.flush()
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
                db.flush()
                return FlowOutcome(handled=True, success=False, error_message=error_message)

            tweet = fetch_result.tweet
        processing_text = build_tweet_processing_text(tweet)
        content_id = _require_content_id(content)
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
        original_url = str(content.url)
        target_content_type, target_platform = self._resolve_target_type(
            resolved_url=primary_external_url,
        )
        content.platform = target_platform or "twitter"
        target_url = primary_external_url or tweet_url
        existing_target = (
            db.query(Content)
            .filter(
                Content.id != content.id,
                Content.url == target_url,
                Content.content_type == target_content_type,
            )
            .first()
        )
        if existing_target is not None:
            _mark_canonical_analysis_duplicate(
                db,
                content=content,
                metadata=metadata,
                existing_target=existing_target,
            )
            original_url_type_owner = (
                db.query(Content.id)
                .filter(
                    Content.id != content.id,
                    Content.url == original_url,
                    Content.content_type == target_content_type,
                )
                .first()
            )
            if original_url_type_owner is None:
                content.content_type = target_content_type
        else:
            content.content_type = target_content_type
            content.url = target_url

        if primary_external_url is None and not tweet.article_text and not tweet.note_tweet_text:
            metadata = update_processing_state(metadata, tweet_only=True)

        content.content_metadata = refresh_merge_content_metadata(
            db,
            content_id=content.id,
            base_metadata=base_metadata,
            updated_metadata=metadata,
        )
        db.flush()
        if is_bookmark_submission and existing_target is not None:
            reconcile_x_bookmark_destinations_for_content_in_session(
                db,
                bookmark_content_id=_require_content_id(content),
                fallback_user_id=submitter_id,
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
        original_content_type = str(content.content_type)
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
                resolution = _resolve_apple_podcast_in_feed_sandbox(
                    url,
                    user_id=metadata.get("submitted_by_user_id"),
                    content_id=content.id,
                )
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

            _rebind_analysis_duplicate(
                db,
                content=content,
                metadata=metadata,
                original_content_type=original_content_type,
            )
            content.content_metadata = refresh_merge_content_metadata(
                db,
                content_id=content.id,
                base_metadata=base_metadata,
                updated_metadata=metadata,
            )
            db.flush()
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
                "user_id": metadata.get("submitted_by_user_id"),
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

        _rebind_analysis_duplicate(
            db,
            content=content,
            metadata=metadata,
            original_content_type=original_content_type,
        )

        content.content_metadata = refresh_merge_content_metadata(
            db,
            content_id=content.id,
            base_metadata=base_metadata,
            updated_metadata=metadata,
        )
        db.flush()
        return result if not isinstance(result, AnalysisError) else None


def _rebind_analysis_duplicate(
    db,
    *,
    content: Content,
    metadata: dict[str, Any],
    original_content_type: str,
) -> None:
    """Turn a classification collision into a canonical content redirect."""
    with db.no_autoflush:
        existing_target = (
            db.query(Content)
            .filter(
                Content.id != content.id,
                Content.url == content.url,
                Content.content_type == content.content_type,
            )
            .first()
        )
    if existing_target is None:
        return

    content.content_type = original_content_type
    _mark_canonical_analysis_duplicate(
        db,
        content=content,
        metadata=metadata,
        existing_target=existing_target,
    )


def _mark_canonical_analysis_duplicate(
    db,
    *,
    content: Content,
    metadata: dict[str, Any],
    existing_target: Content,
) -> None:
    """Persist one analysis-time canonical redirect and its user overlays."""
    content_id = _require_content_id(content)
    existing_target_id = _require_content_id(existing_target)
    metadata["canonical_content_id"] = existing_target_id
    content.status = ContentStatus.SKIPPED.value
    content.error_message = "Canonical URL conflicts with existing content"
    content.processed_at = datetime.now(UTC)
    finalize_canonical_user_state(
        db,
        loser_content_id=content_id,
        winner_content_id=existing_target_id,
    )


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
            db.flush()


class AnalyzeUrlHandler:
    """Handle URL analysis tasks."""

    task_type = TaskType.ANALYZE_URL

    def __init__(self) -> None:
        self._workflow = AnalyzeUrlWorkflow(
            feed_flow=FeedSubscriptionFlow().run,
            tweet_resolution_flow=TweetResolutionFlow().run,
            analysis_flow=UrlAnalysisFlow().run,
            instruction_fanout=InstructionLinkFanout().run,
            payload_cleaner=InstructionPayloadCleaner().run,
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
