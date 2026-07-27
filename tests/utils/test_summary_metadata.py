from app.models.contracts import SummaryKind, SummaryVersion
from app.models.metadata.summary_contracts import infer_summary_kind_version


def test_infer_summary_kind_version_interleaved_v1() -> None:
    summary = {"summary_type": "interleaved", "insights": [{"topic": "AI"}]}
    result = infer_summary_kind_version("article", summary, None, None)
    assert result == (SummaryKind.LONG_INTERLEAVED, SummaryVersion.V1)


def test_infer_summary_kind_version_short_news() -> None:
    summary = {"summary": "Quick summary", "key_points": ["Point"]}
    result = infer_summary_kind_version("article", summary, None, None)
    assert result == (SummaryKind.SHORT_NEWS, SummaryVersion.V1)


def test_infer_summary_kind_version_interleaved_v2() -> None:
    summary = {"key_points": [{"text": "Point"}], "topics": [{"topic": "AI"}]}
    result = infer_summary_kind_version("podcast", summary, None, None)
    assert result == (SummaryKind.LONG_INTERLEAVED, SummaryVersion.V2)


def test_infer_summary_kind_version_structured() -> None:
    summary = {"overview": "Overview", "bullet_points": [{"text": "Point"}]}
    result = infer_summary_kind_version("article", summary, None, None)
    assert result == (SummaryKind.LONG_STRUCTURED, SummaryVersion.V1)


def test_infer_summary_kind_version_bullet_points_only_uses_contracts_fallback() -> None:
    summary = {"bullet_points": [{"text": "Point"}]}
    result = infer_summary_kind_version("article", summary, None, None)
    assert result == (SummaryKind.LONG_BULLETS, SummaryVersion.V1)


def test_infer_summary_kind_version_bullets() -> None:
    summary = {"points": [{"text": "Point"}]}
    result = infer_summary_kind_version("article", summary, None, None)
    assert result == (SummaryKind.LONG_BULLETS, SummaryVersion.V1)


def test_infer_summary_kind_version_editorial_narrative() -> None:
    summary = {
        "editorial_narrative": "Narrative paragraph with enough substance.",
        "key_points": [{"point": "A concrete point"}],
    }
    result = infer_summary_kind_version("article", summary, None, None)
    assert result == (SummaryKind.LONG_EDITORIAL_NARRATIVE, SummaryVersion.V1)


def test_infer_summary_kind_version_editorial_without_key_points_uses_contracts_fallback() -> None:
    summary = {"editorial_narrative": "Narrative paragraph with enough substance."}
    result = infer_summary_kind_version("article", summary, None, None)
    assert result == (SummaryKind.LONG_EDITORIAL_NARRATIVE, SummaryVersion.V1)


def test_infer_summary_kind_version_news_content_type_defaults() -> None:
    summary = {"key_points": ["Item"], "summary": "Summary"}
    result = infer_summary_kind_version("news", summary, None, None)
    assert result == (SummaryKind.SHORT_NEWS, SummaryVersion.V1)


def test_infer_summary_kind_version_preserves_kind_for_missing_version() -> None:
    summary = {"key_points": [{"text": "Point"}], "topics": [{"topic": "AI"}]}
    result = infer_summary_kind_version(
        "article",
        summary,
        SummaryKind.LONG_INTERLEAVED.value,
        None,
    )
    assert result == (SummaryKind.LONG_INTERLEAVED, SummaryVersion.V2)
