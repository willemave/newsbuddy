"""Content transformation and action endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session

from app.commands import convert_news_to_article as convert_news_to_article_command
from app.commands import (
    download_more_from_series as download_more_from_series_command,
)
from app.commands import (
    generate_tweet_suggestions as generate_tweet_suggestions_command,
)
from app.core.db import get_db_session
from app.core.deps import get_current_user, require_user_id
from app.models.api.common import (
    ConvertNewsResponse,
    DownloadMoreRequest,
    DownloadMoreResponse,
    TweetSuggestionsRequest,
    TweetSuggestionsResponse,
)
from app.models.user import User

router = APIRouter()


@router.post(
    "/{content_id}/convert-to-article",
    response_model=ConvertNewsResponse,
    summary="Convert news link to article",
    description=(
        "Convert a news content item to a full article by extracting the article URL "
        "from the news metadata and creating a new article content entry. "
        "If the article already exists, returns the existing article ID."
    ),
    responses={
        200: {"description": "News link converted successfully"},
        400: {"description": "Content cannot be converted (not news or no article URL)"},
        404: {"description": "Content not found"},
    },
)
async def convert_news_to_article(
    content_id: Annotated[int, Path(..., description="News content ID", gt=0)],
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ConvertNewsResponse:
    """Convert a news link to a full article content entry."""
    return convert_news_to_article_command.execute(
        db,
        content_id=content_id,
        user_id=require_user_id(current_user),
    )


@router.post(
    "/{content_id}/download-more",
    response_model=DownloadMoreResponse,
    summary="Download more items from the same feed series",
    description=(
        "Trigger a one-off backfill for the feed that produced this content, "
        "attempting to fetch additional older items without changing the feed's "
        "ongoing limit."
    ),
    responses={
        200: {"description": "Backfill completed"},
        400: {"description": "Feed could not be resolved or is unsupported"},
        403: {"description": "Content not accessible by the current user"},
        404: {"description": "Content not found"},
    },
)
async def download_more_from_series(
    content_id: Annotated[int, Path(..., description="Content ID", gt=0)],
    request: DownloadMoreRequest,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> DownloadMoreResponse:
    """Download older items from the same feed series as this content."""
    return await download_more_from_series_command.execute(
        db,
        user_id=require_user_id(current_user),
        content_id=content_id,
        count=request.count,
    )


@router.post(
    "/{content_id}/tweet-suggestions",
    response_model=TweetSuggestionsResponse,
    summary="Generate tweet suggestions for content",
    description=(
        "Generate 3 tweet suggestions for a content item using Gemini. "
        "Supports all content types. Requires JWT authentication."
    ),
    responses={
        200: {"description": "Tweet suggestions generated successfully"},
        400: {"description": "Content not ready or unsupported type"},
        404: {"description": "Content not found"},
        502: {"description": "LLM generation failed"},
    },
)
async def get_tweet_suggestions(
    content_id: Annotated[int, Path(..., description="Content ID", gt=0)],
    request: TweetSuggestionsRequest,
    db: Annotated[Session, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> TweetSuggestionsResponse:
    """Generate tweet suggestions for one content item."""
    return await generate_tweet_suggestions_command.execute(
        db,
        user_id=require_user_id(current_user),
        content_id=content_id,
        message=request.message,
        creativity=request.creativity,
        length=request.length.value,
        llm_provider=request.llm_provider,
    )
