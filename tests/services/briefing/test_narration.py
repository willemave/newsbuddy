from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.api.briefing import BriefingNarrationResponse
from app.models.contracts import AudioEpisodeStatus
from app.models.db import AudioEpisode, BriefingLens, BriefingSegment
from app.services import audio_episodes
from app.services.briefing.narration import (
    BRIEFING_NARRATION_CHAPTER_TARGET_SECONDS,
    BRIEFING_NARRATION_PROMPT_VERSION,
    create_or_reuse_briefing_narration,
    plan_briefing_narration_chapters,
    present_briefing_narration,
)


class _FirstAudioEpisodeLookupMiss:
    def __init__(self, query: Any) -> None:
        self._query = query

    def filter(self, *criteria: Any) -> _FirstAudioEpisodeLookupMiss:
        self._query = self._query.filter(*criteria)
        return self

    def first(self) -> None:
        return None


class _InsertRaceSession:
    def __init__(self, session: Any) -> None:
        self._session = session
        self._missed_audio_episode_lookup = False

    def query(self, *entities: Any) -> Any:
        query = self._session.query(*entities)
        if (
            not self._missed_audio_episode_lookup
            and len(entities) == 1
            and entities[0] is AudioEpisode
        ):
            self._missed_audio_episode_lookup = True
            return _FirstAudioEpisodeLookupMiss(query)
        return query

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)


def test_failed_legacy_briefing_narration_is_not_reused_for_new_source_hash(
    db_session,
    test_user,
) -> None:
    lens = BriefingLens(
        user_id=test_user.id,
        key="articles",
        tier="longform",
        title="Articles",
        status="active",
    )
    db_session.add(lens)
    db_session.flush()
    narration_text = (
        "Fresh briefing narration that must be spoken exactly as prepared. " * 350
    ).strip()
    assert len(narration_text) > 18_000
    db_session.add(
        BriefingSegment(
            lens_id=lens.id,
            user_id=test_user.id,
            blocks=[],
            markdown_raw=narration_text,
            narration_text=narration_text,
            source_keys=["content:1"],
            status="active",
            model="test:model",
            prompt_version="test",
        )
    )
    legacy = AudioEpisode(
        user_id=test_user.id,
        kind=audio_episodes.BRIEFING_NARRATION_KIND,
        status="failed",
        title="Articles briefing",
        input_hash="legacy-hash",
        source_item_ids=[],
        source_snapshot={
            "kind": audio_episodes.BRIEFING_NARRATION_KIND,
            "lens_key": "articles",
            "script_text": "Old narration",
        },
        script={"invalid": "legacy payload"},
        script_text="Old narration",
        prompt_version=1,
        model="legacy-model",
        error_message="status_code: 404, raw provider response",
    )
    db_session.add(legacy)
    db_session.commit()
    legacy_id = legacy.id

    response = create_or_reuse_briefing_narration(
        db_session,
        user_id=test_user.id,
        lens_key="articles",
        delivery="stream",
    )

    assert len(response.chapters) == 1
    chapter = response.chapters[0]
    assert chapter.id != legacy_id
    assert response.status.value == "pending"
    assert chapter.script_text == narration_text
    legacy_row = db_session.query(AudioEpisode).filter(AudioEpisode.id == legacy_id).one()
    assert legacy_row.status == "failed"
    assert legacy_row.error_message == "status_code: 404, raw provider response"
    assert legacy_row.script_text == "Old narration"

    rebuilt = db_session.query(AudioEpisode).filter(AudioEpisode.id == chapter.id).one()
    assert rebuilt.prompt_version == BRIEFING_NARRATION_PROMPT_VERSION == 3
    assert rebuilt.model == "deterministic"
    assert rebuilt.error_message is None
    assert rebuilt.episode_group_id == response.episode_group_id
    assert rebuilt.chapter_index == 0
    assert rebuilt.source_snapshot["script_text"] == narration_text
    assert rebuilt.script["turns"] == [{"speaker": "host", "text": narration_text}]


@pytest.mark.parametrize("simulate_insert_race", [False, True])
def test_failed_current_hash_briefing_narration_is_reset_in_place(
    db_session,
    test_user,
    simulate_insert_race: bool,
) -> None:
    lens = BriefingLens(
        user_id=test_user.id,
        key="articles",
        tier="longform",
        title="Articles",
        status="active",
    )
    db_session.add(lens)
    db_session.flush()
    narration_text = "The exact current briefing narration."
    db_session.add(
        BriefingSegment(
            lens_id=lens.id,
            user_id=test_user.id,
            blocks=[],
            markdown_raw=narration_text,
            narration_text=narration_text,
            source_keys=["content:1"],
            status="active",
            model="test:model",
            prompt_version="test",
        )
    )
    db_session.commit()

    first = create_or_reuse_briefing_narration(
        db_session,
        user_id=test_user.id,
        lens_key="articles",
        delivery="stream",
    )
    first_chapter = first.chapters[0]
    episode = db_session.query(AudioEpisode).filter(AudioEpisode.id == first_chapter.id).one()
    episode.status = "failed"
    episode.script = {"invalid": "payload"}
    episode.script_text = "Stale text"
    episode.model = "legacy-model"
    episode.error_message = "raw provider response"
    db_session.commit()

    session = cast(Session, _InsertRaceSession(db_session)) if simulate_insert_race else db_session
    retried = create_or_reuse_briefing_narration(
        session,
        user_id=test_user.id,
        lens_key="articles",
        delivery="stream",
    )

    assert retried.episode_group_id == first.episode_group_id
    assert retried.chapters[0].id == first_chapter.id
    assert retried.status.value == "pending"
    db_session.expire_all()
    reset = db_session.query(AudioEpisode).filter(AudioEpisode.id == first_chapter.id).one()
    assert reset.error_message is None
    assert reset.model == "deterministic"
    assert reset.prompt_version == BRIEFING_NARRATION_PROMPT_VERSION
    assert reset.script_text == narration_text
    assert reset.script == {
        "title": "Articles briefing — Chapter 1",
        "estimated_duration_seconds": 3,
        "turns": [{"speaker": "host", "text": narration_text}],
    }


def test_chapter_planner_targets_five_minutes_without_splitting_segments() -> None:
    segments = [
        _segment(segment_id=4, duration_minutes=3, source_key="content:4"),
        _segment(segment_id=3, duration_minutes=2, source_key="content:3"),
        _segment(segment_id=2, duration_minutes=3, source_key="content:2"),
        _segment(segment_id=1, duration_minutes=2, source_key="content:1"),
    ]

    plans = plan_briefing_narration_chapters(segments)

    assert BRIEFING_NARRATION_CHAPTER_TARGET_SECONDS == 300
    assert [plan.segment_ids for plan in plans] == [(4, 3), (2, 1)]
    assert [plan.source_keys for plan in plans] == [
        ("content:4", "content:3"),
        ("content:2", "content:1"),
    ]
    assert [plan.duration_seconds for plan in plans] == [300, 300]


def test_chapter_planner_keeps_oversized_segment_whole() -> None:
    segments = [
        _segment(segment_id=2, duration_minutes=10, source_key="content:2"),
        _segment(segment_id=1, duration_minutes=5, source_key="content:1"),
    ]

    plans = plan_briefing_narration_chapters(segments)

    assert [plan.segment_ids for plan in plans] == [(2,), (1,)]
    assert [plan.duration_seconds for plan in plans] == [600, 300]


def test_manifest_is_playable_when_first_chapter_completes() -> None:
    first = _audio_chapter(chapter_index=0, status="completed")
    second = _audio_chapter(chapter_index=1, status="processing")

    response = present_briefing_narration([second, first])

    assert response.playable is True
    assert response.status.value == "processing"
    assert [chapter.id for chapter in response.chapters] == [100, 101]
    assert response.duration_seconds == 600


def test_briefing_narration_response_requires_a_chapter() -> None:
    with pytest.raises(ValidationError):
        BriefingNarrationResponse(
            episode_group_id="g" * 64,
            lens_key="articles",
            title="Articles briefing",
            status=AudioEpisodeStatus.PENDING,
            playable=False,
            duration_seconds=0,
            chapters=[],
        )


@pytest.mark.parametrize(
    ("episode_group_id", "chapter_index"),
    [("g" * 64, None), (None, 0), ("g" * 64, -1)],
)
def test_audio_episode_chapter_metadata_requires_complete_nonnegative_pair(
    db_session,
    episode_group_id: str | None,
    chapter_index: int | None,
) -> None:
    episode = AudioEpisode(
        user_id=1,
        kind=audio_episodes.BRIEFING_NARRATION_KIND,
        status="pending",
        title="Invalid chapter",
        input_hash=f"invalid-chapter-{episode_group_id is None}-{chapter_index}",
        episode_group_id=episode_group_id,
        chapter_index=chapter_index,
        source_item_ids=[],
        source_snapshot={},
        prompt_version=BRIEFING_NARRATION_PROMPT_VERSION,
    )
    db_session.add(episode)

    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_background_delivery_persists_and_enqueues_chapters_newest_first(
    db_session,
    test_user,
    monkeypatch,
) -> None:
    lens = BriefingLens(
        user_id=test_user.id,
        key="articles",
        tier="longform",
        title="Articles",
        status="active",
    )
    db_session.add(lens)
    db_session.flush()
    for segment_id, duration_minutes in [(4, 3), (3, 2), (2, 3), (1, 2)]:
        segment = _segment(
            segment_id=segment_id,
            duration_minutes=duration_minutes,
            source_key=f"content:{segment_id}",
        )
        segment.lens_id = lens.id
        segment.user_id = test_user.id
        segment.created_at = datetime(2026, 7, 19, 12, segment_id, tzinfo=None)
        db_session.add(segment)
    db_session.commit()

    enqueued_episode_ids: list[int] = []
    monkeypatch.setattr(
        "app.services.audio_episodes.presentation.enqueue_audio_episode_generation",
        enqueued_episode_ids.append,
    )

    response = create_or_reuse_briefing_narration(
        db_session,
        user_id=test_user.id,
        lens_key="articles",
        delivery="background",
    )

    rows = (
        db_session.query(AudioEpisode)
        .filter(AudioEpisode.episode_group_id == response.episode_group_id)
        .order_by(AudioEpisode.chapter_index)
        .all()
    )
    assert len(rows) == len(response.chapters) == 2
    assert [row.chapter_index for row in rows] == [0, 1]
    assert [row.source_snapshot["segment_ids"] for row in rows] == [[4, 3], [2, 1]]
    assert enqueued_episode_ids == [row.id for row in rows]


def _segment(
    *,
    segment_id: int,
    duration_minutes: int,
    source_key: str,
) -> BriefingSegment:
    word_count = 145 * duration_minutes
    narration_text = " ".join(f"word-{index}" for index in range(word_count))
    return BriefingSegment(
        id=segment_id,
        lens_id=1,
        user_id=1,
        blocks=[],
        markdown_raw=narration_text,
        narration_text=narration_text,
        source_keys=[source_key],
        status="active",
        model="test:model",
        prompt_version="test",
    )


def _audio_chapter(*, chapter_index: int, status: str) -> AudioEpisode:
    return AudioEpisode(
        id=100 + chapter_index,
        user_id=1,
        kind=audio_episodes.BRIEFING_NARRATION_KIND,
        status=status,
        title=f"Articles briefing — Chapter {chapter_index + 1}",
        input_hash=f"chapter-{chapter_index}",
        episode_group_id="g" * 64,
        chapter_index=chapter_index,
        source_item_ids=[],
        source_snapshot={
            "lens_key": "articles",
            "lens_title": "Articles",
            "source_count": 1,
            "source_keys": [f"content:{chapter_index + 1}"],
            "script_text": "Narration",
        },
        script_text="Narration",
        prompt_version=BRIEFING_NARRATION_PROMPT_VERSION,
        model="deterministic",
        duration_seconds=300,
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )
