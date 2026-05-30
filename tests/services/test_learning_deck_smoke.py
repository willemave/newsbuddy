from __future__ import annotations

from app.models.contracts import ContentStatus, ContentType
from app.models.db import Content, LearningDeckRun
from app.services.gateways.object_storage_gateway import LocalObjectStorageGateway
from app.services.learning_deck_agent import LearningDeckAgentResult
from app.services.learning_deck_generation import generate_learning_deck
from app.services.learning_decks import (
    create_or_rerun_learning_deck,
    enable_learning_deck_share,
    get_deck_by_valid_share_token,
)
from tests.support.builders import create_content_status_entry_row


def _required_id(value: int | None) -> int:
    assert value is not None
    return value


def test_learning_deck_smoke_matrix_article_github_arxiv_url_and_failed_rerun(
    db_session,
    test_user,
    content_factory,
    tmp_path,
    monkeypatch,
) -> None:
    gateway = LocalObjectStorageGateway(tmp_path)
    monkeypatch.setattr(
        "app.services.learning_deck_artifacts.get_object_storage_gateway",
        lambda: gateway,
    )

    article = content_factory(
        content_type=ContentType.ARTICLE,
        title="Queue Architecture Article",
        content_metadata={"content": "Article source text about queues, workers, and retries."},
    )
    create_content_status_entry_row(db_session, user=test_user, content=article)

    article_deck = create_or_rerun_learning_deck(
        db_session,
        current_user=test_user,
        content_id=article.id,
        interests_prompt="Teach the queue architecture",
    )
    article_run = _latest_run(db_session, _required_id(article_deck.id))
    _generate_success(db_session, _required_id(article_run.id), title="Article Deck")

    db_session.refresh(article_deck)
    original_article_key = article_deck.deck_object_key
    assert article_deck.latest_successful_run_id == article_run.id
    assert original_article_key
    assert b"Article Deck" in gateway.get_bytes(key=original_article_key)

    share_token = enable_learning_deck_share(
        db_session,
        user_id=_required_id(test_user.id),
        deck_id=_required_id(article_deck.id),
    )
    shared_deck = get_deck_by_valid_share_token(db_session, token=share_token)
    assert shared_deck.deck_object_key == original_article_key

    github_deck = create_or_rerun_learning_deck(
        db_session,
        current_user=test_user,
        url="https://github.com/openai/codex/tree/main",
        interests_prompt="Focus on repo architecture",
    )
    github_run = _latest_run(db_session, _required_id(github_deck.id))
    _generate_success(
        db_session,
        _required_id(github_run.id),
        title="GitHub Deck",
        source_metadata={"default_branch": "main", "commit_sha": "abc123"},
    )

    db_session.refresh(github_deck)
    assert github_deck.source_identity == "github:openai/codex"
    assert github_deck.latest_successful_run_id == github_run.id
    assert isinstance(github_deck.source_metadata, dict)
    assert github_deck.source_metadata["commit_sha"] == "abc123"

    arxiv_deck = create_or_rerun_learning_deck(
        db_session,
        current_user=test_user,
        url="https://arxiv.org/pdf/2501.00001",
        interests_prompt="Explain the paper like a systems lesson",
    )
    arxiv_content = db_session.query(Content).filter_by(id=arxiv_deck.source_content_id).one()
    arxiv_content.content_type = ContentType.ARTICLE.value
    arxiv_content.status = ContentStatus.COMPLETED.value
    arxiv_content.title = "arXiv Systems Paper"
    arxiv_content.content_metadata = {
        "content": "PDF text extracted from an arXiv paper about system design.",
        "source_type": "arxiv_pdf",
    }
    db_session.commit()

    arxiv_run = _latest_run(db_session, _required_id(arxiv_deck.id))
    _generate_success(db_session, _required_id(arxiv_run.id), title="arXiv Deck")

    db_session.refresh(arxiv_deck)
    assert arxiv_deck.source_identity == f"content:{arxiv_content.id}"
    assert arxiv_deck.latest_successful_run_id == arxiv_run.id

    failed_rerun_deck = create_or_rerun_learning_deck(
        db_session,
        current_user=test_user,
        content_id=article.id,
        interests_prompt="Try a broken rerun",
    )
    failed_run = _latest_run(db_session, _required_id(failed_rerun_deck.id))
    assert failed_rerun_deck.id == article_deck.id

    try:
        generate_learning_deck(
            db_session,
            learning_deck_run_id=_required_id(failed_run.id),
            agent_runner=lambda *_args: LearningDeckAgentResult(
                index_html="<html><body>not a reveal deck</body></html>",
                source_notes_md="# Source Notes\n\n## Sources\n\n- Article.",
                assets={},
                model_provider="openai",
                model_name="openai:test",
                sandbox_provider="local",
                sandbox_id="smoke-failure",
                source_metadata_updates={},
            ),
        )
    except ValueError as exc:
        assert "Reveal.js deck" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected failed rerun validation")

    db_session.refresh(article_deck)
    db_session.refresh(failed_run)
    assert failed_run.status == "failed"
    assert article_deck.latest_successful_run_id == article_run.id
    assert article_deck.deck_object_key == original_article_key
    assert b"Article Deck" in gateway.get_bytes(key=original_article_key)
    assert get_deck_by_valid_share_token(db_session, token=share_token).deck_object_key == (
        original_article_key
    )


def _latest_run(db_session, deck_id: int) -> LearningDeckRun:
    run = (
        db_session.query(LearningDeckRun)
        .filter_by(deck_id=deck_id)
        .order_by(LearningDeckRun.id.desc())
        .first()
    )
    assert run is not None
    return run


def _generate_success(
    db_session,
    run_id: int,
    *,
    title: str,
    source_metadata: dict[str, str] | None = None,
) -> None:
    generate_learning_deck(
        db_session,
        learning_deck_run_id=run_id,
        agent_runner=lambda *_args: LearningDeckAgentResult(
            index_html=_index_html(title),
            source_notes_md=_source_notes(title),
            assets={},
            model_provider="openai",
            model_name="openai:test",
            sandbox_provider="local",
            sandbox_id=f"smoke-{run_id}",
            source_metadata_updates=source_metadata or {},
        ),
    )


def _index_html(title: str) -> str:
    return f"""<!doctype html>
<html>
<head>
  <style>
    :root {{ --deck-bg: #11110f; --deck-accent: #c77d3a; }}
    .reveal {{ background: var(--deck-bg); color: #f4f0e8; }}
  </style>
</head>
<body>
  <div class="reveal">
    <div class="slides">
      <section>{title}</section>
      <section>Architecture</section>
    </div>
  </div>
  <script src="https://cdn.jsdelivr.net/npm/reveal.js/dist/reveal.js"></script>
  <script>Reveal.initialize();</script>
</body>
</html>
"""


def _source_notes(title: str) -> str:
    return f"""# Source Notes

## Sources

- Primary source for {title}.

## Source-to-slide mapping

- Slide 1 introduces {title}.
- Slide 2 maps the architecture.
"""
