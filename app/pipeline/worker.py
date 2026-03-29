import asyncio
import re
from datetime import UTC, datetime
from html import unescape
from typing import Any

from sqlalchemy.exc import IntegrityError

from app.core.db import get_db
from app.core.logging import get_logger
from app.core.settings import get_settings
from app.domain.converters import content_to_domain, domain_to_content
from app.models.metadata import ContentData, ContentStatus, ContentType
from app.models.metadata_state import (
    normalize_metadata_shape,
    update_processing_state,
)
from app.models.schema import Content
from app.pipeline.checkout import get_checkout_manager
from app.pipeline.podcast_workers import PodcastDownloadWorker, PodcastTranscribeWorker
from app.pipeline.workflows.content_processing_workflow import ContentProcessingWorkflow
from app.processing_strategies.registry import get_strategy_registry
from app.processing_strategies.youtube_strategy import YouTubeProcessorStrategy
from app.services.content_metadata_merge import refresh_merge_content_metadata
from app.services.exa_client import ExaClientError, exa_get_contents
from app.services.gateways.task_queue_gateway import get_task_queue_gateway
from app.services.http import NonRetryableError, get_http_service
from app.services.llm_summarization import ContentSummarizer, get_content_summarizer
from app.services.queue import TaskType, get_queue_service
from app.utils.dates import parse_date_with_tz
from app.utils.summarization_inputs import (
    build_summarization_payload,
    compute_summarization_input_fingerprint,
)
from app.utils.url_utils import is_http_url, normalize_http_url

logger = get_logger(__name__)
settings = get_settings()
DISCUSSION_PREVIEW_METADATA_KEYS: tuple[str, ...] = ("top_comment", "comment_count")


def get_llm_service() -> ContentSummarizer:
    """Return the shared summarization service."""
    return get_content_summarizer()


class ContentWorker:
    """Unified worker for processing all content types."""

    def __init__(self):
        self.checkout_manager = get_checkout_manager()
        self.http_service = get_http_service()
        self.queue_service = get_queue_service()
        self.queue_gateway = get_task_queue_gateway()
        self.strategy_registry = get_strategy_registry()
        self.podcast_download_worker = PodcastDownloadWorker()
        self.podcast_transcribe_worker = PodcastTranscribeWorker()
        self.processing_workflow = ContentProcessingWorkflow()

    def _mark_article_extraction_failure(
        self,
        content: ContentData,
        extracted_data: dict[str, Any],
        reason: str,
        fallback_text: str | None,
    ) -> None:
        """Update content metadata and status when extraction fails."""
        logger.warning(
            "Marking content %s as failed due to extraction error: %s",
            content.id,
            reason,
        )

        failure_metadata = {
            "extraction_failed": True,
            "extraction_error": reason,
            "extraction_failure_details": fallback_text.strip() if fallback_text else None,
            "content_type": extracted_data.get("content_type", "html"),
            "source": extracted_data.get("source"),
            "final_url_after_redirects": extracted_data.get(
                "final_url_after_redirects", str(content.url)
            ),
            "author": extracted_data.get("author"),
            "publication_date": extracted_data.get("publication_date"),
        }

        # Remove summary/content snapshots so the UI does not render a success state.
        content.metadata.pop("summary", None)
        if "content" in content.metadata:
            content.metadata["content"] = None

        # Merge metadata while omitting empty values.
        content.metadata.update(
            {key: value for key, value in failure_metadata.items() if value not in (None, "", {})}
        )

        content.status = ContentStatus.FAILED
        content.error_message = reason
        content.processed_at = datetime.now(UTC)

    def _mark_non_retryable_failure(self, content: ContentData, reason: str) -> None:
        """Mark content as terminal failure that should not be retried."""
        metadata = dict(content.metadata or {})
        metadata["error"] = reason
        metadata["error_type"] = "non_retryable"
        content.metadata = metadata
        content.status = ContentStatus.FAILED
        content.error_message = reason
        content.processed_at = datetime.now(UTC)

    @staticmethod
    def _should_reuse_existing_summary(
        content: ContentData,
        starting_metadata: dict[str, Any],
    ) -> bool:
        """Return True when processed content already has a matching summary."""
        if not isinstance(starting_metadata.get("summary"), dict):
            return False

        current_payload = build_summarization_payload(content.content_type, content.metadata or {})
        previous_payload = build_summarization_payload(content.content_type, starting_metadata)
        if not current_payload or not previous_payload:
            return False

        current_fingerprint = compute_summarization_input_fingerprint(
            content.content_type,
            current_payload,
        )
        previous_fingerprint = starting_metadata.get("summarization_input_fingerprint")
        if isinstance(previous_fingerprint, str) and previous_fingerprint == current_fingerprint:
            return True

        previous_payload_fingerprint = compute_summarization_input_fingerprint(
            content.content_type,
            previous_payload,
        )
        return previous_payload_fingerprint == current_fingerprint

    @staticmethod
    def _normalize_rss_content_text(raw_rss_content: str) -> str:
        """Convert RSS HTML-ish content into compact plain text."""
        without_scripts = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw_rss_content)
        without_tags = re.sub(r"(?is)<[^>]+>", " ", without_scripts)
        return re.sub(r"\s+", " ", unescape(without_tags)).strip()

    @classmethod
    def _get_rss_fallback_text(cls, metadata: dict[str, Any]) -> str:
        """Return fallback text from RSS payload when present."""
        rss_content = metadata.get("rss_content")
        if not isinstance(rss_content, str) or not rss_content.strip():
            return ""

        normalized = cls._normalize_rss_content_text(rss_content)
        return normalized if normalized else rss_content.strip()

    @staticmethod
    def _normalize_exa_fallback_text(raw_text: str) -> str:
        return re.sub(r"\s+", " ", raw_text).strip()

    def _get_exa_fallback_text(self, url: str) -> str:
        """Return fallback article text from Exa when available."""
        results = exa_get_contents(
            [url],
            max_characters=None,
            livecrawl="always",
            raise_on_error=True,
        )
        if not results:
            return ""

        primary = results[0]
        for candidate in (primary.text, primary.summary):
            if not isinstance(candidate, str) or not candidate.strip():
                continue
            normalized = self._normalize_exa_fallback_text(candidate)
            if normalized:
                return normalized
        return ""

    def process_content(self, content_id: int, worker_id: str) -> bool:
        """
        Process a single content item.

        Returns:
            True if successful, False otherwise
        """
        logger.info(f"Worker {worker_id} processing content {content_id}")

        try:
            enqueue_summarize_task = False
            state_persisted = False
            starting_metadata: dict[str, Any] = {}

            # Get content from database
            with get_db() as db:
                db_content = db.query(Content).filter(Content.id == content_id).first()

                if not db_content:
                    logger.error(f"Content {content_id} not found")
                    return False

                content = content_to_domain(db_content)
                starting_metadata = dict(content.metadata or {})

            # Process based on type
            if content.content_type in {ContentType.ARTICLE, ContentType.NEWS}:
                success = self._process_article(content)
            elif content.content_type == ContentType.PODCAST:
                success = self._process_podcast(content)
            else:
                logger.error(f"Unknown content type: {content.content_type}")
                self._mark_non_retryable_failure(
                    content,
                    f"Unknown content type: {content.content_type}",
                )
                success = False

            transition = self.processing_workflow.infer_transition(content=content, success=success)
            content.metadata = update_processing_state(
                normalize_metadata_shape(content.metadata),
                workflow_from=transition.from_status.value,
                workflow_to=transition.to_status.value,
                workflow_transition=transition.reason,
            )

            if not success and content.status not in self.processing_workflow.TERMINAL_STATUSES:
                content.status = ContentStatus.FAILED

            if success:
                enqueue_summarize_task = self.processing_workflow.should_enqueue_summarize(content)
                if enqueue_summarize_task and self._should_reuse_existing_summary(
                    content,
                    starting_metadata,
                ):
                    current_payload = build_summarization_payload(
                        content.content_type,
                        content.metadata or {},
                    )
                    if current_payload:
                        content.metadata["summarization_input_fingerprint"] = (
                            compute_summarization_input_fingerprint(
                                content.content_type,
                                current_payload,
                            )
                        )
                    content.status = ContentStatus.COMPLETED
                    content.processed_at = datetime.now(UTC)
                    enqueue_summarize_task = False
                    logger.info(
                        "Skipping summarize enqueue for content %s; summarization input unchanged",
                        content.id,
                    )

            # Update database when processing succeeded or content was marked failed/skipped.
            if success or content.status in self.processing_workflow.TERMINAL_STATUSES:
                with get_db() as db:
                    db_content = db.query(Content).filter(Content.id == content_id).first()
                    if db_content:
                        content.metadata = refresh_merge_content_metadata(
                            db,
                            content_id=content.id,
                            base_metadata=starting_metadata,
                            updated_metadata=content.metadata,
                            latest_metadata=db_content.content_metadata,
                            preserve_latest_keys=DISCUSSION_PREVIEW_METADATA_KEYS,
                        )
                        domain_to_content(content, db_content)
                        try:
                            db.commit()
                            state_persisted = True
                        except IntegrityError as exc:
                            db.rollback()
                            if self._handle_canonical_integrity_conflict(content, exc):
                                return True
                            raise

            if enqueue_summarize_task and state_persisted and content.id is not None:
                self.queue_gateway.enqueue(TaskType.SUMMARIZE, content_id=content.id)
                logger.info(
                    "Enqueued SUMMARIZE task for content %s (%s)",
                    content.id,
                    content.content_type.value,
                )

            return success

        except Exception as e:
            if isinstance(e, IntegrityError):
                candidate_content = locals().get("content")
                if isinstance(candidate_content, ContentData):
                    try:
                        if self._handle_canonical_integrity_conflict(candidate_content, e):
                            return True
                    except Exception:  # noqa: BLE001
                        logger.exception(
                            "Failed to handle canonical URL integrity conflict for content %s",
                            content_id,
                        )
            logger.exception(
                "Error processing content %s: %s",
                content_id,
                e,
                extra={
                    "component": "content_worker",
                    "operation": "process_content",
                    "item_id": str(content_id),
                    "context_data": {"worker_id": worker_id, "content_id": content_id},
                },
            )
            return False

    def _process_article(self, content: ContentData) -> bool:
        """Process article content."""
        try:
            target_url = self._resolve_article_url(content)

            # Get processing strategy first (before downloading)
            strategy = self.strategy_registry.get_strategy(target_url)
            if not strategy:
                logger.error(f"No strategy for URL: {target_url}")
                return False

            logger.info(f"Using {strategy.__class__.__name__} for {target_url}")

            # Preprocess URL if needed
            processed_url = strategy.preprocess_url(target_url)

            # Download content using strategy (HTML strategy uses crawl4ai)
            try:
                # Handle async methods from YouTubeStrategy
                if asyncio.iscoroutinefunction(strategy.download_content):
                    raw_content = asyncio.run(strategy.download_content(processed_url))
                else:
                    raw_content = strategy.download_content(processed_url)
            except NonRetryableError as e:
                logger.warning(f"Non-retryable error for {processed_url}: {e}")
                self._mark_non_retryable_failure(content, str(e))
                return False

            # Extract data using strategy
            try:
                # Handle async methods from YouTubeStrategy
                if asyncio.iscoroutinefunction(strategy.extract_data):
                    extracted_data = asyncio.run(strategy.extract_data(raw_content, processed_url))
                else:
                    extracted_data = strategy.extract_data(raw_content, processed_url)
            except NonRetryableError as e:
                logger.warning(
                    "Non-retryable extraction error for %s: %s",
                    processed_url,
                    e,
                )
                self._mark_non_retryable_failure(content, str(e))
                return False

            # Check if this is a delegation case (e.g., from PubMed)
            delegated_url = extracted_data.get("next_url_to_process")
            if delegated_url:
                logger.info("Delegation detected. Processing next URL: %s", delegated_url)
                # Update the URL and process recursively
                if content.source_url is None:
                    content.source_url = str(content.url)
                content.url = delegated_url
                return self._process_article(content)

            existing_metadata = content.metadata or {}
            gate_page_detected = bool(extracted_data.get("gate_page_detected"))
            gate_page_reason = extracted_data.get("extraction_error")
            if gate_page_detected:
                exa_fallback_text = ""
                try:
                    exa_fallback_text = self._get_exa_fallback_text(str(content.url))
                except ExaClientError as exc:
                    logger.error(
                        "Access gate Exa fallback failed for content %s",
                        content.id,
                        extra={
                            "component": "content_worker",
                            "operation": "process_article",
                            "item_id": str(content.id),
                            "context_data": {
                                "url": str(content.url),
                                "gate_reason": gate_page_reason,
                                "error": str(exc),
                            },
                        },
                    )

                if exa_fallback_text:
                    logger.warning(
                        "Access gate detected for content %s; using Exa fallback",
                        content.id,
                        extra={
                            "component": "content_worker",
                            "operation": "process_article",
                            "item_id": str(content.id),
                            "context_data": {
                                "url": str(content.url),
                                "fallback_text_len": len(exa_fallback_text),
                                "gate_reason": gate_page_reason,
                            },
                        },
                    )
                    extracted_data["text_content"] = exa_fallback_text
                    extracted_data["used_exa_fallback"] = True
                    extracted_data["exa_fallback_length"] = len(exa_fallback_text)
                    extracted_data["gate_page_reason"] = gate_page_reason
                    extracted_data["extraction_error"] = None

                rss_fallback_text = self._get_rss_fallback_text(existing_metadata)
                if not exa_fallback_text and rss_fallback_text:
                    logger.warning(
                        "Access gate detected for content %s; using rss_content fallback",
                        content.id,
                        extra={
                            "component": "content_worker",
                            "operation": "process_article",
                            "item_id": str(content.id),
                            "context_data": {
                                "url": str(content.url),
                                "fallback_text_len": len(rss_fallback_text),
                                "gate_reason": gate_page_reason,
                            },
                        },
                    )
                    extracted_data["text_content"] = rss_fallback_text
                    extracted_data["used_rss_fallback"] = True
                    extracted_data["rss_fallback_length"] = len(rss_fallback_text)
                    extracted_data["gate_page_reason"] = gate_page_reason
                    extracted_data["extraction_error"] = None
                elif not exa_fallback_text:
                    logger.warning(
                        "Access gate detected for content %s and no rss fallback is available",
                        content.id,
                        extra={
                            "component": "content_worker",
                            "operation": "process_article",
                            "item_id": str(content.id),
                            "context_data": {
                                "url": str(content.url),
                                "gate_reason": gate_page_reason,
                            },
                        },
                    )

            # Prepare for LLM processing
            try:
                # Handle async methods from strategies like YouTubeStrategy
                if asyncio.iscoroutinefunction(strategy.prepare_for_llm):
                    llm_data = asyncio.run(strategy.prepare_for_llm(extracted_data)) or {}
                else:
                    llm_data = strategy.prepare_for_llm(extracted_data) or {}
            except NonRetryableError as e:
                logger.warning(
                    "Non-retryable LLM-prep error for %s: %s",
                    processed_url,
                    e,
                )
                self._mark_non_retryable_failure(content, str(e))
                return False

            # Check if strategy marked this content to be skipped (e.g., images, YouTube auth)
            if extracted_data.get("skip_processing") or llm_data.get("skip_processing"):
                skip_reason = (
                    extracted_data.get("skip_reason")
                    or llm_data.get("skip_reason")
                    or "marked by strategy"
                )
                logger.info(
                    f"Skipping processing for content {content.id}: {skip_reason} "
                    f"({strategy.__class__.__name__})"
                )
                content.status = ContentStatus.SKIPPED
                content.processed_at = datetime.now(UTC)
                # Store minimal metadata
                content.metadata["content_type"] = extracted_data.get("content_type", "unknown")
                content.metadata["image_url"] = extracted_data.get("image_url")
                content.metadata["final_url"] = extracted_data.get("final_url_after_redirects")
                if extracted_data.get("title"):
                    content.title = extracted_data.get("title")
                return True

            # Update content with extracted data
            content.title = extracted_data.get("title") or content.title

            # Build metadata update dict
            final_url = extracted_data.get("final_url_after_redirects") or processed_url
            final_url = str(final_url)

            subscribe_to_feed = bool(existing_metadata.get("subscribe_to_feed"))

            if content.source_url is None:
                content.source_url = str(content.url)

            canonical_url = normalize_http_url(final_url) or str(content.url)
            self._update_canonical_url(content, canonical_url)

            metadata_update = {
                "content": extracted_data.get("text_content", ""),
                "author": extracted_data.get("author"),
                "publication_date": extracted_data.get("publication_date"),
                "content_type": extracted_data.get("content_type", "html"),
                "source": existing_metadata.get("source"),  # Never overwrite source from scraper
            }
            if extracted_data.get("used_rss_fallback"):
                metadata_update["used_rss_fallback"] = True
                metadata_update["rss_fallback_length"] = extracted_data.get("rss_fallback_length")
                if extracted_data.get("gate_page_reason"):
                    metadata_update["gate_page_reason"] = extracted_data.get("gate_page_reason")
            if extracted_data.get("used_exa_fallback"):
                metadata_update["used_exa_fallback"] = True
                metadata_update["exa_fallback_length"] = extracted_data.get("exa_fallback_length")
                if extracted_data.get("gate_page_reason"):
                    metadata_update["gate_page_reason"] = extracted_data.get("gate_page_reason")
            if subscribe_to_feed:
                metadata_update["subscribe_to_feed"] = True

            if content.content_type == ContentType.NEWS:
                article_info = existing_metadata.get("article", {}).copy()
                article_info["url"] = str(content.url)
                if extracted_data.get("title"):
                    article_info["title"] = extracted_data.get("title")
                if metadata_update.get("source"):
                    article_info["source_domain"] = metadata_update.get("source")
                metadata_update["article"] = article_info

            # Do not override platform here; platform should reflect the scraper.

            # Add HackerNews-specific metadata if present
            hn_fields = [
                "hn_score",
                "hn_comments_count",
                "hn_submitter",
                "hn_discussion_url",
                "hn_item_type",
                "hn_linked_url",
                "is_hn_text_post",
            ]
            for field in hn_fields:
                if field in extracted_data:
                    metadata_update[field] = extracted_data[field]

            # Detect feeds for user-submitted content
            feed_links = extracted_data.get("feed_links")
            if feed_links:
                from app.services.feed_detection import FeedDetector

                feed_detector = FeedDetector()
                feed_data = feed_detector.detect_from_links(
                    feed_links,
                    final_url,
                    page_title=extracted_data.get("title"),
                    source=existing_metadata.get("source"),
                    content_type=content.content_type,
                )
                if feed_data:
                    metadata_update["detected_feed"] = feed_data.get("detected_feed")
                    if feed_data.get("all_detected_feeds"):
                        metadata_update["all_detected_feeds"] = feed_data.get("all_detected_feeds")
                    detected = metadata_update.get("detected_feed") or {}
                    if detected.get("url") and detected.get("type"):
                        logger.info(
                            "Detected feed for content %s: %s (type=%s)",
                            content.id,
                            detected.get("url"),
                            detected.get("type"),
                        )

            content.metadata.update(metadata_update)

            if subscribe_to_feed:
                from app.services.feed_subscription import subscribe_to_detected_feed

                detected_feed = metadata_update.get("detected_feed") or existing_metadata.get(
                    "detected_feed"
                )
                submitter_id = existing_metadata.get("submitted_by_user_id")
                subscription_status = "no_feed_found"
                if detected_feed:
                    with get_db() as db:
                        created, subscription_status = subscribe_to_detected_feed(
                            db,
                            submitter_id,
                            detected_feed,
                            display_name=detected_feed.get("title"),
                        )
                    metadata_update["feed_subscription"] = {
                        "status": subscription_status,
                        "feed_url": detected_feed.get("url"),
                        "feed_type": detected_feed.get("type"),
                        "created": created,
                    }
                else:
                    metadata_update["feed_subscription"] = {"status": subscription_status}

                content.metadata.update(metadata_update)
                content.status = ContentStatus.SKIPPED
                content.processed_at = datetime.now(UTC)
                logger.info(
                    "Feed subscription flow completed for content %s (status=%s)",
                    content.id,
                    metadata_update.get("feed_subscription", {}).get("status"),
                )
                return True

            extraction_error = extracted_data.get("extraction_error")
            llm_content = llm_data.get("content_to_summarize")
            llm_content_text = llm_content.strip() if isinstance(llm_content, str) else ""
            text_content = (extracted_data.get("text_content") or "").strip()

            if llm_content_text:
                content.metadata["content_to_summarize"] = llm_content_text

            failure_reason: str | None = None
            if extraction_error:
                failure_reason = extraction_error
            elif not llm_content_text:
                failure_reason = "missing content_to_summarize"
            elif llm_content_text.lower().startswith("failed to extract content"):
                failure_reason = llm_content_text
            elif text_content.lower().startswith("failed to extract content"):
                failure_reason = text_content

            if failure_reason:
                if failure_reason == "missing content_to_summarize":
                    logger.warning(
                        "Missing content_to_summarize for content %s; extracted_text_len=%s",
                        content.id,
                        len(text_content),
                        extra={
                            "component": "content_worker",
                            "operation": "process_article",
                            "item_id": str(content.id),
                            "context_data": {
                                "content_type": content.content_type.value,
                                "url": str(content.url),
                                "extracted_text_len": len(text_content),
                            },
                        },
                    )
                self._mark_article_extraction_failure(
                    content,
                    extracted_data,
                    failure_reason,
                    llm_content_text or text_content,
                )
                return True

            if not llm_data.get("content_to_summarize"):
                logger.error(
                    "No LLM payload generated for content %s; keys=%s",
                    content.id,
                    sorted(llm_data.keys()),
                    extra={
                        "component": "content_worker",
                        "operation": "process_article",
                        "item_id": str(content.id),
                        "context_data": {
                            "content_type": content.content_type.value,
                            "url": str(content.url),
                            "llm_keys": sorted(llm_data.keys()),
                        },
                    },
                )

            # Extract internal URLs for potential future crawling
            internal_urls = strategy.extract_internal_urls(
                extracted_data.get("links", []), final_url
            )
            if internal_urls:
                content.metadata["internal_urls"] = internal_urls

            # Update publication_date from metadata
            pub_date = extracted_data.get("publication_date")
            if pub_date:
                parsed_pub_date = parse_date_with_tz(pub_date)
                if parsed_pub_date:
                    content.publication_date = parsed_pub_date
                else:
                    logger.warning("Could not parse publication date: %s", pub_date)
                    content.publication_date = content.created_at
            else:
                # Fallback to created_at if no publication date
                content.publication_date = content.created_at

            # Update status - keep as PROCESSING, SUMMARIZE task will set COMPLETED
            content.status = ContentStatus.PROCESSING
            content.processed_at = datetime.now(UTC)

            logger.info(
                "Extracted article %s [%s], awaiting summarization. Title: %s...",
                content.id,
                strategy.__class__.__name__,
                content.title[:50] if content.title else "No title",
            )

            return True

        except Exception as e:
            logger.exception(
                "Error processing article %s: %s",
                content.url,
                e,
                extra={
                    "component": "content_worker",
                    "operation": "process_article",
                    "item_id": str(content.id),
                    "context_data": {
                        "url": str(content.url),
                        "content_type": content.content_type.value,
                    },
                },
            )
            return False

    def _resolve_article_url(self, content: ContentData) -> str:
        """Select the best URL to fetch when processing an article/news item."""

        base_url = str(content.url)

        if content.content_type != ContentType.NEWS:
            return base_url

        metadata = content.metadata or {}
        platform = (metadata.get("platform") or content.platform or "").lower()

        candidate_urls: list[str | None] = []

        if is_http_url(base_url):
            return self._normalize_target_url(base_url)

        article_info = metadata.get("article", {})
        candidate_urls.append(article_info.get("url"))

        if platform == "hackernews":
            aggregator_meta = metadata.get("aggregator", {})
            candidate_urls.append(aggregator_meta.get("metadata", {}).get("hn_linked_url"))

        candidate_urls.extend(
            [
                metadata.get("primary_article_url"),
                metadata.get("primary_url"),
                metadata.get("url"),
            ]
        )

        for candidate in candidate_urls:
            normalized = normalize_http_url(candidate) if isinstance(candidate, str) else None
            if normalized:
                return normalized

        return base_url

    @staticmethod
    def _normalize_target_url(url: str) -> str:
        normalized = url.strip()
        if normalized.startswith("http://"):
            normalized = "https://" + normalized[len("http://") :]
        return normalized

    def _update_canonical_url(self, content: ContentData, canonical_url: str) -> None:
        """Update content.url to canonical_url if safe and unique."""
        if not is_http_url(canonical_url):
            return

        current_url = str(content.url)
        if canonical_url == current_url:
            return

        with get_db() as db:
            existing_row = (
                db.query(Content.id)
                .filter(Content.id != content.id)
                .filter(Content.content_type == content.content_type.value)
                .filter(Content.url == canonical_url)
                .first()
            )

        existing_id = int(existing_row[0]) if existing_row else None
        if existing_id is not None:
            content.metadata["canonical_content_id"] = existing_id
            logger.warning(
                "Canonical URL already exists for content %s -> %s (existing=%s)",
                content.id,
                canonical_url,
                existing_id,
            )
            return

        content.url = canonical_url

    def _handle_canonical_integrity_conflict(
        self,
        content: ContentData,
        exc: IntegrityError,
    ) -> bool:
        """Handle canonical URL uniqueness conflicts as terminal, non-retryable outcomes."""
        error_text = str(exc).lower()
        looks_like_canonical_conflict = (
            "contents.url, contents.content_type" in error_text
            or (
                "duplicate key value violates unique constraint" in error_text
                and "contents" in error_text
                and "url" in error_text
                and "content_type" in error_text
            )
        )
        if not looks_like_canonical_conflict:
            return False

        duplicate_id: int | None = None
        with get_db() as db:
            duplicate_row = (
                db.query(Content.id)
                .filter(Content.id != content.id)
                .filter(Content.content_type == content.content_type.value)
                .filter(Content.url == str(content.url))
                .first()
            )
            if duplicate_row:
                duplicate_id = int(duplicate_row[0])

            db_content = db.query(Content).filter(Content.id == content.id).first()
            if not db_content:
                logger.warning(
                    "Canonical URL conflict for content %s but row no longer exists",
                    content.id,
                )
                return True

            metadata = dict(db_content.content_metadata or {})
            if duplicate_id is not None:
                metadata["canonical_content_id"] = duplicate_id

            existing_errors = metadata.get("processing_errors")
            processing_errors = existing_errors.copy() if isinstance(existing_errors, list) else []
            processing_errors.append(
                {
                    "stage": "process_content",
                    "reason": "canonical_url_conflict",
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            )
            metadata["processing_errors"] = processing_errors

            db_content.content_metadata = metadata
            db_content.status = ContentStatus.SKIPPED.value
            db_content.error_message = "Canonical URL conflicts with existing content"
            db_content.processed_at = datetime.now(UTC)
            db.commit()

        logger.warning(
            "Marked content %s as skipped due to canonical URL conflict (existing=%s)",
            content.id,
            duplicate_id,
        )
        return True

    def _process_podcast(self, content: ContentData) -> bool:
        """Process podcast content."""
        try:
            # Update content metadata
            if not content.metadata:
                content.metadata = {}

            # Mark as in progress
            content.status = ContentStatus.PROCESSING
            content.processed_at = datetime.now(UTC)

            youtube_strategy = self._resolve_youtube_podcast_strategy(content)
            if youtube_strategy is not None:
                prepared_for_summary = self._prepare_youtube_podcast_for_summary(
                    content=content,
                    strategy=youtube_strategy,
                )
                if prepared_for_summary or content.status == ContentStatus.SKIPPED:
                    return True

            # Save initial state to DB
            with get_db() as db:
                db_content = db.query(Content).filter(Content.id == content.id).first()
                if db_content:
                    domain_to_content(content, db_content)
                    db.commit()

            # Queue download task
            self.queue_gateway.enqueue(TaskType.DOWNLOAD_AUDIO, content_id=content.id)

            logger.info(f"Queued download task for podcast {content.url}")

            return True

        except Exception as e:
            logger.exception(
                "Error processing podcast %s: %s",
                content.url,
                e,
                extra={
                    "component": "content_worker",
                    "operation": "process_podcast",
                    "item_id": str(content.id),
                    "context_data": {
                        "url": str(content.url),
                        "content_type": content.content_type.value,
                    },
                },
            )
            return False

    def _process_podcast_sync(self, content: ContentData) -> bool:
        """Compatibility shim used by legacy tests."""

        return self._process_podcast(content)

    def _resolve_youtube_podcast_strategy(
        self,
        content: ContentData,
    ) -> YouTubeProcessorStrategy | None:
        """Return the YouTube strategy for podcast-style YouTube content."""
        candidates: list[str] = []
        audio_url = content.metadata.get("audio_url")
        if isinstance(audio_url, str) and audio_url.strip():
            candidates.append(audio_url)
        candidates.append(str(content.url))

        for candidate in candidates:
            strategy = self.strategy_registry.get_strategy(candidate)
            if isinstance(strategy, YouTubeProcessorStrategy):
                return strategy
        return None

    def _prepare_youtube_podcast_for_summary(
        self,
        *,
        content: ContentData,
        strategy: YouTubeProcessorStrategy,
    ) -> bool:
        """Populate YouTube podcast metadata so podcasts can summarize before download."""
        target_url = str(
            content.metadata.get("video_url") or content.metadata.get("audio_url") or content.url
        )
        processed_url = strategy.preprocess_url(target_url)

        try:
            if asyncio.iscoroutinefunction(strategy.download_content):
                raw_content = asyncio.run(strategy.download_content(processed_url))
            else:
                raw_content = strategy.download_content(processed_url)

            if asyncio.iscoroutinefunction(strategy.extract_data):
                extracted_data = asyncio.run(strategy.extract_data(raw_content, processed_url))
            else:
                extracted_data = strategy.extract_data(raw_content, processed_url)

            if asyncio.iscoroutinefunction(strategy.prepare_for_llm):
                llm_data = asyncio.run(strategy.prepare_for_llm(extracted_data)) or {}
            else:
                llm_data = strategy.prepare_for_llm(extracted_data) or {}
        except NonRetryableError as exc:
            logger.warning("Non-retryable YouTube extraction error for %s: %s", processed_url, exc)
            self._mark_non_retryable_failure(content, str(exc))
            return False

        if extracted_data.get("skip_processing") or llm_data.get("skip_processing"):
            skip_reason = (
                extracted_data.get("skip_reason")
                or llm_data.get("skip_reason")
                or "marked by strategy"
            )
            content.metadata["youtube_video"] = True
            content.metadata["skip_reason"] = skip_reason
            content.status = ContentStatus.SKIPPED
            content.error_message = skip_reason
            content.processed_at = datetime.now(UTC)
            logger.info("Skipping YouTube podcast %s: %s", content.id, skip_reason)
            return True

        if content.source_url is None:
            content.source_url = str(content.url)

        final_url = str(extracted_data.get("final_url_after_redirects") or processed_url)
        canonical_url = normalize_http_url(final_url) or str(content.url)
        self._update_canonical_url(content, canonical_url)

        if extracted_data.get("title"):
            content.title = extracted_data["title"]

        publication_date = extracted_data.get("publication_date")
        if publication_date:
            parsed_publication_date = parse_date_with_tz(publication_date)
            if parsed_publication_date:
                content.publication_date = parsed_publication_date

        extracted_metadata = extracted_data.get("metadata")
        if isinstance(extracted_metadata, dict):
            metadata_update = dict(extracted_metadata)
            if content.metadata.get("source"):
                metadata_update.pop("source", None)
            content.metadata.update(metadata_update)

        llm_content = llm_data.get("content_to_summarize")
        if isinstance(llm_content, str) and llm_content.strip():
            content.metadata["content_to_summarize"] = llm_content

        llm_filter = llm_data.get("content_to_filter")
        if isinstance(llm_filter, str) and llm_filter.strip():
            content.metadata["content_to_filter"] = llm_filter

        for key, value in {
            "author": extracted_data.get("author"),
            "publication_date": publication_date,
            "thumbnail_url": extracted_data.get("thumbnail_url"),
            "video_id": extracted_data.get("video_id"),
            "platform": "youtube",
            "youtube_video": True,
            "audio_url": content.metadata.get("audio_url") or target_url,
            "video_url": content.metadata.get("video_url") or target_url,
        }.items():
            if value not in (None, "", {}):
                content.metadata[key] = value

        summary_text = content.metadata.get("transcript") or content.metadata.get(
            "content_to_summarize"
        )
        if isinstance(summary_text, str) and summary_text.strip():
            logger.info(
                "Prepared YouTube podcast %s for summarization without audio download",
                content.id,
            )
            return True

        logger.info(
            "YouTube podcast %s still needs audio download after metadata extraction",
            content.id,
        )
        return False
