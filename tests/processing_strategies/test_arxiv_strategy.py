"""Unit tests for the arXiv processing strategy."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.http_client.robust_http_client import RobustHttpClient
from app.models.metadata.source import SourceMetadataEnvelope
from app.processing_strategies import arxiv_strategy as arxiv_mod
from app.processing_strategies.arxiv_strategy import ArxivProcessorStrategy


@pytest.fixture
def strategy() -> ArxivProcessorStrategy:
    """Return an ArxivProcessorStrategy with a mocked HTTP client."""
    return ArxivProcessorStrategy(Mock(spec=RobustHttpClient))


def test_can_handle_arxiv_abs_url(strategy: ArxivProcessorStrategy) -> None:
    """Strategy should accept canonical arXiv abstract URLs."""
    assert strategy.can_handle_url("https://arxiv.org/abs/2509.15194")


def test_can_handle_arxiv_pdf_url_with_www(strategy: ArxivProcessorStrategy) -> None:
    """Strategy should accept direct PDF URLs on www.arxiv.org."""
    assert strategy.can_handle_url("https://www.arxiv.org/pdf/2509.15194")


def test_cannot_handle_non_arxiv_domain(strategy: ArxivProcessorStrategy) -> None:
    """Strategy should reject non-arXiv domains."""
    assert not strategy.can_handle_url("https://example.com/pdf/2509.15194")


def test_preprocess_converts_abs_to_pdf(strategy: ArxivProcessorStrategy) -> None:
    """Abstract URLs should be converted to canonical PDF URLs."""
    normalized = strategy.preprocess_url("http://www.arxiv.org/abs/2509.15194v2?context=cs")
    assert normalized == "https://arxiv.org/pdf/2509.15194v2"


def test_preprocess_normalizes_pdf_host(strategy: ArxivProcessorStrategy) -> None:
    """Direct PDF URLs should be normalized to https://arxiv.org."""
    normalized = strategy.preprocess_url("https://www.arxiv.org/pdf/2509.15194")
    assert normalized == "https://arxiv.org/pdf/2509.15194"


def test_preprocess_preserves_pdf_query(strategy: ArxivProcessorStrategy) -> None:
    """Query parameters for direct PDFs should be preserved."""
    normalized = strategy.preprocess_url("https://arxiv.org/pdf/2509.15194.pdf?download=1")
    assert normalized == "https://arxiv.org/pdf/2509.15194.pdf?download=1"


def test_extract_data_does_not_fall_back_to_local_pdf_text(mocker, monkeypatch) -> None:
    monkeypatch.setattr(
        arxiv_mod,
        "settings",
        SimpleNamespace(openai_api_key="test-key", pdf_extraction_model="test-model"),
    )
    mocker.patch(
        "app.processing_strategies.arxiv_strategy.create_openai_pdf_client",
        return_value=Mock(),
    )
    mocker.patch(
        "app.processing_strategies.arxiv_strategy.extract_pdf_with_openai",
        side_effect=RuntimeError("provider unavailable"),
    )

    strategy = arxiv_mod.ArxivProcessorStrategy(Mock(spec=RobustHttpClient))
    data = strategy.extract_data(b"%PDF-1.4", "https://arxiv.org/pdf/1234.5678.pdf")
    llm_input = strategy.prepare_for_llm(data)

    assert data["title"] == "1234.5678.pdf"
    assert data["text_content"] == ""
    assert llm_input["content_to_summarize"] == ""
    assert llm_input["is_pdf"] is True


def test_extract_data_includes_source_metadata(mocker, monkeypatch) -> None:
    monkeypatch.setattr(arxiv_mod, "settings", SimpleNamespace(openai_api_key=None))
    monkeypatch.setattr(
        arxiv_mod,
        "fetch_arxiv_source_metadata",
        lambda _url, http_client: SourceMetadataEnvelope(
            source_id="1234.5678",
            canonical_abs_url="https://arxiv.org/abs/1234.5678",
            title="Arxiv Title",
            brief_synopsis="A brief source synopsis.",
        ),
    )

    strategy = arxiv_mod.ArxivProcessorStrategy(Mock(spec=RobustHttpClient))
    data = strategy.extract_data(b"%PDF-1.4", "https://arxiv.org/pdf/1234.5678.pdf")

    assert data["source_metadata"]["source_id"] == "1234.5678"
    assert data["source_metadata"]["brief_synopsis"] == "A brief source synopsis."
