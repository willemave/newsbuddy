from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.processing_strategies import arxiv_strategy as arxiv_mod
from app.processing_strategies import pdf_strategy as pdf_mod
from app.processing_strategies.hackernews_strategy import HackerNewsProcessorStrategy
from app.processing_strategies.image_strategy import ImageProcessorStrategy
from app.processing_strategies.pubmed_strategy import PubMedProcessorStrategy
from app.processing_strategies.twitter_share_strategy import (
    TweetContent,
    TwitterShareProcessorStrategy,
)


def test_twitter_share_extract_data_contains_text(mocker):
    strategy = TwitterShareProcessorStrategy(http_client=mocker.Mock())
    content = TweetContent(
        text="Hello world",
        author="@tester",
        publication_date=datetime(2025, 1, 1),
    )

    data = strategy.extract_data(content, "https://twitter.com/test/status/1")

    assert data["text_content"] == "Hello world"
    assert data["content_type"] == "text"


def test_twitter_share_download_content_prefers_native_article_text(mocker, monkeypatch):
    strategy = TwitterShareProcessorStrategy(http_client=mocker.Mock())

    monkeypatch.setattr(
        "app.processing_strategies.twitter_share_strategy.resolve_tweet_content",
        lambda **_kwargs: TweetContent(
            text="Native Article Title\n\nFull native article body text.",
            author="@tester",
            publication_date=datetime(2026, 3, 28, 10, 0, 0),
        ),
    )

    content = strategy.download_content("https://x.com/tester/status/123")

    assert content.text == "Native Article Title\n\nFull native article body text."
    assert content.author == "@tester"


def test_twitter_share_download_content_uses_metadata_snapshot_without_fetch(
    mocker,
    monkeypatch,
):
    strategy = TwitterShareProcessorStrategy(http_client=mocker.Mock())

    def _unexpected_fetch(**_kwargs):
        raise AssertionError("shared tweet resolver should use metadata snapshot first")

    monkeypatch.setattr(
        "app.processing_strategies.twitter_share_strategy.fetch_tweet_by_url",
        _unexpected_fetch,
    )
    monkeypatch.setattr(
        "app.processing_strategies.twitter_share_strategy.resolve_tweet_content",
        lambda **_kwargs: TweetContent(
            text="Metadata body",
            author="@tester",
            publication_date=datetime(2026, 3, 28, 10, 0, 0),
        ),
    )

    content = strategy.download_content(
        "https://x.com/tester/status/123",
        metadata={"tweet_article_text": "Metadata body"},
    )

    assert content.text == "Metadata body"


def test_hackernews_extract_data_contains_text(mocker, monkeypatch):
    strategy = HackerNewsProcessorStrategy(http_client=mocker.Mock())

    async def fake_fetch_item_data(_item_id, *, client):
        del client
        return {
            "title": "Ask HN",
            "by": "alice",
            "score": 5,
            "descendants": 1,
            "time": 1_700_000_000,
            "type": "story",
            "text": "<p>What are you building?</p>",
        }

    async def fake_fetch_comments(_item_data, *, client, max_comments=30):
        del client
        return [{"author": "bob", "text": "Nice!", "time": 1_700_000_100, "kids": [], "depth": 0}]

    monkeypatch.setattr(strategy, "_fetch_item_data", fake_fetch_item_data)
    monkeypatch.setattr(strategy, "_fetch_comments", fake_fetch_comments)

    data = strategy.extract_data("ignored", "https://news.ycombinator.com/item?id=123")

    assert data["text_content"]
    assert data["content_type"] == "html"


def test_image_strategy_marks_skip_processing(mocker):
    strategy = ImageProcessorStrategy(http_client=mocker.Mock())
    url = "https://example.com/image.jpg"

    data = strategy.extract_data(url, url)

    assert data["skip_processing"] is True
    assert data["text_content"] == ""


def test_pdf_strategy_extract_data_sets_text_content(mocker, monkeypatch):
    monkeypatch.setattr(
        pdf_mod,
        "settings",
        SimpleNamespace(openai_api_key="test-key", pdf_extraction_model="test-model"),
    )
    monkeypatch.setattr(pdf_mod, "create_openai_pdf_client", lambda _api_key: object())
    monkeypatch.setattr(
        pdf_mod,
        "extract_pdf_with_openai",
        lambda *_args, **_kwargs: "PDF Title\nBody",
    )

    strategy = pdf_mod.PdfProcessorStrategy(http_client=mocker.Mock())
    data = strategy.extract_data(b"%PDF-1.4", "https://example.com/doc.pdf")

    assert data["text_content"]
    assert data["content_type"] == "pdf"


def test_arxiv_strategy_extract_data_sets_text_content(mocker, monkeypatch):
    monkeypatch.setattr(
        arxiv_mod,
        "settings",
        SimpleNamespace(openai_api_key="test-key", pdf_extraction_model="test-model"),
    )
    monkeypatch.setattr(arxiv_mod, "create_openai_pdf_client", lambda _api_key: object())
    monkeypatch.setattr(
        arxiv_mod,
        "extract_pdf_with_openai",
        lambda *_args, **_kwargs: "Arxiv Title\nBody",
    )

    strategy = arxiv_mod.ArxivProcessorStrategy(http_client=mocker.Mock())
    data = strategy.extract_data(b"%PDF-1.4", "https://arxiv.org/pdf/1234.5678.pdf")

    assert data["text_content"]
    assert data["content_type"] == "pdf"


def test_pubmed_strategy_returns_delegation(mocker, monkeypatch):
    strategy = PubMedProcessorStrategy(http_client=mocker.Mock())

    class DummyResult:
        success = True
        html = "<html></html>"

    class DummyCrawler:
        def __init__(self, config=None):
            self.config = config

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def arun(self, url, config=None):
            return DummyResult()

    monkeypatch.setattr(
        "app.processing_strategies.pubmed_strategy.AsyncWebCrawler",
        DummyCrawler,
    )
    monkeypatch.setattr(
        strategy, "_extract_full_text_link_from_html", lambda *_: "https://example.com/full"
    )

    data = strategy.extract_data("", "https://pubmed.ncbi.nlm.nih.gov/123/")

    assert data["next_url_to_process"] == "https://example.com/full"
    assert data["content_type"] == "pubmed_delegation"
