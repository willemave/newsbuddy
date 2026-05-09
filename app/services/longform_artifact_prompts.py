"""Prompt builders for single-pass long-form artifact generation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.models.longform_artifacts import ArtifactType
from app.services.longform_artifact_routing import ArtifactSourceHint

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

EXTRAS_SCHEMA_HINTS: dict[str, str] = {
    "argument": '{"thesis": "...", "counterpoint": "..."}',
    "mental_model": '{"what_it_explains": "...", "when_to_use_it": "..."}',
    "playbook": '{"situation": "...", "outcome": "..."}',
    "portrait": '{"background": "...", "current_focus": "..."}',
    "briefing": (
        '{"timeline": [{"when": "...", "what": "..."}], '
        '"key_actors": [{"name": "...", "stake": "..."}], "what_to_watch": "..."}'
    ),
    "walkthrough": '{"what_youll_make": "...", "prereqs": ["..."], "time_or_cost": "..."}',
    "findings": '{"question": "...", "method": "...", "limits": "..."}',
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

    system_prompt = f"""You are Newsly's long-form artifact generator.

Your task is to produce one typed artifact from the source content. Do not write a generic summary.

First choose exactly one artifact type from the candidate list, then generate the matching artifact
in the same JSON response. The choice is part of selection_trace; do not call for a separate
classifier.

Candidate artifact types:
{candidate_guidance}

Every payload must use this five-block shape:
- overview: 2-4 sentence narrative lede with who/what/when.
- quotes: 2-5 direct supporting quotes from the source, each with attribution when available.
- extras: type-specific source facts, not commentary.
- key_points: 4-8 items, each with heading and 1-2 sentences of real content.
- takeaway: one sentence stating what the reader should leave with.

Allowed extras shapes for the candidate types:
{extras_guidance}

Return ONLY valid JSON with exactly these top-level fields:
{{
  "title": "clear title",
  "one_line": "single sentence for feed previews: what this is and why now",
  "ask": "judge|learn|copy|absorb|track|try|update",
  "artifact": {{
    "type": one of [{candidates_json}],
    "payload": {{
      "overview": "...",
      "quotes": [{{"text": "...", "attribution": "..."}}],
      "extras": {{ }},
      "key_points": [{{"heading": "...", "content": "..."}}],
      "takeaway": "..."
    }}
  }},
  "generated_at": "ISO 8601 timestamp",
  "source_context": {{
    "url": "...",
    "source_name": "...",
    "publication_date": "...",
    "platform": "..."
  }},
  "selection_trace": {{
    "source_hint": "{source_hint.source_hint}",
    "candidates": [{candidates_json}],
    "selected": "same as artifact.type",
    "reason": "why this shape is most useful",
    "confidence": 0.0
  }},
  "feed_preview": {{
    "title": "feed title",
    "one_line": "feed one-line",
    "preview_bullets": ["bullet 1", "bullet 2", "bullet 3"],
    "reason_to_read": "why this is worth opening",
    "artifact_type": "same as artifact.type"
  }}
}}

Rules:
- The selected artifact type must be one of the candidates.
- The ask must match the artifact type: argument=judge, mental_model=learn, playbook=copy,
  portrait=absorb, briefing=track, walkthrough=try, findings=update.
- Never include envelope-level summary, key_points, source_details, or classification.
- Preserve names, numbers, dates, and technical terms exactly.
- If the source is thin, still create the best fitting artifact and note uncertainty in content,
  not in extra fields.
- Do not invent quotes. If attribution is unavailable, use null.
- No markdown outside JSON."""

    user_message = f"""Source metadata:
{metadata_context}

Source content:

{content_payload}"""

    return system_prompt, user_message


def build_longform_artifact_repair_prompt(
    *,
    invalid_payload: str,
    validation_error: str,
    candidates: Sequence[ArtifactType],
) -> tuple[str, str]:
    """Build a targeted repair prompt for invalid artifact JSON."""
    candidate_list = ", ".join(candidates)
    system_prompt = f"""Repair a Newsly long-form artifact JSON payload.

Return ONLY corrected JSON. Do not add commentary.

Constraints:
- artifact.type must be one of: {candidate_list}
- selection_trace.selected and feed_preview.artifact_type must match artifact.type
- ask must match artifact.type
- Do not add envelope-level summary, key_points, source_details, or classification
- Preserve the original meaning; only repair schema and missing required fields."""
    user_message = f"""Validation error:
{validation_error}

Invalid JSON:
{invalid_payload}"""
    return system_prompt, user_message
