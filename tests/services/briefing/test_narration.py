from __future__ import annotations

from typing import Any, cast

import pytest
from sqlalchemy.orm import Session

from app.models.db import AudioEpisode, BriefingLens, BriefingSegment
from app.services import audio_episodes
from app.services.briefing.narration import (
    BRIEFING_NARRATION_PROMPT_VERSION,
    create_or_reuse_briefing_narration,
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

    assert response.id != legacy_id
    assert response.status.value == "pending"
    assert response.script_text == narration_text
    legacy_row = db_session.query(AudioEpisode).filter(AudioEpisode.id == legacy_id).one()
    assert legacy_row.status == "failed"
    assert legacy_row.error_message == "status_code: 404, raw provider response"
    assert legacy_row.script_text == "Old narration"

    rebuilt = db_session.query(AudioEpisode).filter(AudioEpisode.id == response.id).one()
    assert rebuilt.prompt_version == BRIEFING_NARRATION_PROMPT_VERSION == 2
    assert rebuilt.model == "deterministic"
    assert rebuilt.error_message is None
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
    episode = db_session.query(AudioEpisode).filter(AudioEpisode.id == first.id).one()
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

    assert retried.id == first.id
    assert retried.status.value == "pending"
    db_session.expire_all()
    reset = db_session.query(AudioEpisode).filter(AudioEpisode.id == first.id).one()
    assert reset.error_message is None
    assert reset.model == "deterministic"
    assert reset.prompt_version == BRIEFING_NARRATION_PROMPT_VERSION
    assert reset.script_text == narration_text
    assert reset.script == {
        "title": "Articles briefing",
        "estimated_duration_seconds": 30,
        "turns": [{"speaker": "host", "text": narration_text}],
    }
