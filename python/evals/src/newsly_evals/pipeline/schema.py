"""Synthetic pipeline inputs; no expected output is inserted into the target stage."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from newsly_evals.chat.schema import Record, Stub


class Source(Record):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    kind: Literal["article", "podcast", "news"]
    title: str = Field(min_length=1)
    text: str = Field(min_length=1)
    source: str = "Synthetic Gazette"
    platform: str | None = None
    publication_date: str | None = None
    url: str | None = Field(default=None, pattern=r"^https://[^\s]+$")
    key_points: list[str] = Field(default_factory=list)
    lens: str = Field(default="news", pattern=r"^[a-z][a-z0-9_]{0,39}$")


class NewsWordTarget(Record):
    target_min: int = Field(ge=1)
    target_max: int = Field(ge=1)
    hard_max: int = Field(default=75, ge=1)

    @model_validator(mode="after")
    def ordered(self) -> NewsWordTarget:
        if not self.target_min <= self.target_max <= self.hard_max:
            raise ValueError("news word target must satisfy target_min <= target_max <= hard_max")
        return self


class Case(Record):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    stage: Literal["summary", "briefing"]
    sources: list[Source] = Field(min_length=1, max_length=8)
    expects: str = Field(min_length=1)
    stubs: list[Stub] = Field(default_factory=list)
    news_word_target: NewsWordTarget | None = None

    @model_validator(mode="after")
    def valid_sources(self) -> Case:
        if len({s.id for s in self.sources}) != len(self.sources):
            raise ValueError("source IDs must be unique")
        if self.stage == "summary" and (len(self.sources) != 1 or self.sources[0].kind == "news"):
            raise ValueError("summary stage requires one article or podcast")
        if self.news_word_target is not None and (
            self.stage != "briefing" or not any(source.kind == "news" for source in self.sources)
        ):
            raise ValueError("news_word_target requires a briefing case with news sources")
        groups: dict[str, int] = {}
        for source in self.sources:
            if source.kind == "news":
                groups[source.lens] = groups.get(source.lens, 0) + 1
        if any(n > 4 for n in groups.values()):
            raise ValueError("news composition fixtures allow at most four sources per lens")
        if self.stage == "briefing" and any(
            sum(s.kind == kind for s in self.sources) > 1 for kind in ("article", "podcast")
        ):
            raise ValueError("one refresh supports one article and one podcast composition window")
        return self


class Suite(Record):
    version: Literal[1]
    cases: list[Case] = Field(min_length=1)

    @model_validator(mode="after")
    def unique(self) -> Suite:
        if len({c.id for c in self.cases}) != len(self.cases):
            raise ValueError("case IDs must be unique")
        return self
