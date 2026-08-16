"""Source resolution and snapshot helpers for Learning Decks."""

from __future__ import annotations

from hashlib import sha256
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.commands import ingest_content as ingest_content_command
from app.commands.convert_news_to_article import (
    convert_article_url_to_content,
    ensure_article_saved_to_knowledge,
)
from app.models.api.submissions import SubmitContentRequest
from app.models.contracts import (
    ContentStatus,
    ContentType,
    LearningDeckSourceKind,
    TaskStatus,
    TaskType,
)
from app.models.db import Content, LearningDeck, ProcessingTask, User
from app.models.metadata.access import metadata_view
from app.repositories.content_detail_repository import get_visible_content
from app.services.canonical_content_state import finalize_canonical_user_state
from app.services.content_bodies import get_content_body_resolver
from app.services.github_urls import GitHubFileUrl, parse_github_file_url
from app.services.learning_deck_common import (
    LearningDeckError,
    LearningDeckSource,
    LearningDeckSourceNotReady,
    require_int_value,
    require_user_id,
    utcnow,
)
from app.services.news_feed import get_visible_news_item
from app.services.twitter_share import extract_tweet_id
from app.utils.news_titles import get_news_article_title
from app.utils.title_utils import clean_title, resolve_title_candidate
from app.utils.url_utils import is_http_url, normalize_http_url


def resolve_learning_deck_create_source(
    db: Session,
    *,
    current_user: User,
    content_id: int | None,
    news_item_id: int | None,
    url: str | None,
) -> LearningDeckSource:
    """Resolve one create request into a stable deck source."""
    user_id = require_user_id(current_user)
    if content_id is not None:
        content = get_visible_content(
            db,
            user_id=user_id,
            content_id=content_id,
            allowed_statuses={
                ContentStatus.COMPLETED.value,
                ContentStatus.AWAITING_IMAGE.value,
            },
        )
        if content is None:
            raise LearningDeckError("Content not found or not ready", status_code=404)
        body_text = get_content_body_resolver().resolve_text(db, content=content)
        if not body_text or not body_text.strip():
            raise LearningDeckError("Content source text is not available", status_code=409)
        return content_learning_deck_source(content)

    if news_item_id is not None:
        item = get_visible_news_item(db, user_id=user_id, news_item_id=news_item_id)
        if item is None:
            raise LearningDeckError("Fast Read not found", status_code=404)
        article_url = normalize_http_url(item.article_url or item.canonical_story_url)
        if not is_http_url(article_url):
            raise LearningDeckError("No article URL found for Fast Read", status_code=400)
        article, _already_exists = convert_article_url_to_content(
            db,
            article_url=str(article_url),
            title=get_news_article_title(item.raw_metadata) or item.article_title,
            source=item.article_domain,
        )
        if article.id is None:
            raise LearningDeckError("Converted article is missing an id", status_code=500)
        ensure_article_saved_to_knowledge(db, user_id=user_id, content_id=int(article.id))
        return content_learning_deck_source(article)

    if url is not None:
        normalized_url = url.strip()
        github_source = normalize_github_repository_source(normalized_url)
        if github_source is not None:
            return github_source
        submission = ingest_content_command.execute(
            db,
            payload=SubmitContentRequest.model_validate(
                {
                    "url": normalized_url,
                    "save_to_knowledge_and_mark_read": True,
                }
            ),
            current_user=current_user,
            submitted_via="learning_deck",
        ).response
        content = db.query(Content).filter(Content.id == submission.content_id).first()
        if content is None:
            raise LearningDeckError("Submitted content could not be found", status_code=500)
        return content_learning_deck_source(content)

    raise LearningDeckError("Missing Learning Deck source")


def normalize_github_repository_source(url: str) -> LearningDeckSource | None:
    """Return a GitHub repo source for public GitHub URLs, otherwise None."""
    github_file = parse_github_file_url(url)
    if github_file is not None:
        return _github_file_learning_deck_source(github_file)

    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        return None
    if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
        return None
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2:
        raise LearningDeckError("GitHub URL must include owner and repository", status_code=400)
    owner = parts[0]
    repo = parts[1].removesuffix(".git")
    if not owner or not repo:
        raise LearningDeckError("GitHub URL must include owner and repository", status_code=400)
    normalized_url = f"https://github.com/{owner}/{repo}"
    identity = f"github:{owner.lower()}/{repo.lower()}"
    return LearningDeckSource(
        source_kind=LearningDeckSourceKind.GITHUB_REPO,
        source_identity=identity,
        source_url=normalized_url,
        source_content_id=None,
        source_title=f"{owner}/{repo}",
        source_metadata={"owner": owner, "repo": repo},
    )


def _github_file_learning_deck_source(github_file: GitHubFileUrl) -> LearningDeckSource:
    title = f"{github_file.repo_full_name}: {github_file.filename}"
    artifact = {
        "url": github_file.canonical_blob_url,
        "raw_url": github_file.raw_url,
        "path": github_file.path,
        "filename": github_file.filename,
        "ref": github_file.ref,
        "content_type": "pdf" if github_file.is_pdf else None,
    }
    return LearningDeckSource(
        source_kind=LearningDeckSourceKind.GITHUB_REPO,
        source_identity=_github_file_source_identity(github_file),
        source_url=github_file.canonical_blob_url,
        source_content_id=None,
        source_title=title,
        source_metadata={
            "owner": github_file.owner,
            "repo": github_file.repo,
            "repo_url": github_file.repo_url,
            "title": title,
            "linked_artifact": artifact,
        },
    )


def _github_file_source_identity(github_file: GitHubFileUrl) -> str:
    identity = (
        f"github:{github_file.owner.lower()}/{github_file.repo.lower()}:"
        f"file:{github_file.ref}/{github_file.path}"
    )
    if len(identity) <= 512:
        return identity
    digest = sha256(identity.encode("utf-8")).hexdigest()
    return f"github:{github_file.owner.lower()}/{github_file.repo.lower()}:file:{digest}"


def content_learning_deck_source(content: Content) -> LearningDeckSource:
    """Return a normalized deck source for one content row."""
    if content.id is None:
        raise LearningDeckError("Content is missing an id", status_code=500)
    title = content_learning_deck_title(content, fallback=f"Content {content.id}")
    return LearningDeckSource(
        source_kind=LearningDeckSourceKind.CONTENT,
        source_identity=f"content:{int(content.id)}",
        source_url=content.source_url or content.url,
        source_content_id=int(content.id),
        source_title=title,
        source_metadata={"content_type": content.content_type, "status": content.status},
    )


def resolve_canonical_learning_deck_content(db: Session, content: Content) -> Content:
    """Follow persisted canonical-content redirects for a deck source."""
    current = content
    visited_ids: set[int] = set()
    while True:
        current_id = require_int_value(current.id, "Content id")
        if current_id in visited_ids:
            return current
        visited_ids.add(current_id)

        raw_canonical_id = metadata_view(current.content_metadata).processing_flag(
            "canonical_content_id"
        )
        try:
            canonical_id = int(raw_canonical_id)
        except (TypeError, ValueError):
            return _find_ready_unrecorded_duplicate(db, current) or current
        if canonical_id <= 0 or canonical_id in visited_ids:
            return current

        canonical = db.query(Content).filter(Content.id == canonical_id).first()
        if canonical is None:
            return current
        current = canonical


def _find_ready_unrecorded_duplicate(db: Session, content: Content) -> Content | None:
    """Recover a ready duplicate when the redirect write itself rolled back."""
    if content.content_type != ContentType.UNKNOWN.value:
        return None
    content_id = require_int_value(content.id, "Content id")
    latest_analysis_status = (
        db.query(ProcessingTask.status)
        .filter(
            ProcessingTask.content_id == content_id,
            ProcessingTask.task_type == TaskType.ANALYZE_URL.value,
        )
        .order_by(ProcessingTask.id.desc())
        .limit(1)
        .scalar()
    )
    if latest_analysis_status != TaskStatus.FAILED.value:
        return None

    source_urls = [value for value in (content.url, content.source_url) if isinstance(value, str)]
    tweet_ids = {tweet_id for value in source_urls if (tweet_id := extract_tweet_id(value))}
    query = (
        db.query(Content)
        .filter(Content.id != content_id)
        .filter(Content.content_type != ContentType.UNKNOWN.value)
        .filter(
            Content.status.in_([ContentStatus.COMPLETED.value, ContentStatus.AWAITING_IMAGE.value])
        )
    )
    if tweet_ids:
        query = query.filter(
            or_(
                *[
                    column.contains(tweet_id)
                    for tweet_id in sorted(tweet_ids)
                    for column in (Content.url, Content.source_url)
                ]
            )
        )
    else:
        query = query.filter(Content.url.in_(source_urls))

    for candidate in query.order_by(Content.id).all():
        if not tweet_ids:
            return candidate
        candidate_urls = (candidate.url, candidate.source_url)
        if any(extract_tweet_id(str(value)) in tweet_ids for value in candidate_urls if value):
            return candidate
    return None


def rebind_learning_deck_to_canonical_content(
    db: Session,
    *,
    deck: LearningDeck,
    content: Content,
) -> Content:
    """Point a deck at the canonical content row selected by ingestion."""
    canonical = resolve_canonical_learning_deck_content(db, content)
    original_content_id = require_int_value(content.id, "Content id")
    canonical_content_id = require_int_value(canonical.id, "Canonical content id")
    if canonical_content_id == original_content_id:
        return canonical

    finalize_canonical_user_state(
        db,
        loser_content_id=original_content_id,
        winner_content_id=canonical_content_id,
    )
    source = content_learning_deck_source(canonical)
    identity_owner = (
        db.query(LearningDeck.id)
        .filter(
            LearningDeck.user_id == deck.user_id,
            LearningDeck.source_identity == source.source_identity,
            LearningDeck.deleted_at.is_(None),
            LearningDeck.id != deck.id,
        )
        .first()
    )
    if identity_owner is None:
        deck.source_identity = source.source_identity
    deck.source_content_id = canonical_content_id
    deck.source_url = source.source_url
    deck.source_title = source.source_title
    source_metadata = dict(source.source_metadata)
    existing_metadata = deck.source_metadata if isinstance(deck.source_metadata, dict) else {}
    submission = existing_metadata.get("submission")
    if isinstance(submission, dict):
        source_metadata["submission"] = dict(submission)
    deck.source_metadata = source_metadata
    if not usable_learning_deck_title(deck.title):
        deck.title = source.source_title
    deck.updated_at = utcnow()
    db.flush()
    return canonical


def persisted_learning_deck_source(db: Session, deck: LearningDeck) -> LearningDeckSource:
    """Rebuild a generation source from current persisted deck state."""
    if deck.source_kind == LearningDeckSourceKind.CONTENT.value:
        if not deck.source_content_id:
            raise LearningDeckError("Learning Deck content source not found", status_code=404)
        content = db.query(Content).filter(Content.id == deck.source_content_id).first()
        if content is None:
            raise LearningDeckError("Content source not found", status_code=404)
        rebind_learning_deck_to_canonical_content(db, deck=deck, content=content)
    try:
        source_kind = LearningDeckSourceKind(str(deck.source_kind))
    except ValueError as exc:
        raise LearningDeckError(
            f"Unsupported Learning Deck source kind: {deck.source_kind}",
            status_code=409,
        ) from exc
    return LearningDeckSource(
        source_kind=source_kind,
        source_identity=str(deck.source_identity),
        source_url=deck.source_url,
        source_content_id=deck.source_content_id,
        source_title=deck.source_title or deck.title or "Learning Deck",
        source_metadata=(
            dict(deck.source_metadata) if isinstance(deck.source_metadata, dict) else {}
        ),
    )


def build_content_source_snapshot_for_deck(
    db: Session,
    *,
    deck: LearningDeck | None,
) -> dict[str, Any]:
    """Build source text snapshot for a content-backed deck."""
    if deck is None or not deck.source_content_id:
        raise LearningDeckError("Learning Deck content source not found", status_code=404)
    content = db.query(Content).filter(Content.id == deck.source_content_id).first()
    if content is None:
        raise LearningDeckError("Content source not found", status_code=404)
    content = rebind_learning_deck_to_canonical_content(db, deck=deck, content=content)
    body_text = get_content_body_resolver().resolve_text(db, content=content)
    if not body_text or not body_text.strip():
        if content.status != ContentStatus.COMPLETED.value:
            raise LearningDeckSourceNotReady("Source content is still processing")
        raise LearningDeckSourceNotReady("Source text is not available yet")
    content_id = require_int_value(content.id, "Content id")
    metadata = content.content_metadata if isinstance(content.content_metadata, dict) else {}
    source_title = content_learning_deck_title(content, fallback=deck.source_title or deck.title)
    return {
        "source_kind": LearningDeckSourceKind.CONTENT.value,
        "source_identity": deck.source_identity,
        "source_content_id": content_id,
        "source_url": content.source_url or content.url,
        "source_title": source_title,
        "content_type": content.content_type,
        "metadata": metadata,
        "body_text": body_text,
    }


def build_github_source_snapshot_for_deck(deck: LearningDeck) -> dict[str, Any]:
    """Build a GitHub source snapshot directly from stable deck state."""
    return {
        "source_kind": LearningDeckSourceKind.GITHUB_REPO.value,
        "source_identity": deck.source_identity,
        "source_url": deck.source_url,
        "source_title": deck.source_title,
        "source_metadata": deck.source_metadata if isinstance(deck.source_metadata, dict) else {},
    }


def learning_deck_display_title(db: Session, deck: LearningDeck) -> str:
    """Resolve the strongest user-visible title for a Learning Deck."""
    deck_metadata = deck.source_metadata if isinstance(deck.source_metadata, dict) else {}
    metadata_title = source_metadata_title(deck_metadata)
    content_title: str | None = None
    if deck.source_content_id:
        content = db.query(Content).filter(Content.id == deck.source_content_id).first()
        if content is not None:
            content_title = content_learning_deck_title(content, fallback=None)
    return (
        resolve_title_candidate(
            metadata_title,
            content_title,
            deck.source_title,
            deck.title,
        )
        or "Learning Deck"
    )


def content_learning_deck_title(content: Content, *, fallback: str | None) -> str:
    """Resolve a useful Learning Deck title from content/source metadata."""
    metadata = content.content_metadata if isinstance(content.content_metadata, dict) else {}
    raw_source_metadata = metadata.get("source_metadata")
    source_metadata = raw_source_metadata if isinstance(raw_source_metadata, dict) else {}
    raw_summary = metadata.get("summary")
    summary = raw_summary if isinstance(raw_summary, dict) else {}
    return (
        resolve_title_candidate(
            source_metadata_title(source_metadata),
            metadata.get("extracted_title"),
            summary.get("title"),
            content.title,
            fallback,
        )
        or fallback
        or "Learning Deck"
    )


def source_metadata_title(metadata: dict[str, Any]) -> str | None:
    """Resolve a title from source metadata shape variants."""
    primary_source = metadata.get("primary_source")
    primary_source_title = primary_source.get("title") if isinstance(primary_source, dict) else None
    return resolve_title_candidate(
        metadata.get("paper_title"),
        metadata.get("title"),
        primary_source_title,
    )


def usable_learning_deck_title(value: Any) -> bool:
    """Return true when a stored title is not blank or URL-like."""
    return clean_title(value) is not None
