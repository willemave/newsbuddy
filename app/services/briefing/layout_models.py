"""Typed model-output contract for Briefing layout composition."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.contracts import BriefingFigurePlacement


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


class PullquoteBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["pullquote"]
    source_key: str = Field(min_length=1)
    text: str = Field(min_length=1)


ComposerBlock = Annotated[
    PassageBlock | FigureBlock | PullquoteBlock,
    Field(discriminator="type"),
]


class ComposerLayout(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blocks: list[ComposerBlock] = Field(min_length=1)
