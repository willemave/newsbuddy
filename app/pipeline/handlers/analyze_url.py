"""Analyze URL task handler."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError

from app.constants import SELF_SUBMISSION_SOURCE
from app.core.logging import get_logger
from app.models.metadata import ContentClassification, ContentStatus, ContentType
from app.models.metadata_state import normalize_metadata_shape, update_processing_state
from app.models.schema import Content, ProcessingTask
from app.pipeline.task_context import TaskContext
from app.pipeline.task_models import TaskEnvelope, TaskResult
from app.pipeline.workflows.analyze_url_workflow import AnalyzeUrlWorkflow
from app.services.apple_podcasts import resolve_apple_podcast_episode
from app.services.content_analyzer import AnalysisError
from app.services.content_metadata_merge import refresh_merge_content_metadata
from app.services.content_submission import normalize_url
from app.services.feed_detection import FeedDetector, detect_feeds_from_html
from app.services.feed_subscription import subscribe_to_detected_feed
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
from app.services.x_api import fetch_tweet_by_id
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


class FeedSubscriptionFlow:
    """Handle feed subscription requests during URL analysis."""

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
            processing_updates: dict[str, object] = {"subscribe_to_feed": True}
            processing_updates["detected_feed"] = detected_feed
            if all_detected_feeds:
                processing_updates["all_detected_feeds"] = all_detected_feeds

            created, fetch_status = subscribe_to_detected_feed(
                db,
                metadata.get("submitted_by_user_id"),
                detected_feed,
                display_name=detected_feed.get("title"),
            )
            processing_updates["feed_subscription"] = {
                "status": fetch_status,
                "feed_url": detected_feed.get("url"),
                "feed_type": detected_feed.get("type"),
                "created": created,
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

    def run(
        self,
        db,
        content: Content,
        metadata: dict[str, Any],
        url: str,
        task_queue_gateway,
    ) -> FlowOutcome:
        """Process tweet URLs and enqueue follow-up tasks."""
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
        thread_text = _build_thread_text([tweet.text])
        external_urls: list[str] = []
        for raw_url in tweet.external_urls:
            try:
                external_urls.append(normalize_url(raw_url))
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Skipping invalid tweet external URL: %s",
                    raw_url,
                    extra={
                        "component": "twitter_share",
                        "operation": "normalize_external_url",
                        "item_id": content.id,
                    },
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
                "tweet_thread_text": thread_text,
                "tweet_external_urls": external_urls,
            }
        )

        content.content_type = ContentType.ARTICLE.value
        content.platform = "twitter"
        if not content.source_url:
            content.source_url = tweet_url

        fanout_urls: list[str] = []
        if external_urls:
            content.url = external_urls[0]
            fanout_urls = external_urls[1:]
        else:
            content.url = tweet_url
            metadata = update_processing_state(metadata, tweet_only=True)

        content.content_metadata = refresh_merge_content_metadata(
            db,
            content_id=content.id,
            base_metadata=base_metadata,
            updated_metadata=metadata,
        )
        db.commit()

        submitted_via = metadata.get("submitted_via") or "share_sheet"
        for normalized_url in fanout_urls:
            existing = (
                db.query(Content)
                .filter(
                    Content.url == normalized_url,
                    Content.content_type == ContentType.ARTICLE.value,
                )
                .first()
            )
            if existing:
                if submitter_id:
                    status_created = ensure_inbox_status(
                        db,
                        submitter_id,
                        existing.id,
                        content_type=existing.content_type,
                    )
                    db.commit()
                    if status_created:
                        enqueue_visible_long_form_image_if_needed(db, existing)
                continue

            fanout_metadata = dict(metadata)
            fanout_metadata["source"] = SELF_SUBMISSION_SOURCE
            if submitter_id:
                fanout_metadata["submitted_by_user_id"] = submitter_id
            fanout_metadata["submitted_via"] = f"{submitted_via}_tweet_fanout"

            new_content = Content(
                url=normalized_url,
                source_url=tweet_url,
                content_type=ContentType.ARTICLE.value,
                title=None,
                source=SELF_SUBMISSION_SOURCE,
                platform="twitter",
                is_aggregate=False,
                status=ContentStatus.NEW.value,
                classification=ContentClassification.TO_READ.value,
                content_metadata=fanout_metadata,
            )
            db.add(new_content)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                continue
            db.refresh(new_content)

            if submitter_id:
                status_created = ensure_inbox_status(
                    db,
                    submitter_id,
                    new_content.id,
                    content_type=new_content.content_type,
                )
                db.commit()
                if status_created:
                    enqueue_visible_long_form_image_if_needed(db, new_content)

            task_queue_gateway.enqueue(TaskType.ANALYZE_URL, content_id=new_content.id)

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
