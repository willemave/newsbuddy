"""Load Markdown-backed prompt templates."""

from __future__ import annotations

import re
from functools import cache
from importlib.resources import files
from string import Template

PROMPT_PACKAGE = "app.prompts"
SECTION_MARKER_PATTERN = "<!-- prompt-section: {section} -->"
SECTION_END_MARKER = "<!-- /prompt-section -->"


def _split_prompt_reference(prompt_ref: str) -> tuple[str, str | None]:
    prompt_name, separator, section_name = prompt_ref.partition("#")
    if not separator:
        return prompt_name, None
    section = section_name.strip().lower()
    if not section:
        raise ValueError(f"Prompt section cannot be empty: {prompt_ref}")
    return prompt_name, section


def _normalize_prompt_name(prompt_name: str) -> str:
    normalized = prompt_name.strip().removeprefix("/")
    if not normalized:
        raise ValueError("Prompt name cannot be empty")
    if ".." in normalized.split("/"):
        raise ValueError(f"Invalid prompt path: {prompt_name}")
    if not normalized.endswith(".md"):
        normalized = f"{normalized}.md"
    return normalized


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---\n"):
        return text.strip()
    end = text.find("\n---\n", 4)
    if end == -1:
        return text.strip()
    return text[end + len("\n---\n") :].strip()


def _heading_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _extract_prompt_section(text: str, section: str) -> str:
    marker = SECTION_MARKER_PATTERN.format(section=section)
    start = text.find(marker)
    if start != -1:
        start += len(marker)
        end = text.find(SECTION_END_MARKER, start)
        if end == -1:
            raise ValueError(f"Prompt section marker is not closed: {section}")
        return text[start:end].strip()

    lines = text.splitlines()
    section_start: int | None = None
    section_end = len(lines)
    for index, line in enumerate(lines):
        if not line.startswith("## "):
            continue
        if section_start is not None:
            section_end = index
            break
        if _heading_slug(line.removeprefix("## ")) == section:
            section_start = index + 1
    if section_start is None:
        raise ValueError(f"Prompt section not found: {section}")
    return "\n".join(lines[section_start:section_end]).strip()


@cache
def load_prompt(prompt_ref: str) -> str:
    """Load a Markdown prompt body, excluding frontmatter metadata."""
    prompt_name, section = _split_prompt_reference(prompt_ref)
    normalized = _normalize_prompt_name(prompt_name)
    resource = files(PROMPT_PACKAGE)
    for part in normalized.split("/"):
        resource = resource.joinpath(part)
    text = _strip_frontmatter(resource.read_text(encoding="utf-8"))
    if section:
        return _extract_prompt_section(text, section)
    return text


def render_prompt(prompt_ref: str, **values: object) -> str:
    """Load and substitute a Markdown prompt template using string.Template syntax."""
    mapping = {key: str(value) for key, value in values.items()}
    return Template(load_prompt(prompt_ref)).substitute(mapping)
