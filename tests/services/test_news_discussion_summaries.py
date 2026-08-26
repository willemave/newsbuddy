from __future__ import annotations

from datetime import datetime, timedelta

from app.models.db import NewsItemDiscussion
from app.services.news_discussion_summaries import (
    DiscussionSummaryInput,
    DiscussionSummaryPlan,
    DiscussionSummaryPlanMode,
    SummaryPromptComment,
    plan_discussion_summary,
)


def _summary_input(*, changed_comment_count: int) -> DiscussionSummaryInput:
    comments = [
        SummaryPromptComment(
            comment_id=f"new-{index}",
            author="reader",
            depth=0,
            text=f"Changed comment {index}",
            fingerprint=f"fingerprint-{index}",
        )
        for index in range(changed_comment_count)
    ]
    return DiscussionSummaryInput(
        prompt="new prompt",
        input_sha256="new-input",
        comment_count=len(comments),
        comment_fingerprints={comment.comment_id: comment.fingerprint for comment in comments},
        comments=comments,
        links=[],
    )


def _completed_row(
    *,
    generated_at: datetime,
    seen_input_sha256: str | None = None,
) -> NewsItemDiscussion:
    return NewsItemDiscussion(
        news_item_id=1,
        platform="hackernews",
        summary={"overview": "Existing summary"},
        summary_status="completed",
        summary_input_sha256="old-input",
        summary_comment_fingerprints={"old-comment": "old-fingerprint"},
        summary_seen_input_sha256=seen_input_sha256,
        summary_incremental_update_count=0,
        summary_generated_at=generated_at,
    )


def _plan(
    *,
    now: datetime,
    generated_at: datetime,
    changed_comment_count: int,
    seen_input_sha256: str | None = None,
) -> DiscussionSummaryPlan:
    return plan_discussion_summary(
        row=_completed_row(
            generated_at=generated_at,
            seen_input_sha256=seen_input_sha256,
        ),
        summary_input=_summary_input(changed_comment_count=changed_comment_count),
        previous_raw_sha="old-raw",
        current_raw_sha="new-raw",
        now=now,
    )


def test_plan_discussion_summary_coalesces_material_changes_during_cooldown() -> None:
    now = datetime(2026, 8, 25, 12, 0)

    plan = _plan(
        now=now,
        generated_at=now - timedelta(hours=5),
        changed_comment_count=26,
    )

    assert plan.mode == DiscussionSummaryPlanMode.TRACK_SEEN
    assert len(plan.changed_comments) == 26


def test_plan_discussion_summary_requires_more_than_twenty_five_changes() -> None:
    now = datetime(2026, 8, 25, 12, 0)

    plan = _plan(
        now=now,
        generated_at=now - timedelta(hours=7),
        changed_comment_count=25,
    )

    assert plan.mode == DiscussionSummaryPlanMode.TRACK_SEEN


def test_plan_discussion_summary_refreshes_stale_changed_summary() -> None:
    now = datetime(2026, 8, 25, 12, 0)

    plan = _plan(
        now=now,
        generated_at=now - timedelta(hours=25),
        changed_comment_count=1,
        seen_input_sha256="new-input",
    )

    assert plan.mode == DiscussionSummaryPlanMode.MERGE
