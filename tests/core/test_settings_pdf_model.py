"""Tests for PDF Gemini model settings."""

import importlib

import pytest

from app.core.settings import Settings, get_settings


def _set_required_env(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres@localhost/test_db")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-password")


def test_pdf_gemini_model_default(monkeypatch):
    """Default model is gemini-3.1-flash-lite-preview."""
    _set_required_env(monkeypatch)
    monkeypatch.delenv("PDF_GEMINI_MODEL", raising=False)
    get_settings.cache_clear()

    settings = Settings()
    assert settings.pdf_gemini_model == "gemini-3.1-flash-lite-preview"


def test_pdf_gemini_model_invalid(monkeypatch):
    """Invalid model names fail validation."""
    _set_required_env(monkeypatch)
    monkeypatch.setenv("PDF_GEMINI_MODEL", "flash-3")
    get_settings.cache_clear()

    with pytest.raises(ValueError):
        Settings()


def test_pdf_strategy_uses_settings_model(monkeypatch):
    """PdfProcessorStrategy picks up settings-based model."""
    _set_required_env(monkeypatch)
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("PDF_GEMINI_MODEL", "gemini-3.1-flash-lite-preview")
    get_settings.cache_clear()

    from app.processing_strategies import pdf_strategy as pdf_module

    importlib.reload(pdf_module)

    class DummyClient:
        def __init__(self, api_key: str):
            self.api_key = api_key

    monkeypatch.setattr(pdf_module.genai, "Client", DummyClient)

    class DummyHttpClient:
        pass

    strategy = pdf_module.PdfProcessorStrategy(http_client=DummyHttpClient())  # type: ignore[arg-type]
    assert strategy.model_name == "gemini-3.1-flash-lite-preview"
