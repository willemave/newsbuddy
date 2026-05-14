"""API endpoints for content with OpenAPI documentation."""

from fastapi import APIRouter

from app.models.api.analytics import (
    RecordContentInteractionRequest,
    RecordContentInteractionResponse,
)
from app.models.api.content import (
    ContentDetailResponse,
    ContentListResponse,
    ContentSummaryResponse,
    NarrationResponse,
)
from app.models.api.content_actions import (
    BulkMarkReadRequest,
    ChatGPTUrlResponse,
    ConvertNewsResponse,
    UnreadCountsResponse,
)
from app.models.api.content_discussions import ContentDiscussionResponse
from app.routers.api import (
    audio_episodes,
    chat,
    content_actions,
    content_detail,
    content_list,
    knowledge,
    narration,
    read_status,
    scraper_configs,
    stats,
    submission,
)

router = APIRouter(
    tags=["content"],
    responses={404: {"description": "Not found"}},
)

router.include_router(content_list.router)
router.include_router(narration.router)
router.include_router(audio_episodes.router)
router.include_router(stats.router)
router.include_router(content_detail.router)
router.include_router(read_status.router)
router.include_router(knowledge.router)
router.include_router(content_actions.router)
router.include_router(scraper_configs.router)
router.include_router(submission.router)
router.include_router(chat.router)

__all__ = [
    "router",
    "ContentSummaryResponse",
    "ContentListResponse",
    "ContentDetailResponse",
    "ContentDiscussionResponse",
    "BulkMarkReadRequest",
    "ChatGPTUrlResponse",
    "UnreadCountsResponse",
    "ConvertNewsResponse",
    "NarrationResponse",
    "RecordContentInteractionRequest",
    "RecordContentInteractionResponse",
]
