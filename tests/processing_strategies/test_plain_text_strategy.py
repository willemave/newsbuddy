"""Tests for the plain-text processing strategy."""

from unittest.mock import Mock

import httpx
import pytest

from app.http_client.robust_http_client import RobustHttpClient
from app.processing_strategies.plain_text_strategy import PlainTextProcessorStrategy
from app.services.http import NonRetryableError


@pytest.fixture
def mock_http_client() -> Mock:
    return Mock(spec=RobustHttpClient)


@pytest.fixture
def plain_text_strategy(mock_http_client: Mock) -> PlainTextProcessorStrategy:
    return PlainTextProcessorStrategy(mock_http_client)


def test_can_handle_url_by_text_extension(plain_text_strategy: PlainTextProcessorStrategy) -> None:
    assert plain_text_strategy.can_handle_url("https://example.com/article.txt")
    assert plain_text_strategy.can_handle_url("https://example.com/article.TXT?cache=1")
    assert plain_text_strategy.can_handle_url("https://example.com/article.text")


def test_can_handle_url_by_content_type(plain_text_strategy: PlainTextProcessorStrategy) -> None:
    headers = httpx.Headers({"content-type": "text/plain; charset=utf-8"})

    assert plain_text_strategy.can_handle_url("https://example.com/article", headers)


def test_rejects_non_text_urls(plain_text_strategy: PlainTextProcessorStrategy) -> None:
    assert not plain_text_strategy.can_handle_url("https://example.com/article.html")
    assert not plain_text_strategy.can_handle_url("https://example.com/data.json")
    assert not plain_text_strategy.can_handle_url("file:///tmp/article.txt")


def test_download_extract_and_prepare_plain_text(
    plain_text_strategy: PlainTextProcessorStrategy,
    mock_http_client: Mock,
) -> None:
    url = "https://example.com/article.txt"
    mock_http_client.get.return_value = httpx.Response(
        200,
        request=httpx.Request("GET", url),
        text="\nExample Title\n\nFirst paragraph.\n\nSecond paragraph.\n",
    )

    raw_content = plain_text_strategy.download_content(url)
    extracted = plain_text_strategy.extract_data(raw_content, url)
    prepared = plain_text_strategy.prepare_for_llm(extracted)

    mock_http_client.get.assert_called_once()
    assert extracted["title"] == "Example Title"
    assert extracted["content_type"] == "text"
    assert extracted["extraction_error"] is None
    assert prepared["content_to_summarize"] == (
        "Example Title\n\nFirst paragraph.\n\nSecond paragraph."
    )
    assert prepared["is_pdf"] is False


def test_empty_text_reports_extraction_error(
    plain_text_strategy: PlainTextProcessorStrategy,
) -> None:
    extracted = plain_text_strategy.extract_data("   ", "https://example.com/empty.txt")

    assert extracted["title"] == "empty.txt"
    assert extracted["text_content"] == ""
    assert extracted["extraction_error"] == "Plain text response was empty"


def test_download_marks_client_errors_non_retryable(
    plain_text_strategy: PlainTextProcessorStrategy,
    mock_http_client: Mock,
) -> None:
    url = "https://example.com/missing.txt"
    response = httpx.Response(404, request=httpx.Request("GET", url))
    mock_http_client.get.side_effect = httpx.HTTPStatusError(
        "not found",
        request=response.request,
        response=response,
    )

    with pytest.raises(NonRetryableError):
        plain_text_strategy.download_content(url)
