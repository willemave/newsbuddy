from __future__ import annotations

from admin.remote_ops import RemoteContext, briefing_status
from app.core.settings import get_settings
from app.models.db import (
    BriefingLens,
    BriefingPendingSource,
    BriefingSegment,
    BriefingState,
)
from app.testing.postgres_harness import create_temporary_postgres_harness


def test_briefing_status_surfaces_over_cap_and_source_reference_health(
    tmp_path,
    monkeypatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "briefing_max_segments_per_lens", 2)
    harness = create_temporary_postgres_harness(
        schema_prefix="newsly_test",
        tables=[
            BriefingLens.__table__,
            BriefingSegment.__table__,
            BriefingPendingSource.__table__,
            BriefingState.__table__,
        ],
    )
    try:
        with harness.session_factory() as session:
            session.add(
                BriefingState(
                    user_id=7,
                    version=4,
                    masthead_title="Today",
                    masthead_deck="What matters",
                )
            )
            lens = BriefingLens(
                user_id=7,
                key="technology",
                tier="news",
                title="Technology",
                deck="Technology news",
                position=0,
            )
            session.add(lens)
            articles_lens = BriefingLens(
                user_id=7,
                key="articles",
                tier="longform",
                title="Articles",
                deck="Long reads",
                position=1,
            )
            session.add(articles_lens)
            session.flush()
            for index in range(3):
                session.add(
                    BriefingSegment(
                        lens_id=lens.id,
                        user_id=7,
                        blocks=[{"type": "passage", "text": f"segment {index}"}],
                        source_keys=[f"news:{index}", f"content:{index}"],
                        status="active",
                        model="test",
                        prompt_version="test",
                    )
                )
            session.add(
                BriefingSegment(
                    lens_id=articles_lens.id,
                    user_id=7,
                    blocks=[{"type": "passage", "text": "article segment"}],
                    source_keys=["content:10"],
                    status="degraded",
                    model="test",
                    prompt_version="test",
                )
            )
            session.add_all(
                [
                    BriefingPendingSource(
                        user_id=7,
                        lens_key="technology",
                        source_kind="news",
                        source_id=100,
                    ),
                    BriefingPendingSource(
                        user_id=7,
                        lens_key="articles",
                        source_kind="content",
                        source_id=101,
                    ),
                    BriefingPendingSource(
                        user_id=7,
                        lens_key="articles",
                        source_kind="content",
                        source_id=102,
                    ),
                ]
            )
            session.commit()

        result = briefing_status(
            RemoteContext(
                database_url=harness.database_url,
                logs_dir=tmp_path / "logs",
                service_log_dir=tmp_path / "service-logs",
            ),
            user_id=7,
        )

        assert result["health"]["configured_segment_cap"] == 2
        assert result["health"]["lenses_above_cap"] == ["technology"]
        assert result["health"]["max_active_segments"] == 3
        assert result["health"]["total_active_segments"] == 4
        assert result["health"]["total_source_references"] == 7
        assert result["health"]["stored_payload_bytes_estimate"] > 0
        assert result["lenses"][0]["above_segment_cap"] is True
        assert result["lenses"][0]["pending_sources"] == 1
        assert result["lenses"][1]["active_segments"] == 1
        assert result["lenses"][1]["pending_sources"] == 2
    finally:
        harness.close()
