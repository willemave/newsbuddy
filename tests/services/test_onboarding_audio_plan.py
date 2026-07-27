from app.services.onboarding.internal_models import _AudioLane, _AudioPlanOutput
from app.services.onboarding.llm_plans import _normalize_audio_lane_plan


def test_audio_lane_plan_preserves_reddit_lane_when_full() -> None:
    plan = _AudioPlanOutput(
        topic_summary="AI news",
        inferred_topics=["AI"],
        lanes=[
            _AudioLane(
                name=f"Lane {idx}",
                goal="Goal",
                target="feeds" if idx % 2 == 0 else "podcasts",
                queries=["query one", "query two"],
            )
            for idx in range(5)
        ],
    )

    normalized = _normalize_audio_lane_plan(plan, "AI news transcript")

    assert len(normalized.lanes) == 5
    assert any(lane.target == "reddit" for lane in normalized.lanes)


def test_audio_lane_plan_refines_queries_to_sentence_style() -> None:
    plan = _AudioPlanOutput(
        topic_summary="biology psychology business books",
        inferred_topics=["biology", "psychology", "business books"],
        lanes=[
            _AudioLane(
                name="Biology",
                goal="Find biology podcasts",
                target="podcasts",
                queries=["biology podcasts", "genetics evolution show"],
            ),
            _AudioLane(
                name="Psychology",
                goal="Find psychology feeds",
                target="feeds",
                queries=["psychology feed", "behavior science newsletter"],
            ),
            _AudioLane(
                name="Communities",
                goal="Find reddit communities",
                target="reddit",
                queries=["psychology subreddit", "business books reddit"],
            ),
        ],
    )

    normalized = _normalize_audio_lane_plan(plan, "biology psychology business books transcript")

    for lane in normalized.lanes:
        assert len(lane.queries) >= 2
        for query in lane.queries:
            word_count = len(query.split())
            assert 5 <= word_count <= 10


def test_audio_lane_plan_fallback_queries_keep_reddit_focus() -> None:
    plan = _AudioPlanOutput(
        topic_summary="biology psychology business books",
        inferred_topics=["biology", "psychology", "business books"],
        lanes=[
            _AudioLane(
                name="Too short",
                goal="bad lane",
                target="feeds",
                queries=["biology"],
            )
        ],
    )

    normalized = _normalize_audio_lane_plan(plan, "biology psychology business books transcript")

    reddit_lanes = [lane for lane in normalized.lanes if lane.target == "reddit"]
    assert reddit_lanes
    assert any(
        "reddit" in query.lower() or "subreddit" in query.lower()
        for query in reddit_lanes[0].queries
    )
