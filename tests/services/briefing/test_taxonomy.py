import pytest
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.models.db import BriefingLens, BriefingPendingSource, BriefingSegment
from app.models.db.users import User
from app.services.briefing.taxonomy import (
    TaxonomyCategory,
    TaxonomyPlan,
    TaxonomyPlanError,
    apply_taxonomy_if_needed,
    apply_taxonomy_plan,
)


def test_taxonomy_plan_requires_exact_lens_coverage(
    db_session: Session,
    test_user: User,
) -> None:
    assert test_user.id is not None
    user_id = test_user.id
    _add_lens(db_session, user_id=user_id, key="news-ai", title="AI")
    _add_lens(db_session, user_id=user_id, key="news-security", title="Security")
    db_session.flush()

    plan = TaxonomyPlan(
        categories=[
            TaxonomyCategory(
                key="news-ai-practice",
                title="AI in Practice",
                deck="Practical AI systems and deployment stories.",
                routing_rule="Use for practical deployment and evaluation of AI systems.",
                include_lens_keys=["news-ai"],
            )
        ],
        operating_model="Preserve stable desks unless the evidence changes.",
    )

    with pytest.raises(TaxonomyPlanError, match="missing lens keys"):
        apply_taxonomy_plan(db_session, user_id=user_id, plan=plan, settings=get_settings())


def test_apply_taxonomy_plan_merges_lenses_and_repoints_rows(
    db_session: Session,
    test_user: User,
) -> None:
    settings = get_settings()
    assert test_user.id is not None
    user_id = test_user.id
    ai = _add_lens(
        db_session,
        user_id=user_id,
        key="news-ai",
        title="AI",
        centroid=[1.0, 0.0],
        centroid_weight=2,
    )
    dev = _add_lens(
        db_session,
        user_id=user_id,
        key="news-dev-tools",
        title="Dev Tools",
        centroid=[0.8, 0.2],
        centroid_weight=3,
    )
    security = _add_lens(
        db_session,
        user_id=user_id,
        key="news-security",
        title="Security",
        centroid=[0.0, 1.0],
        centroid_weight=1,
    )
    db_session.flush()
    assert ai.key is not None
    assert dev.id is not None
    assert dev.key is not None
    assert security.key is not None
    db_session.add(
        BriefingSegment(
            lens_id=int(dev.id),
            user_id=user_id,
            blocks=[],
            markdown_raw="",
            narration_text="",
            source_keys=["news:1"],
            status="active",
            model="test",
            prompt_version="v1",
            warnings=[],
        )
    )
    db_session.add(
        BriefingPendingSource(
            user_id=user_id,
            lens_key=dev.key,
            source_kind="news",
            source_id=2,
        )
    )
    db_session.flush()

    plan = TaxonomyPlan(
        categories=[
            TaxonomyCategory(
                key="news-ai-in-practice",
                title="AI in Practice",
                deck="AI systems, developer workflows, and deployment trade-offs.",
                routing_rule="Use for practical AI systems and developer-facing AI tooling.",
                include_lens_keys=[ai.key, dev.key],
            ),
            TaxonomyCategory(
                key=security.key,
                title="Cybersecurity",
                deck="Vulnerabilities, privacy risks, and defensive security work.",
                routing_rule="Use for vulnerabilities, privacy risks, and defensive security.",
                include_lens_keys=[security.key],
            ),
        ],
        operating_model="Preserve desks when boundaries remain stable.",
    )

    changed = apply_taxonomy_plan(db_session, user_id=user_id, plan=plan, settings=settings)

    assert changed >= 2
    active = {
        lens.key: lens
        for lens in db_session.query(BriefingLens)
        .filter(BriefingLens.user_id == user_id, BriefingLens.status == "active")
        .all()
    }
    assert set(active) == {"news-ai-in-practice", "news-security"}
    winner = active["news-ai-in-practice"]
    assert winner.title == "AI in Practice"
    assert winner.routing_rule == "Use for practical AI systems and developer-facing AI tooling."
    assert winner.centroid_weight == 5
    assert winner.centroid_model == settings.briefing_category_embedding_model
    assert ai.status == "merged"
    assert dev.status == "merged"
    assert security.status == "active"
    segment = db_session.query(BriefingSegment).one()
    assert segment.lens_id == winner.id
    pending = db_session.query(BriefingPendingSource).one()
    assert pending.lens_key == winner.key


def test_apply_taxonomy_if_needed_uses_planner_only_when_over_cap(
    db_session: Session,
    test_user: User,
    monkeypatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "briefing_max_news_lenses", 3)
    assert test_user.id is not None
    user_id = test_user.id
    for index in range(4):
        _add_lens(
            db_session,
            user_id=user_id,
            key=f"news-topic-{index}",
            title=f"Topic {index}",
            position=index + 2,
        )
    db_session.flush()
    seen_lens_counts: list[int] = []

    def fake_planner(planner_input):  # noqa: ANN001
        seen_lens_counts.append(len(planner_input.lens_dossiers))
        return TaxonomyPlan(
            categories=[
                TaxonomyCategory(
                    key="news-general-a",
                    title="General A",
                    deck="Generalized desk for the first pair of news topics.",
                    routing_rule="Use for the first pair of related news topics.",
                    include_lens_keys=["news-topic-0", "news-topic-1"],
                ),
                TaxonomyCategory(
                    key="news-general-b",
                    title="General B",
                    deck="Generalized desk for the second pair of news topics.",
                    routing_rule="Use for the second pair of related news topics.",
                    include_lens_keys=["news-topic-2", "news-topic-3"],
                ),
            ],
            operating_model="Keep generalized desks stable.",
        )

    changed = apply_taxonomy_if_needed(
        db_session,
        user_id=user_id,
        settings=settings,
        planner=fake_planner,
        use_llm=False,
    )

    assert changed >= 2
    assert seen_lens_counts == [4]
    active_keys = {
        lens.key
        for lens in db_session.query(BriefingLens)
        .filter(BriefingLens.user_id == user_id, BriefingLens.status == "active")
        .all()
    }
    assert active_keys == {"news-general-a", "news-general-b"}


def _add_lens(
    db_session: Session,
    *,
    user_id: int,
    key: str,
    title: str,
    position: int = 2,
    centroid: list[float] | None = None,
    centroid_weight: int = 0,
) -> BriefingLens:
    lens = BriefingLens(
        user_id=user_id,
        key=key,
        tier="news",
        title=title,
        deck=f"{title} stories.",
        position=position,
        status="active",
        centroid=centroid,
        centroid_weight=centroid_weight,
    )
    db_session.add(lens)
    return lens
