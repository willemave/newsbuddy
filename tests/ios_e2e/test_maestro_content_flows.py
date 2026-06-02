"""Maestro-backed iOS end-to-end tests using shared backend fixtures."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from app.models.contracts import TaskType
from app.models.db import (
    ChatMessage,
    ChatSession,
    ContentKnowledgeSave,
    ContentReadStatus,
    NewsItem,
    NewsItemDiscussion,
    OnboardingDiscoveryLane,
    OnboardingDiscoveryRun,
    ProcessingTask,
    UserScraperConfig,
)
from app.services.chat_agent import ChatRunResult, save_messages
from app.services.onboarding import (
    _AudioLane,
    _AudioPlanOutput,
    _DiscoverOutput,
    _DiscoverSuggestion,
    _DiscoveryWebResult,
    run_audio_discovery,
)

pytestmark = [pytest.mark.integration, pytest.mark.ios_e2e]


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


def test_long_form_detail_flow_uses_seeded_fixture_data(
    run_ios_flow,
    create_sample_content,
    sample_article_long,
) -> None:
    """The seeded long-form content fixture should render in the iOS app."""
    content = create_sample_content(sample_article_long)

    run_ios_flow(
        "long_form_detail.yaml",
        extra_env={
            "CONTENT_ID": str(content.id),
            "CONTENT_TITLE": content.title,
        },
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


def test_long_form_list_mark_read_action_updates_backend_state(
    run_ios_flow,
    create_sample_content,
    sample_article_long,
    test_user,
    db_session,
) -> None:
    """Mark-as-read from the long-form list should persist to the shared backend DB."""
    content = create_sample_content(sample_article_long)

    run_ios_flow(
        "long_form_mark_read.yaml",
        extra_env={"CONTENT_ID": str(content.id)},
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


def test_short_form_detail_discussion_sheet_renders_embedded_comments(
    run_ios_flow,
    db_session,
    test_user,
) -> None:
    """Comments button should open the in-app discussion sheet for news items."""
    comment_id = "comment-1"
    news_item = NewsItem(
        ingest_key="ios-e2e-discussion",
        visibility_scope="user",
        owner_user_id=test_user.id,
        platform="hackernews",
        source_type="hackernews",
        source_label="Hacker News",
        source_external_id="ios-e2e-discussion",
        canonical_item_url="https://news.ycombinator.com/item?id=424242",
        canonical_story_url="https://example.com/herbie-floating-point",
        article_url="https://example.com/herbie-floating-point",
        article_title="Herbie Automatically Optimizes Code to Fix Floating-Point Precision Errors",
        article_domain="example.com",
        discussion_url="https://news.ycombinator.com/item?id=424242",
        summary_title="Herbie Automatically Optimizes Code to Fix Floating-Point Precision Errors",
        summary_key_points=[
            "Herbie suggests numerically stable rewrites for floating-point expressions."
        ],
        summary_text="Herbie improves floating-point expressions by proposing stable alternatives.",
        raw_metadata={
            "discussion_url": "https://news.ycombinator.com/item?id=424242",
            "summary": {
                "article_url": "https://example.com/herbie-floating-point",
                "summary": (
                    "Herbie improves floating-point expressions by proposing stable alternatives."
                ),
                "key_points": [
                    "Herbie suggests numerically stable rewrites for floating-point expressions."
                ],
            },
            "discussion_payload": {
                "mode": "comments",
                "source_url": "https://news.ycombinator.com/item?id=424242",
                "comments": [
                    {
                        "comment_id": comment_id,
                        "author": "alice",
                        "text": "This kind of numerical tooling saves real debugging time.",
                        "compact_text": "This kind of numerical tooling saves real debugging time.",
                        "depth": 0,
                    }
                ],
                "discussion_groups": [],
                "links": [],
                "stats": {"fetched_count": 1},
            },
        },
        status="ready",
        published_at=datetime.now(UTC).replace(tzinfo=None),
        ingested_at=datetime.now(UTC).replace(tzinfo=None),
        processed_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db_session.add(news_item)
    db_session.commit()

    run_ios_flow(
        "short_form_discussion.yaml",
        extra_env={
            "CONTENT_ID": str(news_item.id),
            "COMMENT_ID": comment_id,
        },
    )


def test_short_form_detail_discussion_sheet_renders_discussion_summary(
    run_ios_flow,
    db_session,
    test_user,
) -> None:
    """Comments button should render the new stored news-item discussion summary."""
    summary_overview = (
        "Developers compare parser ergonomics, runtime tradeoffs, and deployment risks."
    )
    topic_title = "Parser ergonomics"
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
                        "summary": (
                            "Commenters focus on simpler grammar changes and clearer failures."
                        ),
                        "stance": "Mostly supportive, with concerns about migration cost.",
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
            "SUMMARY_OVERVIEW": summary_overview,
            "TOPIC_TITLE": topic_title,
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
            all_messages=messages,
            tool_calls=[],
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
    _fake_process_message_async, _fake_process_assistant_turn_async = (
        completed_chat_processors_factory(assistant_reply=assistant_reply)
    )

    monkeypatch.setattr("app.routers.api.chat.process_message_async", _fake_process_message_async)
    monkeypatch.setattr(
        "app.routers.api.chat.process_assistant_turn_async",
        _fake_process_assistant_turn_async,
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
    _fake_process_message_async, _fake_process_assistant_turn_async = (
        completed_chat_processors_factory(assistant_reply=assistant_reply)
    )

    monkeypatch.setattr("app.routers.api.chat.process_message_async", _fake_process_message_async)
    monkeypatch.setattr(
        "app.routers.api.chat.process_assistant_turn_async",
        _fake_process_assistant_turn_async,
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

        def enqueue(self, task_type, payload=None, **_kwargs) -> int:
            self._next_task_id += 1
            normalized_payload = payload or {}
            self.calls.append((task_type, normalized_payload))

            if task_type == TaskType.ONBOARDING_DISCOVER and "run_id" in normalized_payload:
                worker_db = db_session_factory()
                try:
                    run_audio_discovery(worker_db, int(normalized_payload["run_id"]))
                finally:
                    worker_db.close()

            return self._next_task_id

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
    ):
        del num_results, include_social, telemetry
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

    empty_curated_defaults: dict[str, list[object]] = {
        "substack": [],
        "atom": [],
        "podcast_rss": [],
        "reddit": [],
    }
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

    monkeypatch.setattr(
        "app.services.scraper_config_validation.FEED_VALIDATOR.validate_feed_url",
        lambda url: {"feed_url": url},
    )
    monkeypatch.setattr(
        "app.services.onboarding._build_audio_lane_plan",
        _fake_build_audio_lane_plan,
    )
    monkeypatch.setattr(
        "app.services.onboarding._run_discovery_exa_queries",
        _fake_run_discovery_exa_queries,
    )
    monkeypatch.setattr(
        "app.services.onboarding._run_discover_output_with_fallback",
        _fake_run_discover_output_with_fallback,
    )
    monkeypatch.setattr(
        "app.services.onboarding._load_curated_defaults",
        lambda: empty_curated_defaults,
    )
    monkeypatch.setattr(
        "app.services.onboarding.get_task_queue_gateway",
        lambda: queue_gateway,
    )

    run_ios_flow(
        "onboarding_personalized.yaml",
        extra_env={"TRANSCRIPT": transcript},
    )

    db_session.expire_all()
    db_session.refresh(test_user)
    assert test_user.has_completed_onboarding is True
    assert test_user.has_completed_new_user_tutorial is True

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
