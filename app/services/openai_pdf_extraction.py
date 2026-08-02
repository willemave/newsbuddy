"""Native PDF extraction through the OpenAI Responses API."""

from __future__ import annotations

import base64

from openai import OpenAI

PDF_MAX_OUTPUT_TOKENS = 50_000


def create_openai_pdf_client(api_key: str) -> OpenAI:
    """Create the direct OpenAI client used for PDF extraction."""
    return OpenAI(api_key=api_key)


def extract_pdf_with_openai(
    client: OpenAI,
    *,
    content: bytes,
    model: str,
    prompt: str,
) -> str:
    """Extract PDF text while retaining page images for visual content."""
    encoded_pdf = base64.b64encode(content).decode("ascii")
    response = client.responses.create(
        model=model,
        reasoning={"effort": "none"},
        max_output_tokens=PDF_MAX_OUTPUT_TOKENS,
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_file",
                        "filename": "document.pdf",
                        "file_data": f"data:application/pdf;base64,{encoded_pdf}",
                        "detail": "high",
                    },
                    {"type": "input_text", "text": prompt},
                ],
            }
        ],
    )
    return response.output_text.strip()


def classify_openai_pdf_error(exc: Exception) -> str:
    """Return a stable operational category for OpenAI PDF failures."""
    status_code = getattr(exc, "status_code", None)
    message = str(exc).lower()
    if status_code in {400, 404} and "model" in message:
        return "model_unavailable"
    if status_code == 413 or "too large" in message:
        return "pdf_too_large"
    if status_code == 429:
        return "rate_limited"
    return "provider_error"
