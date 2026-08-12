"""Maestro-backed iOS end-to-end tests using shared backend fixtures."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from sqlalchemy import event

from app.core.settings import get_settings
from app.models.contracts import (
    LlmTaskKind,
    LlmTaskMode,
    LlmTaskStatus,
    LlmWorkflowState,
    TaskType,
)
from app.models.db import (
    ChatMessage,
    ChatSession,
    ContentKnowledgeSave,
    ContentReadStatus,
    LearningDeck,
    LearningDeckRun,
    LlmTask,
    NewsItem,
    NewsItemDiscussion,
    OnboardingDiscoveryLane,
    OnboardingDiscoveryRun,
    ProcessingTask,
    UserScraperConfig,
)
from app.services.briefing.presentation import get_briefing_lens
from app.services.briefing.refresh import run_briefing_refresh
from app.services.chat_agent import ChatRunResult, save_messages
from app.services.llm_tasks import create_llm_task
from app.services.onboarding import run_audio_discovery
from app.services.onboarding.internal_models import (
    _AudioLane,
    _AudioPlanOutput,
    _DiscoverOutput,
    _DiscoverSuggestion,
    _DiscoveryWebResult,
)
from app.services.tweet_suggestions import TweetSuggestionData, TweetSuggestionsResult
from tests.support.feed_subscription_test_helpers import stub_feed_validator

pytestmark = [
    pytest.mark.integration,
    pytest.mark.ios_e2e,
    pytest.mark.usefixtures("stub_briefing_layout_generator"),
]


def _personalized_onboarding_plan() -> _AudioPlanOutput:
    return _AudioPlanOutput(
        topic_summary="Semiconductors, AI infrastructure, and engineering management.",
        inferred_topics=["semiconductors", "AI infrastructure", "engineering management"],
        lanes=[
            _AudioLane(
                name="AI Newsletters",
                goal="Find newsletters about AI infra, semis, and software teams.",
                target="feeds",
                queries=["AI infrastructure newsletters", "semiconductor substack"],
            ),
            _AudioLane(
                name="Podcasts",
                goal="Find podcasts about company strategy and technical systems.",
                target="podcasts",
                queries=["AI infrastructure podcast", "semiconductor podcast rss"],
            ),
            _AudioLane(
                name="Reddit",
                goal="Find active communities for model builders and practitioners.",
                target="reddit",
                queries=["AI infrastructure subreddit", "semiconductor reddit"],
            ),
        ],
    )


def _personalized_onboarding_output() -> _DiscoverOutput:
    return _DiscoverOutput(
        substacks=[
            _DiscoverSuggestion(
                title="Stratechery",
                site_url="https://stratechery.com",
                feed_url="https://stratechery.com/feed",
                rationale="High-signal strategy analysis on large tech companies.",
                score=0.97,
            ),
            _DiscoverSuggestion(
                title="Latent Space",
                site_url="https://www.latent.space",
                feed_url="https://www.latent.space/feed",
                rationale="Dense AI builder coverage with technical signal.",
                score=0.95,
            ),
        ],
        podcasts=[
            _DiscoverSuggestion(
                title="Hard Fork",
                site_url="https://www.nytimes.com/column/hard-fork",
                feed_url="https://feeds.simplecast.com/54nAGcIl",
                rationale="Timely discussion of major AI and tech developments.",
                score=0.94,
            ),
            _DiscoverSuggestion(
                title="Decoder",
                site_url="https://www.theverge.com/decoder-podcast-with-nilay-patel",
                feed_url="https://feeds.megaphone.fm/vergecast",
                rationale="Founder and operator interviews around tech strategy.",
                score=0.93,
            ),
            _DiscoverSuggestion(
                title="Software Engineering Daily",
                site_url="https://softwareengineeringdaily.com",
                feed_url="https://softwareengineeringdaily.com/feed/podcast/",
                rationale="Reliable technical interviews on infra and software systems.",
                score=0.91,
            ),
            _DiscoverSuggestion(
                title="Invest Like the Best",
                site_url="https://www.joincolossus.com/episodes",
                feed_url="https://feeds.megaphone.fm/colossus",
                rationale="Strong operator and investor conversations on market shifts.",
                score=0.90,
            ),
        ],
        subreddits=[
            _DiscoverSuggestion(
                title="LocalLLaMA",
                site_url="https://reddit.com/r/LocalLLaMA",
                subreddit="LocalLLaMA",
                rationale="Active discussion of open model tooling and infra.",
                score=0.89,
            ),
            _DiscoverSuggestion(
                title="MachineLearning",
                site_url="https://reddit.com/r/MachineLearning",
                subreddit="MachineLearning",
                rationale="Broad research and industry discussion with useful links.",
                score=0.88,
            ),
        ],
    )


def test_long_form_detail_knowledge_save_action_updates_backend_state(
    run_ios_flow,
    create_sample_content,
    sample_article_long,
    test_user,
    db_session,
) -> None:
    """Saving to knowledge from the detail screen should persist to the shared backend DB."""
    content = create_sample_content(sample_article_long)

    run_ios_flow(
        "long_form_save_to_knowledge.yaml",
        extra_env={
            "CONTENT_ID": str(content.id),
            "CONTENT_TITLE": content.title,
        },
    )

    knowledge_save = (
        db_session.query(ContentKnowledgeSave)
        .filter(
            ContentKnowledgeSave.user_id == test_user.id,
            ContentKnowledgeSave.content_id == content.id,
        )
        .one_or_none()
    )
    assert knowledge_save is not None


def test_long_form_detail_learning_deck_create_ignores_failed_legacy_attempt(
    run_ios_flow,
    create_sample_content,
    sample_article_long,
    test_user,
    db_session,
) -> None:
    """A reconciled legacy queue failure should not make Create appear unresponsive."""
    stale_deck = LearningDeck(
        user_id=test_user.id,
        source_kind="content",
        source_identity="content:failed-legacy-attempt",
        source_url="https://example.com/failed-legacy-attempt",
        source_title="Failed legacy attempt",
        source_metadata={},
        title="Failed legacy attempt",
        artifact_object_keys=[],
        share_enabled=False,
    )
    db_session.add(stale_deck)
    db_session.flush()
    stale_task = create_llm_task(
        db_session,
        user_id=test_user.id,
        task_kind=LlmTaskKind.LEARNING_DECK,
        mode=LlmTaskMode.LEARNING_DECK_PRESENTATION,
        workflow_key="learning_deck.presentation.v1",
        subject_id=stale_deck.id,
    )
    stale_task.status = LlmTaskStatus.FAILED.value
    stale_task.workflow_state = LlmWorkflowState.FAILED.value
    stale_task.error_type = "queue_task_failed"
    stale_task.error_message = "Source content is still processing"
    stale_run = LearningDeckRun(
        deck_id=stale_deck.id,
        user_id=test_user.id,
        llm_task_id=stale_task.id,
        status="failed",
        source_snapshot={},
        timeline=[],
        artifact_object_keys=[],
        error_message="Source content is still processing",
    )
    db_session.add(stale_run)
    db_session.flush()
    stale_deck.latest_task_id = stale_task.id
    stale_deck.latest_run_id = stale_run.id
    db_session.commit()

    content = create_sample_content(sample_article_long)

    run_ios_flow(
        "learning_deck_create_from_content.yaml",
        extra_env={"CONTENT_ID": str(content.id)},
    )

    deck = (
        db_session.query(LearningDeck)
        .filter(
            LearningDeck.user_id == test_user.id,
            LearningDeck.source_content_id == content.id,
        )
        .one_or_none()
    )
    assert deck is not None
    db_session.refresh(stale_run)
    db_session.refresh(stale_task)
    assert stale_run.status == "failed"
    assert stale_task.status == LlmTaskStatus.FAILED.value


def test_learning_tab_long_press_regenerates_deck_with_existing_focus(
    run_ios_flow,
    create_sample_content,
    sample_article_long,
    test_user,
    db_session,
) -> None:
    content = create_sample_content(sample_article_long)
    deck = LearningDeck(
        user_id=test_user.id,
        source_kind="content",
        source_identity=f"content:{content.id}",
        source_url=content.source_url or content.url,
        source_content_id=content.id,
        source_title=content.title,
        source_metadata={"content_type": content.content_type},
        title=content.title,
        artifact_object_keys=[],
        share_enabled=False,
    )
    db_session.add(deck)
    db_session.flush()
    original_task = create_llm_task(
        db_session,
        user_id=test_user.id,
        task_kind=LlmTaskKind.LEARNING_DECK,
        mode=LlmTaskMode.LEARNING_DECK_PRESENTATION,
        workflow_key="learning_deck.presentation.v1",
        subject_id=deck.id,
        input_json={
            "deck_id": deck.id,
            "interests_prompt": "Focus on the existing tradeoffs",
        },
        status=LlmTaskStatus.COMPLETED,
        workflow_state=LlmWorkflowState.COMPLETED,
    )
    deck.latest_task_id = original_task.id
    deck.latest_successful_task_id = original_task.id
    db_session.commit()

    run_ios_flow(
        "learning_deck_regenerate_from_learning.yaml",
        extra_env={"DECK_ID": str(deck.id)},
    )

    task_query = db_session.query(LlmTask).filter(LlmTask.subject_id == deck.id)
    tasks = task_query.order_by(LlmTask.id).all()
    assert len(tasks) == 2
    assert tasks[-1].status == LlmTaskStatus.QUEUED.value
    assert tasks[-1].input_json["interests_prompt"] == "Focus on the existing tradeoffs"


def test_more_search_detail_chat_handoff_dismisses_sheet_and_shows_chat(
    run_ios_flow,
    create_sample_content,
    sample_article_long,
    test_user,
    db_session,
) -> None:
    fixture = deepcopy(sample_article_long)
    fixture["title"] = "Maestro Search Handoff Article"
    content = create_sample_content(fixture)

    run_ios_flow(
        "more_search_detail_chat_handoff.yaml",
        extra_env={
            "CONTENT_ID": str(content.id),
            "QUERY": "Maestro Search Handoff",
        },
    )

    db_session.expire_all()
    session = (
        db_session.query(ChatSession)
        .filter(
            ChatSession.user_id == test_user.id,
            ChatSession.content_id == content.id,
        )
        .one_or_none()
    )
    assert session is not None


def test_tweet_adjustment_fake_speech_regenerates_visible_suggestions(
    run_ios_flow,
    create_sample_content,
    sample_article_long,
    monkeypatch,
) -> None:
    content = create_sample_content(sample_article_long)
    transcript = "Focus on the surprising tradeoff"

    def _fake_generate_tweet_suggestions(
        *,
        content,
        message,
        creativity,
        length,
        llm_provider,
    ):
        del content, llm_provider
        prefix = "Voice-adjusted" if message == transcript else "Baseline"
        return TweetSuggestionsResult(
            content_id=content_id,
            creativity=creativity,
            length=length,
            model="ios-e2e",
            suggestions=[
                TweetSuggestionData(
                    id=index,
                    text=f"{prefix} tweet {index}",
                    style_label="test",
                )
                for index in range(1, 4)
            ],
        )

    content_id = content.id
    monkeypatch.setattr(
        "app.commands.generate_tweet_suggestions.generate_tweet_suggestions",
        _fake_generate_tweet_suggestions,
    )

    run_ios_flow(
        "tweet_fake_speech.yaml",
        extra_env={
            "CONTENT_ID": str(content.id),
            "TRANSCRIPT": transcript,
        },
    )


def test_learning_deck_focus_fake_speech_populates_focus_field(
    run_ios_flow,
    create_sample_content,
    sample_article_long,
) -> None:
    content = create_sample_content(sample_article_long)

    run_ios_flow(
        "learning_deck_focus_fake_speech.yaml",
        extra_env={
            "CONTENT_ID": str(content.id),
            "TRANSCRIPT": "Focus on operating tradeoffs",
        },
    )


def test_long_form_list_mark_read_action_updates_backend_state(
    run_ios_flow,
    create_sample_content,
    sample_article_long,
    test_user,
    db_session,
) -> None:
    """Mark-all-read from Briefing should persist the source read state."""
    content = create_sample_content(sample_article_long)
    test_user.reading_experience = "briefing"
    settings = get_settings().model_copy(
        update={
            "briefing_enabled_user_ids": [test_user.id],
            "briefing_window_min": 1,
            "briefing_debounce_seconds": 0,
            "briefing_pending_max_age_seconds": 60,
        }
    )
    refresh = run_briefing_refresh(
        db_session,
        user_id=test_user.id,
        mode="full",
        use_llm=False,
        settings=settings,
    )
    db_session.commit()
    lens = get_briefing_lens(db_session, user_id=test_user.id, lens_key="articles")
    assert refresh.appended_segments == 1
    assert lens is not None
    assert len(lens.segments) == 1

    run_ios_flow(
        "long_form_mark_read.yaml",
        extra_env={"SEGMENT_ID": str(lens.segments[0].id)},
    )

    read_status = (
        db_session.query(ContentReadStatus)
        .filter(
            ContentReadStatus.user_id == test_user.id,
            ContentReadStatus.content_id == content.id,
        )
        .one_or_none()
    )
    assert read_status is not None


def test_short_form_detail_renders_comment_key_topics_inline(
    run_ios_flow,
    db_session,
    test_user,
) -> None:
    """Stored discussion topics should render inline without a Discussion sheet."""
    summary_overview = (
        "Developers compare parser ergonomics, runtime tradeoffs, and deployment risks."
    )
    topic_title = "Parser ergonomics"
    topic_summary = "Commenters focus on simpler grammar changes and clearer failures."
    topic_stance = "Mostly supportive, with concerns about migration cost."
    news_item = NewsItem(
        ingest_key="ios-e2e-discussion-summary",
        visibility_scope="user",
        owner_user_id=test_user.id,
        platform="hackernews",
        source_type="hackernews",
        source_label="Hacker News",
        source_external_id="ios-e2e-discussion-summary",
        canonical_item_url="https://news.ycombinator.com/item?id=515151",
        canonical_story_url="https://example.com/parser-runtime",
        article_url="https://example.com/parser-runtime",
        article_title="A Practical Parser Runtime for Production Systems",
        article_domain="example.com",
        discussion_url="https://news.ycombinator.com/item?id=515151",
        summary_title="A Practical Parser Runtime for Production Systems",
        summary_key_points=[
            "The runtime emphasizes predictable production behavior over maximal cleverness."
        ],
        summary_text="A practical parser runtime for production systems.",
        raw_metadata={
            "discussion_url": "https://news.ycombinator.com/item?id=515151",
            "summary": {
                "article_url": "https://example.com/parser-runtime",
                "summary": "A practical parser runtime for production systems.",
                "key_points": [
                    "The runtime emphasizes predictable production behavior over "
                    "maximal cleverness."
                ],
            },
        },
        status="ready",
        published_at=datetime.now(UTC).replace(tzinfo=None),
        ingested_at=datetime.now(UTC).replace(tzinfo=None),
        processed_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db_session.add(news_item)
    db_session.flush()
    db_session.add(
        NewsItemDiscussion(
            news_item_id=news_item.id,
            platform="hackernews",
            external_id="515151",
            discussion_url="https://news.ycombinator.com/item?id=515151",
            title=news_item.summary_title,
            comment_count=37,
            fetched_comment_count=24,
            raw_comments_ref={"provider": "local", "key": "ios-e2e-discussion-summary.json"},
            raw_comments_sha256="0" * 64,
            summary={
                "overview": summary_overview,
                "topics": [
                    {
                        "title": topic_title,
                        "summary": topic_summary,
                        "stance": topic_stance,
                    }
                ],
                "notable_links": [
                    {
                        "url": "https://example.com/parser-notes",
                        "title": "Parser notes",
                        "reason": "Background material linked by the discussion.",
                    }
                ],
                "representative_comments": [
                    {
                        "comment_id": "summary-comment-1",
                        "author": "alice",
                        "text": "The ergonomics matter more than another percent of throughput.",
                        "reason": "Captures the thread's practical framing.",
                    }
                ],
                "external_discussion_url": "https://news.ycombinator.com/item?id=515151",
                "generated_at": datetime.now(UTC).isoformat(),
            },
            summary_status="completed",
            summary_version=1,
            summary_model="test-model",
            summary_generated_at=datetime.now(UTC).replace(tzinfo=None),
            last_refresh_status="completed",
            last_comments_fetched_at=datetime.now(UTC).replace(tzinfo=None),
        )
    )
    db_session.commit()

    run_ios_flow(
        "short_form_discussion_summary.yaml",
        extra_env={
            "CONTENT_ID": str(news_item.id),
            "TOPIC_STANCE": topic_stance.upper(),
            "TOPIC_SUMMARY": topic_summary,
        },
    )


def test_council_tabs_switch_between_mocked_branch_replies(
    run_ios_flow,
    create_sample_content,
    sample_article_long,
    test_user,
    chat_session_factory,
    db_session,
    monkeypatch,
) -> None:
    """Council mode should switch visible branch replies using deterministic mocked backend data."""
    content = create_sample_content(sample_article_long)
    test_user.council_personas = [
        {
            "id": "paul_graham",
            "display_name": "Paul Graham",
            "instruction_prompt": "",
            "sort_order": 0,
        },
        {
            "id": "ben_thompson",
            "display_name": "Ben Thompson",
            "instruction_prompt": "",
            "sort_order": 1,
        },
        {
            "id": "byrne_hobart",
            "display_name": "Byrne Hobart",
            "instruction_prompt": "",
            "sort_order": 2,
        },
    ]
    db_session.commit()
    db_session.refresh(test_user)

    session = chat_session_factory(
        user=test_user,
        content=content,
        title="Mocked Council Session",
        session_type="knowledge_chat",
    )
    save_messages(
        db_session,
        session.id,
        [
            ModelRequest(parts=[UserPromptPart(content="Summarize the article.")]),
            ModelResponse(parts=[TextPart(content="Initial mocked assistant reply.")]),
        ],
        display_user_prompt="Summarize the article.",
    )

    async def _fake_run_chat_turn(db, branch_session, user_prompt, source="chat"):
        del source
        assistant_text = f"{branch_session.council_persona_name} mocked council reply"
        messages = [
            ModelRequest(parts=[UserPromptPart(content=user_prompt)]),
            ModelResponse(parts=[TextPart(content=assistant_text)]),
        ]
        save_messages(db, branch_session.id, messages, display_user_prompt=user_prompt)
        return ChatRunResult(
            output_text=assistant_text,
            new_messages=messages,
        )

    monkeypatch.setattr("app.services.council_chat.run_chat_turn", _fake_run_chat_turn)

    run_ios_flow(
        "long_form_council_mocked.yaml",
        extra_env={
            "CONTENT_ID": str(content.id),
            "PRIMARY_PERSONA_NAME": "Paul Graham",
            "SECONDARY_PERSONA_NAME": "Ben Thompson",
            "PRIMARY_PERSONA_REPLY": "Paul Graham mocked council reply",
            "SECONDARY_PERSONA_REPLY": "Ben Thompson mocked council reply",
        },
    )

    db_session.expire_all()
    parent_session = db_session.query(ChatSession).filter(ChatSession.id == session.id).one()
    assert parent_session.council_mode is True
    assert parent_session.active_child_session_id is not None


def test_chat_mic_toggle_flow_uses_mocked_speech_and_sends_message(
    run_ios_flow,
    test_user,
    chat_session_factory,
    db_session,
    completed_chat_processors_factory,
    monkeypatch,
) -> None:
    """The chat mic should toggle recording, surface the transcript, and send it."""
    transcript = "Mocked mic transcript for chat UI"
    assistant_reply = "Mocked assistant reply for chat UI"
    session = chat_session_factory(
        user=test_user,
        title="Mocked Mic Session",
        session_type="knowledge_chat",
    )
    complete_queued_turn = completed_chat_processors_factory(assistant_reply=assistant_reply)
    monkeypatch.setattr(
        "app.commands.send_chat_message.stage_queued_chat_turn",
        complete_queued_turn,
    )

    run_ios_flow(
        "chat_mic_toggle.yaml",
        extra_env={
            "CHAT_SESSION_ID": str(session.id),
            "TRANSCRIPT": transcript,
            "ASSISTANT_REPLY": assistant_reply,
        },
    )

    db_session.expire_all()
    message = (
        db_session.query(ChatMessage)
        .filter(ChatMessage.session_id == session.id)
        .order_by(ChatMessage.id.desc())
        .first()
    )
    assert message is not None
    assert message.status == "completed"
    assert transcript in (message.message_list or "")
    assert assistant_reply in (message.message_list or "")


def test_knowledge_new_chat_mic_opens_full_chat_session(
    run_ios_flow,
    test_user,
    db_session,
    completed_chat_processors_factory,
    monkeypatch,
) -> None:
    """Tapping the Knowledge tab mic should record, transcribe, and open a chat turn."""
    transcript = "Mocked Knowledge mic transcript"
    assistant_reply = "Mocked Knowledge mic reply"
    initial_count = (
        db_session.query(ChatSession).filter(ChatSession.user_id == test_user.id).count()
    )
    complete_queued_turn = completed_chat_processors_factory(assistant_reply=assistant_reply)
    monkeypatch.setattr(
        "app.commands.create_assistant_turn.stage_queued_chat_turn",
        complete_queued_turn,
    )

    run_ios_flow("knowledge_new_chat.yaml", extra_env={"TRANSCRIPT": transcript})

    db_session.expire_all()
    new_count = db_session.query(ChatSession).filter(ChatSession.user_id == test_user.id).count()
    assert new_count == initial_count + 1
    message = (
        db_session.query(ChatMessage)
        .join(ChatSession, ChatSession.id == ChatMessage.session_id)
        .filter(ChatSession.user_id == test_user.id)
        .order_by(ChatMessage.id.desc())
        .first()
    )
    assert message is not None
    assert message.status == "completed"
    assert transcript in (message.message_list or "")
    assert assistant_reply in (message.message_list or "")


def test_personalized_onboarding_flow_runs_live_audio_discovery_with_fake_mic(
    run_ios_flow,
    content_factory,
    db_session,
    db_session_factory,
    monkeypatch,
    status_entry_factory,
    test_user,
) -> None:
    """Personalized onboarding should use the real API flow with deterministic discovery output."""

    transcript = (
        "I follow semiconductors, AI infrastructure, engineering leadership, and product strategy."
    )
    expected_substacks = {"Stratechery"}
    expected_podcasts = {
        "Hard Fork",
        "Decoder",
        "Software Engineering Daily",
        "Invest Like the Best",
    }
    expected_reddits = {"LocalLLaMA", "MachineLearning"}

    class ImmediateOnboardingQueueGateway:
        def __init__(self) -> None:
            self.calls: list[tuple[TaskType, dict | None]] = []
            self._next_task_id = 0

        def enqueue_many_in_session(self, db, requests) -> list[int]:
            task_ids: list[int] = []
            audio_runs: list[tuple[int, int]] = []
            for request in requests:
                self._next_task_id += 1
                normalized_payload = request.payload or {}
                self.calls.append((request.task_type, normalized_payload))
                task_ids.append(self._next_task_id)
                if (
                    request.task_type == TaskType.ONBOARDING_DISCOVER
                    and "run_id" in normalized_payload
                ):
                    audio_runs.append(
                        (
                            int(normalized_payload["run_id"]),
                            int(normalized_payload["user_id"]),
                        )
                    )

            if audio_runs:

                def process_audio_runs_after_commit(_session) -> None:
                    for run_id, user_id in audio_runs:
                        worker_db = db_session_factory()
                        try:
                            run_audio_discovery(
                                worker_db,
                                run_id,
                                user_id=user_id,
                            )
                        finally:
                            worker_db.close()

                event.listen(db, "after_commit", process_audio_runs_after_commit, once=True)
            return task_ids

    async def _fake_build_audio_lane_plan(transcript: str, locale: str | None) -> _AudioPlanOutput:
        del transcript, locale
        return _personalized_onboarding_plan()

    def _fake_run_discovery_exa_queries(
        queries,
        *,
        num_results,
        include_social=False,
        lane_name=None,
        lane_target=None,
        telemetry=None,
        request_timeout_seconds=None,
    ):
        del num_results, include_social, telemetry, request_timeout_seconds
        return [
            _DiscoveryWebResult(
                title=f"{lane_name or 'Discovery'} result {index + 1}",
                url=f"https://example.com/{lane_target or 'feeds'}/{index + 1}",
                snippet=f"Result for {query}",
                query=query,
                lane_name=lane_name,
                lane_target=lane_target,
            )
            for index, query in enumerate(queries)
        ]

    def _fake_run_discover_output_with_fallback(**_kwargs) -> _DiscoverOutput:
        return _personalized_onboarding_output()

    queue_gateway = ImmediateOnboardingQueueGateway()

    processing_article = content_factory(
        content_type="article",
        title="Stratechery Queue Item",
        url="https://example.com/stratechery-queue-item",
        source="Stratechery",
        status="processing",
        content_metadata={"feed_url": "https://stratechery.com/feed"},
    )
    processing_podcast = content_factory(
        content_type="podcast",
        title="Decoder Queue Item",
        url="https://example.com/decoder-queue-item",
        source="Decoder",
        status="processing",
        content_metadata={
            "feed_url": "https://feeds.megaphone.fm/vergecast",
            "audio_url": "https://example.com/audio/decoder-queue-item.mp3",
        },
    )
    status_entry_factory(user=test_user, content=processing_article, status="inbox")
    status_entry_factory(user=test_user, content=processing_podcast, status="inbox")
    db_session.add_all(
        [
            ProcessingTask(
                task_type="process_content",
                content_id=processing_article.id,
                status="pending",
                queue_name="content",
            ),
            ProcessingTask(
                task_type="process_content",
                content_id=processing_podcast.id,
                status="pending",
                queue_name="content",
            ),
        ]
    )
    db_session.commit()

    stub_feed_validator(monkeypatch)

    @contextmanager
    def _feed_runtime(**_kwargs):
        yield SimpleNamespace(detector=SimpleNamespace())

    monkeypatch.setattr(
        "app.services.onboarding.discovery_run.feed_research_runtime",
        _feed_runtime,
    )
    monkeypatch.setattr(
        "app.services.onboarding.audio_discovery_run.feed_research_runtime",
        _feed_runtime,
    )
    monkeypatch.setattr(
        "app.services.onboarding.suggestion_projection.resolve_feed_candidate",
        lambda **kwargs: {
            "feed_url": kwargs["candidate_feed_urls"][0],
            "feed_format": "rss",
            "title": kwargs.get("title") or "",
        },
    )
    monkeypatch.setattr(
        "app.services.onboarding.entrypoints._build_audio_lane_plan",
        _fake_build_audio_lane_plan,
    )
    monkeypatch.setattr(
        "app.services.onboarding.discovery_run._run_discovery_exa_queries",
        _fake_run_discovery_exa_queries,
    )
    monkeypatch.setattr(
        "app.services.onboarding.audio_discovery_run._run_discovery_exa_queries",
        _fake_run_discovery_exa_queries,
    )
    monkeypatch.setattr(
        "app.services.onboarding.discovery_run._run_discover_output_with_fallback",
        _fake_run_discover_output_with_fallback,
    )
    monkeypatch.setattr(
        "app.services.onboarding.audio_discovery_run._run_discover_output_with_fallback",
        _fake_run_discover_output_with_fallback,
    )
    monkeypatch.setattr(
        "app.services.onboarding.entrypoints.get_task_queue_gateway",
        lambda: queue_gateway,
    )

    run_ios_flow(
        "onboarding_personalized.yaml",
        extra_env={"TRANSCRIPT": transcript},
    )

    db_session.expire_all()
    db_session.refresh(test_user)
    assert test_user.has_completed_onboarding is True
    assert test_user.has_completed_new_user_tutorial is False

    configs = (
        db_session.query(UserScraperConfig).filter(UserScraperConfig.user_id == test_user.id).all()
    )
    configs_by_type: dict[str, set[str]] = {}
    for row in configs:
        configs_by_type.setdefault(row.scraper_type, set()).add(row.display_name or "")

    assert configs_by_type["substack"] == expected_substacks
    assert configs_by_type["podcast_rss"] == expected_podcasts
    assert configs_by_type["reddit"] == expected_reddits

    audio_discovery_runs = (
        db_session.query(OnboardingDiscoveryRun)
        .filter(OnboardingDiscoveryRun.user_id == test_user.id)
        .all()
    )
    assert len(audio_discovery_runs) == 1
    assert audio_discovery_runs[0].status == "completed"

    lanes = (
        db_session.query(OnboardingDiscoveryLane)
        .filter(OnboardingDiscoveryLane.run_id == audio_discovery_runs[0].id)
        .all()
    )
    assert {lane.status for lane in lanes} == {"completed"}

    assert any(
        task_type == TaskType.ONBOARDING_DISCOVER and payload is not None and "run_id" in payload
        for task_type, payload in queue_gateway.calls
    )
    assert any(task_type == TaskType.SCRAPE for task_type, _ in queue_gateway.calls)
