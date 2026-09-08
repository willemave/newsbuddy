"""Strict authoring models shared by the generator and runner."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class User(Record):
    name: str


class Feed(Record):
    title: str
    url: str
    users: list[str]


class Episode(Record):
    title: str
    feed: str
    url: str
    published_at: datetime
    body: str
    visible_to: list[str]
    read_by: list[str] = Field(default_factory=list)
    saved_by: list[str] = Field(default_factory=list)


class Stub(Record):
    method: Literal["GET", "POST"]
    path: str
    body_contains: dict[str, Any] = Field(default_factory=dict)
    status: int = Field(default=200, ge=100, le=599)
    response: Any = None
    embedding_vector: list[float] | None = Field(default=None, min_length=1)
    text: str | None = None
    content_type: str = "application/json"
    delay_seconds: float = Field(default=0, ge=0, le=30)


class Scenario(Record):
    version: Literal[1]
    name: str
    users: dict[str, User]
    feeds: dict[str, Feed]
    episodes: dict[str, Episode]
    stubs: list[Stub] = Field(default_factory=list)
    external_conditions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def references(self) -> Scenario:
        if not self.users or not self.episodes:
            raise ValueError("scenario needs users and episodes")
        for feed in self.feeds.values():
            if set(feed.users) - self.users.keys():
                raise ValueError("feed refers to unknown user")
        for episode in self.episodes.values():
            if episode.feed not in self.feeds:
                raise ValueError(f"unknown feed: {episode.feed}")
            if set(episode.visible_to + episode.read_by + episode.saved_by) - self.users.keys():
                raise ValueError("episode refers to unknown user")
            if set(episode.read_by + episode.saved_by) - set(episode.visible_to):
                raise ValueError("read/saved users must have visibility")
            if episode.published_at.tzinfo is None:
                raise ValueError("published_at requires a timezone")
        return self


class Case(Record):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    user: str = "reader"
    turns: list[str] = Field(min_length=1, max_length=10)
    expects: str = Field(min_length=1)
    context_episode: str | None = None
    required_links: list[str] = Field(default_factory=list)
    forbidden_links: list[str] = Field(default_factory=list)
    minimum_episode_links: int = Field(default=0, ge=0)
    link_feed: str | None = None
    forbidden_text: list[str] = Field(default_factory=list)
    expected_result_kind: Literal["episodes"] | None = None


class Suite(Record):
    version: Literal[1]
    scenario: str
    cases: list[Case] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_ids(self) -> Suite:
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("duplicate case ID")
        return self


def read_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_suite(path: Path) -> tuple[Suite, Scenario]:
    suite = Suite.model_validate(read_yaml(path))
    scenario = Scenario.model_validate(read_yaml(path.parent / suite.scenario))
    for case in suite.cases:
        if case.user not in scenario.users:
            raise ValueError(f"{case.id}: unknown user")
        refs = case.required_links + ([case.context_episode] if case.context_episode else [])
        if set(refs) - scenario.episodes.keys():
            raise ValueError(f"{case.id}: unknown episode")
        if case.link_feed and case.link_feed not in scenario.feeds:
            raise ValueError(f"{case.id}: unknown link_feed")
        known = scenario.episodes.keys() | scenario.feeds.keys()
        if set(case.forbidden_links) - known:
            raise ValueError(f"{case.id}: unknown forbidden link")
    return suite, scenario
