"""Application command for adding discovery item suggestions."""

from __future__ import annotations

from pydantic import HttpUrl, TypeAdapter
from sqlalchemy.orm import Session

from app.commands import ingest_content as ingest_content_command
from app.core.logging import get_logger
from app.models.api.discovery import DiscoveryAddItemRequest, DiscoveryAddItemResponse
from app.models.api.submissions import SubmitContentRequest
from app.models.db.users import User
from app.repositories.discovery_repository import list_user_suggestions_by_ids

logger = get_logger(__name__)

_HTTP_URL_ADAPTER = TypeAdapter(HttpUrl)


def _require_user_id(current_user: User) -> int:
    user_id = current_user.id
    if user_id is None:
        raise ValueError("Current user is missing an id")
    return int(user_id)


def execute(
    db: Session,
    *,
    current_user: User,
    payload: DiscoveryAddItemRequest,
) -> DiscoveryAddItemResponse:
    """Create content from selected single-item discovery suggestions."""
    user_id = _require_user_id(current_user)
    suggestions = list_user_suggestions_by_ids(
        db,
        user_id=user_id,
        suggestion_ids=payload.suggestion_ids,
    )

    created: list[int] = []
    skipped: list[int] = []
    errors: list[dict[str, str]] = []

    for suggestion in suggestions:
        suggestion_id = suggestion.id
        item_url = suggestion.item_url
        if suggestion_id is None:
            continue
        if not item_url:
            skipped.append(suggestion_id)
            continue

        try:
            validated_item_url = _HTTP_URL_ADAPTER.validate_python(item_url)
            response = ingest_content_command.execute(
                db,
                payload=SubmitContentRequest(
                    url=validated_item_url,
                    content_type=None,
                    title=suggestion.title,
                    platform=None,
                    instruction=None,
                    crawl_links=False,
                    subscribe_to_feed=False,
                    share_and_chat=False,
                    chat_initial_message=None,
                    save_to_knowledge_and_mark_read=False,
                ),
                current_user=current_user,
            ).response
            if response.already_exists:
                skipped.append(suggestion_id)
            else:
                created.append(response.content_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Failed to add discovery item",
                extra={
                    "component": "feed_discovery",
                    "operation": "add_item",
                    "item_id": str(suggestion_id),
                    "context_data": {"error": str(exc)},
                },
            )
            errors.append({"id": str(suggestion_id), "error": str(exc)})

    db.commit()
    return DiscoveryAddItemResponse(created=created, skipped=skipped, errors=errors)
