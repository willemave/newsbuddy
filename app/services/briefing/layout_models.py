"""Typed model-output contract for Briefing layout composition."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.contracts import BriefingFigureAlignment, BriefingFigurePlacement


class PassageBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["passage"]
    markdown: str = Field(min_length=1)
    weight: Literal["feature", "brief"] = "brief"


class FigureBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["figure"]
    source_key: str = Field(min_length=1)
    caption: str = Field(min_length=1)
    placement: BriefingFigurePlacement
    alignment: BriefingFigureAlignment | None = None


class SuggestedQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=360)


class PullquoteBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["pullquote"]
    suggestion_id: str = Field(min_length=1, max_length=80)


ComposerBlock = Annotated[
    PassageBlock | FigureBlock | PullquoteBlock,
    Field(discriminator="type"),
]


class ComposerLayout(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suggested_quotes: list[SuggestedQuote] = Field(default_factory=list)
    blocks: list[ComposerBlock] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_pullquote_references(self) -> ComposerLayout:
        suggestions_by_id = {suggestion.id: suggestion for suggestion in self.suggested_quotes}
        if len(suggestions_by_id) != len(self.suggested_quotes):
            raise ValueError("Suggested quote IDs must be unique")
        missing_ids = {
            block.suggestion_id
            for block in self.blocks
            if isinstance(block, PullquoteBlock) and block.suggestion_id not in suggestions_by_id
        }
        if missing_ids:
            raise ValueError(
                "Pullquote blocks reference unknown suggestion IDs: "
                + ", ".join(sorted(missing_ids))
            )
        selected_ids = [
            block.suggestion_id for block in self.blocks if isinstance(block, PullquoteBlock)
        ]
        if len(selected_ids) != len(set(selected_ids)):
            raise ValueError("A suggested quote may be selected at most once")
        return self

    def resolved_blocks(self) -> list[dict[str, object]]:
        suggestions_by_id = {suggestion.id: suggestion for suggestion in self.suggested_quotes}
        resolved: list[dict[str, object]] = []
        for block in self.blocks:
            if isinstance(block, PullquoteBlock):
                resolved.append(
                    {
                        "type": "pullquote",
                        "text": suggestions_by_id[block.suggestion_id].text,
                    }
                )
                continue
            resolved.append(block.model_dump(mode="json", exclude_none=True))
        return resolved
