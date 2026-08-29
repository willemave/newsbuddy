from __future__ import annotations

import pytest

from app.services.gateways.object_storage_gateway import LocalObjectStorageGateway
from app.services.learning_deck_artifacts import (
    LEARNING_DECK_THUMBNAIL_PATH,
    LearningDeckArtifactError,
    render_source_notes_html,
    store_learning_deck_artifact,
    validate_learning_deck_artifact,
)

VALID_INDEX_HTML = """<!doctype html>
<html>
<head>
  <meta name="newsly-deck-layout" content="responsive-v2">
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/reveal.js@6.0.1/dist/reveal.css">
  <style>
    :root { --deck-bg: #11110f; --deck-accent: #c77d3a; }
    .reveal { background: var(--deck-bg); color: #f4f0e8; }
  </style>
</head>
<body>
  <div class="reveal"><div class="slides"><section>Intro</section></div></div>
  <script src="https://cdn.jsdelivr.net/npm/reveal.js@6.0.1/dist/reveal.js"></script>
  <script>Reveal.initialize();</script>
</body>
</html>
"""

VALID_SOURCE_NOTES = """# Source Notes

## Sources

- Primary source: test fixture.

## Source-to-slide mapping

- Slide 1 maps to the primary source.
"""


def test_validate_learning_deck_artifact_accepts_reveal_deck() -> None:
    validate_learning_deck_artifact(
        index_html=VALID_INDEX_HTML,
        source_notes_md=VALID_SOURCE_NOTES,
    )


def test_validate_learning_deck_artifact_requires_responsive_layout_metadata() -> None:
    html = VALID_INDEX_HTML.replace(
        '  <meta name="newsly-deck-layout" content="responsive-v2">\n',
        "",
    )

    with pytest.raises(LearningDeckArtifactError, match="responsive-v2"):
        validate_learning_deck_artifact(index_html=html, source_notes_md=VALID_SOURCE_NOTES)


def test_validate_learning_deck_artifact_accepts_linked_local_theme() -> None:
    html = VALID_INDEX_HTML.replace(
        """  <style>
    :root { --deck-bg: #11110f; --deck-accent: #c77d3a; }
    .reveal { background: var(--deck-bg); color: #f4f0e8; }
  </style>
""",
        '  <link href="assets/theme.css" rel="stylesheet">\n',
    )

    validate_learning_deck_artifact(index_html=html, source_notes_md=VALID_SOURCE_NOTES)


def test_validate_learning_deck_artifact_accepts_presentation_runtime_scripts() -> None:
    html = VALID_INDEX_HTML.replace(
        '<script src="https://cdn.jsdelivr.net/npm/reveal.js@6.0.1/dist/reveal.js"></script>',
        "\n".join(
            [
                '<script src="https://cdn.jsdelivr.net/npm/reveal.js@6.0.1/dist/reveal.js"></script>',
                '<script src="https://cdn.jsdelivr.net/npm/react@18/umd/react.production.min.js"></script>',
                (
                    '<script src="https://cdn.jsdelivr.net/npm/react-dom@18/umd/'
                    'react-dom.production.min.js"></script>'
                ),
                '<script src="https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js"></script>',
                '<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>',
                '<script src="assets/deck.js"></script>',
            ]
        ),
    )

    validate_learning_deck_artifact(index_html=html, source_notes_md=VALID_SOURCE_NOTES)


def test_validate_learning_deck_artifact_rejects_unversioned_reveal_script() -> None:
    html = VALID_INDEX_HTML.replace("reveal.js@6.0.1/dist/reveal.js", "reveal.js/dist/reveal.js")

    with pytest.raises(LearningDeckArtifactError, match="disallowed script source"):
        validate_learning_deck_artifact(index_html=html, source_notes_md=VALID_SOURCE_NOTES)


def test_validate_learning_deck_artifact_rejects_unsupported_reveal_stylesheet() -> None:
    html = VALID_INDEX_HTML.replace(
        "reveal.js@6.0.1/dist/reveal.css", "reveal.js@5.2.1/dist/reveal.css"
    )

    with pytest.raises(LearningDeckArtifactError, match="pin Reveal.js stylesheets"):
        validate_learning_deck_artifact(index_html=html, source_notes_md=VALID_SOURCE_NOTES)


def test_validate_learning_deck_artifact_rejects_missing_source_notes() -> None:
    with pytest.raises(LearningDeckArtifactError, match="source-notes.md is empty"):
        validate_learning_deck_artifact(index_html=VALID_INDEX_HTML, source_notes_md="")


def test_validate_learning_deck_artifact_rejects_stock_reveal_styling() -> None:
    html = VALID_INDEX_HTML.replace(
        """  <style>
    :root { --deck-bg: #11110f; --deck-accent: #c77d3a; }
    .reveal { background: var(--deck-bg); color: #f4f0e8; }
  </style>
""",
        "",
    )

    with pytest.raises(LearningDeckArtifactError, match="custom deck styling"):
        validate_learning_deck_artifact(index_html=html, source_notes_md=VALID_SOURCE_NOTES)


def test_validate_learning_deck_artifact_rejects_arbitrary_external_script() -> None:
    html = VALID_INDEX_HTML.replace(
        "https://cdn.jsdelivr.net/npm/reveal.js@6.0.1/dist/reveal.js",
        "https://evil.example/script.js",
    )

    with pytest.raises(LearningDeckArtifactError, match="disallowed script source"):
        validate_learning_deck_artifact(index_html=html, source_notes_md=VALID_SOURCE_NOTES)


def test_validate_learning_deck_artifact_rejects_cdn_substring_bypass() -> None:
    html = VALID_INDEX_HTML.replace(
        "https://cdn.jsdelivr.net/npm/reveal.js@6.0.1/dist/reveal.js",
        "https://evil.example/cdn.jsdelivr.net/npm/react@18/x.js",
    )

    with pytest.raises(LearningDeckArtifactError, match="disallowed script source"):
        validate_learning_deck_artifact(index_html=html, source_notes_md=VALID_SOURCE_NOTES)


def test_validate_learning_deck_artifact_rejects_browser_transpiler_runtime() -> None:
    html = VALID_INDEX_HTML.replace(
        "https://cdn.jsdelivr.net/npm/reveal.js@6.0.1/dist/reveal.js",
        "https://cdn.jsdelivr.net/npm/@babel/standalone/babel.min.js",
    )

    with pytest.raises(LearningDeckArtifactError, match="disallowed script source"):
        validate_learning_deck_artifact(index_html=html, source_notes_md=VALID_SOURCE_NOTES)


def test_validate_learning_deck_artifact_rejects_inline_event_handlers() -> None:
    html = VALID_INDEX_HTML.replace(
        "<section>Intro</section>",
        '<section onclick="x()">Intro</section>',
    )

    with pytest.raises(LearningDeckArtifactError, match="inline event-handler"):
        validate_learning_deck_artifact(index_html=html, source_notes_md=VALID_SOURCE_NOTES)


def test_render_source_notes_html_sanitizes_scripts() -> None:
    rendered = render_source_notes_html(
        "# Source Notes\n\n## Sources\n\n<script>alert(1)</script>\n\nUseful notes.",
        title="Notes",
    )

    assert "<script" not in rendered.lower()
    assert "Useful notes." in rendered


def test_store_learning_deck_artifact_tracks_thumbnail_in_bundle(tmp_path) -> None:
    gateway = LocalObjectStorageGateway(tmp_path)

    stored = store_learning_deck_artifact(
        user_id=7,
        deck_id=41,
        run_id=82,
        index_html=VALID_INDEX_HTML,
        source_notes_md=VALID_SOURCE_NOTES,
        source_notes_html="<html><body>Sources</body></html>",
        extra_assets={LEARNING_DECK_THUMBNAIL_PATH: (b"thumbnail-png", "image/png")},
        gateway=gateway,
    )

    assert stored.thumbnail_object_key == (
        f"{stored.storage_prefix}/{LEARNING_DECK_THUMBNAIL_PATH}"
    )
    assert stored.thumbnail_object_key in stored.artifact_object_keys
    assert gateway.get_bytes(key=stored.thumbnail_object_key) == b"thumbnail-png"
