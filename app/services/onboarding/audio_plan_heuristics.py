"""Audio lane normalization and deterministic fallback planning."""

from __future__ import annotations

from app.services.onboarding.internal_models import _AudioLane, _AudioPlanOutput
from app.services.onboarding.query_heuristics import _merge_topics, _refine_lane_queries


def _normalize_audio_lane_plan_with_metadata(
    plan: _AudioPlanOutput, transcript: str
) -> tuple[_AudioPlanOutput, bool]:
    topic_summary = (plan.topic_summary or "").strip()
    if not topic_summary:
        topic_summary = _fallback_topic_summary(transcript)
        used_fallback = True
    else:
        used_fallback = False

    inferred_topics = _merge_topics(plan.inferred_topics, max_topics=6)
    lanes: list[_AudioLane] = []
    seen_names: set[str] = set()
    has_reddit = False

    for lane in plan.lanes:
        name = (lane.name or "").strip()
        if not name:
            continue
        normalized_name = name.lower()
        if normalized_name in seen_names:
            continue
        seen_names.add(normalized_name)

        goal = (lane.goal or "").strip()
        queries = _refine_lane_queries(
            target=lane.target,
            queries=lane.queries,
            lane_goal=goal,
            inferred_topics=inferred_topics,
            topic_summary=topic_summary,
        )
        if len(queries) < 2:
            continue

        target = lane.target
        if target == "reddit":
            has_reddit = True

        lanes.append(
            _AudioLane(
                name=name,
                goal=goal,
                target=target,
                queries=queries[:4],
            )
        )
        if len(lanes) >= 5:
            break

    if not lanes:
        return _fallback_audio_lane_plan(transcript), True

    if not has_reddit:
        reddit_lane = _fallback_reddit_lane(transcript, inferred_topics, topic_summary)
        if lanes:
            existing_names = {lane.name.lower() for lane in lanes if lane.name}
            if reddit_lane.name.lower() in existing_names:
                reddit_lane = _AudioLane(
                    name=f"{reddit_lane.name} Suggestions",
                    goal=reddit_lane.goal,
                    target=reddit_lane.target,
                    queries=reddit_lane.queries,
                )
        if len(lanes) >= 5:
            lanes[-1] = reddit_lane
        else:
            lanes.append(reddit_lane)
        used_fallback = True

    if len(lanes) < 3:
        lanes.extend(_fallback_core_lanes(transcript, inferred_topics, existing=lanes))
        used_fallback = True

    return (
        _AudioPlanOutput(
            topic_summary=topic_summary,
            inferred_topics=inferred_topics,
            lanes=lanes[:5],
        ),
        used_fallback,
    )


def _fallback_audio_lane_plan(transcript: str) -> _AudioPlanOutput:
    inferred_topics = _merge_topics([_fallback_topic_summary(transcript)], max_topics=3)
    lanes = _fallback_core_lanes(transcript, inferred_topics, existing=[])
    return _AudioPlanOutput(
        topic_summary=_fallback_topic_summary(transcript),
        inferred_topics=inferred_topics,
        lanes=lanes,
    )


def _fallback_core_lanes(
    transcript: str,
    inferred_topics: list[str],
    *,
    existing: list[_AudioLane],
) -> list[_AudioLane]:
    seed = _seed_phrase(transcript, inferred_topics)
    topic_summary = _fallback_topic_summary(transcript)
    lanes = list(existing)
    if len(lanes) < 3:
        goal = "Find newsletters and RSS feeds aligned with the user's interests."
        lanes.append(
            _AudioLane(
                name="Newsletters & Feeds",
                goal=goal,
                target="feeds",
                queries=_refine_lane_queries(
                    target="feeds",
                    queries=[
                        f"{seed} newsletter",
                        f"{seed} RSS feed",
                        f"best {seed} Substack",
                    ],
                    lane_goal=goal,
                    inferred_topics=inferred_topics,
                    topic_summary=topic_summary,
                ),
            )
        )
    if len(lanes) < 3:
        goal = "Find podcast feeds covering the user's interests."
        lanes.append(
            _AudioLane(
                name="Podcasts",
                goal=goal,
                target="podcasts",
                queries=_refine_lane_queries(
                    target="podcasts",
                    queries=[
                        f"{seed} podcast",
                        f"{seed} podcast RSS",
                        f"best {seed} podcasts",
                    ],
                    lane_goal=goal,
                    inferred_topics=inferred_topics,
                    topic_summary=topic_summary,
                ),
            )
        )
    if not any(lane.target == "reddit" for lane in lanes):
        lanes.append(_fallback_reddit_lane(transcript, inferred_topics, topic_summary))
    return lanes


def _fallback_reddit_lane(
    transcript: str, inferred_topics: list[str], topic_summary: str | None = None
) -> _AudioLane:
    seed = _seed_phrase(transcript, inferred_topics)
    goal = "Find active subreddits for the user's interests."
    return _AudioLane(
        name="Reddit",
        goal=goal,
        target="reddit",
        queries=_refine_lane_queries(
            target="reddit",
            queries=[
                f"{seed} subreddit",
                f"best subreddits for {seed}",
                f"{seed} reddit community",
            ],
            lane_goal=goal,
            inferred_topics=inferred_topics,
            topic_summary=topic_summary or _fallback_topic_summary(transcript),
        ),
    )


def _fallback_topic_summary(transcript: str) -> str:
    cleaned = transcript.strip().strip(".")
    if not cleaned:
        return "general news interests"
    words = cleaned.split()
    return " ".join(words[:10])


def _seed_phrase(transcript: str, inferred_topics: list[str]) -> str:
    if inferred_topics:
        return inferred_topics[0]
    summary = _fallback_topic_summary(transcript)
    return summary or "technology news"
