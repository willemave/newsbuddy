"""Application command for tweet suggestion generation."""

from __future__ import annotations

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.observability import build_log_extra
from app.models.api.common import TweetLength, TweetSuggestion, TweetSuggestionsResponse
from app.models.content_mapper import content_to_domain
from app.models.metadata import ContentStatus
from app.models.schema import Content
from app.services.tweet_suggestions import generate_tweet_suggestions

logger = get_logger(__name__)


async def execute(
    db: Session,
    *,
    user_id: int,
    content_id: int,
    message: str | None,
    creativity: int,
    length: str,
    llm_provider: str | None,
) -> TweetSuggestionsResponse:
    """Generate tweet suggestions for one content item."""
    content = db.query(Content).filter(Content.id == content_id).first()
    if not content:
        raise HTTPException(status_code=404, detail="Content not found")
    if content.status != ContentStatus.COMPLETED.value:
        raise HTTPException(
            status_code=400,
            detail=f"Content not ready for tweets (status: {content.status})",
        )

    content_data = content_to_domain(content)
    result = await run_in_threadpool(
        generate_tweet_suggestions,
        content=content_data,
        message=message,
        creativity=creativity,
        length=length,
        llm_provider=llm_provider,
    )

    if result is None:
        logger.error(
            "Tweet suggestion generation failed",
            extra=build_log_extra(
                component="tweet_suggestions",
                operation="generate",
                event_name="tweet_suggestions.generate",
                status="failed",
                content_id=content_id,
                user_id=user_id,
                context_data={"creativity": creativity},
            ),
        )
        raise HTTPException(
            status_code=502,
            detail="Tweet generation failed. Please try again.",
        )

    logger.info(
        "Tweet suggestion generation completed",
        extra=build_log_extra(
            component="tweet_suggestions",
            operation="generate",
            event_name="tweet_suggestions.generate",
            status="completed",
            content_id=content_id,
            user_id=user_id,
            context_data={"creativity": creativity, "model": result.model},
        ),
    )

    return TweetSuggestionsResponse(
        content_id=result.content_id,
        creativity=result.creativity,
        length=TweetLength(result.length),
        model=result.model,
        suggestions=[
            TweetSuggestion(
                id=suggestion.id,
                text=suggestion.text,
                style_label=suggestion.style_label,
            )
            for suggestion in result.suggestions
        ],
    )
