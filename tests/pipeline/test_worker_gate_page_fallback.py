"""Tests for gate-page handling in the content worker."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from unittest.mock import Mock

from app.models.content_mapper import content_to_domain
from app.models.metadata import ContentStatus, ContentType
from app.models.schema import Content
from app.pipeline.worker import ContentWorker


def _patch_worker_db(monkeypatch, db_session) -> None:
    @contextmanager
    def _get_db_override():
        try:
            yield db_session
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise

    monkeypatch.setattr("app.pipeline.worker.get_db", _get_db_override)


class _FakeHtmlStrategy:
    """Minimal strategy stub to exercise `_process_article` logic."""

    def __init__(self, extracted_data: dict[str, Any]) -> None:
        self._extracted_data = extracted_data
        self.last_context: dict[str, Any] | None = None

    def preprocess_url(self, url: str) -> str:
        return url

    def download_content(self, url: str) -> str:
        return f"<html>{url}</html>"

    def extract_data(
        self,
        _content: str,
        _url: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.last_context = dict(context or {})
        return dict(self._extracted_data)

    def prepare_for_llm(self, extracted_data: dict[str, Any]) -> dict[str, str]:
        return {"content_to_summarize": str(extracted_data.get("text_content", ""))}

    def extract_internal_urls(self, _content: str, _original_url: str) -> list[str]:
        return []


def _patch_worker_dependencies(monkeypatch, strategy: _FakeHtmlStrategy) -> None:
    registry = Mock()
    registry.get_strategy.return_value = strategy
    monkeypatch.setattr("app.pipeline.worker.get_strategy_registry", lambda: registry)
    monkeypatch.setattr("app.pipeline.worker.get_http_service", lambda: Mock())
    monkeypatch.setattr("app.pipeline.worker.get_queue_service", lambda: Mock())
    monkeypatch.setattr("app.pipeline.worker.get_checkout_manager", lambda: Mock())


def _build_gate_extracted_data() -> dict[str, Any]:
    return {
        "title": "[AINews] Anthropic's Agent Autonomy study - Latent.Space",
        "text_content": (
            "This site requires JavaScript to run correctly. "
            "Please turn on JavaScript or unblock scripts."
        ),
        "author": None,
        "publication_date": None,
        "content_type": "html",
        "source": "www.latent.space",
        "final_url_after_redirects": "https://www.latent.space/p/ainews-anthropics-agent-autonomy",
        "extraction_error": "access gate detected: challenge/JS wall content",
    }


def test_process_article_uses_rss_fallback_for_gate_page(monkeypatch, db_session) -> None:
    _patch_worker_db(monkeypatch, db_session)
    strategy = _FakeHtmlStrategy(
        {
            **_build_gate_extracted_data(),
            "text_content": "Recovered RSS content about agent autonomy and model behavior.",
            "gate_page_reason": "access gate detected: challenge/JS wall content",
            "extraction_error": None,
            "used_rss_fallback": True,
            "rss_fallback_length": 62,
        }
    )
    _patch_worker_dependencies(monkeypatch, strategy)

    db_content = Content(
        content_type=ContentType.ARTICLE.value,
        url="https://www.latent.space/p/ainews-anthropics-agent-autonomy",
        status=ContentStatus.NEW.value,
        content_metadata={
            "source": "Latent Space",
            "rss_content": (
                "<p>There's a lot of small tidbits going on, with former guest "
                "Fei-Fei Li discussing agent autonomy and model behavior.</p>"
            ),
        },
    )
    db_session.add(db_content)
    db_session.commit()
    db_session.refresh(db_content)

    worker = ContentWorker()
    content = content_to_domain(db_content)
    success = worker._process_article(content)

    assert success is True
    assert content.status == ContentStatus.PROCESSING
    assert content.metadata["used_rss_fallback"] is True
    assert content.metadata["gate_page_reason"] == "access gate detected: challenge/JS wall content"
    assert "agent autonomy and model behavior" in content.metadata["content_to_summarize"].lower()
    assert strategy.last_context is not None
    assert strategy.last_context["content_id"] == db_content.id
    assert strategy.last_context["existing_metadata"]["rss_content"].startswith("<p>There's a lot")


def test_process_article_prefers_exa_fallback_for_gate_page(monkeypatch, db_session) -> None:
    _patch_worker_db(monkeypatch, db_session)
    strategy = _FakeHtmlStrategy(
        {
            **_build_gate_extracted_data(),
            "text_content": (
                "Exa recovered the full article body about agent autonomy and model behavior."
            ),
            "gate_page_reason": "access gate detected: challenge/JS wall content",
            "extraction_error": None,
            "used_exa_fallback": True,
            "exa_fallback_length": 76,
        }
    )
    _patch_worker_dependencies(monkeypatch, strategy)

    db_content = Content(
        content_type=ContentType.ARTICLE.value,
        url="https://www.latent.space/p/ainews-anthropics-agent-autonomy",
        status=ContentStatus.NEW.value,
        content_metadata={
            "source": "Latent Space",
            "rss_content": "<p>RSS content should not win when Exa succeeds.</p>",
        },
    )
    db_session.add(db_content)
    db_session.commit()
    db_session.refresh(db_content)

    worker = ContentWorker()
    content = content_to_domain(db_content)
    success = worker._process_article(content)

    assert success is True
    assert content.metadata["used_exa_fallback"] is True
    assert content.metadata.get("used_rss_fallback") is None
    assert "exa recovered the full article body" in content.metadata["content_to_summarize"].lower()


def test_process_article_keeps_existing_title_when_extracted_title_is_blocked(
    monkeypatch,
    db_session,
) -> None:
    _patch_worker_db(monkeypatch, db_session)
    extracted_data = _build_gate_extracted_data()
    extracted_data["title"] = "wsj.com"
    extracted_data["text_content"] = "Enterprise AI infrastructure details from RSS."
    extracted_data["gate_page_reason"] = "access gate detected: challenge/JS wall content"
    extracted_data["extraction_error"] = None
    extracted_data["used_rss_fallback"] = True
    strategy = _FakeHtmlStrategy(extracted_data)
    _patch_worker_dependencies(monkeypatch, strategy)

    db_content = Content(
        content_type=ContentType.NEWS.value,
        url="https://www.wsj.com/tech/ai/example-story",
        title="Anthropic and Oracle discuss enterprise AI infrastructure",
        status=ContentStatus.NEW.value,
        content_metadata={
            "source": "Hacker News",
            "article": {
                "url": "https://www.wsj.com/tech/ai/example-story",
                "title": "Anthropic and Oracle discuss enterprise AI infrastructure",
            },
            "rss_content": "<p>Enterprise AI infrastructure details from RSS.</p>",
        },
    )
    db_session.add(db_content)
    db_session.commit()
    db_session.refresh(db_content)

    worker = ContentWorker()
    content = content_to_domain(db_content)
    success = worker._process_article(content)

    assert success is True
    assert content.title == "Anthropic and Oracle discuss enterprise AI infrastructure"
    assert content.metadata["article"]["title"] == (
        "Anthropic and Oracle discuss enterprise AI infrastructure"
    )


def test_process_article_fails_gate_page_without_rss_fallback(monkeypatch, db_session) -> None:
    _patch_worker_db(monkeypatch, db_session)
    strategy = _FakeHtmlStrategy(_build_gate_extracted_data())
    _patch_worker_dependencies(monkeypatch, strategy)

    db_content = Content(
        content_type=ContentType.ARTICLE.value,
        url="https://www.latent.space/p/ainews-anthropics-agent-autonomy",
        status=ContentStatus.NEW.value,
        content_metadata={"source": "Latent Space"},
    )
    db_session.add(db_content)
    db_session.commit()
    db_session.refresh(db_content)

    worker = ContentWorker()
    content = content_to_domain(db_content)
    success = worker._process_article(content)

    assert success is True
    assert content.status == ContentStatus.FAILED
    assert content.metadata["extraction_failed"] is True
    assert content.error_message == "access gate detected: challenge/JS wall content"
