"""API endpoints for content with OpenAPI documentation."""

from fastapi import APIRouter

from app.routers.api import (
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
from app.models.api.common import (
    BulkMarkReadRequest,
    ChatGPTUrlResponse,
    ContentDetailResponse,
    ContentDiscussionResponse,
    ContentListResponse,
    ContentSummaryResponse,
    ConvertNewsResponse,
    NarrationResponse,
    RecordContentInteractionRequest,
    RecordContentInteractionResponse,
    UnreadCountsResponse,
)

router = APIRouter(
    tags=["content"],
    responses={404: {"description": "Not found"}},
)

router.include_router(content_list.router)
router.include_router(narration.router)
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
