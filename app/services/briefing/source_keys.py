from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BriefingSourceKey:
    kind: str
    source_id: int

    @property
    def value(self) -> str:
        return build_source_key(self.kind, self.source_id)


def build_source_key(kind: str, source_id: int) -> str:
    normalized_kind = kind.strip().lower()
    if normalized_kind not in {"content", "news"}:
        raise ValueError(f"Unsupported briefing source kind: {kind}")
    return f"{normalized_kind}:{int(source_id)}"


def parse_source_key(value: str) -> BriefingSourceKey | None:
    parts = value.split(":", 1)
    if len(parts) != 2:
        return None
    kind, raw_id = parts
    normalized_kind = kind.strip().lower()
    if normalized_kind not in {"content", "news"}:
        return None
    try:
        source_id = int(raw_id)
    except (TypeError, ValueError):
        return None
    if source_id <= 0:
        return None
    return BriefingSourceKey(normalized_kind, source_id)
