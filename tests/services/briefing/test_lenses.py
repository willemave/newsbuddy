from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.models.db import BriefingLens, BriefingPendingSource
from app.models.db.users import User
from app.services.briefing.lenses import LensName, assign_pending_lenses


def test_stale_low_volume_news_uses_misc_lens_without_embedding(
    db_session: Session,
    test_user: User,
    news_item_factory,
    monkeypatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "briefing_new_lens_min_items", 3)
    monkeypatch.setattr(settings, "briefing_semantic_category_assignment_enabled", False)
    assert test_user.id is not None
    user_id = test_user.id
    item = news_item_factory(
        raw_metadata={},
        visibility_scope="user",
        owner_user_id=user_id,
    )

    db_session.add(
        BriefingLens(
            user_id=user_id,
            key="news-ai",
            tier="news",
            title="AI",
            deck="Artificial intelligence reads.",
            position=2,
            status="active",
            centroid=[0.1, 0.2],
        )
    )
    pending = BriefingPendingSource(
        user_id=user_id,
        source_kind="news",
        source_id=item.id,
        enqueued_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=2),
    )
    db_session.add(pending)
    db_session.flush()

    def fail_encode(_texts):  # noqa: ANN001
        raise AssertionError("stale single-item lens assignment should not embed")

    monkeypatch.setattr("app.services.briefing.lenses.encode_news_texts", fail_encode)

    changed = assign_pending_lenses(db_session, user_id=user_id, settings=settings)

    assert changed == 1
    assert pending.lens_key == "misc"
    misc_lens = (
        db_session.query(BriefingLens)
        .filter(BriefingLens.user_id == user_id, BriefingLens.key == "misc")
        .one()
    )
    assert misc_lens.title == "Briefs"


def test_new_news_lens_skips_centroid_embedding_by_default(
    db_session: Session,
    test_user: User,
    news_item_factory,
    monkeypatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "briefing_new_lens_min_items", 4)
    monkeypatch.setattr(settings, "briefing_semantic_category_assignment_enabled", False)
    monkeypatch.setattr(settings, "briefing_centroid_assignment_enabled", False)
    assert test_user.id is not None
    user_id = test_user.id
    items = [
        news_item_factory(
            raw_metadata={},
            visibility_scope="user",
            owner_user_id=user_id,
            article_title=f"Vector Load Story {index}",
            summary_title=f"Vector Load Story {index}",
        )
        for index in range(4)
    ]
    for item in items:
        db_session.add(
            BriefingPendingSource(
                user_id=user_id,
                source_kind="news",
                source_id=item.id,
            )
        )
    db_session.flush()
    encode_calls = 0

    def track_encode(_texts):  # noqa: ANN001
        nonlocal encode_calls
        encode_calls += 1
        raise AssertionError("briefing lens assignment should not embed by default")

    monkeypatch.setattr("app.services.briefing.lenses.encode_news_texts", track_encode)

    changed = assign_pending_lenses(db_session, user_id=user_id, settings=settings)

    assert changed == 4
    assert encode_calls == 0
    lens = db_session.query(BriefingLens).filter(BriefingLens.user_id == user_id).one()
    assert lens.key == "news-vector"
    assert lens.centroid is None


def test_semantic_category_assignment_splits_news_clusters(
    db_session: Session,
    test_user: User,
    news_item_factory,
    monkeypatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "briefing_semantic_category_assignment_enabled", True)
    monkeypatch.setattr(settings, "briefing_new_lens_min_items", 2)
    monkeypatch.setattr(settings, "briefing_category_cluster_similarity", 0.9)
    assert test_user.id is not None
    user_id = test_user.id
    titles = [
        "Nvidia GPU supply tightens",
        "AI chip startups raise new funds",
        "Treasury yields move lower",
        "Bond funds brace for Fed cuts",
    ]
    items = [
        news_item_factory(
            raw_metadata={},
            visibility_scope="user",
            owner_user_id=user_id,
            article_title=title,
            summary_title=title,
            summary_text=f"{title} summary",
        )
        for title in titles
    ]
    for item in items:
        db_session.add(
            BriefingPendingSource(
                user_id=user_id,
                source_kind="news",
                source_id=item.id,
            )
        )
    db_session.flush()

    def fake_encode(
        texts: list[str],
        *,
        model_spec: str,
        batch_size: int,
        timeout_seconds: int,
    ) -> np.ndarray:
        assert model_spec == "openrouter:qwen/qwen3-embedding-8b"
        assert batch_size == settings.briefing_category_embedding_batch_size
        assert timeout_seconds == settings.briefing_category_embedding_timeout_seconds
        vectors = []
        for text in texts:
            if "Nvidia" in text or "chip" in text:
                vectors.append([1.0, 0.0])
            elif "Treasury" in text or "Bond" in text:
                vectors.append([0.0, 1.0])
            else:
                vectors.append([0.0, 0.0])
        return np.asarray(vectors, dtype=np.float32)

    def naming_fn(sources):  # noqa: ANN001
        title = sources[0].title
        if "Nvidia" in title:
            return LensName(
                key="news-ai-chips",
                title="AI Chips",
                deck="AI infrastructure and semiconductor supply stories.",
            )
        return LensName(
            key="news-rates",
            title="Rates",
            deck="Bond market and interest-rate stories.",
        )

    monkeypatch.setattr(
        "app.services.briefing.lenses.encode_texts_with_embedding_model",
        fake_encode,
    )

    changed = assign_pending_lenses(
        db_session,
        user_id=user_id,
        naming_fn=naming_fn,
        settings=settings,
    )

    assert changed == 4
    pending_lens_keys = {
        row.lens_key
        for row in db_session.query(BriefingPendingSource).order_by(BriefingPendingSource.id)
    }
    assert pending_lens_keys == {"news-ai-chips", "news-rates"}
    lenses = {
        lens.key: lens
        for lens in db_session.query(BriefingLens).filter(BriefingLens.user_id == user_id).all()
    }
    assert lenses["news-ai-chips"].title == "AI Chips"
    assert lenses["news-rates"].title == "Rates"
    assert lenses["news-ai-chips"].centroid == [1.0, 0.0]
    assert lenses["news-rates"].centroid == [0.0, 1.0]


def test_semantic_category_assignment_uses_existing_lens_profile(
    db_session: Session,
    test_user: User,
    news_item_factory,
    monkeypatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "briefing_semantic_category_assignment_enabled", True)
    monkeypatch.setattr(settings, "briefing_new_lens_min_items", 4)
    monkeypatch.setattr(settings, "briefing_category_similarity", 0.9)
    assert test_user.id is not None
    user_id = test_user.id
    item = news_item_factory(
        raw_metadata={},
        visibility_scope="user",
        owner_user_id=user_id,
        article_title="Nvidia GPU demand keeps rising",
        summary_title="Nvidia GPU demand keeps rising",
    )
    existing = BriefingLens(
        user_id=user_id,
        key="news-ai",
        tier="news",
        title="AI Chips",
        deck="Semiconductors, accelerators, and AI infrastructure.",
        position=2,
        status="active",
    )
    db_session.add(existing)
    pending = BriefingPendingSource(
        user_id=user_id,
        source_kind="news",
        source_id=item.id,
    )
    db_session.add(pending)
    db_session.flush()

    def fake_encode(
        texts: list[str],
        *,
        model_spec: str,
        batch_size: int,
        timeout_seconds: int,
    ) -> np.ndarray:
        del model_spec, batch_size, timeout_seconds
        assert len(texts) == 2
        return np.asarray([[1.0, 0.0], [1.0, 0.0]], dtype=np.float32)

    def fail_naming(_sources):  # noqa: ANN001
        raise AssertionError("existing lens assignment should not name a new lens")

    monkeypatch.setattr(
        "app.services.briefing.lenses.encode_texts_with_embedding_model",
        fake_encode,
    )

    changed = assign_pending_lenses(
        db_session,
        user_id=user_id,
        naming_fn=fail_naming,
        settings=settings,
    )

    assert changed == 1
    assert pending.lens_key == "news-ai"
    assert existing.centroid == [1.0, 0.0]
    assert existing.centroid_weight == 1
    assert existing.centroid_model == settings.briefing_category_embedding_model


def test_semantic_category_assignment_skips_clustering_when_news_lens_cap_reached(
    db_session: Session,
    test_user: User,
    news_item_factory,
    monkeypatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "briefing_semantic_category_assignment_enabled", True)
    monkeypatch.setattr(settings, "briefing_max_news_lenses", 2)
    monkeypatch.setattr(settings, "briefing_category_similarity", 0.95)
    monkeypatch.setattr(settings, "briefing_category_absorb_similarity", 0.45)
    assert test_user.id is not None
    user_id = test_user.id
    db_session.add_all(
        [
            BriefingLens(
                user_id=user_id,
                key="news-ai",
                tier="news",
                title="AI",
                deck="Artificial intelligence stories.",
                position=2,
                status="active",
            ),
            BriefingLens(
                user_id=user_id,
                key="news-markets",
                tier="news",
                title="Markets",
                deck="Markets and economy stories.",
                position=3,
                status="active",
            ),
        ]
    )
    items = [
        news_item_factory(
            raw_metadata={},
            visibility_scope="user",
            owner_user_id=user_id,
            article_title=title,
            summary_title=title,
        )
        for title in [
            "AI infrastructure demand grows",
            "Bond market volatility rises",
            "Mixed technology policy update",
        ]
    ]
    for item in items:
        db_session.add(
            BriefingPendingSource(
                user_id=user_id,
                source_kind="news",
                source_id=item.id,
            )
        )
    db_session.flush()

    def fake_encode(
        texts: list[str],
        *,
        model_spec: str,
        batch_size: int,
        timeout_seconds: int,
    ) -> np.ndarray:
        del model_spec, batch_size, timeout_seconds
        assert len(texts) == 5
        return np.asarray(
            [
                [1.0, 0.0],
                [0.0, 1.0],
                [0.5, 0.5],
                [1.0, 0.0],
                [0.0, 1.0],
            ],
            dtype=np.float32,
        )

    def fail_naming(_sources):  # noqa: ANN001
        raise AssertionError("capped news assignment should not name new lenses")

    def fail_clustering(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("capped news assignment should not cluster remaining sources")

    monkeypatch.setattr(
        "app.services.briefing.lenses.encode_texts_with_embedding_model",
        fake_encode,
    )
    monkeypatch.setattr(
        "app.services.briefing.lenses._cluster_sources_by_embedding",
        fail_clustering,
    )

    changed = assign_pending_lenses(
        db_session,
        user_id=user_id,
        naming_fn=fail_naming,
        settings=settings,
    )

    assert changed == 3
    assert {
        row.lens_key
        for row in db_session.query(BriefingPendingSource).filter(
            BriefingPendingSource.user_id == user_id
        )
    } == {"news-ai", "news-markets"}
    assert db_session.query(BriefingLens).filter(BriefingLens.user_id == user_id).count() == 2


def test_topic_slug_does_not_create_missing_news_lens(
    db_session: Session,
    test_user: User,
    news_item_factory,
    monkeypatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "briefing_semantic_category_assignment_enabled", False)
    monkeypatch.setattr(settings, "briefing_new_lens_min_items", 4)
    assert test_user.id is not None
    user_id = test_user.id
    item = news_item_factory(
        raw_metadata={"aggregator": {"topic": "ai", "topic_title": "AI"}},
        visibility_scope="user",
        owner_user_id=user_id,
        article_title="AI startup launches new tool",
        summary_title="AI startup launches new tool",
    )
    pending = BriefingPendingSource(
        user_id=user_id,
        source_kind="news",
        source_id=item.id,
    )
    db_session.add(pending)
    db_session.flush()

    changed = assign_pending_lenses(db_session, user_id=user_id, settings=settings)

    assert changed == 0
    assert pending.lens_key is None
    assert db_session.query(BriefingLens).filter(BriefingLens.user_id == user_id).count() == 0


def test_topic_slug_assigns_existing_active_news_lens(
    db_session: Session,
    test_user: User,
    news_item_factory,
    monkeypatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "briefing_semantic_category_assignment_enabled", False)
    assert test_user.id is not None
    user_id = test_user.id
    db_session.add(
        BriefingLens(
            user_id=user_id,
            key="news-ai",
            tier="news",
            title="AI",
            deck="Artificial intelligence stories.",
            position=2,
            status="active",
        )
    )
    item = news_item_factory(
        raw_metadata={"aggregator": {"topic": "ai", "topic_title": "AI"}},
        visibility_scope="user",
        owner_user_id=user_id,
        article_title="AI startup launches new tool",
        summary_title="AI startup launches new tool",
    )
    pending = BriefingPendingSource(
        user_id=user_id,
        source_kind="news",
        source_id=item.id,
    )
    db_session.add(pending)
    db_session.flush()

    changed = assign_pending_lenses(db_session, user_id=user_id, settings=settings)

    assert changed == 1
    assert pending.lens_key == "news-ai"


def test_no_llm_capped_news_assignment_uses_existing_news_lenses(
    db_session: Session,
    test_user: User,
    news_item_factory,
    monkeypatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "briefing_semantic_category_assignment_enabled", False)
    monkeypatch.setattr(settings, "briefing_centroid_assignment_enabled", False)
    monkeypatch.setattr(settings, "briefing_new_lens_min_items", 2)
    monkeypatch.setattr(settings, "briefing_max_news_lenses", 2)
    assert test_user.id is not None
    user_id = test_user.id
    db_session.add_all(
        [
            BriefingLens(
                user_id=user_id,
                key="news-ai",
                tier="news",
                title="AI",
                deck="Artificial intelligence stories.",
                position=2,
                status="active",
            ),
            BriefingLens(
                user_id=user_id,
                key="news-markets",
                tier="news",
                title="Markets",
                deck="Markets and economy stories.",
                position=3,
                status="active",
            ),
        ]
    )
    items = [
        news_item_factory(
            raw_metadata={},
            visibility_scope="user",
            owner_user_id=user_id,
            article_title=f"Capped story {index}",
            summary_title=f"Capped story {index}",
        )
        for index in range(5)
    ]
    for item in items:
        db_session.add(
            BriefingPendingSource(
                user_id=user_id,
                source_kind="news",
                source_id=item.id,
            )
        )
    db_session.flush()

    def fail_encode(_texts):  # noqa: ANN001
        raise AssertionError("no-llm capped assignment should not embed")

    monkeypatch.setattr("app.services.briefing.lenses.encode_news_texts", fail_encode)

    changed = assign_pending_lenses(db_session, user_id=user_id, settings=settings)

    assert changed == 5
    assigned = [
        row.lens_key
        for row in db_session.query(BriefingPendingSource)
        .filter(BriefingPendingSource.user_id == user_id)
        .order_by(BriefingPendingSource.id.asc())
        .all()
    ]
    assert assigned == [
        "news-ai",
        "news-markets",
        "news-ai",
        "news-markets",
        "news-ai",
    ]
    assert db_session.query(BriefingLens).filter(BriefingLens.user_id == user_id).count() == 2


def test_semantic_category_assignment_raises_when_lens_naming_fails(
    db_session: Session,
    test_user: User,
    news_item_factory,
    monkeypatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "briefing_semantic_category_assignment_enabled", True)
    monkeypatch.setattr(settings, "briefing_new_lens_min_items", 2)
    monkeypatch.setattr(settings, "briefing_category_cluster_similarity", 0.9)
    assert test_user.id is not None
    user_id = test_user.id
    titles = [
        "Soatok's cryptography writeup",
        "Soatok's protocol analysis",
    ]
    items = [
        news_item_factory(
            raw_metadata={},
            visibility_scope="user",
            owner_user_id=user_id,
            article_title=title,
            summary_title=title,
        )
        for title in titles
    ]
    for item in items:
        db_session.add(
            BriefingPendingSource(
                user_id=user_id,
                source_kind="news",
                source_id=item.id,
            )
        )
    db_session.flush()

    def fake_encode(
        texts: list[str],
        *,
        model_spec: str,
        batch_size: int,
        timeout_seconds: int,
    ) -> np.ndarray:
        del model_spec, batch_size, timeout_seconds
        assert len(texts) == 2
        return np.asarray([[1.0, 0.0], [1.0, 0.0]], dtype=np.float32)

    def fail_naming(_sources):  # noqa: ANN001
        raise RuntimeError("structured lens naming failed")

    monkeypatch.setattr(
        "app.services.briefing.lenses.encode_texts_with_embedding_model",
        fake_encode,
    )

    with pytest.raises(RuntimeError, match="structured lens naming failed"):
        assign_pending_lenses(
            db_session,
            user_id=user_id,
            naming_fn=fail_naming,
            settings=settings,
        )

    assert (
        db_session.query(BriefingPendingSource)
        .filter(BriefingPendingSource.user_id == user_id)
        .filter(BriefingPendingSource.lens_key.is_(None))
        .count()
        == 2
    )
    assert db_session.query(BriefingLens).filter(BriefingLens.user_id == user_id).count() == 0


def test_semantic_category_assignment_falls_back_when_embedding_fails(
    db_session: Session,
    test_user: User,
    news_item_factory,
    monkeypatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "briefing_semantic_category_assignment_enabled", True)
    monkeypatch.setattr(settings, "briefing_new_lens_min_items", 2)
    assert test_user.id is not None
    user_id = test_user.id
    items = [
        news_item_factory(
            raw_metadata={},
            visibility_scope="user",
            owner_user_id=user_id,
            article_title=f"Fallback story {index}",
            summary_title=f"Fallback story {index}",
        )
        for index in range(2)
    ]
    for item in items:
        db_session.add(
            BriefingPendingSource(
                user_id=user_id,
                source_kind="news",
                source_id=item.id,
            )
        )
    db_session.flush()

    def fail_encode(
        texts: list[str],
        *,
        model_spec: str,
        batch_size: int,
        timeout_seconds: int,
    ) -> np.ndarray:
        del texts, model_spec, batch_size, timeout_seconds
        raise TypeError("'NoneType' object is not iterable")

    def naming_fn(_sources):  # noqa: ANN001
        return LensName(
            key="news-fallback",
            title="Fallback",
            deck="Fallback assignment when embeddings are unavailable.",
        )

    monkeypatch.setattr(
        "app.services.briefing.lenses.encode_texts_with_embedding_model",
        fail_encode,
    )

    changed = assign_pending_lenses(
        db_session,
        user_id=user_id,
        naming_fn=naming_fn,
        settings=settings,
    )

    assert changed == 2
    assert {
        row.lens_key
        for row in db_session.query(BriefingPendingSource).filter(
            BriefingPendingSource.user_id == user_id
        )
    } == {"news-fallback"}


def test_semantic_category_assignment_makes_duplicate_named_lens_keys_unique(
    db_session: Session,
    test_user: User,
    news_item_factory,
    monkeypatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "briefing_semantic_category_assignment_enabled", True)
    monkeypatch.setattr(settings, "briefing_new_lens_min_items", 2)
    monkeypatch.setattr(settings, "briefing_category_cluster_similarity", 0.9)
    assert test_user.id is not None
    user_id = test_user.id
    titles = [
        "Nvidia GPU demand keeps rising",
        "AI chip startups raise new funds",
        "Treasury yields move lower",
        "Bond funds brace for Fed cuts",
    ]
    items = [
        news_item_factory(
            raw_metadata={},
            visibility_scope="user",
            owner_user_id=user_id,
            article_title=title,
            summary_title=title,
        )
        for title in titles
    ]
    for item in items:
        db_session.add(
            BriefingPendingSource(
                user_id=user_id,
                source_kind="news",
                source_id=item.id,
            )
        )
    db_session.flush()

    def fake_encode(
        texts: list[str],
        *,
        model_spec: str,
        batch_size: int,
        timeout_seconds: int,
    ) -> np.ndarray:
        del model_spec, batch_size, timeout_seconds
        vectors = []
        for text in texts:
            if "Nvidia" in text or "chip" in text:
                vectors.append([1.0, 0.0])
            elif "Treasury" in text or "Bond" in text:
                vectors.append([0.0, 1.0])
            else:
                vectors.append([0.0, 0.0])
        return np.asarray(vectors, dtype=np.float32)

    def duplicate_name(_sources):  # noqa: ANN001
        return LensName(
            key="news-ai",
            title="AI",
            deck="Artificial intelligence and markets.",
        )

    monkeypatch.setattr(
        "app.services.briefing.lenses.encode_texts_with_embedding_model",
        fake_encode,
    )

    changed = assign_pending_lenses(
        db_session,
        user_id=user_id,
        naming_fn=duplicate_name,
        settings=settings,
    )

    assert changed == 4
    lens_keys = {
        lens.key for lens in db_session.query(BriefingLens).filter(BriefingLens.user_id == user_id)
    }
    assert lens_keys == {"news-ai", "news-ai-2"}


def test_semantic_category_assignment_packs_unrelated_small_clusters(
    db_session: Session,
    test_user: User,
    news_item_factory,
    monkeypatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "briefing_semantic_category_assignment_enabled", True)
    monkeypatch.setattr(settings, "briefing_new_lens_min_items", 4)
    monkeypatch.setattr(settings, "briefing_news_window_max", 4)
    monkeypatch.setattr(settings, "briefing_category_cluster_similarity", 0.99)
    assert test_user.id is not None
    user_id = test_user.id
    items = [
        news_item_factory(
            raw_metadata={},
            visibility_scope="user",
            owner_user_id=user_id,
            article_title=f"Unrelated story {index}",
            summary_title=f"Unrelated story {index}",
        )
        for index in range(8)
    ]
    for item in items:
        db_session.add(
            BriefingPendingSource(
                user_id=user_id,
                source_kind="news",
                source_id=item.id,
            )
        )
    db_session.flush()

    def fake_encode(
        texts: list[str],
        *,
        model_spec: str,
        batch_size: int,
        timeout_seconds: int,
    ) -> np.ndarray:
        del model_spec, batch_size, timeout_seconds
        assert len(texts) == 8
        return np.eye(8, dtype=np.float32)

    named_groups: list[list[str]] = []

    def naming_fn(sources):  # noqa: ANN001
        named_groups.append([source.title for source in sources])
        group_number = len(named_groups)
        return LensName(
            key=f"news-group-{group_number}",
            title=f"Group {group_number}",
            deck=f"Grouped singleton stories {group_number}.",
        )

    monkeypatch.setattr(
        "app.services.briefing.lenses.encode_texts_with_embedding_model",
        fake_encode,
    )

    changed = assign_pending_lenses(
        db_session,
        user_id=user_id,
        naming_fn=naming_fn,
        settings=settings,
    )

    assert changed == 8
    assert [len(group) for group in named_groups] == [4, 4]
    lens_keys = {
        lens.key for lens in db_session.query(BriefingLens).filter(BriefingLens.user_id == user_id)
    }
    assert lens_keys == {"news-group-1", "news-group-2"}
