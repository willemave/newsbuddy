"""Typed workflow specs for host-applied ShareSheet actions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, RootModel

from app.models.api.share_actions import ShareActionAgentResult, ShareActionBriefingTarget
from app.models.contracts import LlmTaskMode
from app.models.db import LlmTask
from app.services.llm_tasks import LlmTaskError


class ShareActionInputModel(BaseModel):
    """Base model for host action input parsed from agent output."""

    model_config = ConfigDict(extra="forbid")


class ContentActionInput(ShareActionInputModel):
    """Input for submitting one content URL through existing ingest services."""

    url: str
    title: str | None = None
    platform: str | None = None
    content_type: str | None = None
    instruction: str | None = None
    chat_initial_message: str | None = None


class FeedActionInput(ShareActionInputModel):
    """Input for subscribing to a discovered feed through content ingest."""

    url: str
    title: str | None = None
    platform: str | None = None
    instruction: str | None = None


class AddLinksActionInput(ShareActionInputModel):
    """Input for submitting a bounded list of extracted content URLs."""

    url: str
    content_urls: list[ContentActionInput]


class LearningDeckActionInput(ShareActionInputModel):
    """Input for host-side Learning Deck creation or rerun."""

    source_url: str
    title: str | None = None
    interests_prompt: str | None = None


class AddToBriefingActionInput(RootModel[ShareActionBriefingTarget]):
    """Discriminated feed-or-content target persisted for host application."""


type ShareActionInput = (
    ContentActionInput
    | FeedActionInput
    | AddLinksActionInput
    | LearningDeckActionInput
    | AddToBriefingActionInput
)
ShareActionInputBuilder = Callable[[LlmTask, ShareActionAgentResult], ShareActionInput]


@dataclass(frozen=True)
class ShareActionWorkflowSpec:
    """One ShareSheet mode mapped to a single host action contract."""

    mode: LlmTaskMode
    host_action_name: str
    accepted_result_actions: frozenset[str]
    input_model: type[ShareActionInput]
    build_input: ShareActionInputBuilder
    save_shared_source_to_knowledge: bool = True
    share_and_chat: bool = False


@dataclass(frozen=True)
class ShareActionRequest:
    """Validated host action request derived from output/result.json."""

    action_name: str
    action_input: dict[str, Any]
    idempotency_key: str


def allowed_share_actions(mode: LlmTaskMode) -> list[str]:
    """Return the only host action names allowed for one ShareSheet mode."""
    return [share_action_workflow_for_mode(mode).host_action_name]


def share_action_idempotency_key(action_name: str, action_input: dict[str, Any]) -> str:
    """Build the stable bounded idempotency key used for host-applied actions."""
    return _action_idempotency_key(action_name, action_input)


def build_share_action_request(
    *,
    task: LlmTask,
    result: ShareActionAgentResult,
) -> ShareActionRequest | None:
    """Convert a typed agent result artifact into a typed host action request."""
    if result.action == "no_action":
        return None

    mode = LlmTaskMode(str(task.mode))
    spec = share_action_workflow_for_mode(mode)
    if result.action not in spec.accepted_result_actions and result.action != spec.host_action_name:
        raise LlmTaskError(
            f"Share Action result action {result.action!r} does not match mode {mode.value!r}"
        )

    action_input = spec.build_input(task, result)
    action_input_json = action_input.model_dump(mode="json", exclude_none=True)
    return ShareActionRequest(
        action_name=spec.host_action_name,
        action_input=action_input_json,
        idempotency_key=_action_idempotency_key(spec.host_action_name, action_input_json),
    )


def parse_share_action_input(
    *,
    task: LlmTask,
    action_name: str,
    action_input: object,
) -> ShareActionInput:
    """Parse a persisted action input payload against the mode's host action schema."""
    spec = share_action_workflow_for_mode(LlmTaskMode(str(task.mode)))
    if action_name != spec.host_action_name:
        raise LlmTaskError(
            f"Share Action mode {spec.mode.value!r} cannot apply action {action_name!r}"
        )
    return spec.input_model.model_validate(action_input if isinstance(action_input, dict) else {})


def share_action_workflow_for_mode(mode: LlmTaskMode) -> ShareActionWorkflowSpec:
    """Look up the typed workflow spec for one ShareSheet mode."""
    spec = SHARE_ACTION_WORKFLOWS.get(mode)
    if spec is None:
        raise LlmTaskError(f"Unsupported share action mode: {mode.value}")
    return spec


def _build_content_input(task: LlmTask, result: ShareActionAgentResult) -> ContentActionInput:
    return ContentActionInput(
        url=result.primary_url or _input_url(task),
        title=result.title,
        platform=result.platform,
        content_type=result.content_type,
        instruction=result.rationale,
    )


def _build_bookmark_input(task: LlmTask, result: ShareActionAgentResult) -> ContentActionInput:
    return _build_content_input(task, result)


def _build_add_to_briefing_input(
    _task: LlmTask,
    result: ShareActionAgentResult,
) -> AddToBriefingActionInput:
    target = result.briefing_target
    if target is None:
        raise LlmTaskError("Add-to-Briefing result is missing briefing_target")
    return AddToBriefingActionInput(root=target)


def _build_feed_input(task: LlmTask, result: ShareActionAgentResult) -> FeedActionInput:
    return FeedActionInput(
        url=result.feed_url or result.primary_url or _input_url(task),
        title=result.title,
        platform=result.platform,
        instruction=result.rationale,
    )


def _build_chat_input(task: LlmTask, result: ShareActionAgentResult) -> ContentActionInput:
    chat = result.chat
    url = chat.content_url if chat and chat.content_url else result.primary_url or _input_url(task)
    return ContentActionInput(
        url=url,
        title=result.title,
        platform=result.platform,
        content_type=result.content_type,
        instruction=result.rationale,
        chat_initial_message=(
            chat.initial_message
            if chat and chat.initial_message
            else _input_chat_initial_message(task)
        ),
    )


def _build_add_links_input(task: LlmTask, result: ShareActionAgentResult) -> AddLinksActionInput:
    return AddLinksActionInput(
        url=result.primary_url or _input_url(task),
        content_urls=[
            ContentActionInput(
                url=candidate.url,
                title=candidate.title,
                platform=candidate.platform,
                content_type=candidate.content_type,
                instruction=candidate.rationale,
            )
            for candidate in result.content_urls
        ],
    )


def _build_learning_deck_input(
    task: LlmTask,
    result: ShareActionAgentResult,
) -> LearningDeckActionInput:
    presentation = result.presentation
    user_interests_prompt = _input_interests_prompt(task)
    return LearningDeckActionInput(
        source_url=(
            presentation.source_url
            if presentation and presentation.source_url
            else result.primary_url or _input_url(task)
        ),
        title=presentation.title if presentation else result.title,
        interests_prompt=(
            user_interests_prompt
            if user_interests_prompt is not None
            else presentation.interests_prompt
            if presentation
            else None
        ),
    )


SHARE_ACTION_WORKFLOWS: dict[LlmTaskMode, ShareActionWorkflowSpec] = {
    LlmTaskMode.ADD_CONTENT: ShareActionWorkflowSpec(
        mode=LlmTaskMode.ADD_CONTENT,
        host_action_name="add_content",
        accepted_result_actions=frozenset({"add_content"}),
        input_model=ContentActionInput,
        build_input=_build_content_input,
    ),
    LlmTaskMode.ADD_TO_BRIEFING: ShareActionWorkflowSpec(
        mode=LlmTaskMode.ADD_TO_BRIEFING,
        host_action_name="add_to_briefing",
        accepted_result_actions=frozenset({"add_to_briefing"}),
        input_model=AddToBriefingActionInput,
        build_input=_build_add_to_briefing_input,
        save_shared_source_to_knowledge=False,
    ),
    LlmTaskMode.ADD_LINKS: ShareActionWorkflowSpec(
        mode=LlmTaskMode.ADD_LINKS,
        host_action_name="add_links",
        accepted_result_actions=frozenset({"add_links"}),
        input_model=AddLinksActionInput,
        build_input=_build_add_links_input,
    ),
    LlmTaskMode.ADD_FEED: ShareActionWorkflowSpec(
        mode=LlmTaskMode.ADD_FEED,
        host_action_name="subscribe_to_feed",
        accepted_result_actions=frozenset({"add_feed"}),
        input_model=FeedActionInput,
        build_input=_build_feed_input,
        save_shared_source_to_knowledge=False,
    ),
    LlmTaskMode.CHAT: ShareActionWorkflowSpec(
        mode=LlmTaskMode.CHAT,
        host_action_name="enqueue_chat",
        accepted_result_actions=frozenset({"chat"}),
        input_model=ContentActionInput,
        build_input=_build_chat_input,
        share_and_chat=True,
    ),
    LlmTaskMode.PRESENTATION: ShareActionWorkflowSpec(
        mode=LlmTaskMode.PRESENTATION,
        host_action_name="create_learning_deck",
        accepted_result_actions=frozenset({"presentation"}),
        input_model=LearningDeckActionInput,
        build_input=_build_learning_deck_input,
    ),
    LlmTaskMode.BOOKMARK_ONLY: ShareActionWorkflowSpec(
        mode=LlmTaskMode.BOOKMARK_ONLY,
        host_action_name="save_to_knowledge",
        accepted_result_actions=frozenset({"bookmark_only"}),
        input_model=ContentActionInput,
        build_input=_build_bookmark_input,
    ),
}


def _action_idempotency_key(action_name: str, action_input: dict[str, Any]) -> str:
    canonical_input = json.dumps(
        action_input,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical_input.encode("utf-8")).hexdigest()
    return f"{action_name}:{digest}"


def _input_url(task: LlmTask) -> str:
    input_json = task.input_json if isinstance(task.input_json, dict) else {}
    value = input_json.get("url")
    if not isinstance(value, str) or not value.strip():
        raise LlmTaskError("Share Action task input is missing url")
    return value.strip()


def _input_chat_initial_message(task: LlmTask) -> str | None:
    input_json = task.input_json if isinstance(task.input_json, dict) else {}
    value = input_json.get("chat_initial_message")
    cleaned = value.strip() if isinstance(value, str) else ""
    return cleaned or None


def _input_interests_prompt(task: LlmTask) -> str | None:
    input_json = task.input_json if isinstance(task.input_json, dict) else {}
    value = input_json.get("interests_prompt")
    cleaned = value.strip() if isinstance(value, str) else ""
    return cleaned or None
