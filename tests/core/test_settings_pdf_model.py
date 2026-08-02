"""Tests for the native OpenAI PDF extraction model setting."""

import importlib

import pytest

from app.core.settings import Settings, get_settings


def _set_required_env(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres@localhost/test_db")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-password")


def test_pdf_extraction_model_default(monkeypatch):
    """PDF extraction defaults to the evaluated high-volume OpenAI model."""
    _set_required_env(monkeypatch)
    monkeypatch.delenv("PDF_EXTRACTION_MODEL", raising=False)
    get_settings.cache_clear()

    settings = Settings()
    assert settings.pdf_extraction_model == "gpt-5.6-luna"


def test_pdf_extraction_model_invalid(monkeypatch):
    """Invalid model names fail validation."""
    _set_required_env(monkeypatch)
    monkeypatch.setenv("PDF_EXTRACTION_MODEL", "gemini-3.1-flash-lite")
    get_settings.cache_clear()

    with pytest.raises(ValueError):
        Settings()


def test_pdf_strategy_uses_settings_model(monkeypatch):
    """PdfProcessorStrategy picks up settings-based model."""
    _set_required_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("PDF_EXTRACTION_MODEL", "gpt-5.6-luna")
    get_settings.cache_clear()

    from app.processing_strategies import pdf_strategy as pdf_module

    importlib.reload(pdf_module)

    class DummyClient:
        pass

    client = DummyClient()

    def client_factory(_api_key: str) -> DummyClient:
        return client

    monkeypatch.setattr(pdf_module, "create_openai_pdf_client", client_factory)

    class DummyHttpClient:
        pass

    strategy = pdf_module.PdfProcessorStrategy(http_client=DummyHttpClient())
    assert strategy.client is client
    assert strategy.model_name == "gpt-5.6-luna"
