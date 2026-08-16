import pytest
from sqlalchemy.orm import Session

import app.services.briefing.compaction as compaction_service
import app.services.briefing.window_composition as window_composition_service
from app.constants import AGGREGATOR_FEED_URL_PREFIX, AGGREGATOR_SCRAPER_TYPE
from app.core.settings import get_settings
from app.models.contracts import ContentClassification, ContentType
from app.models.db import BriefingLens, BriefingSegment, BriefingState, UserScraperConfig
from app.models.db.users import User
from app.services.briefing.refresh import run_briefing_refresh

pytestmark = pytest.mark.usefixtures("stub_briefing_layout_generator")


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


def test_fragmentation_metrics_handle_zero_sources() -> None:
    settings = get_settings()

    metrics = compaction_service.briefing_fragmentation_metrics(
        [[], []],
        tier="longform",
        read_keys=set(),
        settings=settings,
    )

    assert metrics.unique_unread_source_count == 0
    assert metrics.minimum_required_segment_count == 0
    assert metrics.excess_fragmentation == 2


def test_release_path_compacts_two_singleton_news_segments(
    db_session: Session,
    test_user: User,
    news_item_factory,
    monkeypatch,
) -> None:
    settings = get_settings()
    assert test_user.id is not None
    user_id = test_user.id
    monkeypatch.setattr(settings, "briefing_enabled_user_ids", [user_id])
    monkeypatch.setattr(settings, "briefing_taxonomy_planner_enabled", False)
    items = [
        news_item_factory(
            visibility_scope="user",
            owner_user_id=user_id,
            article_title=f"Singleton news story {index}",
            summary_title=f"Singleton news story {index}",
        )
        for index in range(2)
    ]
    lens = BriefingLens(
        user_id=user_id,
        key="news-software",
        tier="news",
        title="Software",
        deck="Software news.",
        position=1,
    )
    db_session.add(lens)
    db_session.flush()
    db_session.add_all(
        [
            BriefingSegment(
                lens_id=lens.id,
                user_id=user_id,
                blocks=[],
                source_keys=[f"news:{item.id}"],
                status="active",
                model="test",
                prompt_version="test",
            )
            for item in items
        ]
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
    assert result.compacted_segments == 2
    assert len(active) == 1
    assert set(active[0].source_keys or []) == {f"news:{item.id}" for item in items}
    assert "compaction_segment" in (active[0].warnings or [])


def test_release_path_compacts_visible_news_without_unavailable_donor_poisoning_lens(
    db_session: Session,
    test_user: User,
    news_item_factory,
    monkeypatch,
) -> None:
    settings = get_settings()
    assert test_user.id is not None
    user_id = test_user.id
    monkeypatch.setattr(settings, "briefing_enabled_user_ids", [user_id])
    monkeypatch.setattr(settings, "briefing_taxonomy_planner_enabled", False)
    _add_aggregator_subscription(db_session, user_id=user_id, key="techmeme")
    visible_items = [
        news_item_factory(
            platform="techmeme",
            article_title=f"Visible singleton story {index}",
            summary_title=f"Visible singleton story {index}",
        )
        for index in range(2)
    ]
    unavailable_item = news_item_factory(
        platform="mediagazer",
        article_title="Unavailable stale story",
        summary_title="Unavailable stale story",
    )
    lens = BriefingLens(
        user_id=user_id,
        key="news-ai-society",
        tier="news",
        title="AI & Society",
        deck="AI and society news.",
        position=1,
    )
    db_session.add(lens)
    db_session.flush()
    donors = [
        BriefingSegment(
            lens_id=lens.id,
            user_id=user_id,
            blocks=[],
            source_keys=[f"news:{item.id}"],
            status="active",
            model="test",
            prompt_version="test",
        )
        for item in [visible_items[0], unavailable_item, visible_items[1]]
    ]
    db_session.add_all(donors)
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
    assert result.compacted_segments == 3
    assert len(active) == 1
    assert set(active[0].source_keys or []) == {f"news:{item.id}" for item in visible_items}
    assert all(donor.status == "compacted" for donor in donors)


def test_release_path_removes_stale_only_news_segment_without_composition(
    db_session: Session,
    test_user: User,
    news_item_factory,
    monkeypatch,
) -> None:
    settings = get_settings()
    assert test_user.id is not None
    user_id = test_user.id
    monkeypatch.setattr(settings, "briefing_enabled_user_ids", [user_id])
    monkeypatch.setattr(settings, "briefing_taxonomy_planner_enabled", False)
    _add_aggregator_subscription(db_session, user_id=user_id, key="techmeme")
    unavailable_item = news_item_factory(
        platform="mediagazer",
        article_title="Unavailable stale story",
        summary_title="Unavailable stale story",
    )
    lens = BriefingLens(
        user_id=user_id,
        key="news-ai-society",
        tier="news",
        title="AI & Society",
        deck="AI and society news.",
        position=1,
    )
    db_session.add(lens)
    db_session.flush()
    donor = BriefingSegment(
        lens_id=lens.id,
        user_id=user_id,
        blocks=[],
        source_keys=[f"news:{unavailable_item.id}"],
        status="active",
        model="test",
        prompt_version="test",
    )
    db_session.add(donor)
    db_session.commit()
    monkeypatch.setattr(
        window_composition_service,
        "compose_window",
        lambda *_args, **_kwargs: pytest.fail("stale-only repair must not compose a window"),
    )

    result = run_briefing_refresh(
        db_session,
        user_id=user_id,
        mode="sweep",
        use_llm=False,
        settings=settings,
    )
    db_session.commit()

    assert result.compacted_segments == 1
    assert _active_segments(db_session, user_id=user_id) == []
    assert donor.status == "compacted"


def test_release_path_splits_multi_source_article_segments_without_loss(
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
    assert result.compacted_segments == 5
    assert len(active) == 10
    assert after_keys == before_keys
    compacted = [segment for segment in active if "compaction_segment" in (segment.warnings or [])]
    assert len(compacted) == 10
    assert all(len(segment.source_keys or []) == 1 for segment in compacted)


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


def test_release_path_aborts_when_source_eligibility_changes_during_compose(
    db_session: Session,
    test_user: User,
    news_item_factory,
    monkeypatch,
) -> None:
    settings = get_settings()
    assert test_user.id is not None
    user_id = test_user.id
    monkeypatch.setattr(settings, "briefing_enabled_user_ids", [user_id])
    monkeypatch.setattr(settings, "briefing_taxonomy_planner_enabled", False)
    subscription = _add_aggregator_subscription(
        db_session,
        user_id=user_id,
        key="techmeme",
    )
    items = [
        news_item_factory(
            platform="techmeme",
            article_title=f"Eligibility story {index}",
            summary_title=f"Eligibility story {index}",
        )
        for index in range(2)
    ]
    lens = BriefingLens(
        user_id=user_id,
        key="news-ai-society",
        tier="news",
        title="AI & Society",
        deck="AI and society news.",
        position=1,
    )
    db_session.add(lens)
    db_session.flush()
    db_session.add_all(
        [
            BriefingSegment(
                lens_id=lens.id,
                user_id=user_id,
                blocks=[],
                source_keys=[f"news:{item.id}"],
                status="active",
                model="test",
                prompt_version="test",
            )
            for item in items
        ]
    )
    db_session.commit()
    original_compose = window_composition_service.compose_window_groups

    def compose_after_subscription_change(*args, **kwargs):
        composed = original_compose(*args, **kwargs)
        subscription.is_active = False
        db_session.commit()
        return composed

    monkeypatch.setattr(
        window_composition_service,
        "compose_window_groups",
        compose_after_subscription_change,
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
    assert result.compacted_segments == 0
    assert len(active) == 2
    assert {str(key) for segment in active for key in (segment.source_keys or [])} == {
        f"news:{item.id}" for item in items
    }


def test_append_does_not_compact_singleton_article_segments(
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
    existing_source_groups = [[content] for content in contents[:6]]
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
    assert result.compacted_segments == 0
    assert len(active) == 7
    assert active_source_keys == expected_source_keys


def test_large_lens_does_not_compact_full_windows() -> None:
    segments = [
        BriefingSegment(
            source_keys=[f"news:{segment_index}:{source_index}" for source_index in range(4)]
        )
        for segment_index in range(63)
    ]

    assert (
        compaction_service._compaction_donors(
            segments,
            read_keys=set(),
            tier="news",
        )
        == []
    )


@pytest.mark.parametrize("tier", ["longform", "audio"])
def test_non_news_compaction_selects_only_multi_source_segments(tier: str) -> None:
    singleton = BriefingSegment(source_keys=["content:1"])
    combined = BriefingSegment(source_keys=["content:2", "content:3"])

    assert compaction_service._compaction_donors(
        [singleton, combined],
        read_keys=set(),
        tier=tier,
    ) == [combined]


def test_non_news_compaction_selects_partially_read_multi_source_segment() -> None:
    combined = BriefingSegment(source_keys=["content:1", "content:2"])

    assert compaction_service._compaction_donors(
        [combined],
        read_keys={"content:1"},
        tier="longform",
    ) == [combined]
    assert compaction_service._ordered_unread_source_keys(
        [combined],
        read_keys={"content:1"},
    ) == ["content:2"]


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


def _add_aggregator_subscription(
    db_session: Session,
    *,
    user_id: int,
    key: str,
) -> UserScraperConfig:
    row = UserScraperConfig(
        user_id=user_id,
        scraper_type=AGGREGATOR_SCRAPER_TYPE,
        display_name=key.title(),
        feed_url=f"{AGGREGATOR_FEED_URL_PREFIX}{key}",
        config={"key": key},
        is_active=True,
    )
    db_session.add(row)
    db_session.flush()
    return row
