from __future__ import annotations

from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

from app.models.metadata.base import BaseContentMetadata


class InsightReportDigDeeperArea(BaseModel):
    """Chat-starter prompt attached to an insight report."""

    title: str = Field(..., min_length=1, max_length=200)
    prompt: str = Field(..., min_length=1, max_length=2000)


class InsightReportMetadata(BaseContentMetadata):
    """Metadata for the generated long-form insight report content type."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "example": {
                "source": "Newsly",
                "subtitle": "What your library is converging on this week",
                "intro": (
                    "Your saves are converging on the idea that memory, "
                    "orchestration, and harness design are overtaking raw model "
                    "capability as the bottleneck."
                ),
                "themes": ["Agent memory", "Enterprise orchestration"],
                "insights": ["Memory and orchestration are the same problem at different scales."],
                "learnings": ["Treat memory as governed state, not retrieval cache."],
                "curiosities": ["Does the 'memory benchmark race' still measure anything real?"],
                "dig_deeper_areas": [
                    {
                        "title": "Memory as governance",
                        "prompt": (
                            "Help me compare provenance-first memory designs with "
                            "the vector-recall approaches in my library."
                        ),
                    }
                ],
                "referenced_knowledge_ids": [29320, 29308, 29227],
                "user_id": 1,
                "generated_at": "2026-04-21T04:21:27Z",
                "generated_by_model": "anthropic:claude-opus-4-6",
                "effort": "high",
                "image_url": "/static/images/content/insight_reports/...jpg",
                "thumbnail_url": "/static/images/content/insight_reports/...jpg",
            }
        },
    )

    user_id: int = Field(..., ge=1, description="Owner of this insight report")
    subtitle: str | None = Field(None, max_length=500)
    intro: str = Field(..., min_length=1, description="2-3 sentence framing paragraph")
    themes: list[str] = Field(default_factory=list)
    insights: list[str] = Field(default_factory=list)
    learnings: list[str] = Field(default_factory=list)
    curiosities: list[str] = Field(default_factory=list)
    dig_deeper_areas: list[InsightReportDigDeeperArea] = Field(default_factory=list)
    referenced_knowledge_ids: list[int] = Field(
        default_factory=list,
        description="content_id values from the user's library that informed this report",
    )

    generated_at: datetime | None = Field(
        None, description="UTC timestamp when the report synthesis completed"
    )
    generated_by_model: str | None = Field(
        None,
        max_length=200,
        description="Model spec used for synthesis (e.g. 'anthropic:claude-opus-4-6')",
    )
    effort: str | None = Field(
        None,
        pattern="^(low|medium|high|max)$",
        description="Reasoning effort used during synthesis",
    )
    image_url: str | None = Field(None, max_length=2000)
    thumbnail_url: str | None = Field(None, max_length=2000)


# Helper functions from app/schemas/metadata.py
