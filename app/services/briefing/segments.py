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
    extra_warnings: Iterable[str] = (),
) -> BriefingSegment:
    return BriefingSegment(
        lens_id=lens_id,
        user_id=user_id,
        blocks=segment.blocks,
        markdown_raw=segment.markdown_raw,
        narration_text=segment.narration_text,
        source_keys=list(source_keys),
        status=segment.status,
        model=segment.model[:64],
        prompt_version=segment.prompt_version,
        input_tokens=segment.input_tokens,
        output_tokens=segment.output_tokens,
        generation_ms=segment.generation_ms,
        warnings=[*segment.warnings, *extra_warnings],
    )
