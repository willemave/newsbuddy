from sqlalchemy.orm import Session

import app.services.briefing.compaction as compaction_service
import app.services.briefing.window_composition as window_composition_service
from app.core.settings import get_settings
from app.models.contracts import ContentClassification, ContentType
from app.models.db import BriefingLens, BriefingSegment, BriefingState
from app.models.db.users import User
from app.services.briefing.refresh import run_briefing_refresh


def test_fragmentation_metrics_report_achievable_floor_and_duplicates(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "briefing_news_window_max", 3)

    metrics = compaction_service.briefing_fragmentation_metrics(
        [
            ["news:1", "news:2", "news:2"],
            ["news:3", "news:4"],
            ["news:5", "news:6"],
            ["news:7", "news:8"],
        ],
        tier="news",
        read_keys={"news:8"},
        settings=settings,
    )

    assert metrics.unique_unread_source_count == 7
    assert metrics.window_source_limit == 3
    assert metrics.minimum_required_segment_count == 3
    assert metrics.excess_fragmentation == 1


def test_fragmentation_metrics_handle_zero_sources(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "briefing_window_max", 5)

    metrics = compaction_service.briefing_fragmentation_metrics(
        [[], []],
        tier="longform",
        read_keys=set(),
        settings=settings,
    )

    assert metrics.unique_unread_source_count == 0
    assert metrics.minimum_required_segment_count == 0
    assert metrics.excess_fragmentation == 2


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
    original_compose = window_composition_service.compose_window_groups

    def compose_without_compaction(first_windows, _second_windows, **kwargs):
        composed_first, _ = original_compose(first_windows, [], **kwargs)
        return composed_first, []

    monkeypatch.setattr(
        window_composition_service,
        "compose_window_groups",
        compose_without_compaction,
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
    original_compose = window_composition_service.compose_window_groups

    def compose_after_concurrent_version_change(*args, **kwargs):
        composed = original_compose(*args, **kwargs)
        state = db_session.query(BriefingState).filter_by(user_id=user_id).one()
        state.version = int(state.version or 0) + 1
        db_session.commit()
        return composed

    monkeypatch.setattr(
        window_composition_service,
        "compose_window_groups",
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
