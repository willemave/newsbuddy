"""Regression tests for noisy/oversized news article metadata titles."""

from app.models.content_mapper import content_to_domain
from app.models.metadata import ContentData, ContentStatus, ContentType
from app.models.schema import Content as DBContent


def _build_noisy_title() -> str:
    noisy = (
        "Learn Module | OpenClaw Army "
        '<meta property="og:locale:alternate" content="es" /> '
        '<script>var data = {"foo": "bar"};</script> '
        "<div>ignored</div> "
    )
    return noisy * 30


def _build_news_metadata(raw_title: str) -> dict:
    return {
        "source": "artificial",
        "article": {
            "url": "https://openclaw.army/learn",
            "title": raw_title,
            "source_domain": "openclaw.army",
        },
    }


def test_content_data_normalizes_noisy_news_article_title() -> None:
    content = ContentData(
        content_type=ContentType.NEWS,
        url="https://news.ycombinator.com/item?id=123",
        status=ContentStatus.COMPLETED,
        metadata=_build_news_metadata(_build_noisy_title()),
    )

    title = content.metadata["article"]["title"]
    assert isinstance(title, str)
    assert len(title) <= 500
    assert "<script" not in title.lower()
    assert "<meta" not in title.lower()
    assert "<div>" not in title.lower()


def test_content_data_drops_placeholder_news_article_title() -> None:
    content = ContentData(
        content_type=ContentType.NEWS,
        url="https://news.ycombinator.com/item?id=1234",
        status=ContentStatus.COMPLETED,
        metadata=_build_news_metadata("SKILL0"),
    )

    assert content.metadata["article"].get("title") is None


def test_content_data_drops_blocked_page_titles() -> None:
    content = ContentData(
        content_type=ContentType.NEWS,
        url="https://news.ycombinator.com/item?id=12345",
        status=ContentStatus.COMPLETED,
        metadata=_build_news_metadata("Subscribe to read"),
    )

    assert content.metadata["article"].get("title") is None


def test_content_data_drops_bare_domain_titles() -> None:
    content = ContentData(
        content_type=ContentType.NEWS,
        url="https://news.ycombinator.com/item?id=12346",
        status=ContentStatus.COMPLETED,
        metadata=_build_news_metadata("wsj.com"),
    )

    assert content.metadata["article"].get("title") is None


def test_content_data_display_title_falls_back_to_summary_text() -> None:
    content = ContentData(
        content_type=ContentType.NEWS,
        url="https://news.ycombinator.com/item?id=4321",
        title="SKILL0",
        status=ContentStatus.COMPLETED,
        metadata={
            "source": "artificial",
            "article": {
                "url": "https://openclaw.army/learn",
                "title": "SKILL0",
                "source_domain": "openclaw.army",
            },
            "summary_kind": "short_news_digest",
            "summary_version": 1,
            "summary": {
                "title": "SKILL0",
                "summary": (
                    "A Hugging Face space demo that explains how a tiny skill model "
                    "works in practice."
                ),
            },
        },
    )

    assert (
        content.display_title
        == "A Hugging Face space demo that explains how a tiny skill model works in practice."
    )


def test_content_to_domain_handles_noisy_news_article_title() -> None:
    db_content = DBContent(
        id=21461,
        content_type=ContentType.NEWS.value,
        url="https://news.ycombinator.com/item?id=21461",
        status=ContentStatus.COMPLETED.value,
        content_metadata=_build_news_metadata(_build_noisy_title()),
    )

    domain = content_to_domain(db_content)
    title = domain.metadata["article"]["title"]
    assert isinstance(title, str)
    assert len(title) <= 500
