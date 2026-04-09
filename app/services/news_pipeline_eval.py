"""End-to-end eval helpers for the news-native digest pipeline."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.constants import CONTENT_DIGEST_VISIBILITY_DIGEST_ONLY
from app.core.logging import get_logger
from app.models.contracts import NewsItemStatus, NewsItemVisibilityScope
from app.models.news_digest_models import NewsDigestBulletDraft, NewsDigestHeaderDraft
from app.models.news_pipeline_eval_models import (
    NewsPipelineEvalBulletResult,
    NewsPipelineEvalCase,
    NewsPipelineEvalItemResult,
    NewsPipelineEvalRunResult,
    NewsPipelineEvalSuiteResult,
    NewsPipelineEvalUserContext,
)
from app.models.schema import NewsDigest, NewsItem
from app.models.user import User
from app.services.llm_summarization import ContentSummarizer
from app.services.news_digests import (
    NewsDigestCluster,
    NewsDigestCuratedBulletDraft,
    generate_news_digest_for_user,
    list_digest_bullets_with_sources,
)
from app.services.news_ingestion import (
    NewsItemUpsertInput,
    build_news_item_upsert_input_from_scraped_item,
    upsert_news_item,
)
from app.services.news_processing import process_news_item
from app.testing.postgres_harness import open_temporary_postgres_session

logger = get_logger(__name__)

EVAL_TRIGGER_REASON = "pipeline_eval"
EMPTY_DISCUSSION_PAYLOAD = {"compact_comments": []}
CASE_ID_CLEAN_PATTERN = re.compile(r"[^a-z0-9]+")


@contextmanager
def open_eval_session() -> Generator[Session]:
    """Create an isolated PostgreSQL-backed database session for one eval run."""
    with open_temporary_postgres_session() as session:
        yield session


def load_eval_case(path: Path) -> NewsPipelineEvalCase:
    """Load one eval case from disk."""
    return NewsPipelineEvalCase.model_validate_json(path.read_text(encoding="utf-8"))


def load_eval_cases(paths: list[Path]) -> list[NewsPipelineEvalCase]:
    """Load multiple eval cases."""
    return [load_eval_case(path) for path in paths]


def write_eval_artifact(
    result: NewsPipelineEvalRunResult,
    *,
    artifacts_dir: Path,
) -> Path:
    """Write one eval result artifact to disk."""
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifacts_dir / f"{result.case_id}.json"
    artifact_path.write_text(
        json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return artifact_path


def run_eval_cases(
    *,
    cases: list[NewsPipelineEvalCase],
    allow_summary_generation: bool = False,
    summarizer: ContentSummarizer | None = None,
    curated_bullet_generator: Callable[
        [User, list[NewsDigestCluster]],
        tuple[list[NewsDigestCuratedBulletDraft], bool],
    ]
    | None = None,
    header_draft_generator: Callable[[list[NewsDigestBulletDraft]], NewsDigestHeaderDraft]
    | None = None,
) -> NewsPipelineEvalSuiteResult:
    """Run a list of eval cases and aggregate pass/fail state."""
    results = [
        run_eval_case(
            case=case,
            allow_summary_generation=allow_summary_generation,
            summarizer=summarizer,
            curated_bullet_generator=curated_bullet_generator,
            header_draft_generator=header_draft_generator,
        )
        for case in cases
    ]
    return NewsPipelineEvalSuiteResult(
        case_count=len(results),
        passed=all(result.passed for result in results),
        results=results,
    )


def run_eval_case(
    *,
    case: NewsPipelineEvalCase,
    allow_summary_generation: bool = False,
    summarizer: ContentSummarizer | None = None,
    curated_bullet_generator: Callable[
        [User, list[NewsDigestCluster]],
        tuple[list[NewsDigestCuratedBulletDraft], bool],
    ]
    | None = None,
    header_draft_generator: Callable[[list[NewsDigestBulletDraft]], NewsDigestHeaderDraft]
    | None = None,
) -> NewsPipelineEvalRunResult:
    """Run one eval case through ingest, processing, and digest generation."""
    with open_eval_session() as db:
        user = _resolve_eval_user(db, case.user, case_id=case.case_id)
        ingested_items, created_count, updated_count = _ingest_case_items(
            db,
            case=case,
            user=user,
        )
        item_results = _process_case_items(
            db,
            items=ingested_items,
            mode=case.mode,
            allow_summary_generation=allow_summary_generation,
            summarizer=summarizer,
        )
        digest_result = generate_news_digest_for_user(
            db,
            user_id=user.id,
            trigger_reason=EVAL_TRIGGER_REASON,
            force=True,
            curated_bullet_generator=curated_bullet_generator,
            header_draft_generator=header_draft_generator,
        )
        digest = (
            db.query(NewsDigest).filter(NewsDigest.id == digest_result.digest_id).first()
            if digest_result.digest_id is not None
            else None
        )
        bullets = _build_bullet_results(db, digest_id=digest.id) if digest else []
        citation_validity = _calculate_citation_validity(bullets, item_results)
        result = NewsPipelineEvalRunResult(
            case_id=case.case_id,
            mode=case.mode,
            digest_id=digest.id if digest else None,
            digest_title=digest.title if digest else None,
            digest_summary=digest.summary if digest else None,
            source_count=digest.source_count if digest else 0,
            curated_group_count=digest.group_count if digest else 0,
            ingest_created_count=created_count,
            ingest_updated_count=updated_count,
            processed_count=sum(1 for item in item_results if not item.skipped),
            generated_summary_count=sum(1 for item in item_results if item.generated_summary),
            reused_summary_count=sum(1 for item in item_results if item.used_existing_summary),
            skipped_processing_count=sum(1 for item in item_results if item.skipped),
            failed_processing_count=sum(
                1 for item in item_results if item.final_status == "failed"
            ),
            citation_validity=citation_validity,
            bullets=bullets,
            items=item_results,
        )
    failures = _validate_run_result(case=case, result=result)
    return result.model_copy(update={"failures": failures, "passed": not failures})


def _resolve_eval_user(
    db: Session,
    user_context: NewsPipelineEvalUserContext,
    *,
    case_id: str,
) -> User:
    user = None
    if user_context.user_id is not None:
        user = db.query(User).filter(User.id == user_context.user_id).first()
    if user is None:
        if user_context.user_id is not None and not user_context.create_if_missing:
            raise ValueError(f"User {user_context.user_id} not found for eval case {case_id}")
        clean_case_id = CASE_ID_CLEAN_PATTERN.sub("-", case_id.casefold()).strip("-") or "eval-case"
        user = User(
            id=user_context.user_id,
            apple_id=user_context.apple_id or f"eval.{clean_case_id}",
            email=user_context.email or f"{clean_case_id}@example.com",
            full_name=user_context.full_name or f"Eval {case_id}",
            news_list_preference_prompt=user_context.news_list_preference_prompt,
            is_active=True,
        )
        db.add(user)
        db.flush()
        return user

    user.full_name = user_context.full_name or user.full_name
    if user_context.news_list_preference_prompt is not None:
        user.news_list_preference_prompt = user_context.news_list_preference_prompt
    db.flush()
    return user


def _ingest_case_items(
    db: Session,
    *,
    case: NewsPipelineEvalCase,
    user: User,
) -> tuple[list[NewsItem], int, int]:
    created_count = 0
    updated_count = 0
    ingested_items: list[NewsItem] = []

    if case.input_mode == "scraped_items":
        raw_items = [
            _remap_scraped_item_for_user(item, user_id=user.id)
            for item in case.scraped_items
        ]
        for index, raw_item in enumerate(raw_items, start=1):
            payload = build_news_item_upsert_input_from_scraped_item(raw_item)
            news_item = _create_eval_news_item(
                db,
                payload=payload,
                case_id=case.case_id,
                index=index,
            )
            ingested_items.append(news_item)
            created_count += 1
        db.flush()
        return ingested_items, created_count, updated_count

    for record in case.news_item_records:
        payload = _build_upsert_input_from_record(record, user_id=user.id)
        news_item, was_created = upsert_news_item(db, payload)
        ingested_items.append(news_item)
        created_count += 1
        if was_created:
            updated_count += 1
    db.flush()
    return ingested_items, created_count, updated_count


def _process_case_items(
    db: Session,
    *,
    items: list[NewsItem],
    mode: str,
    allow_summary_generation: bool,
    summarizer: ContentSummarizer | None,
) -> list[NewsPipelineEvalItemResult]:
    results: list[NewsPipelineEvalItemResult] = []
    for item in items:
        if mode == "snapshot" and not allow_summary_generation and _requires_new_summary(item):
            item.status = NewsItemStatus.NEW.value
            db.flush()
            results.append(
                NewsPipelineEvalItemResult(
                    news_item_id=item.id,
                    platform=item.platform,
                    source_label=item.source_label,
                    visibility_scope=item.visibility_scope,
                    final_status=item.status,
                    skipped=True,
                    skipped_reason="missing_existing_summary",
                )
            )
            continue

        processing_result = process_news_item(
            db,
            news_item_id=item.id,
            summarizer=summarizer,
        )
        db.refresh(item)
        results.append(
            NewsPipelineEvalItemResult(
                news_item_id=item.id,
                platform=item.platform,
                source_label=item.source_label,
                visibility_scope=item.visibility_scope,
                final_status=item.status,
                used_existing_summary=processing_result.used_existing_summary,
                generated_summary=processing_result.generated_summary,
                error_message=processing_result.error_message,
            )
        )
    db.flush()
    return results


def _remap_scraped_item_for_user(item: dict[str, Any], *, user_id: int) -> dict[str, Any]:
    remapped = dict(item)
    metadata = dict(remapped.get("metadata") or {})
    metadata = _ensure_discussion_payload(metadata)

    raw_scope = remapped.get("visibility_scope")
    is_user_scoped = raw_scope == "user"
    if metadata.get("digest_visibility") == CONTENT_DIGEST_VISIBILITY_DIGEST_ONLY:
        is_user_scoped = True
    if metadata.get("submitted_by_user_id") is not None:
        is_user_scoped = True
    if remapped.get("owner_user_id") is not None or remapped.get("user_id") is not None:
        is_user_scoped = True

    if is_user_scoped:
        remapped["visibility_scope"] = "user"
        remapped["owner_user_id"] = user_id
        remapped["user_id"] = user_id
        metadata["submitted_by_user_id"] = user_id

    remapped["metadata"] = metadata
    return remapped


def _disambiguate_scraped_item_for_eval(item: dict[str, Any], *, index: int) -> dict[str, Any]:
    """Ensure eval scraped items stay distinct even when they describe the same story."""
    disambiguated = dict(item)
    metadata = dict(disambiguated.get("metadata") or {})
    source_external_id = disambiguated.get("source_external_id")
    if source_external_id:
        disambiguated["source_external_id"] = f"{source_external_id}__eval_{index}"
    else:
        disambiguated["source_external_id"] = f"eval-{index}"
    unique_suffix = f"eval_item={index}"
    for key in ("url",):
        value = disambiguated.get(key)
        if isinstance(value, str) and value:
            separator = "&" if "?" in value else "?"
            disambiguated[key] = f"{value}{separator}{unique_suffix}"
    discussion_url = metadata.get("discussion_url")
    if isinstance(discussion_url, str) and discussion_url:
        separator = "&" if "?" in discussion_url else "?"
        metadata["discussion_url"] = f"{discussion_url}{separator}{unique_suffix}"
    disambiguated["metadata"] = metadata
    return disambiguated


def _create_eval_news_item(
    db: Session,
    *,
    payload: NewsItemUpsertInput,
    case_id: str,
    index: int,
) -> NewsItem:
    """Create a distinct eval news item without running the production dedupe path."""
    ingest_material = {
        "case_id": case_id,
        "index": index,
        "visibility_scope": payload.visibility_scope.value,
        "owner_user_id": payload.owner_user_id,
        "platform": payload.platform,
        "source_type": payload.source_type,
        "source_label": payload.source_label,
        "source_external_id": payload.source_external_id,
        "canonical_item_url": payload.canonical_item_url,
        "canonical_story_url": payload.canonical_story_url,
        "discussion_url": payload.discussion_url,
    }
    ingest_key = hashlib.sha256(
        json.dumps(ingest_material, sort_keys=True, default=str, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    news_item = NewsItem(
        ingest_key=ingest_key,
        visibility_scope=payload.visibility_scope.value,
        owner_user_id=payload.owner_user_id,
        platform=payload.platform,
        source_type=payload.source_type,
        source_label=payload.source_label,
        source_external_id=payload.source_external_id,
        user_scraper_config_id=payload.user_scraper_config_id,
        user_integration_connection_id=payload.user_integration_connection_id,
        canonical_item_url=payload.canonical_item_url,
        canonical_story_url=payload.canonical_story_url,
        article_url=payload.article_url,
        article_title=payload.article_title,
        article_domain=payload.article_domain,
        discussion_url=payload.discussion_url,
        summary_title=payload.summary_title,
        summary_key_points=payload.summary_key_points,
        summary_text=payload.summary_text,
        raw_metadata=payload.raw_metadata,
        status=payload.status.value,
        legacy_content_id=payload.legacy_content_id,
        published_at=payload.published_at,
        ingested_at=payload.ingested_at,
    )
    db.add(news_item)
    db.flush()
    return news_item


def _build_upsert_input_from_record(record: dict[str, Any], *, user_id: int) -> NewsItemUpsertInput:
    raw_metadata = dict(record.get("raw_metadata") or {})
    raw_metadata = _ensure_discussion_payload(raw_metadata)
    visibility_scope = str(record.get("visibility_scope") or "global")
    owner_user_id = user_id if visibility_scope == "user" else None
    if visibility_scope == "user":
        raw_metadata["submitted_by_user_id"] = user_id

    return NewsItemUpsertInput(
        visibility_scope=(
            NewsItemVisibilityScope.USER
            if visibility_scope == NewsItemVisibilityScope.USER.value
            else NewsItemVisibilityScope.GLOBAL
        ),
        owner_user_id=owner_user_id,
        platform=record.get("platform"),
        source_type=record.get("source_type"),
        source_label=record.get("source_label"),
        source_external_id=record.get("source_external_id"),
        user_scraper_config_id=record.get("user_scraper_config_id"),
        user_integration_connection_id=record.get("user_integration_connection_id"),
        canonical_item_url=record.get("canonical_item_url"),
        canonical_story_url=record.get("canonical_story_url"),
        article_url=record.get("article_url"),
        article_title=record.get("article_title"),
        article_domain=record.get("article_domain"),
        discussion_url=record.get("discussion_url"),
        summary_title=record.get("summary_title"),
        summary_key_points=_coerce_key_points(record.get("summary_key_points")),
        summary_text=record.get("summary_text"),
        raw_metadata=raw_metadata,
        status=NewsItemStatus(str(record.get("status") or NewsItemStatus.NEW.value)),
        published_at=_parse_datetime(record.get("published_at")),
        ingested_at=_parse_datetime(record.get("ingested_at")),
        legacy_content_id=record.get("legacy_content_id"),
    )


def _build_bullet_results(
    db: Session,
    *,
    digest_id: int,
) -> list[NewsPipelineEvalBulletResult]:
    rows = list_digest_bullets_with_sources(db, digest_id=digest_id)
    results: list[NewsPipelineEvalBulletResult] = []
    for bullet, items in rows:
        results.append(
            NewsPipelineEvalBulletResult(
                position=bullet.position,
                topic=bullet.topic,
                details=bullet.details,
                news_item_ids=[item.id for item in items],
            )
        )
    return results


def _calculate_citation_validity(
    bullets: list[NewsPipelineEvalBulletResult],
    item_results: list[NewsPipelineEvalItemResult],
) -> float:
    valid_item_ids = {item.news_item_id for item in item_results}
    cited_ids = [news_item_id for bullet in bullets for news_item_id in bullet.news_item_ids]
    if not cited_ids:
        return 0.0
    valid_citation_count = sum(1 for news_item_id in cited_ids if news_item_id in valid_item_ids)
    return valid_citation_count / len(cited_ids)


def _validate_run_result(
    *,
    case: NewsPipelineEvalCase,
    result: NewsPipelineEvalRunResult,
) -> list[str]:
    failures: list[str] = []
    if result.digest_id is None:
        failures.append("Digest was not created")
    if result.failed_processing_count > 0:
        failures.append(f"{result.failed_processing_count} items failed processing")
    if result.digest_id is not None and result.citation_validity < 1.0:
        failures.append(
            f"Citation validity was {result.citation_validity:.2f}; expected 1.00"
        )

    expectations = case.expectations
    if expectations is None:
        return failures

    if expectations.expected_digest_count is not None:
        actual_digest_count = 1 if result.digest_id is not None else 0
        if actual_digest_count != expectations.expected_digest_count:
            failures.append(
                "Expected digest count "
                f"{expectations.expected_digest_count}, got {actual_digest_count}"
            )
    if (
        expectations.minimum_processed_count is not None
        and result.processed_count < expectations.minimum_processed_count
    ):
        failures.append(
            f"Expected at least {expectations.minimum_processed_count} processed items, "
            f"got {result.processed_count}"
        )
    if (
        expectations.minimum_generated_summary_count is not None
        and result.generated_summary_count < expectations.minimum_generated_summary_count
    ):
        failures.append(
            "Expected at least "
            f"{expectations.minimum_generated_summary_count} generated summaries, "
            f"got {result.generated_summary_count}"
        )
    if (
        expectations.minimum_reused_summary_count is not None
        and result.reused_summary_count < expectations.minimum_reused_summary_count
    ):
        failures.append(
            f"Expected at least {expectations.minimum_reused_summary_count} reused summaries, "
            f"got {result.reused_summary_count}"
        )
    if (
        expectations.minimum_bullet_count is not None
        and len(result.bullets) < expectations.minimum_bullet_count
    ):
        failures.append(
            f"Expected at least {expectations.minimum_bullet_count} bullets, "
            f"got {len(result.bullets)}"
        )
    if (
        expectations.required_citation_validity is not None
        and result.citation_validity < expectations.required_citation_validity
    ):
        failures.append(
            "Expected citation validity >= "
            f"{expectations.required_citation_validity:.2f}, "
            f"got {result.citation_validity:.2f}"
        )
    return failures


def _requires_new_summary(item: NewsItem) -> bool:
    if item.summary_title or item.summary_text:
        return False
    if isinstance(item.summary_key_points, list) and any(item.summary_key_points):
        return False
    summary = item.raw_metadata.get("summary")
    if isinstance(summary, dict):
        if summary.get("title") or summary.get("summary"):
            return False
        key_points = summary.get("key_points")
        if isinstance(key_points, list) and any(key_points):
            return False
    return True


def _ensure_discussion_payload(metadata: dict[str, Any]) -> dict[str, Any]:
    updated = dict(metadata)
    if not isinstance(updated.get("discussion_payload"), dict):
        updated["discussion_payload"] = dict(EMPTY_DISCUSSION_PAYLOAD)
    return updated


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value
        return value.astimezone(UTC).replace(tzinfo=None)
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(UTC).replace(tzinfo=None)


def _coerce_key_points(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    key_points: list[str] = []
    for raw in value:
        if isinstance(raw, str) and raw.strip():
            key_points.append(raw.strip())
            continue
        if isinstance(raw, dict):
            text = raw.get("text")
            if isinstance(text, str) and text.strip():
                key_points.append(text.strip())
    return key_points
