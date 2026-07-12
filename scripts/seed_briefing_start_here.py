"""Seed deterministic Start Here states for simulator and manual UI testing."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.db import get_db  # noqa: E402
from app.models.db import (  # noqa: E402
    BriefingLens,
    BriefingSegment,
    BriefingState,
    OnboardingFirstEditionRun,
    OnboardingFirstEditionSource,
    User,
)

STATES = (
    "initial",
    "early",
    "mid",
    "partial_failure",
    "delayed",
    "ready",
    "resumed",
    "completed",
)
SOURCE_NAMES = ("Techmeme", "Stratechery", "Decoder", "Hacker News")
SOURCE_ITEM_COUNTS = (28, 12, 9, 34)
LENS_KEY = "start-here-technology"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--state", choices=STATES, required=True)
    args = parser.parse_args()

    with get_db() as db:
        user = db.query(User).filter(User.id == args.user_id).first()
        if user is None:
            raise SystemExit(f"User {args.user_id} was not found")
        result = seed_state(db, user=user, state=args.state)
    print(json.dumps(result, sort_keys=True))


def seed_state(db, *, user: User, state: str) -> dict[str, object]:
    """Reset and seed one stable first-run state for a local user."""

    now = datetime.now(UTC).replace(tzinfo=None)
    user.reading_experience = "briefing"
    user.has_completed_onboarding = True
    user.has_completed_new_user_tutorial = state == "completed"

    run_ids = [
        int(run_id)
        for (run_id,) in db.query(OnboardingFirstEditionRun.id)
        .filter(OnboardingFirstEditionRun.user_id == user.id)
        .all()
    ]
    if run_ids:
        db.query(OnboardingFirstEditionSource).filter(
            OnboardingFirstEditionSource.run_id.in_(run_ids)
        ).delete(synchronize_session=False)
        db.query(OnboardingFirstEditionRun).filter(
            OnboardingFirstEditionRun.id.in_(run_ids)
        ).delete(synchronize_session=False)

    lens = (
        db.query(BriefingLens)
        .filter(BriefingLens.user_id == user.id, BriefingLens.key == LENS_KEY)
        .first()
    )
    if lens is not None:
        db.query(BriefingSegment).filter(BriefingSegment.lens_id == lens.id).delete()
        db.delete(lens)
        db.flush()

    briefing_state = db.query(BriefingState).filter(BriefingState.user_id == user.id).first()
    if briefing_state is None:
        briefing_state = BriefingState(
            user_id=user.id,
            version=1,
            masthead_title="Briefing",
            masthead_deck="Your first edition is taking shape.",
        )
        db.add(briefing_state)
    else:
        briefing_state.version = int(briefing_state.version or 0) + 1

    if state == "completed":
        db.flush()
        return {"user_id": user.id, "state": state, "run_id": None}

    completed_count = 0
    if state == "early":
        completed_count = 1
    elif state in {"mid", "resumed"}:
        completed_count = 2
    elif state == "partial_failure":
        completed_count = 3
    if state in {"delayed", "ready"}:
        completed_count = len(SOURCE_NAMES)
    ready_keys = [LENS_KEY] if state == "ready" else []
    run = OnboardingFirstEditionRun(
        user_id=user.id,
        status="active",
        revision=completed_count + 1,
    )
    db.add(run)
    db.flush()
    for position, display_name in enumerate(SOURCE_NAMES):
        is_complete = position < completed_count
        is_unavailable = state == "partial_failure" and position == 2
        source_status = "queued"
        if is_complete:
            source_status = "unavailable" if is_unavailable else "processed"
        item_count = SOURCE_ITEM_COUNTS[position] if is_complete and not is_unavailable else 0
        db.add(
            OnboardingFirstEditionSource(
                run_id=run.id,
                source_key=f"fixture:{position}",
                display_name=display_name,
                source_kind="fixture",
                position=position,
                status=source_status,
                processed_item_count=item_count,
                completed_at=now if is_complete else None,
            )
        )

    if state == "ready":
        lens = BriefingLens(
            user_id=user.id,
            key=LENS_KEY,
            tier="news",
            title="Technology",
            deck="The first stories ready from your sources.",
            position=20,
            status="active",
        )
        db.add(lens)
        db.flush()
        db.add(
            BriefingSegment(
                lens_id=lens.id,
                user_id=user.id,
                blocks=[
                    {
                        "type": "passage",
                        "weight": "lead",
                        "paragraphs": [
                            {
                                "runs": [
                                    {
                                        "kind": "text",
                                        "text": (
                                            "Your first technology briefing is ready. "
                                            "Future stories will continue to append here."
                                        ),
                                    }
                                ]
                            }
                        ],
                    }
                ],
                markdown_raw="Your first technology briefing is ready.",
                narration_text="Your first technology briefing is ready.",
                source_keys=[],
                status="active",
                model="fixture",
                prompt_version="fixture-v1",
                created_at=now,
            )
        )

    db.flush()
    return {
        "user_id": user.id,
        "state": state,
        "run_id": run.id,
        "completed_sources": completed_count,
        "ready_category_keys": ready_keys,
    }


if __name__ == "__main__":
    main()
