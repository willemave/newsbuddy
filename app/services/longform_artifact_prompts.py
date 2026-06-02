"""Prompt builders for single-pass long-form artifact generation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.services.longform_artifact_routing import ArtifactSourceHint
from app.services.prompt_library import render_prompt

ARTIFACT_TYPE_GUIDANCE: dict[str, str] = {
    "argument": (
        "Use for essays, op-eds, manifestos, and analysis where the source is making a "
        "claim. key_points are the reasons in the author's order. extras has thesis and "
        "counterpoint."
    ),
    "mental_model": (
        "Use for explainers, frameworks, and conceptual deep-dives. key_points are the "
        "parts or stages of the model. extras has what_it_explains and when_to_use_it."
    ),
    "playbook": (
        "Use for tactical operator stories and practitioner interviews. key_points are "
        "the phases of the work in chronological order. extras has situation and outcome."
    ),
    "portrait": (
        "Use for profiles and person-centered interviews. key_points are themes in the "
        "person's worldview. extras has background and current_focus."
    ),
    "briefing": (
        "Use for news events, announcements, and regulatory updates. key_points "
        "are major beats of what happened. extras has timeline, key_actors, and what_to_watch."
    ),
    "walkthrough": (
        "Use for tutorials, recipes, READMEs, and build guides. key_points are steps in "
        "execution order. extras has what_youll_make, prereqs, and time_or_cost."
    ),
    "findings": (
        "Use for research papers, benchmark posts, data analysis, and reports. key_points "
        "are findings in order of significance. extras has question, method, and limits."
    ),
}

SHARED_EXTRAS_SCHEMA_HINT = (
    '"evidence": ["..."], "mental_model": ["..."], '
    '"counter_arguments": ["..."], "supporting_arguments": ["..."]'
)


def _extras_schema_hint(*fields: str) -> str:
    return "{" + ", ".join((*fields, SHARED_EXTRAS_SCHEMA_HINT)) + "}"


EXTRAS_SCHEMA_HINTS: dict[str, str] = {
    "argument": _extras_schema_hint('"thesis": "..."', '"counterpoint": "..."'),
    "mental_model": _extras_schema_hint(
        '"what_it_explains": "..."',
        '"when_to_use_it": "..."',
    ),
    "playbook": _extras_schema_hint('"situation": "..."', '"outcome": "..."'),
    "portrait": _extras_schema_hint('"background": "..."', '"current_focus": "..."'),
    "briefing": _extras_schema_hint(
        '"timeline": [{"when": "...", "what": "..."}]',
        '"key_actors": [{"name": "...", "stake": "..."}]',
        '"what_to_watch": "..."',
    ),
    "walkthrough": _extras_schema_hint(
        '"what_youll_make": "..."',
        '"prereqs": ["..."]',
        '"time_or_cost": "..."',
    ),
    "findings": _extras_schema_hint(
        '"question": "..."',
        '"method": "..."',
        '"limits": "..."',
    ),
}


def _source_line(label: str, value: Any) -> str:
    if value is None:
        return f"{label}: unknown"
    text = str(value).strip()
    return f"{label}: {text or 'unknown'}"


def build_longform_artifact_prompt(
    *,
    source_hint: ArtifactSourceHint,
    content_payload: str,
    title: str | None,
    url: str | None,
    source_name: str | None,
    platform: str | None,
    publication_date: str | None,
    metadata: Mapping[str, Any] | None = None,
) -> tuple[str, str]:
    """Build the single-pass artifact generation prompt."""
    candidate_guidance = "\n".join(
        f"- {candidate}: {ARTIFACT_TYPE_GUIDANCE[candidate]}"
        for candidate in source_hint.candidates
    )
    extras_guidance = "\n".join(
        f"- {candidate}: {EXTRAS_SCHEMA_HINTS[candidate]}" for candidate in source_hint.candidates
    )
    candidates_json = ", ".join(f'"{candidate}"' for candidate in source_hint.candidates)
    metadata_map = metadata or {}
    metadata_context = "\n".join(
        _source_line(label, value)
        for label, value in (
            ("Title", title),
            ("URL", url),
            ("Source", source_name or metadata_map.get("source")),
            ("Platform", platform or metadata_map.get("platform")),
            ("Publication date", publication_date or metadata_map.get("publication_date")),
            ("Source hint", source_hint.source_hint),
            ("Candidates", f"[{candidates_json}]"),
        )
    )

    system_prompt = render_prompt(
        "summarization/longform_artifact#system",
        candidate_guidance=candidate_guidance,
        extras_guidance=extras_guidance,
        candidates_json=candidates_json,
        source_hint=source_hint.source_hint,
    )
    user_message = render_prompt(
        "summarization/longform_artifact#user",
        metadata_context=metadata_context,
        content_payload=content_payload,
    )

    return system_prompt, user_message
