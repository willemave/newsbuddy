from unittest.mock import MagicMock

import httpx  # For creating mock Headers
import pytest

from app.http_client.robust_http_client import RobustHttpClient
from app.processing_strategies.pdf_strategy import PdfProcessorStrategy

# Sample PDF content (minimal valid PDF structure for testing purposes)
# This is a very simple, tiny, valid PDF.
SAMPLE_PDF_BYTES = (
    b"%PDF-1.0\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/"
    b"Kids[3 0 R]/Count 1>>endobj 3 0 obj<</Type/Page/MediaBox[0 0 3 3]>>endobj\nxref\n"
    b"0 4\n0000000000 65535 f\n0000000010 00000 n\n0000000058 00000 n\n0000000111 00000 n\n"
    b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n149\n%%EOF"
)


@pytest.fixture
def mock_http_client(mocker):
    """Fixture to mock RobustHttpClient."""
    mock = MagicMock(spec=RobustHttpClient)
    return mock


@pytest.fixture
def pdf_strategy(mock_http_client):
    """Fixture to provide an instance of PdfProcessorStrategy with a mocked http_client."""
    return PdfProcessorStrategy(http_client=mock_http_client)


def test_can_handle_url_pdf_content_type(pdf_strategy: PdfProcessorStrategy):
    """Test can_handle_url with 'application/pdf' content type."""
    headers = httpx.Headers({"Content-Type": "application/pdf"})
    assert pdf_strategy.can_handle_url("http://example.com/document.pdf", headers) is True


def test_can_handle_url_pdf_extension(pdf_strategy: PdfProcessorStrategy):
    """Test can_handle_url with '.pdf' extension and no headers."""
    assert pdf_strategy.can_handle_url("http://example.com/document.pdf", None) is True


def test_can_handle_url_excludes_arxiv(pdf_strategy: PdfProcessorStrategy):
    """Test can_handle_url excludes arXiv URLs (handled by ArxivProcessorStrategy)."""
    assert pdf_strategy.can_handle_url("https://arxiv.org/pdf/1234.5678", None) is False
    assert pdf_strategy.can_handle_url("https://arxiv.org/pdf/1234.5678.pdf", None) is False
    assert pdf_strategy.can_handle_url("https://arxiv.org/abs/1234.5678", None) is False


def test_can_handle_url_non_pdf(pdf_strategy: PdfProcessorStrategy):
    """Test can_handle_url with non-PDF content type and extension."""
    headers = httpx.Headers({"Content-Type": "text/html"})
    assert pdf_strategy.can_handle_url("http://example.com/page.html", headers) is False
    assert pdf_strategy.can_handle_url("http://example.com/page.html", None) is False
    assert pdf_strategy.can_handle_url("http://example.com/document.doc", None) is False


def test_download_content(pdf_strategy: PdfProcessorStrategy, mock_http_client: MagicMock):
    """Test PDF content download."""
    url = "http://example.com/document.pdf"
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.content = SAMPLE_PDF_BYTES
    mock_response.url = url  # Simulate final URL

    def mock_get_op(*args, **kwargs):
        return mock_response

    mock_http_client.get = MagicMock(side_effect=mock_get_op)

    content = pdf_strategy.download_content(url)

    mock_http_client.get.assert_called_once_with(url)
    assert content == SAMPLE_PDF_BYTES


def test_extract_data_successful(pdf_strategy: PdfProcessorStrategy, mocker):
    """Test successful data extraction from PDF content."""
    """Test successful data extraction from PDF content."""
    # Mock the Google Gemini client
    mock_response = MagicMock()
    mock_response.text = "Test PDF Title\n\nThis is the extracted text content from the PDF."

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response
    pdf_strategy.client = mock_client

    url = "http://example.com/mydoc.pdf"
    extracted_data = pdf_strategy.extract_data(SAMPLE_PDF_BYTES, url)

    assert extracted_data["title"] == "Test PDF Title"
    assert extracted_data["author"] is None
    assert extracted_data["publication_date"] is None
    expected_text = "Test PDF Title\n\nThis is the extracted text content from the PDF."
    assert extracted_data["text_content"] == expected_text
    assert extracted_data["content_type"] == "pdf"
    assert extracted_data["final_url_after_redirects"] == url


def test_extract_data_no_content(pdf_strategy: PdfProcessorStrategy, mocker):
    """Fall back to local PDF extraction when Gemini extraction fails."""
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = Exception("Failed to extract")
    pdf_strategy.client = mock_client
    mocker.patch(
        "app.processing_strategies.pdf_strategy.extract_pdf_text",
        return_value="Local PDF Title\nRecovered body",
    )

    url = "http://example.com/empty.pdf"
    extracted_data = pdf_strategy.extract_data(SAMPLE_PDF_BYTES, url)

    assert extracted_data["title"] == "Local PDF Title"
    assert extracted_data["text_content"] == "Local PDF Title\nRecovered body"
    assert extracted_data["content_type"] == "pdf"


def test_extract_data_returns_failure_when_all_pdf_extraction_paths_fail(
    pdf_strategy: PdfProcessorStrategy,
    mocker,
):
    """Return a failed extraction payload when Gemini and local fallback both fail."""
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = Exception("Failed to extract")
    pdf_strategy.client = mock_client
    mocker.patch("app.processing_strategies.pdf_strategy.extract_pdf_text", return_value="")

    extracted_data = pdf_strategy.extract_data(SAMPLE_PDF_BYTES, "http://example.com/empty.pdf")

    assert extracted_data["title"] == "PDF Extraction Failed"
    assert extracted_data["text_content"] == ""


def test_prepare_for_llm(pdf_strategy: PdfProcessorStrategy):
    """Test preparation of extracted PDF data for LLM processing."""
    extracted_data = {
        "title": "mydoc.pdf",
        "text_content": "This is the extracted text content.",
        "content_type": "pdf",
        "final_url_after_redirects": "http://example.com/mydoc.pdf",
    }
    llm_input = pdf_strategy.prepare_for_llm(extracted_data)

    assert llm_input["content_to_filter"] == "This is the extracted text content."
    assert llm_input["content_to_summarize"] == "This is the extracted text content."
    assert llm_input["is_pdf"] is True


def test_prepare_for_llm_no_text_content(pdf_strategy: PdfProcessorStrategy):
    """Test LLM prep when text_content is missing."""
    extracted_data = {
        "title": "error.pdf",
        "text_content": None,  # Simulate missing text
        "content_type": "pdf",
        "final_url_after_redirects": "http://example.com/error.pdf",
    }
    llm_input = pdf_strategy.prepare_for_llm(extracted_data)
    # When text_content is None, get returns empty string as default
    assert llm_input["content_to_summarize"] == ""
    assert llm_input["content_to_filter"] == ""
    assert llm_input["is_pdf"] is True
