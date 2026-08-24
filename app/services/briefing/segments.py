from __future__ import annotations

from collections.abc import Iterable, Sequence

from app.models.db import BriefingSegment
from app.services.briefing.composer import ComposedSegment


def build_briefing_segment(
    *,
    lens_id: int,
    user_id: int,
    segment: ComposedSegment,
    source_keys: Sequence[str],
    event_groups: Sequence[Sequence[str]],
    extra_warnings: Iterable[str] = (),
) -> BriefingSegment:
    return BriefingSegment(
        lens_id=lens_id,
        user_id=user_id,
        blocks=segment.blocks,
        markdown_raw=segment.markdown_raw,
        narration_text=segment.narration_text,
        source_keys=list(source_keys),
        event_groups=[list(group) for group in event_groups],
        status=segment.status,
        model=segment.model[:64],
        prompt_version=segment.prompt_version,
        input_tokens=segment.input_tokens,
        output_tokens=segment.output_tokens,
        generation_ms=segment.generation_ms,
        warnings=[*segment.warnings, *extra_warnings],
    )


def segment_event_groups(segment: BriefingSegment) -> list[list[str]]:
    """Return the segment's source keys grouped by event.

    Segments composed before event grouping existed carry no groups; each of
    their sources counts as its own event.
    """
    source_keys = [str(key) for key in (segment.source_keys or [])]
    raw_groups = segment.event_groups
    if not isinstance(raw_groups, list) or not raw_groups:
        return [[key] for key in source_keys]
    groups = [[str(key) for key in group] for group in raw_groups if isinstance(group, list)]
    covered = {key for group in groups for key in group}
    groups.extend([key] for key in source_keys if key not in covered)
    return [group for group in groups if group]
