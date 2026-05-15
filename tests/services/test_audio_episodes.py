from __future__ import annotations

from types import SimpleNamespace

from app.models.contracts import ContentType
from app.models.db import AudioEpisode, NewsItemReadStatus
from app.services import audio_episodes as service
from tests.support.builders import create_content_status_entry_row, create_news_item_row


def test_create_fast_news_digest_episode_uses_unread_summaries(db_session, test_user) -> None:
    unread = create_news_item_row(
        db_session,
        index=1,
        summary_title="AI labs ship voice agents",
        summary_text="Several labs shipped lower-latency voice agents.",
        summary_key_points=["Latency fell", "Voice UX moved from demos to products"],
    )
    read = create_news_item_row(
        db_session,
        index=2,
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


def test_generate_audio_episode_persists_script_and_audio(
    db_session,
    tmp_path,
    monkeypatch,
) -> None:
    episode = AudioEpisode(
        user_id=123,
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

    script = service.AudioEpisodeScript(
        title="Fast Reads Brief",
        estimated_duration_seconds=300,
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
            assert user_id == 123
            return b"fake-mp3"

    monkeypatch.setattr(service, "_generate_script", lambda _episode: script)
    monkeypatch.setattr(
        service,
        "get_content_narration_tts_service",
        lambda: FakeTtsService(),
    )
    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(media_base_dir=tmp_path))

    generated = service.generate_audio_episode(db_session, audio_episode_id=episode.id)
    db_session.commit()

    assert generated.status == "completed"
    script_payload = generated.script
    assert isinstance(script_payload, dict)
    assert script_payload["title"] == "Fast Reads Brief"
    assert generated.script_text is not None
    assert generated.script_text.startswith("Fast Reads Brief\n\nHost:")
    assert generated.audio_storage_path is not None
    audio_path = tmp_path / "audio_episodes" / f"audio-episode-{episode.id}.mp3"
    assert audio_path.read_bytes() == b"fake-mp3"
    assert captured_turns[0] == {"speaker": "host", "text": "Here is the setup."}


def test_stream_audio_episode_chunks_persists_streamed_audio(
    db_session,
    tmp_path,
    monkeypatch,
) -> None:
    episode = AudioEpisode(
        user_id=123,
        kind=service.FAST_NEWS_DIGEST_KIND,
        status="pending",
        title="Fast Reads Brief",
        input_hash="stream",
        source_item_ids=[1],
        source_snapshot={"kind": service.FAST_NEWS_DIGEST_KIND, "items": []},
        prompt_version=service.PROMPT_VERSION,
    )
    db_session.add(episode)
    db_session.commit()
    db_session.refresh(episode)
    assert episode.id is not None

    script = service.AudioEpisodeScript(
        title="Fast Reads Stream",
        estimated_duration_seconds=300,
        turns=[
            service.AudioEpisodeTurn(speaker="host", text="Here is the setup."),
            service.AudioEpisodeTurn(speaker="cohost", text="Here is why it matters."),
            service.AudioEpisodeTurn(speaker="expert", text="Here is the sharper read."),
            service.AudioEpisodeTurn(speaker="host", text="That is the takeaway."),
            service.AudioEpisodeTurn(speaker="cohost", text="Watch the follow-up."),
            service.AudioEpisodeTurn(speaker="expert", text="Keep an eye on adoption."),
        ],
    )

    class FakeTtsService:
        def stream_dialogue_mp3(self, *, turns, item_id=None, user_id=None):
            assert item_id == episode.id
            assert user_id == 123
            assert list(turns)[0] == {"speaker": "host", "text": "Here is the setup."}
            yield b"fake-"
            yield b"stream"

    monkeypatch.setattr(service, "_generate_script", lambda _episode: script)
    monkeypatch.setattr(
        service,
        "get_content_narration_tts_service",
        lambda: FakeTtsService(),
    )
    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(media_base_dir=tmp_path))

    chunks = list(service.stream_audio_episode_chunks(audio_episode_id=episode.id, user_id=123))

    assert chunks == [b"fake-", b"stream"]
    db_session.expire_all()
    generated = db_session.query(AudioEpisode).filter(AudioEpisode.id == episode.id).one()
    assert generated.status == "completed"
    assert generated.script_text is not None
    assert generated.script_text.startswith("Fast Reads Stream")
    audio_path = tmp_path / "audio_episodes" / f"audio-episode-{episode.id}.mp3"
    assert generated.audio_storage_path == str(audio_path)
    assert audio_path.read_bytes() == b"fake-stream"
    assert not (tmp_path / "audio_episodes" / f"audio-episode-{episode.id}.mp3.part").exists()


def test_fit_script_to_dialogue_limit_trims_overlong_turns() -> None:
    script = service.AudioEpisodeScript(
        title="Too Long",
        estimated_duration_seconds=300,
        turns=[
            service.AudioEpisodeTurn(speaker="host", text="One sentence. " + ("x" * 800)),
            service.AudioEpisodeTurn(speaker="cohost", text="Another sentence. " + ("x" * 800)),
            service.AudioEpisodeTurn(speaker="expert", text="Third sentence. " + ("x" * 800)),
            service.AudioEpisodeTurn(speaker="host", text="Fourth sentence. " + ("x" * 800)),
            service.AudioEpisodeTurn(speaker="cohost", text="Fifth sentence. " + ("x" * 800)),
            service.AudioEpisodeTurn(speaker="expert", text="Sixth sentence. " + ("x" * 800)),
        ],
    )

    fitted = service._fit_script_to_dialogue_limit(script)

    assert sum(len(turn.text) for turn in fitted.turns) <= service.DIALOGUE_TEXT_CHAR_LIMIT
    assert len(fitted.turns) == len(script.turns)
    assert all(turn.text for turn in fitted.turns)
