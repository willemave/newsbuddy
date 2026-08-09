from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core.model_defaults import OPENROUTER_DEEPSEEK_FLASH_MODEL_SPEC
from app.models.contracts import ContentType, TaskType
from app.models.db import (
    AudioEpisode,
    ContentKnowledgeSave,
    NewsItemReadStatus,
    ProcessingTask,
)
from app.services import audio_episodes as service
from app.services.audio_episode_kinds import (
    AUDIO_EPISODE_KIND_SPECS,
    CUSTOM_NARRATION_MODEL,
)
from app.services.audio_episode_sources import LONGFORM_BODY_MAX_CHARS, excerpt_longform_source_text
from app.services.audio_episodes import generation, scripting, streaming
from tests.support.builders import create_content_status_entry_row, create_news_item_row


def test_create_fast_news_digest_episode_uses_unread_summaries(db_session, test_user) -> None:
    unread = create_news_item_row(
        db_session,
        index=1,
        visibility_scope="user",
        owner_user_id=test_user.id,
        summary_title="AI labs ship voice agents",
        summary_text="Several labs shipped lower-latency voice agents.",
        summary_key_points=["Latency fell", "Voice UX moved from demos to products"],
    )
    read = create_news_item_row(
        db_session,
        index=2,
        visibility_scope="user",
        owner_user_id=test_user.id,
        summary_title="Already read",
        summary_text="This one should not appear.",
    )
    db_session.add(NewsItemReadStatus(user_id=test_user.id, news_item_id=read.id))
    db_session.commit()

    episode = service.create_fast_news_digest_episode(db_session, user_id=test_user.id)

    assert episode.kind == service.FAST_NEWS_DIGEST_KIND
    assert episode.status == "pending"
    assert episode.source_item_ids == [unread.id]
    source_snapshot = episode.source_snapshot
    assert isinstance(source_snapshot, dict)
    assert source_snapshot["items"] == [
        {
            "id": unread.id,
            "title": "AI labs ship voice agents",
            "source": "Hacker News",
            "platform": "hackernews",
            "published_at": None,
            "summary": "Several labs shipped lower-latency voice agents.",
            "key_points": ["Latency fell", "Voice UX moved from demos to products"],
            "article_url": "https://example.com/story-1",
            "discussion_url": "https://news.ycombinator.com/item?id=1001",
        }
    ]


def test_create_news_item_discussion_episode_uses_visible_summary(
    db_session,
    test_user,
) -> None:
    item = create_news_item_row(
        db_session,
        index=1,
        visibility_scope="user",
        owner_user_id=test_user.id,
        summary_title="AI chips move to liquid cooling",
        summary_text="A startup raised fresh funding for dense liquid-cooled AI clusters.",
        summary_key_points=["Density is rising", "Cooling is becoming a bottleneck"],
    )
    assert item.id is not None
    db_session.add(NewsItemReadStatus(user_id=test_user.id, news_item_id=item.id))
    db_session.commit()

    episode = service.create_news_item_discussion_episode(
        db_session,
        user_id=test_user.id,
        news_item_id=item.id,
    )

    assert episode.kind == service.NEWS_ITEM_DISCUSSION_KIND
    assert episode.source_content_id is None
    assert episode.source_item_ids == [item.id]
    source_snapshot = episode.source_snapshot
    assert isinstance(source_snapshot, dict)
    assert source_snapshot["kind"] == service.NEWS_ITEM_DISCUSSION_KIND
    assert source_snapshot["item"]["id"] == item.id
    assert source_snapshot["item"]["summary"] == (
        "A startup raised fresh funding for dense liquid-cooled AI clusters."
    )
    assert source_snapshot["item"]["key_points"] == [
        "Density is rising",
        "Cooling is becoming a bottleneck",
    ]


def test_create_content_council_episode_includes_summary_and_source_text(
    db_session,
    test_user,
    content_factory,
) -> None:
    content = content_factory(
        content_type=ContentType.ARTICLE,
        title="The Future of Agents",
        content_metadata={
            "summary": {
                "overview": "Agents are moving from demos to durable workflows.",
                "key_points": ["Long-running work matters", "Tool use needs guardrails"],
            },
            "content": "Full article body about agents, tool calls, and durable execution.",
        },
    )
    create_content_status_entry_row(db_session, user=test_user, content=content)

    episode = service.create_content_council_episode(
        db_session,
        user_id=test_user.id,
        content_id=content.id,
    )

    assert episode.kind == service.CONTENT_COUNCIL_DISCUSSION_KIND
    assert episode.source_content_id == content.id
    source_snapshot = episode.source_snapshot
    assert isinstance(source_snapshot, dict)
    summary = source_snapshot["summary"]
    assert isinstance(summary, dict)
    assert summary["overview"] == ("Agents are moving from demos to durable workflows.")
    assert summary["key_points"] == [
        "Long-running work matters",
        "Tool use needs guardrails",
    ]
    assert source_snapshot["source_text"] == (
        "Full article body about agents, tool calls, and durable execution."
    )
    assert source_snapshot["source_text_excerpt_strategy"] == "full"


def test_content_council_episode_excerpts_long_source_text(
    db_session,
    test_user,
    content_factory,
) -> None:
    body_text = (
        ("opening " * 950)
        + ("middle " * 300)
        + "MIDDLE_MARKER "
        + ("middle " * 250)
        + "CLOSING_MARKER "
        + ("closing " * 1_000)
    )
    content = content_factory(
        content_type=ContentType.ARTICLE,
        title="Long Audio Source",
        content_metadata={
            "summary": "A long source needs a bounded podcast prompt.",
            "content": body_text,
        },
    )
    create_content_status_entry_row(db_session, user=test_user, content=content)

    episode = service.create_content_council_episode(
        db_session,
        user_id=test_user.id,
        content_id=content.id,
    )

    source_snapshot = episode.source_snapshot
    assert isinstance(source_snapshot, dict)
    source_text = source_snapshot["source_text"]
    assert len(source_text) <= LONGFORM_BODY_MAX_CHARS + 120
    assert source_snapshot["source_text_excerpt_strategy"] == "head_middle_tail"
    assert "[Source opening excerpt]" in source_text
    assert "MIDDLE_MARKER" in source_text
    assert "CLOSING_MARKER" in source_text
    assert source_snapshot["source_text_truncated"] is True


def test_create_custom_narration_episode_uses_selected_source_text(
    db_session,
    test_user,
    content_factory,
) -> None:
    article_body = "Full article source text " * 40
    podcast_body = "Full podcast transcript " * 35
    article = content_factory(
        content_type=ContentType.ARTICLE,
        title="Agentic Browsers",
        content_metadata={
            "summary": "Browsers are becoming agent runtimes.",
            "content": article_body,
        },
    )
    podcast = content_factory(
        content_type=ContentType.PODCAST,
        title="Compute Markets",
        content_metadata={
            "summary": "A conversation about AI compute markets.",
            "transcript": podcast_body,
        },
    )
    create_content_status_entry_row(db_session, user=test_user, content=article)
    db_session.add(ContentKnowledgeSave(user_id=test_user.id, content_id=podcast.id))
    db_session.commit()

    episode = service.create_custom_narration_episode(
        db_session,
        user_id=test_user.id,
        content_ids=[article.id, podcast.id, article.id],
        title="AI market briefing",
    )

    assert episode.kind == service.CUSTOM_NARRATION_KIND
    assert episode.source_content_id is None
    assert episode.source_item_ids == []
    assert episode.title == "AI market briefing"
    source_snapshot = episode.source_snapshot
    assert isinstance(source_snapshot, dict)
    assert source_snapshot["kind"] == service.CUSTOM_NARRATION_KIND
    assert source_snapshot["content_ids"] == [article.id, podcast.id]
    assert source_snapshot["source_count"] == 2
    assert source_snapshot["items"][0]["title"] == "Agentic Browsers"
    assert source_snapshot["items"][0]["source_text"] == article_body.strip()
    assert source_snapshot["items"][1]["title"] == "Compute Markets"
    assert source_snapshot["items"][1]["source_text"] == podcast_body.strip()


def test_create_custom_narration_episode_rejects_unsupported_content_type(
    db_session,
    test_user,
    content_factory,
) -> None:
    content = content_factory(
        content_type=ContentType.NEWS,
        title="Short news",
        content_metadata={"content": "Short-form article body."},
    )
    create_content_status_entry_row(db_session, user=test_user, content=content)

    with pytest.raises(HTTPException) as exc_info:
        service.create_custom_narration_episode(
            db_session,
            user_id=test_user.id,
            content_ids=[content.id],
        )

    assert exc_info.value.status_code == 400
    assert "articles and podcasts" in str(exc_info.value.detail)


def test_create_custom_narration_episode_rejects_missing_body(
    db_session,
    test_user,
    content_factory,
) -> None:
    content = content_factory(
        content_type=ContentType.ARTICLE,
        title="No body",
        content_metadata={"summary": "No source text is available."},
    )
    create_content_status_entry_row(db_session, user=test_user, content=content)

    with pytest.raises(HTTPException) as exc_info:
        service.create_custom_narration_episode(
            db_session,
            user_id=test_user.id,
            content_ids=[content.id],
        )

    assert exc_info.value.status_code == 400
    assert "No article or transcript text" in str(exc_info.value.detail)


def test_create_custom_narration_episode_rejects_invalid_ids(db_session, test_user) -> None:
    with pytest.raises(HTTPException) as exc_info:
        service.create_custom_narration_episode(
            db_session,
            user_id=test_user.id,
            content_ids=[0],
        )

    assert exc_info.value.status_code == 400
    assert "positive" in str(exc_info.value.detail)


def test_custom_narration_prompt_uses_deepseek_flash_and_bounded_excerpts(
    monkeypatch,
) -> None:
    full_text = "Opening. " + ("Long source paragraph. " * 1_000) + "CLOSING_MARKER."
    script = service.AudioEpisodeScript(
        title="Custom Script",
        estimated_duration_seconds=240,
        turns=[
            service.AudioEpisodeTurn(speaker="host", text="One."),
            service.AudioEpisodeTurn(speaker="cohost", text="Two."),
            service.AudioEpisodeTurn(speaker="expert", text="Three."),
            service.AudioEpisodeTurn(speaker="host", text="Four."),
            service.AudioEpisodeTurn(speaker="cohost", text="Five."),
            service.AudioEpisodeTurn(speaker="expert", text="Six."),
            service.AudioEpisodeTurn(speaker="host", text="Seven."),
            service.AudioEpisodeTurn(speaker="cohost", text="Eight."),
            service.AudioEpisodeTurn(speaker="expert", text="Nine."),
            service.AudioEpisodeTurn(speaker="host", text="Ten."),
        ],
    )
    captured: dict[str, str] = {}
    source_text, excerpt_strategy = excerpt_longform_source_text(full_text)

    class FakeAgent:
        def __init__(self, model_spec: str) -> None:
            self.model_spec = model_spec

        def run_sync(self, message, model_settings=None):  # noqa: ANN001
            del model_settings
            captured["model"] = self.model_spec
            captured["message"] = message
            return SimpleNamespace(output=script)

    def fake_get_basic_agent(model_spec, _output_type, _system_prompt):  # noqa: ANN001
        return FakeAgent(model_spec)

    episode = AudioEpisode(
        id=100,
        user_id=123,
        kind=service.CUSTOM_NARRATION_KIND,
        source_snapshot={
            "kind": service.CUSTOM_NARRATION_KIND,
            "content_ids": [1],
            "source_count": 1,
            "items": [
                {
                    "content_id": 1,
                    "content_type": "article",
                    "title": "Full source",
                    "source_text": source_text,
                    "source_text_excerpt_strategy": excerpt_strategy,
                }
            ],
        },
    )
    monkeypatch.setattr(scripting, "get_basic_agent", fake_get_basic_agent)
    monkeypatch.setattr(scripting, "extract_usage_from_result", lambda _result: None)

    generated = scripting.generate_script(episode)

    assert generated.model == CUSTOM_NARRATION_MODEL
    assert captured["model"] == CUSTOM_NARRATION_MODEL
    assert len(source_text) <= LONGFORM_BODY_MAX_CHARS + 120
    assert full_text not in captured["message"]
    assert "[Source opening excerpt]" in captured["message"]
    assert "CLOSING_MARKER" in captured["message"]


def test_generate_script_uses_audio_episode_model(monkeypatch) -> None:
    script = service.AudioEpisodeScript(
        title="Fast Script",
        estimated_duration_seconds=60,
        turns=[
            service.AudioEpisodeTurn(speaker="host", text="One."),
            service.AudioEpisodeTurn(speaker="cohost", text="Two."),
            service.AudioEpisodeTurn(speaker="expert", text="Three."),
            service.AudioEpisodeTurn(speaker="host", text="Four."),
            service.AudioEpisodeTurn(speaker="cohost", text="Five."),
            service.AudioEpisodeTurn(speaker="expert", text="Six."),
        ],
    )
    attempts: list[str] = []

    class FakeAgent:
        def __init__(self, model_spec: str) -> None:
            self.model_spec = model_spec

        def run_sync(self, _message, model_settings=None):  # noqa: ANN001
            del model_settings
            return SimpleNamespace(output=script)

    def fake_get_basic_agent(model_spec, _output_type, _system_prompt):  # noqa: ANN001
        attempts.append(model_spec)
        return FakeAgent(model_spec)

    episode = AudioEpisode(
        id=99,
        user_id=123,
        kind=service.FAST_NEWS_DIGEST_KIND,
        source_snapshot={"kind": service.FAST_NEWS_DIGEST_KIND, "items": []},
    )
    monkeypatch.setattr(scripting, "get_basic_agent", fake_get_basic_agent)
    monkeypatch.setattr(scripting, "extract_usage_from_result", lambda _result: None)

    generated = scripting.generate_script(episode)

    assert generated.script == script
    assert generated.model == service.AUDIO_EPISODE_MODEL
    assert attempts == [service.AUDIO_EPISODE_MODEL]


def test_generate_script_accepts_natural_long_provider_turn(monkeypatch) -> None:
    script = service.AudioEpisodeScript(
        title="Unbounded Script",
        estimated_duration_seconds=60,
        turns=[
            service.AudioEpisodeTurn(
                speaker="host",
                text="x" * 3_501,
            )
        ],
    )

    class FakeAgent:
        def run_sync(self, _message, model_settings=None):  # noqa: ANN001
            del model_settings
            return SimpleNamespace(output=script)

    monkeypatch.setattr(scripting, "get_basic_agent", lambda *_args: FakeAgent())
    monkeypatch.setattr(scripting, "extract_usage_from_result", lambda _result: None)
    episode = AudioEpisode(
        id=99,
        user_id=123,
        kind=service.FAST_NEWS_DIGEST_KIND,
        source_snapshot={"kind": service.FAST_NEWS_DIGEST_KIND, "items": []},
    )

    generated = scripting.generate_script_with_model(
        episode,
        "Generate a natural script.",
        service.AUDIO_EPISODE_MODEL,
    )

    assert generated.turns[0].text == "x" * 3_501


def test_all_generated_audio_episode_kinds_use_deepseek_flash() -> None:
    generated_specs = [
        spec
        for spec in AUDIO_EPISODE_KIND_SPECS.values()
        if spec.script_mode == "generated_dialogue"
    ]

    assert generated_specs
    assert {spec.default_model for spec in generated_specs} == {
        OPENROUTER_DEEPSEEK_FLASH_MODEL_SPEC
    }


def test_generate_audio_episode_persists_script_and_audio(
    db_session,
    test_user,
    tmp_path,
    monkeypatch,
) -> None:
    episode = AudioEpisode(
        user_id=test_user.id,
        kind=service.FAST_NEWS_DIGEST_KIND,
        status="pending",
        title="Fast Reads Brief",
        input_hash="abc",
        source_item_ids=[1],
        source_snapshot={"kind": service.FAST_NEWS_DIGEST_KIND, "items": []},
        prompt_version=service.PROMPT_VERSION,
    )
    db_session.add(episode)
    db_session.commit()
    db_session.refresh(episode)
    assert episode.id is not None
    assert episode.id is not None

    script = service.AudioEpisodeScript(
        title="Fast Reads Brief",
        estimated_duration_seconds=90,
        turns=[
            service.AudioEpisodeTurn(speaker="host", text="Here is the setup."),
            service.AudioEpisodeTurn(speaker="cohost", text="Here is why it matters."),
            service.AudioEpisodeTurn(speaker="expert", text="Here is the sharper read."),
            service.AudioEpisodeTurn(speaker="host", text="That is the takeaway."),
            service.AudioEpisodeTurn(speaker="cohost", text="Watch the follow-up."),
            service.AudioEpisodeTurn(speaker="expert", text="Keep an eye on adoption."),
        ],
    )
    captured_turns: list[dict[str, str]] = []

    class FakeTtsService:
        def synthesize_dialogue_mp3(self, *, turns, item_id=None, user_id=None):
            captured_turns.extend(turns)
            assert item_id == episode.id
            assert user_id == test_user.id
            return b"fake-mp3"

    monkeypatch.setattr(
        scripting,
        "generate_script",
        lambda _episode: service.AudioEpisodeScriptGeneration(
            script=script,
            model="test:model",
        ),
    )
    monkeypatch.setattr(
        generation,
        "get_content_narration_tts_service",
        lambda: FakeTtsService(),
    )
    monkeypatch.setattr(
        generation,
        "get_settings",
        lambda: SimpleNamespace(media_base_dir=tmp_path),
    )

    generated = service.generate_audio_episode(db_session, audio_episode_id=episode.id)
    db_session.commit()

    assert generated.status == "completed"
    script_payload = generated.script
    assert isinstance(script_payload, dict)
    assert script_payload["title"] == "Fast Reads Brief"
    assert generated.script_text is not None
    assert generated.script_text.startswith("Fast Reads Brief\n\nHost:")
    assert generated.model == "test:model"
    assert generated.audio_storage_path is not None
    audio_path = tmp_path / "audio_episodes" / f"audio-episode-{episode.id}.mp3"
    assert audio_path.read_bytes() == b"fake-mp3"
    assert captured_turns[0] == {"speaker": "host", "text": "Here is the setup."}


def test_generation_failure_waits_for_caller_retry_disposition(
    db_session,
    test_user,
    monkeypatch,
) -> None:
    episode = AudioEpisode(
        user_id=test_user.id,
        kind=service.FAST_NEWS_DIGEST_KIND,
        status="pending",
        title="Fast Reads Brief",
        input_hash="generation-disposition",
        source_item_ids=[1],
        source_snapshot={"kind": service.FAST_NEWS_DIGEST_KIND, "items": []},
        prompt_version=service.PROMPT_VERSION,
    )
    db_session.add(episode)
    db_session.commit()
    db_session.refresh(episode)
    assert episode.id is not None

    def fail_script(_episode):
        raise RuntimeError("script boom")

    monkeypatch.setattr(scripting, "generate_script", fail_script)

    with pytest.raises(RuntimeError, match="script boom"):
        generation.generate_audio_episode(db_session, audio_episode_id=episode.id)

    assert episode.status == "processing"
    assert episode.error_message is None
    assert episode.completed_at is None

    generation.finalize_audio_episode_failure(
        db_session,
        audio_episode_id=episode.id,
        error=RuntimeError("script boom"),
        retry_scheduled=True,
    )

    assert episode.status == "pending"
    assert episode.error_message == "script boom"
    assert episode.started_at is None


def test_delivery_stages_generation_without_inline_execution(
    db_session,
    test_user,
    monkeypatch,
) -> None:
    episode = AudioEpisode(
        user_id=test_user.id,
        kind=service.FAST_NEWS_DIGEST_KIND,
        status="pending",
        title="Fast Reads Brief",
        input_hash="inline-terminal-failure",
        source_item_ids=[1],
        source_snapshot={"kind": service.FAST_NEWS_DIGEST_KIND, "items": []},
        prompt_version=service.PROMPT_VERSION,
    )
    db_session.add(episode)

    script_calls: list[int] = []
    monkeypatch.setattr(
        scripting,
        "generate_script",
        lambda _episode: script_calls.append(1),
    )

    response = service.commit_audio_episode_delivery(db_session, episode, delivery="inline")

    db_session.expire_all()
    persisted = db_session.query(AudioEpisode).filter(AudioEpisode.id == episode.id).one()
    task = (
        db_session.query(ProcessingTask)
        .filter(ProcessingTask.task_type == TaskType.GENERATE_AUDIO_EPISODE.value)
        .one()
    )
    assert response.status.value == "pending"
    assert persisted.status == "pending"
    assert script_calls == []
    assert task.owner_user_id == test_user.id
    assert task.payload == {
        "audio_episode_id": persisted.id,
        "user_id": test_user.id,
    }


def test_follow_audio_episode_stream_waits_for_pending_generation(
    db_session,
    monkeypatch,
) -> None:
    script = service.AudioEpisodeScript(
        title="Prepared Stream",
        estimated_duration_seconds=90,
        turns=[
            service.AudioEpisodeTurn(speaker="host", text="Here is the setup."),
            service.AudioEpisodeTurn(speaker="cohost", text="Here is why it matters."),
            service.AudioEpisodeTurn(speaker="expert", text="Here is the sharper read."),
            service.AudioEpisodeTurn(speaker="host", text="That is the takeaway."),
            service.AudioEpisodeTurn(speaker="cohost", text="Watch the follow-up."),
            service.AudioEpisodeTurn(speaker="expert", text="Keep an eye on adoption."),
        ],
    )
    episode = AudioEpisode(
        user_id=123,
        kind=service.FAST_NEWS_DIGEST_KIND,
        status="pending",
        title="Fast Reads Brief",
        input_hash="takeover",
        source_item_ids=[1],
        source_snapshot={"kind": service.FAST_NEWS_DIGEST_KIND, "items": []},
        prompt_version=service.PROMPT_VERSION,
        script=script.model_dump(mode="json"),
    )
    db_session.add(episode)
    db_session.commit()
    db_session.refresh(episode)
    assert episode.id is not None

    monkeypatch.setattr(streaming, "AUDIO_EPISODE_FOLLOW_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(streaming, "AUDIO_EPISODE_FOLLOW_POLL_SECONDS", 0.001)

    with pytest.raises(service.AudioEpisodeAlreadyProcessingError):
        list(service.follow_audio_episode_stream_chunks(audio_episode_id=episode.id, user_id=123))


def test_persist_audio_episode_script_keeps_natural_long_dialogue(db_session) -> None:
    long_text = "A complete, naturally paced thought. " * 180
    script = service.AudioEpisodeScript(
        title="Long Conversation",
        estimated_duration_seconds=1_200,
        turns=[
            service.AudioEpisodeTurn(speaker="host", text=long_text),
        ],
    )
    episode = AudioEpisode(
        user_id=123,
        kind=service.FAST_NEWS_DIGEST_KIND,
        status="pending",
        title="Long Conversation",
        input_hash="long-dialogue",
        source_item_ids=[],
        source_snapshot={"kind": service.FAST_NEWS_DIGEST_KIND},
        prompt_version=service.PROMPT_VERSION,
    )
    db_session.add(episode)
    db_session.flush()

    persisted = scripting.persist_audio_episode_script(
        db_session,
        episode,
        script,
        model="test:model",
    )

    assert persisted.turns == script.turns
    assert persisted.turns[0].text == long_text
    assert episode.script is not None
    assert episode.script["turns"][0]["text"] == long_text
    assert long_text.strip() in str(episode.script_text)


@pytest.mark.parametrize("char_count", [24, 1_000, 18_000, 80_000])
def test_background_briefing_narration_never_uses_llm_and_preserves_text(
    db_session,
    test_user,
    tmp_path,
    monkeypatch,
    char_count: int,
) -> None:
    sentence = "Briefing narration sentence with complete context. "
    text = (sentence * ((char_count // len(sentence)) + 1))[:char_count]
    assert len(text) == char_count
    episode = AudioEpisode(
        user_id=test_user.id,
        kind=service.BRIEFING_NARRATION_KIND,
        status="pending",
        title="Articles briefing",
        input_hash=f"briefing-{char_count}",
        source_item_ids=[],
        source_snapshot={
            "kind": service.BRIEFING_NARRATION_KIND,
            "script_text": text,
        },
        script={"legacy": "payload that no longer validates"},
        script_text=text,
        prompt_version=1,
        model="legacy-model",
    )
    db_session.add(episode)
    db_session.commit()
    db_session.refresh(episode)
    assert episode.id is not None
    captured_turns: list[dict[str, str]] = []

    class FakeTtsService:
        def synthesize_dialogue_mp3(self, *, turns, item_id=None, user_id=None):
            captured_turns.extend(turns)
            return b"briefing-mp3"

    monkeypatch.setattr(
        scripting,
        "generate_script",
        lambda _episode: pytest.fail("preauthored narration must not call the LLM"),
    )
    monkeypatch.setattr(
        generation,
        "get_content_narration_tts_service",
        lambda: FakeTtsService(),
    )
    monkeypatch.setattr(
        generation,
        "get_settings",
        lambda: SimpleNamespace(media_base_dir=tmp_path),
    )

    generated = service.generate_audio_episode(db_session, audio_episode_id=episode.id)

    assert generated.status == "completed"
    assert generated.model == "deterministic"
    assert generated.script_text == text
    assert captured_turns == [{"speaker": "host", "text": text}]


def test_present_audio_episode_sanitizes_internal_failure(db_session) -> None:
    episode = AudioEpisode(
        user_id=123,
        kind=service.FAST_NEWS_DIGEST_KIND,
        status="failed",
        title="Failed audio",
        input_hash="failed-public-error",
        source_item_ids=[],
        source_snapshot={"kind": service.FAST_NEWS_DIGEST_KIND},
        prompt_version=service.PROMPT_VERSION,
        error_message="status_code: 404, secret provider diagnostics",
    )
    db_session.add(episode)
    db_session.commit()
    db_session.refresh(episode)

    response = service.present_audio_episode(episode)

    assert response.error_message == service.PUBLIC_AUDIO_EPISODE_ERROR_MESSAGE
    assert "404" not in response.error_message
