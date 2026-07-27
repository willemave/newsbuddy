"""Project heterogeneous summary payloads onto the common content-detail fields."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.models.contracts import SummaryKind, SummaryVersion
from app.models.metadata.summary_contracts import parse_summary_version, resolve_summary_kind

SummaryDict = dict[str, Any]
SummaryContext = tuple[SummaryDict, SummaryKind, SummaryVersion | None]
SummaryListExtractor = Callable[[SummaryDict, SummaryVersion | None], list[dict[str, str]]]
SummaryTopicExtractor = Callable[[SummaryDict, SummaryVersion | None], list[str]]

DISPLAY_SUMMARY_KINDS = frozenset(
    {
        SummaryKind.LONG_STRUCTURED,
        SummaryKind.LONG_INTERLEAVED,
        SummaryKind.LONG_BULLETS,
        SummaryKind.LONG_EDITORIAL_NARRATIVE,
        SummaryKind.LONGFORM_ARTIFACT,
    }
)


def structured_summary(metadata: dict[str, Any]) -> SummaryDict | None:
    """Return a displayable structured payload, including supported legacy shapes."""
    context = _resolve_summary_context(metadata)
    return context[0] if context is not None else None


def bullet_points(metadata: dict[str, Any]) -> list[dict[str, str]]:
    """Project any supported summary kind onto common bullet-point dictionaries."""
    context = _resolve_summary_context(metadata)
    if context is None:
        return []
    summary, summary_kind, summary_version = context
    extractor = BULLET_POINT_EXTRACTORS.get(summary_kind)
    return extractor(summary, summary_version) if extractor is not None else []


def quotes(metadata: dict[str, Any]) -> list[dict[str, str]]:
    """Project any supported summary kind onto common quote dictionaries."""
    context = _resolve_summary_context(metadata)
    if context is None:
        return []
    summary, summary_kind, summary_version = context
    extractor = QUOTE_EXTRACTORS.get(summary_kind)
    return extractor(summary, summary_version) if extractor is not None else []


def topics(metadata: dict[str, Any]) -> list[str] | None:
    """Project summary topics, or return None when metadata topics should be used."""
    context = _resolve_summary_context(metadata)
    if context is None:
        return None
    summary, summary_kind, summary_version = context
    extractor = TOPIC_EXTRACTORS.get(summary_kind)
    return extractor(summary, summary_version) if extractor is not None else []


def _resolve_summary_context(metadata: dict[str, Any]) -> SummaryContext | None:
    summary_data = metadata.get("summary")
    if not isinstance(summary_data, dict):
        return None

    summary_kind = resolve_summary_kind(summary_data, metadata.get("summary_kind"))
    if summary_kind not in DISPLAY_SUMMARY_KINDS:
        return None

    return (
        summary_data,
        summary_kind,
        parse_summary_version(metadata.get("summary_version")),
    )


def _structured_bullet_points(
    summary: SummaryDict,
    version: SummaryVersion | None,
) -> list[dict[str, str]]:
    del version
    raw_points = summary.get("bullet_points", [])
    return raw_points if isinstance(raw_points, list) else []


def _interleaved_bullet_points(
    summary: SummaryDict,
    version: SummaryVersion | None,
) -> list[dict[str, str]]:
    if version == SummaryVersion.V2:
        raw_points = summary.get("key_points", [])
        return raw_points if isinstance(raw_points, list) else []

    insights = summary.get("insights", [])
    if not isinstance(insights, list):
        return []
    return [
        {"text": insight.get("insight", ""), "category": insight.get("topic", "")}
        for insight in insights
        if isinstance(insight, dict) and insight.get("insight")
    ]


def _long_bullets_points(
    summary: SummaryDict,
    version: SummaryVersion | None,
) -> list[dict[str, str]]:
    del version
    points = summary.get("points", [])
    if not isinstance(points, list):
        return []
    return [
        {"text": point.get("text", ""), "category": "key_point"}
        for point in points
        if isinstance(point, dict) and point.get("text")
    ]


def _editorial_points(
    summary: SummaryDict,
    version: SummaryVersion | None,
) -> list[dict[str, str]]:
    del version
    key_points = summary.get("key_points", [])
    if not isinstance(key_points, list):
        return []
    return [
        {"text": point.get("point", ""), "category": "key_point"}
        for point in key_points
        if isinstance(point, dict) and point.get("point")
    ]


def _artifact_points(
    summary: SummaryDict,
    version: SummaryVersion | None,
) -> list[dict[str, str]]:
    del version
    artifact = summary.get("artifact")
    payload = artifact.get("payload") if isinstance(artifact, dict) else None
    raw_points = payload.get("key_points", []) if isinstance(payload, dict) else []
    artifact_type = artifact.get("type") if isinstance(artifact, dict) else None
    if not isinstance(raw_points, list):
        return []
    return [
        {
            "text": " — ".join(
                part
                for part in (
                    str(point.get("heading") or "").strip(),
                    str(point.get("content") or "").strip(),
                )
                if part
            ),
            "category": str(artifact_type or "key_point"),
        }
        for point in raw_points
        if isinstance(point, dict) and (point.get("heading") or point.get("content"))
    ]


BULLET_POINT_EXTRACTORS: dict[SummaryKind, SummaryListExtractor] = {
    SummaryKind.LONG_STRUCTURED: _structured_bullet_points,
    SummaryKind.LONG_INTERLEAVED: _interleaved_bullet_points,
    SummaryKind.LONG_BULLETS: _long_bullets_points,
    SummaryKind.LONG_EDITORIAL_NARRATIVE: _editorial_points,
    SummaryKind.LONGFORM_ARTIFACT: _artifact_points,
}


def _structured_quotes(
    summary: SummaryDict,
    version: SummaryVersion | None,
) -> list[dict[str, str]]:
    del version
    raw_quotes = summary.get("quotes", [])
    return raw_quotes if isinstance(raw_quotes, list) else []


def _interleaved_quotes(
    summary: SummaryDict,
    version: SummaryVersion | None,
) -> list[dict[str, str]]:
    if version == SummaryVersion.V2:
        raw_quotes = summary.get("quotes", [])
        return raw_quotes if isinstance(raw_quotes, list) else []

    insights = summary.get("insights", [])
    if not isinstance(insights, list):
        return []
    result: list[dict[str, str]] = []
    for insight in insights:
        if not isinstance(insight, dict):
            continue
        quote_text = insight.get("supporting_quote")
        if not isinstance(quote_text, str) or not quote_text:
            continue
        context = insight.get("quote_attribution", insight.get("topic", ""))
        result.append(
            {
                "text": quote_text,
                "context": context if isinstance(context, str) else "",
            }
        )
    return result


def _long_bullets_quotes(
    summary: SummaryDict,
    version: SummaryVersion | None,
) -> list[dict[str, str]]:
    del version
    points = summary.get("points", [])
    if not isinstance(points, list):
        return []
    flattened: list[dict[str, str]] = []
    for point in points:
        if not isinstance(point, dict):
            continue
        for quote in point.get("quotes", []) or []:
            if not isinstance(quote, dict):
                continue
            text = quote.get("text")
            if text:
                flattened.append(
                    {
                        "text": text,
                        "context": quote.get("context") or quote.get("attribution", ""),
                    }
                )
    return flattened


def _editorial_quotes(
    summary: SummaryDict,
    version: SummaryVersion | None,
) -> list[dict[str, str]]:
    del version
    raw_quotes = summary.get("quotes", [])
    if not isinstance(raw_quotes, list):
        return []
    return [
        {
            "text": quote.get("text", ""),
            "context": quote.get("attribution", ""),
        }
        for quote in raw_quotes
        if isinstance(quote, dict) and quote.get("text")
    ]


def _artifact_quotes(
    summary: SummaryDict,
    version: SummaryVersion | None,
) -> list[dict[str, str]]:
    del version
    artifact = summary.get("artifact")
    payload = artifact.get("payload") if isinstance(artifact, dict) else None
    raw_quotes = payload.get("quotes", []) if isinstance(payload, dict) else []
    if not isinstance(raw_quotes, list):
        return []
    return [
        {
            "text": quote.get("text", ""),
            "context": quote.get("attribution", ""),
        }
        for quote in raw_quotes
        if isinstance(quote, dict) and quote.get("text")
    ]


QUOTE_EXTRACTORS: dict[SummaryKind, SummaryListExtractor] = {
    SummaryKind.LONG_STRUCTURED: _structured_quotes,
    SummaryKind.LONG_INTERLEAVED: _interleaved_quotes,
    SummaryKind.LONG_BULLETS: _long_bullets_quotes,
    SummaryKind.LONG_EDITORIAL_NARRATIVE: _editorial_quotes,
    SummaryKind.LONGFORM_ARTIFACT: _artifact_quotes,
}


def _structured_topics(
    summary: SummaryDict,
    version: SummaryVersion | None,
) -> list[str]:
    del version
    raw_topics = summary.get("topics", [])
    if isinstance(raw_topics, list):
        return [topic for topic in raw_topics if isinstance(topic, str)]
    return []


def _interleaved_topics(
    summary: SummaryDict,
    version: SummaryVersion | None,
) -> list[str]:
    if version == SummaryVersion.V2:
        topics = summary.get("topics", [])
        if not isinstance(topics, list):
            return []
        return [
            topic_name
            for topic in topics
            if isinstance(topic, dict)
            and isinstance((topic_name := topic.get("topic")), str)
            and topic_name
        ]

    insights = summary.get("insights", [])
    if not isinstance(insights, list):
        return []
    seen: set[str] = set()
    result: list[str] = []
    for insight in insights:
        if not isinstance(insight, dict):
            continue
        topic = insight.get("topic")
        if isinstance(topic, str) and topic and topic not in seen:
            seen.add(topic)
            result.append(topic)
    return result


def _no_topics(
    summary: SummaryDict,
    version: SummaryVersion | None,
) -> list[str]:
    del summary, version
    return []


def _artifact_topics(
    summary: SummaryDict,
    version: SummaryVersion | None,
) -> list[str]:
    del version
    artifact = summary.get("artifact")
    if isinstance(artifact, dict) and isinstance(artifact.get("type"), str):
        return [artifact["type"]]
    return []


TOPIC_EXTRACTORS: dict[SummaryKind, SummaryTopicExtractor] = {
    SummaryKind.LONG_STRUCTURED: _structured_topics,
    SummaryKind.LONG_INTERLEAVED: _interleaved_topics,
    SummaryKind.LONG_BULLETS: _no_topics,
    SummaryKind.LONG_EDITORIAL_NARRATIVE: _no_topics,
    SummaryKind.LONGFORM_ARTIFACT: _artifact_topics,
}
