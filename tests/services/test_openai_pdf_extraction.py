"""Tests for native OpenAI PDF extraction."""

from unittest.mock import Mock

import httpx
from openai import APIStatusError

from app.services.openai_pdf_extraction import (
    PDF_MAX_OUTPUT_TOKENS,
    classify_openai_pdf_error,
    extract_pdf_with_openai,
)


def test_extract_pdf_with_openai_sends_high_detail_pdf_with_no_reasoning() -> None:
    response = Mock(output_text="  PDF Title\nBody  ")
    client = Mock()
    client.responses.create.return_value = response

    text = extract_pdf_with_openai(
        client,
        content=b"%PDF-test",
        model="gpt-5.6-luna",
        prompt="Extract the document",
    )

    assert text == "PDF Title\nBody"
    client.responses.create.assert_called_once_with(
        model="gpt-5.6-luna",
        reasoning={"effort": "none"},
        max_output_tokens=PDF_MAX_OUTPUT_TOKENS,
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_file",
                        "filename": "document.pdf",
                        "file_data": "data:application/pdf;base64,JVBERi10ZXN0",
                        "detail": "high",
                    },
                    {"type": "input_text", "text": "Extract the document"},
                ],
            }
        ],
    )


def test_classify_openai_pdf_error_for_unavailable_model() -> None:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(404, request=request)
    error = APIStatusError("The requested model does not exist", response=response, body=None)

    assert classify_openai_pdf_error(error) == "model_unavailable"
