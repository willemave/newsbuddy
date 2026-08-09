"""AXe-driven Newsly interaction matrix against the live local API.

These tests deliberately use deterministic speech/provider fixtures while
retaining the real SwiftUI, HTTP, command, persistence, and queue boundaries.
Every HID action goes through :class:`AxeRunner`, which captures and asserts a
fresh accessibility tree and screenshot before the test can continue.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from urllib.parse import urlencode, urlparse

import pytest
import requests

from app.constants import (
    DEFAULT_INITIAL_FEED_ARTICLE_DOWNLOAD_COUNT,
    DEFAULT_NEW_FEED_LIMIT,
)
from app.models.contracts import (
    FeedFormat,
    FeedType,
    LlmTaskKind,
    LlmTaskMode,
    LlmTaskStatus,
    LlmWorkflowState,
    TaskQueue,
    TaskStatus,
    TaskType,
)
from app.models.db import (
    ChatMessage,
    ChatSession,
    Content,
    LearningDeck,
    LlmTask,
    ProcessingTask,
    UserScraperConfig,
)
from app.models.domain.chat_render import (
    AssistantFeedOption,
    AssistantFeedOptionsResult,
    ChatMessageRenderMetadata,
)
from app.queries import search_mixed
from app.services.assistant_router import seed_assistant_message
from app.services.llm_tasks import create_llm_task, require_llm_task_id, set_llm_task_status
from app.services.podcast_search import PodcastEpisodeSearchHit
from tests.ios_e2e.axe_harness import (
    AxeHarnessError,
    AxeRunner,
    AxeStateExpectation,
    tree_has_id,
)
from tests.support.feed_subscription_test_helpers import stub_feed_validator

pytestmark = [pytest.mark.integration, pytest.mark.ios_e2e]

FEED_ID = "axe-feed-0001"
FEED_URL = "https://feeds.example.test/axe.xml"
PODCAST_EPISODE_URL = "https://podcasts.example.test/episodes/axe-1"
PODCAST_FEED_URL = "https://podcasts.example.test/feed.xml"
NO_ACTION_TITLE = "AXe No Action Source"
NO_ACTION_URL = "https://example.com/axe-no-action"
NO_ACTION_RATIONALE = "No continuing source or Briefing-eligible item was found."
FAILED_SHARE_TITLE = "AXe Failed Share"
FAILED_SHARE_URL = "https://example.com/axe-failed-share"
SHARE_EXTENSION_ICON_POINTS = (
    (68.0, 653.0),
    (157.0, 653.0),
    (245.0, 653.0),
    (333.0, 653.0),
)


def _launch_arguments(
    *,
    live_server,
    user_id: int,
    completed_onboarding: bool = True,
    completed_tutorial: bool = True,
    extra: dict[str, str | int | bool] | None = None,
) -> dict[str, str | int | bool]:
    parsed = urlparse(live_server.base_url)
    arguments: dict[str, str | int | bool] = {
        "newslyE2EEnabled": True,
        "newslyE2EAutoLogin": True,
        "newslyE2EServerHost": parsed.hostname or "127.0.0.1",
        "newslyE2EServerPort": parsed.port or 80,
        "newslyE2EUseHTTPS": False,
        "newslyE2EUserId": user_id,
        "newslyE2ECompleteOnboarding": completed_onboarding,
        "newslyE2ECompleteTutorial": completed_tutorial,
    }
    if extra:
        arguments.update(extra)
    return arguments


def _launch_completed_app(
    axe_runner: AxeRunner,
    *,
    live_server,
    user_id: int,
) -> None:
    axe_runner.launch(
        arguments=_launch_arguments(live_server=live_server, user_id=user_id),
        expectation=AxeStateExpectation(ids=("briefing.screen",)),
        timeout_seconds=20,
    )


def _navigate_to_search(axe_runner: AxeRunner) -> None:
    axe_runner.tap_id(
        "tab.knowledge",
        name="knowledge_tab",
        expectation=AxeStateExpectation(ids=("knowledge.screen",)),
    )
    axe_runner.tap_id(
        "knowledge.more_menu",
        name="more_sheet",
        expectation=AxeStateExpectation(ids=("more.screen",)),
    )
    axe_runner.tap_id(
        "more.search",
        name="search_screen",
        expectation=AxeStateExpectation(
            ids=("search.input",),
            absent_ids=("more.screen",),
        ),
    )


def _seed_share_extension_session(
    axe_runner: AxeRunner,
    *,
    live_server,
    user_id: int,
) -> None:
    """Create a real app session so the extension receives shared config and tokens."""
    response = requests.post(
        f"{live_server.base_url}/auth/debug/new-user",
        json={
            "user_id": user_id,
            "has_completed_onboarding": True,
            "has_completed_new_user_tutorial": True,
        },
        timeout=10,
    )
    response.raise_for_status()

    axe_runner.launch(
        arguments={"newslyE2EEnabled": True},
        expectation=AxeStateExpectation(ids=("auth.landing.screen",)),
        timeout_seconds=20,
        name="share_auth_landing",
    )
    parsed = urlparse(live_server.base_url)
    query = urlencode(
        {
            "user_id": user_id,
            "host": parsed.hostname or "127.0.0.1",
            "port": parsed.port or 80,
            "https": "false",
        }
    )
    axe_runner.open_url(
        f"newsly://debug-login?{query}",
        expectation=AxeStateExpectation(ids=("briefing.screen",)),
        timeout_seconds=20,
        name="share_authenticated",
    )


def _find_share_extension_icon_point(axe_runner: AxeRunner) -> tuple[float, float]:
    """Find Newsbuddy in the system share row without assuming its column."""
    for point in SHARE_EXTENSION_ICON_POINTS:
        try:
            node = axe_runner.describe_ui(point=point)
        except AxeHarnessError:
            continue
        if node.get("AXLabel") == "Newsbuddy":
            return point
    raise AssertionError("Newsbuddy was not visible in the first system share row")


def _open_share_extension(
    axe_runner: AxeRunner,
    *,
    url: str,
    path_name: str,
) -> None:
    safari = axe_runner.open_url(
        url,
        name=f"{path_name}_safari",
        expectation=AxeStateExpectation(texts=("Safari",)),
        timeout_seconds=20,
    )
    if tree_has_id(safari.tree, "NotNowButton"):
        axe_runner.tap_id(
            "NotNowButton",
            name=f"{path_name}_safari_ready",
            expectation=AxeStateExpectation(texts=("More",)),
            timeout_seconds=15,
        )
    else:
        axe_runner.capture_until(
            name=f"{path_name}_safari_ready",
            expectation=AxeStateExpectation(texts=("More",)),
            timeout_seconds=15,
        )

    axe_runner.tap_label(
        "More",
        element_type="Button",
        name=f"{path_name}_safari_more",
        expectation=AxeStateExpectation(texts=("Share",)),
    )
    axe_runner.tap_label(
        "Share",
        element_type="Button",
        name=f"{path_name}_system_share",
        expectation=AxeStateExpectation(ids=("PopoverDismissRegion",)),
    )
    icon_point = _find_share_extension_icon_point(axe_runner)
    axe_runner.tap_point(
        *icon_point,
        name=f"{path_name}_extension_open",
        inspection_point=(200, 220),
        pre_delay_seconds=1,
        post_delay_seconds=0.5,
        expectation=AxeStateExpectation(
            ids=("share.action.add_to_briefing",),
            texts=("Add to Briefing",),
            enabled_ids=("share.action.add_to_briefing",),
        ),
        timeout_seconds=15,
    )
    axe_runner.capture_point_until(
        name=f"{path_name}_extension_url_ready",
        x=200,
        y=132,
        expectation=AxeStateExpectation(ids=("share.url_status",), texts=("Ready:",)),
        timeout_seconds=10,
    )


def _wait_for_share_task(
    db_session,
    *,
    user_id: int,
    mode: LlmTaskMode,
    url: str,
    timeout_seconds: float = 5,
) -> LlmTask:
    deadline = time.monotonic() + timeout_seconds
    while True:
        db_session.expire_all()
        matches = [
            task
            for task in db_session.query(LlmTask)
            .filter(
                LlmTask.user_id == user_id,
                LlmTask.task_kind == LlmTaskKind.SHARE_ACTION.value,
                LlmTask.mode == mode.value,
            )
            .all()
            if isinstance(task.input_json, dict) and task.input_json.get("url") == url
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1 or time.monotonic() >= deadline:
            raise AssertionError(
                f"Expected one persisted Share Action for mode={mode.value!r}, "
                f"url={url!r}; found {len(matches)}"
            )
        time.sleep(0.1)


def _scroll_until_id(
    axe_runner: AxeRunner,
    *,
    initial_tree,
    identifier: str,
    maximum_swipes: int = 5,
) -> None:
    tree = initial_tree
    for index in range(maximum_swipes):
        if tree_has_id(tree, identifier):
            return
        captured = axe_runner.swipe_up(
            name=f"search_scroll_{index + 1}",
            expectation=AxeStateExpectation(texts=("Search",)),
        )
        tree = captured.tree
    assert tree_has_id(tree, identifier), f"Search result did not become visible: {identifier}"


def _scroll_until_id_absent(
    axe_runner: AxeRunner,
    *,
    initial_tree,
    identifier: str,
    persistent_identifier: str,
    path_name: str,
    maximum_swipes: int = 5,
) -> None:
    tree = initial_tree
    for index in range(maximum_swipes):
        if not tree_has_id(tree, identifier):
            return
        captured = axe_runner.swipe_up(
            name=f"{path_name}_scroll_{index + 1}",
            expectation=AxeStateExpectation(ids=(persistent_identifier,)),
        )
        tree = captured.tree
    assert not tree_has_id(tree, identifier), (
        f"Screen header remained visible after scrolling: {identifier}"
    )


def test_main_navigation_has_no_dead_end(
    axe_runner,
    live_server,
    test_user,
) -> None:
    """Each primary destination should expose a usable onward path."""
    _launch_completed_app(axe_runner, live_server=live_server, user_id=test_user.id)

    axe_runner.tap_id(
        "tab.knowledge",
        name="knowledge_tab",
        expectation=AxeStateExpectation(ids=("knowledge.screen",)),
    )
    axe_runner.tap_id(
        "tab.learning",
        name="learning_tab",
        expectation=AxeStateExpectation(ids=("learning.screen",)),
    )
    axe_runner.tap_id(
        "learning.more_menu",
        name="more_sheet",
        expectation=AxeStateExpectation(ids=("more.screen",)),
    )
    axe_runner.tap_id(
        "more.search",
        name="search_screen",
        expectation=AxeStateExpectation(
            ids=("search.input",),
            absent_ids=("more.screen",),
        ),
    )


def test_settings_has_no_dead_end(
    axe_runner,
    live_server,
    test_user,
) -> None:
    """The More menu should open a usable Settings destination."""
    _launch_completed_app(axe_runner, live_server=live_server, user_id=test_user.id)
    axe_runner.tap_id(
        "tab.knowledge",
        name="settings_knowledge_tab",
        expectation=AxeStateExpectation(ids=("knowledge.screen",)),
    )
    axe_runner.tap_id(
        "knowledge.more_menu",
        name="settings_more_sheet",
        expectation=AxeStateExpectation(ids=("more.screen", "more.settings")),
    )
    axe_runner.tap_id(
        "more.settings",
        name="settings_screen",
        expectation=AxeStateExpectation(
            ids=("settings.screen",),
            absent_ids=("more.screen",),
        ),
    )


@pytest.mark.parametrize(
    (
        "mode",
        "action_identifier",
        "action_label",
        "action_point",
        "submit_label",
        "chat_initial_message",
    ),
    [
        pytest.param(
            LlmTaskMode.ADD_TO_BRIEFING,
            "share.action.add_to_briefing",
            "Add to Briefing",
            (200.0, 220.0),
            "Add to Briefing",
            None,
            id="add-to-briefing",
        ),
        pytest.param(
            LlmTaskMode.BOOKMARK_ONLY,
            "share.action.add_to_knowledge",
            "Add to Knowledge",
            (200.0, 300.0),
            "Add to Knowledge",
            None,
            id="add-to-knowledge",
        ),
        pytest.param(
            LlmTaskMode.PRESENTATION,
            "share.action.create_deck",
            "Create Deck",
            (200.0, 390.0),
            "Create deck",
            None,
            id="create-deck",
        ),
        pytest.param(
            LlmTaskMode.CHAT,
            "share.action.chat",
            "Chat",
            (200.0, 450.0),
            "Start chat",
            "Find more reporting like this",
            id="chat",
        ),
    ],
)
def test_share_extension_modes_reach_live_api_and_queue(
    mode,
    action_identifier,
    action_label,
    action_point,
    submit_label,
    chat_initial_message,
    axe_runner,
    live_server,
    test_user,
    db_session,
) -> None:
    """Every real Share Extension outcome should survive UI -> API -> queue."""
    _seed_share_extension_session(
        axe_runner,
        live_server=live_server,
        user_id=test_user.id,
    )
    url = f"{live_server.base_url}/health?axe_share_mode={mode.value}"
    path_name = f"share_{mode.value}"
    _open_share_extension(axe_runner, url=url, path_name=path_name)

    axe_runner.capture_point_until(
        name=f"{path_name}_action_target",
        x=action_point[0],
        y=action_point[1],
        expectation=AxeStateExpectation(
            ids=(action_identifier,),
            texts=(action_label,),
            enabled_ids=(action_identifier,),
        ),
        timeout_seconds=5,
    )

    if mode == LlmTaskMode.CHAT:
        axe_runner.tap_point(
            *action_point,
            name=f"{path_name}_selected",
            inspection_point=(200, 550),
            expectation=AxeStateExpectation(
                ids=("share.chat.prompt",),
                enabled_ids=("share.chat.prompt",),
            ),
        )
        disabled_submit = axe_runner.capture_point_until(
            name=f"{path_name}_requires_prompt",
            x=200,
            y=670,
            expectation=AxeStateExpectation(ids=("share.submit",), texts=(submit_label,)),
            timeout_seconds=5,
        )
        assert disabled_submit.tree.get("enabled") is False
        axe_runner.type_text(
            chat_initial_message,
            name=f"{path_name}_prompt_typed",
            inspection_point=(200, 550),
            expectation=AxeStateExpectation(
                ids=("share.chat.prompt",),
                id_values={"share.chat.prompt": chat_initial_message},
            ),
        )
        submit_point = (200.0, 670.0)
        axe_runner.capture_point_until(
            name=f"{path_name}_submit_ready",
            x=submit_point[0],
            y=submit_point[1],
            expectation=AxeStateExpectation(
                ids=("share.submit",),
                texts=(submit_label,),
                enabled_ids=("share.submit",),
            ),
            timeout_seconds=5,
        )
    else:
        submit_point = (200.0, 520.0)
        axe_runner.tap_point(
            *action_point,
            name=f"{path_name}_selected",
            inspection_point=submit_point,
            expectation=AxeStateExpectation(
                ids=("share.submit",),
                texts=(submit_label,),
                enabled_ids=("share.submit",),
            ),
        )

    axe_runner.tap_point(
        *submit_point,
        name=f"{path_name}_submitted",
        inspection_point=submit_point,
        expectation=AxeStateExpectation(absent_ids=("share.submit",)),
        timeout_seconds=15,
    )

    task = _wait_for_share_task(
        db_session,
        user_id=test_user.id,
        mode=mode,
        url=url,
    )
    assert task.status == LlmTaskStatus.QUEUED.value
    assert task.workflow_state == LlmWorkflowState.QUEUED.value
    assert task.input_json["chat_initial_message"] == chat_initial_message

    queued_tasks = [
        queued
        for queued in db_session.query(ProcessingTask)
        .filter(
            ProcessingTask.owner_user_id == test_user.id,
            ProcessingTask.task_type == TaskType.RUN_LLM_TASK.value,
        )
        .all()
        if queued.payload == {"llm_task_id": task.id, "user_id": test_user.id}
    ]
    assert len(queued_tasks) == 1
    assert queued_tasks[0].status == TaskStatus.PENDING.value
    assert queued_tasks[0].queue_name == TaskQueue.LLM.value


def test_chat_feed_discovery_round_trips_through_knowledge(
    axe_runner,
    live_server,
    test_user,
    db_session,
    content_factory,
    knowledge_save_factory,
    chat_session_factory,
    monkeypatch,
) -> None:
    """Chat discovery should subscribe durably and retain every onward path."""
    feed_title = "AXe Chat Engineering Feed"
    feed_button_id = f"chat.feed.subscribe.{FEED_ID}"
    content = content_factory(
        title="AXe Knowledge Feed Source",
        url="https://example.com/axe-knowledge-feed-source",
    )
    knowledge_save_factory(user=test_user, content=content)
    option = AssistantFeedOption(
        id=FEED_ID,
        title=feed_title,
        site_url="https://feeds.example.test",
        feed_url=FEED_URL,
        feed_type=FeedType.ATOM,
        feed_format=FeedFormat.RSS,
        description="Deterministic chat discovery result.",
        rationale="Exercises chat rendering and subscription persistence.",
        evidence_url="https://feeds.example.test/about",
    )
    session = chat_session_factory(
        user=test_user,
        content=content,
        title="Find new engineering content",
        session_type="knowledge_chat",
        last_message_at=datetime.now(UTC),
    )
    seed_assistant_message(
        db_session,
        session_id=session.id,
        assistant_text="I found a validated source you can add.",
        render_metadata=ChatMessageRenderMetadata(feed_options=[option]),
    )
    stub_feed_validator(monkeypatch, title=feed_title)

    _launch_completed_app(axe_runner, live_server=live_server, user_id=test_user.id)
    axe_runner.tap_id(
        "tab.learning",
        name="chat_learning_hub",
        timeout_seconds=20,
        expectation=AxeStateExpectation(
            ids=("learning.screen", f"learning.chat.{session.id}"),
        ),
    )
    axe_runner.tap_id(
        f"learning.chat.{session.id}",
        name="chat_feed_discovery_opened",
        timeout_seconds=20,
        expectation=AxeStateExpectation(
            ids=("knowledge.chat_input", feed_button_id),
            texts=(feed_title, f"Add {feed_title}"),
            enabled_ids=(feed_button_id,),
        ),
    )
    axe_runner.tap_id(
        feed_button_id,
        name="chat_feed_added",
        timeout_seconds=15,
        expectation=AxeStateExpectation(
            ids=("knowledge.chat_input", feed_button_id),
            texts=(f"Added {feed_title}",),
        ),
    )

    db_session.expire_all()
    config = (
        db_session.query(UserScraperConfig)
        .filter(
            UserScraperConfig.user_id == test_user.id,
            UserScraperConfig.feed_url == FEED_URL,
        )
        .one()
    )
    assert config.scraper_type == FeedType.ATOM.value
    assert config.display_name == feed_title
    assert config.is_active is True
    assert config.config["feed_url"] == FEED_URL
    assert config.config["limit"] == DEFAULT_NEW_FEED_LIMIT
    backfill = (
        db_session.query(ProcessingTask)
        .filter(
            ProcessingTask.owner_user_id == test_user.id,
            ProcessingTask.task_type == TaskType.BACKFILL_FEEDS.value,
        )
        .one()
    )
    assert backfill.status == TaskStatus.PENDING.value
    assert backfill.queue_name == TaskQueue.BACKFILL.value
    assert backfill.payload == {
        "user_id": test_user.id,
        "config_ids": [config.id],
        "count": DEFAULT_INITIAL_FEED_ARTICLE_DOWNLOAD_COUNT,
    }

    stored_message = (
        db_session.query(ChatMessage).filter(ChatMessage.session_id == session.id).one()
    )
    metadata = ChatMessageRenderMetadata.model_validate(stored_message.render_metadata)
    assert metadata.feed_options == [option]
    assert metadata.feed_options[0].is_subscribed is False

    axe_runner.tap_id(
        "navigation.back",
        name="chat_back_to_learning",
        timeout_seconds=20,
        expectation=AxeStateExpectation(
            ids=("learning.screen", f"learning.chat.{session.id}"),
        ),
    )
    axe_runner.tap_id(
        f"learning.chat.{session.id}",
        name="chat_reopened",
        timeout_seconds=20,
        expectation=AxeStateExpectation(
            ids=("knowledge.chat_input", feed_button_id),
            texts=(f"Already subscribed {feed_title}",),
        ),
    )
    axe_runner.tap_id(
        "navigation.back",
        name="reopened_chat_back",
        expectation=AxeStateExpectation(ids=("learning.screen",)),
    )
    axe_runner.tap_id(
        "tab.knowledge",
        name="knowledge_library_reopened",
        timeout_seconds=15,
        expectation=AxeStateExpectation(
            ids=("knowledge.screen", f"knowledge.saved.{content.id}"),
        ),
    )
    axe_runner.tap_id(
        f"knowledge.saved.{content.id}",
        name="knowledge_detail_reopened",
        timeout_seconds=15,
        expectation=AxeStateExpectation(
            ids=(
                "content.detail.screen",
                f"content.detail.title.{content.id}",
                "content.action.deep_dive",
            ),
            texts=(content.title,),
            enabled_ids=("content.action.deep_dive",),
        ),
    )
    axe_runner.tap_id(
        "content.action.deep_dive",
        name="knowledge_chat_actions",
        expectation=AxeStateExpectation(
            ids=("content.chat.sheet", "content.chat.start"),
            enabled_ids=("content.chat.start",),
        ),
    )
    axe_runner.tap_id(
        "content.chat.start",
        name="knowledge_chat_handoff",
        timeout_seconds=20,
        expectation=AxeStateExpectation(
            ids=("knowledge.chat_input", "knowledge.chat_mic"),
            absent_ids=("content.chat.sheet", "content.detail.screen"),
            enabled_ids=("knowledge.chat_input", "knowledge.chat_mic"),
        ),
    )

    db_session.expire_all()
    handoff_session = (
        db_session.query(ChatSession)
        .filter(
            ChatSession.user_id == test_user.id,
            ChatSession.content_id == content.id,
            ChatSession.id != session.id,
        )
        .one()
    )
    assert handoff_session.title == content.title
    assert handoff_session.session_type == "knowledge_chat"


def test_knowledge_learning_deck_voice_focus_reaches_processing_projection(
    axe_runner,
    live_server,
    test_user,
    db_session,
    content_factory,
    knowledge_save_factory,
) -> None:
    """Knowledge -> deck voice focus should persist and expose a usable pending row."""
    transcript = "Focus on operating tradeoffs"
    content = content_factory(
        title="AXe Learning Deck Source",
        url="https://example.com/axe-learning-deck-source",
        content_metadata={
            "content": "A detailed source about operating tradeoffs for AXe deck creation."
        },
    )
    knowledge_save_factory(user=test_user, content=content)
    axe_runner.launch(
        arguments=_launch_arguments(
            live_server=live_server,
            user_id=test_user.id,
            extra={
                "newslyE2EOpenContentId": content.id,
                "newslyE2EOpenContentType": "article",
                "newslyE2EFakeSpeechEnabled": True,
                "newslyE2EFakeSpeechTranscript": transcript,
            },
        ),
        expectation=AxeStateExpectation(
            ids=("content.detail.screen", "content.action.learning_deck"),
            texts=(content.title,),
        ),
        timeout_seconds=20,
    )
    axe_runner.tap_id(
        "content.action.learning_deck",
        name="learning_deck_create_sheet",
        expectation=AxeStateExpectation(
            ids=(
                "learning_deck.focus_mic",
                "learning_deck.create.submit",
            ),
            texts=("Learning Deck", content.title),
            enabled_ids=("learning_deck.focus_mic", "learning_deck.create.submit"),
        ),
    )
    axe_runner.tap_id(
        "learning_deck.focus_mic",
        name="learning_deck_focus_recording",
        expectation=AxeStateExpectation(
            ids=("learning_deck.focus_mic", "learning_deck.focus_recording"),
            id_values={"learning_deck.focus_mic": "Recording"},
        ),
    )
    axe_runner.tap_id(
        "learning_deck.focus_mic",
        name="learning_deck_focus_transcribed",
        timeout_seconds=15,
        expectation=AxeStateExpectation(
            ids=("learning_deck.create.focus", "learning_deck.create.submit"),
            texts=(transcript,),
            enabled_ids=("learning_deck.create.submit",),
        ),
    )
    axe_runner.tap_id(
        "learning_deck.create.submit",
        name="learning_deck_generating",
        timeout_seconds=15,
        expectation=AxeStateExpectation(ids=("learning_deck.reader.generating",)),
    )

    db_session.expire_all()
    deck = (
        db_session.query(LearningDeck)
        .filter(
            LearningDeck.user_id == test_user.id,
            LearningDeck.source_content_id == content.id,
        )
        .one()
    )
    task = (
        db_session.query(LlmTask)
        .filter(
            LlmTask.user_id == test_user.id,
            LlmTask.task_kind == LlmTaskKind.LEARNING_DECK.value,
            LlmTask.subject_id == deck.id,
        )
        .one()
    )
    assert task.mode == LlmTaskMode.LEARNING_DECK_PRESENTATION.value
    assert task.status == LlmTaskStatus.QUEUED.value
    assert task.input_json["interests_prompt"] == transcript
    queued = (
        db_session.query(ProcessingTask)
        .filter(
            ProcessingTask.owner_user_id == test_user.id,
            ProcessingTask.task_type == TaskType.RUN_LLM_TASK.value,
        )
        .one()
    )
    assert queued.queue_name == TaskQueue.LLM.value
    assert queued.payload == {"llm_task_id": task.id, "user_id": test_user.id}

    axe_runner.tap_id(
        "learning_deck.reader.close",
        name="learning_deck_reader_closed",
        expectation=AxeStateExpectation(ids=("content.detail.screen",)),
    )
    axe_runner.tap_id(
        "navigation.back",
        name="learning_deck_content_closed",
        expectation=AxeStateExpectation(ids=("briefing.screen", "tab.learning")),
    )
    axe_runner.tap_id(
        "tab.learning",
        name="learning_deck_processing_row",
        timeout_seconds=20,
        expectation=AxeStateExpectation(
            ids=(f"learning.deck.{deck.id}",),
            id_values={f"learning.deck.{deck.id}": "Queued"},
        ),
    )


def test_active_tab_reselection_restores_scrolled_headers(
    axe_runner,
    live_server,
    test_user,
    content_factory,
    knowledge_save_factory,
    chat_session_factory,
) -> None:
    """Reselecting a scrolled primary tab should return that path to its top."""
    for index in range(20):
        content = content_factory(
            title=f"AXe Knowledge Item {index + 1}",
            url=f"https://example.com/axe-knowledge-{index + 1}",
        )
        knowledge_save_factory(user=test_user, content=content)
        chat_session_factory(
            user=test_user,
            title=f"AXe Learning Session {index + 1}",
            session_type="knowledge_chat",
        )

    _launch_completed_app(axe_runner, live_server=live_server, user_id=test_user.id)
    knowledge = axe_runner.tap_id(
        "tab.knowledge",
        name="scroll_to_top_knowledge_loaded",
        expectation=AxeStateExpectation(
            ids=("knowledge.screen",),
            texts=("AXe Knowledge Item",),
        ),
        timeout_seconds=15,
    )
    _scroll_until_id_absent(
        axe_runner,
        initial_tree=knowledge.tree,
        identifier="knowledge.screen",
        persistent_identifier="tab.knowledge",
        path_name="knowledge",
    )
    axe_runner.tap_id(
        "tab.knowledge",
        name="scroll_to_top_knowledge_restored",
        expectation=AxeStateExpectation(ids=("knowledge.screen",)),
    )

    learning = axe_runner.tap_id(
        "tab.learning",
        name="scroll_to_top_learning_loaded",
        expectation=AxeStateExpectation(
            ids=("learning.screen",),
            texts=("AXe Learning Session",),
        ),
        timeout_seconds=15,
    )
    _scroll_until_id_absent(
        axe_runner,
        initial_tree=learning.tree,
        identifier="learning.screen",
        persistent_identifier="tab.learning",
        path_name="learning",
    )
    axe_runner.tap_id(
        "tab.learning",
        name="scroll_to_top_learning_restored",
        expectation=AxeStateExpectation(ids=("learning.screen",)),
    )


def test_no_action_submission_exposes_rationale_and_share_extension_recovery(
    axe_runner,
    live_server,
    test_user,
    db_session,
) -> None:
    """A terminal no-action result should explain itself and reopen sharing safely."""
    task = create_llm_task(
        db_session,
        user_id=test_user.id,
        task_kind=LlmTaskKind.SHARE_ACTION,
        mode=LlmTaskMode.ADD_TO_BRIEFING,
        workflow_key="share_action.add_to_briefing.v1",
        allowed_actions=["add_to_briefing"],
        tool_policy={"execute_bash": True, "web_search": True, "files": "read_write"},
        prompt_pack="share_action.add_to_briefing",
        input_json={
            "url": NO_ACTION_URL,
            "mode": LlmTaskMode.ADD_TO_BRIEFING.value,
        },
    )
    set_llm_task_status(
        db_session,
        task,
        status=LlmTaskStatus.COMPLETED,
        workflow_state=LlmWorkflowState.COMPLETED,
        note="AXe no-action fixture completed",
        output_json={
            "action": "no_action",
            "title": NO_ACTION_TITLE,
            "rationale": NO_ACTION_RATIONALE,
        },
    )
    db_session.commit()
    task_id = require_llm_task_id(task)

    _launch_completed_app(axe_runner, live_server=live_server, user_id=test_user.id)
    axe_runner.tap_id(
        "tab.knowledge",
        name="no_action_knowledge_tab",
        expectation=AxeStateExpectation(ids=("knowledge.screen",)),
    )
    axe_runner.tap_id(
        "knowledge.more_menu",
        name="no_action_more_sheet",
        expectation=AxeStateExpectation(ids=("more.screen",), texts=("Submissions",)),
    )
    axe_runner.tap_id(
        "more.submissions",
        name="no_action_submissions",
        timeout_seconds=15,
        expectation=AxeStateExpectation(
            ids=(f"submission.row.{task_id}",),
            texts=("Submissions", NO_ACTION_TITLE, "NO ACTION TAKEN", NO_ACTION_RATIONALE),
        ),
    )
    axe_runner.tap_id(
        f"submission.row.{task_id}",
        name="no_action_submission_detail",
        expectation=AxeStateExpectation(
            ids=("submission.no_action.retry",),
            texts=("No action taken", NO_ACTION_RATIONALE, "Share Again"),
            enabled_ids=("submission.no_action.retry",),
        ),
    )
    axe_runner.tap_id_label(
        "submission.no_action.retry",
        "Share Again",
        name="no_action_system_share_sheet",
        expectation=AxeStateExpectation(
            ids=("PopoverDismissRegion",),
            absent_ids=("submission.no_action.retry",),
        ),
    )

    db_session.expire_all()
    persisted_task = db_session.query(LlmTask).filter(LlmTask.id == task_id).one()
    assert persisted_task.status == LlmTaskStatus.COMPLETED.value
    assert persisted_task.output_json["rationale"] == NO_ACTION_RATIONALE


def test_failed_share_submission_exposes_share_extension_recovery(
    axe_runner,
    live_server,
    test_user,
    db_session,
) -> None:
    """A failed Share Action should expose a usable retry instead of ending the path."""
    task = create_llm_task(
        db_session,
        user_id=test_user.id,
        task_kind=LlmTaskKind.SHARE_ACTION,
        mode=LlmTaskMode.ADD_CONTENT,
        workflow_key="share_action.add_content.v1",
        allowed_actions=["add_content"],
        tool_policy={"execute_bash": True, "web_search": True, "files": "read_write"},
        prompt_pack="share_action.add_content",
        input_json={
            "url": FAILED_SHARE_URL,
            "mode": LlmTaskMode.ADD_CONTENT.value,
        },
    )
    set_llm_task_status(
        db_session,
        task,
        status=LlmTaskStatus.FAILED,
        workflow_state=LlmWorkflowState.FAILED,
        note="AXe failed-share fixture completed",
        error_type="provider_error",
        error_message="Internal provider detail must not be shown",
        output_json={"title": FAILED_SHARE_TITLE},
    )
    db_session.commit()
    task_id = require_llm_task_id(task)

    _launch_completed_app(axe_runner, live_server=live_server, user_id=test_user.id)
    axe_runner.tap_id(
        "tab.knowledge",
        name="failed_share_knowledge_tab",
        expectation=AxeStateExpectation(ids=("knowledge.screen",)),
    )
    axe_runner.tap_id(
        "knowledge.more_menu",
        name="failed_share_more_sheet",
        expectation=AxeStateExpectation(ids=("more.screen",), texts=("Submissions",)),
    )
    axe_runner.tap_id(
        "more.submissions",
        name="failed_share_submissions",
        timeout_seconds=15,
        expectation=AxeStateExpectation(
            ids=(f"submission.row.{task_id}",),
            texts=("Submissions", FAILED_SHARE_TITLE, "FAILED"),
        ),
    )
    axe_runner.tap_id(
        f"submission.row.{task_id}",
        name="failed_share_submission_detail",
        expectation=AxeStateExpectation(
            ids=("submission.retry",),
            texts=("Failed", "Share Again"),
            enabled_ids=("submission.retry",),
        ),
    )
    axe_runner.tap_id_label(
        "submission.retry",
        "Share Again",
        name="failed_share_system_share_sheet",
        expectation=AxeStateExpectation(
            ids=("PopoverDismissRegion",),
            absent_ids=("submission.retry",),
        ),
    )

    db_session.expire_all()
    persisted_task = db_session.query(LlmTask).filter(LlmTask.id == task_id).one()
    assert persisted_task.status == LlmTaskStatus.FAILED.value
    assert persisted_task.error_message == "Internal provider detail must not be shown"


def test_mixed_search_subscribes_feed_and_adds_podcast_over_live_api(
    axe_runner,
    live_server,
    test_user,
    db_session,
    monkeypatch,
) -> None:
    """Search discovery actions should survive SwiftUI -> API -> DB -> queue."""
    discovery_calls: dict[str, dict[str, object]] = {}

    def fake_find_feed_options(query, limit, **kwargs):
        discovery_calls["feeds"] = {"query": query, "limit": limit, **kwargs}
        return AssistantFeedOptionsResult(
            query=query,
            options=[
                AssistantFeedOption(
                    id=FEED_ID,
                    title="AXe Engineering Feed",
                    site_url="https://feeds.example.test",
                    feed_url=FEED_URL,
                    feed_type=FeedType.ATOM,
                    feed_format=FeedFormat.RSS,
                    description="Deterministic feed-discovery result.",
                    rationale="Exercises the external feed result action boundary.",
                    evidence_url="https://feeds.example.test/about",
                )
            ][:limit],
        )

    def fake_search_podcast_episodes(query, limit, **kwargs):
        discovery_calls["podcasts"] = {"query": query, "limit": limit, **kwargs}
        return [
            PodcastEpisodeSearchHit(
                title=f"{query.title()} AXe Podcast Episode",
                episode_url=PODCAST_EPISODE_URL,
                podcast_title="AXe Engineering Podcast",
                source="ios-e2e",
                snippet="A deterministic podcast result for the full action path.",
                feed_url=PODCAST_FEED_URL,
                published_at="2026-08-07T12:00:00Z",
                provider="ios-e2e",
                score=1.0,
            )
        ][:limit]

    monkeypatch.setattr(
        search_mixed,
        "find_feed_options",
        fake_find_feed_options,
    )
    monkeypatch.setattr(
        search_mixed,
        "search_podcast_episodes",
        fake_search_podcast_episodes,
    )
    stub_feed_validator(monkeypatch, title="AXe Validated Feed")

    _launch_completed_app(axe_runner, live_server=live_server, user_id=test_user.id)
    _navigate_to_search(axe_runner)

    axe_runner.tap_id(
        "search.input",
        name="search_input_focused",
        expectation=AxeStateExpectation(ids=("search.input",), texts=("Search",)),
    )
    query = "axe engineering"
    axe_runner.type_text(
        query,
        name="search_query_typed",
        expectation=AxeStateExpectation(
            ids=("search.input",),
            id_values={"search.input": query},
        ),
    )
    search_results = axe_runner.tap_label(
        "Search feeds, sources, and podcasts",
        element_type="Button",
        name="mixed_search_results",
        timeout_seconds=20,
        expectation=AxeStateExpectation(
            ids=(f"search.feed.{FEED_ID}",),
            texts=("AXe Engineering Feed", "Subscribe"),
        ),
    )
    feed_subscribed = axe_runner.tap_id_label(
        f"search.feed.{FEED_ID}",
        "Subscribe",
        name="feed_subscribed",
        timeout_seconds=15,
        expectation=AxeStateExpectation(
            ids=(f"search.feed.{FEED_ID}",),
            texts=("Subscribed",),
        ),
    )

    podcast_result_id = f"search.podcast.{PODCAST_EPISODE_URL}"
    _scroll_until_id(
        axe_runner,
        initial_tree=feed_subscribed.tree or search_results.tree,
        identifier=podcast_result_id,
    )
    podcast_added = axe_runner.tap_id_label(
        podcast_result_id,
        "Add Item",
        name="podcast_episode_added",
        timeout_seconds=15,
        expectation=AxeStateExpectation(ids=(podcast_result_id,), texts=("Added",)),
    )

    if not tree_has_id(podcast_added.tree, podcast_result_id):
        _scroll_until_id(
            axe_runner,
            initial_tree=podcast_added.tree,
            identifier=podcast_result_id,
        )
    axe_runner.tap_id_label(
        podcast_result_id,
        "Subscribe",
        name="podcast_feed_subscribed",
        timeout_seconds=15,
        expectation=AxeStateExpectation(
            ids=(podcast_result_id,),
            texts=("Subscribed",),
        ),
    )

    db_session.expire_all()
    subscribed_urls = {
        row.feed_url
        for row in db_session.query(UserScraperConfig)
        .filter(UserScraperConfig.user_id == test_user.id)
        .all()
    }
    assert {FEED_URL, PODCAST_FEED_URL}.issubset(subscribed_urls)
    added_episode = (
        db_session.query(Content).filter(Content.url == PODCAST_EPISODE_URL).one_or_none()
    )
    assert added_episode is not None
    assert added_episode.title == "Axe Engineering AXe Podcast Episode"
    assert discovery_calls["feeds"]["query"] == query
    assert discovery_calls["feeds"]["limit"] == 5
    assert discovery_calls["feeds"]["user_id"] == test_user.id
    assert isinstance(discovery_calls["feeds"]["deadline"], float)
    assert discovery_calls["podcasts"]["query"] == query
    assert discovery_calls["podcasts"]["limit"] == 10
    assert isinstance(discovery_calls["podcasts"]["deadline"], float)


@pytest.mark.parametrize(
    ("scenario", "expected_error", "automatic_success"),
    [
        pytest.param("success", None, False, id="success"),
        pytest.param("empty", "I didn't catch that. Try again.", False, id="empty"),
        pytest.param("start_failure", "Failed to record audio.", False, id="start-failure"),
        pytest.param(
            "transcription_failure",
            "Transcription failed: The scripted transcription failed.",
            False,
            id="transcription-failure",
        ),
        pytest.param("silence_auto_stop", None, True, id="silence-auto-stop"),
        pytest.param(
            "no_speech_timeout",
            "No speech detected. Try again.",
            False,
            id="no-speech-timeout",
        ),
        pytest.param("maximum_duration", None, True, id="maximum-duration"),
    ],
)
def test_chat_voice_scenario_has_terminal_state_and_recovery(
    scenario,
    expected_error,
    automatic_success,
    axe_runner,
    live_server,
    test_user,
    chat_session_factory,
    db_session,
    completed_chat_processors_factory,
    monkeypatch,
) -> None:
    """Every scripted voice outcome should either send or leave a manual escape."""
    transcript = f"AXe voice transcript for {scenario}"
    recovery_message = f"Axe manual recovery for {scenario}"
    assistant_reply = f"AXe assistant reply for {scenario}"
    session = chat_session_factory(
        user=test_user,
        title=f"AXe Voice {scenario}",
        session_type="knowledge_chat",
    )
    complete_queued_turn = completed_chat_processors_factory(assistant_reply=assistant_reply)
    monkeypatch.setattr(
        "app.commands.send_chat_message.stage_queued_chat_turn",
        complete_queued_turn,
    )

    axe_runner.launch(
        arguments=_launch_arguments(
            live_server=live_server,
            user_id=test_user.id,
            extra={
                "newslyE2EOpenChatSessionId": session.id,
                "newslyE2EFakeSpeechEnabled": True,
                "newslyE2EFakeSpeechTranscript": transcript,
                "newslyE2EFakeSpeechScenario": scenario,
            },
        ),
        expectation=AxeStateExpectation(
            ids=("knowledge.chat_input", "knowledge.chat_mic"),
            id_values={"knowledge.chat_mic": "Idle"},
        ),
        timeout_seconds=20,
    )

    if scenario == "start_failure":
        axe_runner.tap_id(
            "knowledge.chat_mic",
            name="voice_start_failed",
            timeout_seconds=10,
            expectation=AxeStateExpectation(
                ids=("knowledge.chat_error_banner", "knowledge.chat_mic"),
                texts=(expected_error or "",),
                id_values={"knowledge.chat_mic": "Idle"},
                enabled_ids=("knowledge.chat_mic",),
            ),
        )
    elif automatic_success:
        axe_runner.tap_id(
            "knowledge.chat_mic",
            name="voice_auto_stop_completed",
            timeout_seconds=20,
            expectation=AxeStateExpectation(
                ids=("knowledge.chat_mic",),
                texts=(assistant_reply,),
                id_values={"knowledge.chat_mic": "Idle"},
                enabled_ids=("knowledge.chat_mic",),
            ),
        )
    elif scenario == "no_speech_timeout":
        axe_runner.tap_id(
            "knowledge.chat_mic",
            name="voice_no_speech_timeout",
            timeout_seconds=10,
            expectation=AxeStateExpectation(
                ids=("knowledge.chat_error_banner", "knowledge.chat_mic"),
                texts=(expected_error or "",),
                id_values={"knowledge.chat_mic": "Idle"},
                enabled_ids=("knowledge.chat_mic",),
            ),
        )
    else:
        axe_runner.tap_id(
            "knowledge.chat_mic",
            name="voice_recording",
            expectation=AxeStateExpectation(
                ids=("knowledge.chat_mic",),
                id_values={"knowledge.chat_mic": "Recording"},
            ),
        )
        terminal_expectation = (
            AxeStateExpectation(
                ids=("knowledge.chat_error_banner", "knowledge.chat_mic"),
                texts=(expected_error,),
                id_values={"knowledge.chat_mic": "Idle"},
                enabled_ids=("knowledge.chat_mic",),
            )
            if expected_error
            else AxeStateExpectation(
                ids=("knowledge.chat_mic",),
                texts=(assistant_reply,),
                id_values={"knowledge.chat_mic": "Idle"},
                enabled_ids=("knowledge.chat_mic",),
            )
        )
        axe_runner.tap_id(
            "knowledge.chat_mic",
            name="voice_manual_stop_terminal",
            timeout_seconds=20,
            expectation=terminal_expectation,
        )

    sent_text = transcript
    if expected_error:
        axe_runner.tap_id(
            "knowledge.chat_input",
            name="voice_error_manual_input",
            expectation=AxeStateExpectation(
                ids=("knowledge.chat_input", "knowledge.chat_error_banner"),
                texts=(expected_error,),
            ),
        )
        axe_runner.type_text(
            recovery_message,
            name="voice_error_manual_text",
            expectation=AxeStateExpectation(
                ids=("knowledge.chat_input", "knowledge.chat_send"),
                id_values={"knowledge.chat_input": recovery_message},
            ),
        )
        axe_runner.tap_id(
            "knowledge.chat_send",
            name="voice_error_recovered_send",
            timeout_seconds=20,
            expectation=AxeStateExpectation(
                ids=("knowledge.chat_mic",),
                texts=(assistant_reply,),
                id_values={"knowledge.chat_mic": "Idle"},
            ),
        )
        sent_text = recovery_message

    db_session.expire_all()
    latest_message = (
        db_session.query(ChatMessage)
        .filter(ChatMessage.session_id == session.id)
        .order_by(ChatMessage.id.desc())
        .first()
    )
    assert latest_message is not None
    assert latest_message.status == "completed"
    assert sent_text in (latest_message.message_list or "")
    assert assistant_reply in (latest_message.message_list or "")


def test_personalized_onboarding_voice_reaches_live_completion(
    axe_runner,
    live_server,
    ios_onboarding_personalized_fixture,
    test_user,
    db_session,
    monkeypatch,
) -> None:
    """Personalized fake speech should traverse discovery UI and persist completion."""
    test_user.has_completed_onboarding = False
    test_user.has_completed_new_user_tutorial = False
    db_session.commit()
    stub_feed_validator(monkeypatch, title="AXe Onboarding Feed")

    axe_runner.launch(
        arguments=_launch_arguments(
            live_server=live_server,
            user_id=test_user.id,
            completed_onboarding=False,
            completed_tutorial=False,
            extra={
                "newslyE2EFakeSpeechEnabled": True,
                "newslyE2EFakeSpeechScenario": "success",
                "newslyE2EOnboardingFixture": ios_onboarding_personalized_fixture,
            },
        ),
        expectation=AxeStateExpectation(ids=("onboarding.choice.screen",)),
        timeout_seconds=20,
    )
    axe_runner.tap_id(
        "onboarding.choice.personalized",
        name="onboarding_personalized_voice",
        timeout_seconds=10,
        expectation=AxeStateExpectation(
            ids=("onboarding.audio.screen", "onboarding.audio.state.recording"),
        ),
    )
    axe_runner.tap_id(
        "onboarding.audio.mic",
        name="onboarding_voice_discovery_results",
        timeout_seconds=20,
        expectation=AxeStateExpectation(
            ids=(
                "onboarding.suggestions.screen",
                "onboarding.suggestion.https://www.latent.space/feed",
                "onboarding.suggestion.https://www.lennysnewsletter.com/feed",
            ),
        ),
    )
    axe_runner.tap_id(
        "onboarding.suggestions.continue",
        name="onboarding_aggregators",
        expectation=AxeStateExpectation(ids=("onboarding.aggregators.screen",)),
    )
    axe_runner.tap_id(
        "onboarding.aggregators.continue",
        name="onboarding_reddit",
        expectation=AxeStateExpectation(ids=("onboarding.reddit.screen",)),
    )
    axe_runner.tap_id(
        "onboarding.complete",
        name="onboarding_completed",
        timeout_seconds=20,
        expectation=AxeStateExpectation(
            ids=("briefing.screen",),
            absent_ids=("onboarding.reddit.screen",),
        ),
    )

    db_session.expire_all()
    db_session.refresh(test_user)
    assert test_user.has_completed_onboarding is True
    assert (
        db_session.query(UserScraperConfig)
        .filter(UserScraperConfig.user_id == test_user.id)
        .count()
        >= 2
    )
