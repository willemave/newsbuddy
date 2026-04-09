"""Tests for HtmlProcessorStrategy domain overrides."""

from app.core.settings import get_settings
from app.processing_strategies.html_strategy import HtmlProcessorStrategy


def _set_required_env(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres@localhost/test_db")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-password")


def test_domain_override_sets_no_wait_for(monkeypatch):
    """screenrant.com overrides wait_for to None."""
    _set_required_env(monkeypatch)
    get_settings.cache_clear()

    strategy = HtmlProcessorStrategy(http_client=None)  # type: ignore[arg-type]
    overrides = strategy._get_domain_overrides(  # pylint: disable=protected-access
        "https://screenrant.com/some-article"
    )

    assert overrides["wait_for"] is None
