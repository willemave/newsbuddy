from sqlalchemy.orm import Session

import app.services.briefing.compaction as compaction_service
from app.core.settings import get_settings
from app.models.contracts import ContentClassification, ContentType
from app.models.db import BriefingLens, BriefingSegment, BriefingState
from app.models.db.users import User
from app.services.briefing.refresh import run_briefing_refresh


def test_release_path_compacts_every_unread_donor_source_without_loss(
    db_session: Session,
    test_user: User,
    content_factory,
    status_entry_factory,
    monkeypatch,
) -> None:
    settings = get_settings()
    assert test_user.id is not None
    user_id = test_user.id
    monkeypatch.setattr(settings, "briefing_enabled_user_ids", [user_id])
    monkeypatch.setattr(settings, "briefing_taxonomy_planner_enabled", False)
    monkeypatch.setattr(settings, "briefing_max_segments_per_lens", 3)
    monkeypatch.setattr(settings, "briefing_window_min", 1)
    monkeypatch.setattr(settings, "briefing_window_max", 4)
    before_keys = _seed_fragmented_article_lens(
        db_session,
        user=test_user,
        content_factory=content_factory,
        status_entry_factory=status_entry_factory,
    )

    result = run_briefing_refresh(
        db_session,
        user_id=user_id,
        mode="sweep",
        use_llm=False,
        settings=settings,
    )
    db_session.commit()

    active = _active_segments(db_session, user_id=user_id)
    after_keys = {str(key) for segment in active for key in (segment.source_keys or [])}
    assert result.compacted_segments == 4
    assert len(active) == 3
    assert after_keys == before_keys
    compacted = [segment for segment in active if "compaction_segment" in (segment.warnings or [])]
    assert len(compacted) == 2


def test_release_path_leaves_donors_active_when_compaction_coverage_is_incomplete(
    db_session: Session,
    test_user: User,
    content_factory,
    status_entry_factory,
    monkeypatch,
) -> None:
    settings = get_settings()
    assert test_user.id is not None
    user_id = test_user.id
    monkeypatch.setattr(settings, "briefing_enabled_user_ids", [user_id])
    monkeypatch.setattr(settings, "briefing_taxonomy_planner_enabled", False)
    monkeypatch.setattr(settings, "briefing_max_segments_per_lens", 3)
    monkeypatch.setattr(settings, "briefing_window_min", 1)
    monkeypatch.setattr(settings, "briefing_window_max", 4)
    monkeypatch.setattr(
        compaction_service,
        "compose_compactions",
        lambda *_args, **_kwargs: [],
    )
    before_keys = _seed_fragmented_article_lens(
        db_session,
        user=test_user,
        content_factory=content_factory,
        status_entry_factory=status_entry_factory,
    )

    result = run_briefing_refresh(
        db_session,
        user_id=user_id,
        mode="sweep",
        use_llm=False,
        settings=settings,
    )
    db_session.commit()

    active = _active_segments(db_session, user_id=user_id)
    after_keys = {str(key) for segment in active for key in (segment.source_keys or [])}
    assert result.compacted_segments == 0
    assert len(active) == 5
    assert after_keys == before_keys


def test_release_path_aborts_compaction_when_global_version_changes_during_compose(
    db_session: Session,
    test_user: User,
    content_factory,
    status_entry_factory,
    monkeypatch,
) -> None:
    settings = get_settings()
    assert test_user.id is not None
    user_id = test_user.id
    monkeypatch.setattr(settings, "briefing_enabled_user_ids", [user_id])
    monkeypatch.setattr(settings, "briefing_taxonomy_planner_enabled", False)
    monkeypatch.setattr(settings, "briefing_max_segments_per_lens", 3)
    monkeypatch.setattr(settings, "briefing_window_min", 1)
    monkeypatch.setattr(settings, "briefing_window_max", 4)
    before_keys = _seed_fragmented_article_lens(
        db_session,
        user=test_user,
        content_factory=content_factory,
        status_entry_factory=status_entry_factory,
    )
    original_compose = compaction_service.compose_compactions

    def compose_after_concurrent_version_change(*args, **kwargs):
        composed = original_compose(*args, **kwargs)
        state = db_session.query(BriefingState).filter_by(user_id=user_id).one()
        state.version = int(state.version or 0) + 1
        db_session.commit()
        return composed

    monkeypatch.setattr(
        compaction_service,
        "compose_compactions",
        compose_after_concurrent_version_change,
    )

    result = run_briefing_refresh(
        db_session,
        user_id=user_id,
        mode="sweep",
        use_llm=False,
        settings=settings,
    )
    db_session.commit()

    active = _active_segments(db_session, user_id=user_id)
    after_keys = {str(key) for segment in active for key in (segment.source_keys or [])}
    assert result.compacted_segments == 0
    assert len(active) == 5
    assert after_keys == before_keys


def test_release_path_reserves_capacity_for_planned_append(
    db_session: Session,
    test_user: User,
    content_factory,
    status_entry_factory,
    monkeypatch,
) -> None:
    settings = get_settings()
    assert test_user.id is not None
    user_id = test_user.id
    monkeypatch.setattr(settings, "briefing_enabled_user_ids", [user_id])
    monkeypatch.setattr(settings, "briefing_taxonomy_planner_enabled", False)
    monkeypatch.setattr(settings, "briefing_max_segments_per_lens", 3)
    monkeypatch.setattr(settings, "briefing_window_min", 1)
    monkeypatch.setattr(settings, "briefing_window_max", 4)

    contents = []
    for index in range(7):
        content = content_factory(
            content_type=ContentType.ARTICLE,
            title=f"Capacity article {index}",
            classification=ContentClassification.TO_READ.value,
            content_metadata={
                "summary": {
                    "overview": f"Capacity summary {index}",
                    "key_points": [f"Capacity point {index}"],
                }
            },
        )
        status_entry_factory(user=test_user, content=content, status="inbox")
        contents.append(content)

    lens = BriefingLens(
        user_id=user_id,
        key="articles",
        tier="longform",
        title="Articles",
        deck="Existing articles.",
        position=1,
    )
    db_session.add(lens)
    db_session.flush()
    existing_source_groups = [contents[0:1], contents[1:2], contents[2:6]]
    for group in existing_source_groups:
        db_session.add(
            BriefingSegment(
                lens_id=lens.id,
                user_id=user_id,
                blocks=[],
                source_keys=[f"content:{content.id}" for content in group],
                status="active",
                model="test",
                prompt_version="test",
            )
        )
    db_session.commit()

    result = run_briefing_refresh(
        db_session,
        user_id=user_id,
        mode="sweep",
        use_llm=False,
        settings=settings,
    )
    db_session.commit()

    active = _active_segments(db_session, user_id=user_id)
    active_source_keys = {str(key) for segment in active for key in (segment.source_keys or [])}
    expected_source_keys = {f"content:{content.id}" for content in contents}
    assert result.appended_segments == 1
    assert result.compacted_segments == 2
    assert len(active) == settings.briefing_max_segments_per_lens
    assert active_source_keys == expected_source_keys


def _seed_fragmented_article_lens(
    db_session: Session,
    *,
    user: User,
    content_factory,
    status_entry_factory,
) -> set[str]:
    assert user.id is not None
    contents = []
    for index in range(10):
        content = content_factory(
            content_type=ContentType.ARTICLE,
            title=f"Briefing article {100 + index}",
            classification=ContentClassification.TO_READ.value,
            content_metadata={
                "summary": {
                    "overview": f"Summary {100 + index}",
                    "key_points": [f"Point {100 + index}"],
                }
            },
        )
        status_entry_factory(user=user, content=content, status="inbox")
        contents.append(content)

    lens = BriefingLens(
        user_id=user.id,
        key="articles",
        tier="longform",
        title="Articles",
        deck="Existing articles.",
        position=1,
    )
    db_session.add(lens)
    db_session.flush()
    source_keys = [f"content:{content.id}" for content in contents]
    for index in range(0, len(source_keys), 2):
        db_session.add(
            BriefingSegment(
                lens_id=lens.id,
                user_id=user.id,
                blocks=[],
                source_keys=source_keys[index : index + 2],
                status="active",
                model="test",
                prompt_version="test",
            )
        )
    db_session.commit()
    return set(source_keys)


def _active_segments(db_session: Session, *, user_id: int) -> list[BriefingSegment]:
    return (
        db_session.query(BriefingSegment)
        .filter(BriefingSegment.user_id == user_id)
        .filter(BriefingSegment.status.in_(("active", "degraded")))
        .all()
    )
