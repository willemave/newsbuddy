"""Tests for HTML access-gate detection heuristics."""

from app.processing_strategies.html_strategy import HtmlProcessorStrategy


def test_detect_access_gate_from_javascript_notice() -> None:
    """JS-required gate pages should be flagged as extraction failures."""
    reason = HtmlProcessorStrategy._detect_access_gate(  # pylint: disable=protected-access
        title="[AINews] Anthropic's Agent Autonomy study - Latent.Space",
        text_content=(
            "This site requires JavaScript to run correctly. "
            "Please turn on JavaScript or unblock scripts."
        ),
        html_content="<html><body>This site requires JavaScript to run correctly.</body></html>",
    )

    assert reason is not None
    assert reason.startswith("access gate detected")


def test_detect_access_gate_ignores_normal_article_content() -> None:
    """Normal article content should not be mistaken for an access gate."""
    reason = HtmlProcessorStrategy._detect_access_gate(  # pylint: disable=protected-access
        title="Inside AI's $10B+ Capital Flywheel",
        text_content=(
            "Martin Casado and Sarah Wang discuss startup funding, compute contracts, "
            "model training loops, and enterprise go-to-market dynamics."
        ),
        html_content=(
            "<html><body><article>Long-form analysis about AI financing."
            "</article></body></html>"
        ),
    )

    assert reason is None


def test_detect_extraction_issue_for_discussion_block_with_js_wall() -> None:
    """Discussion-first extractions with a JS wall should be treated as malformed."""
    repeated_comment = (
        "The Man U thought experiment is a great framing, but the crowd details feel like "
        "narrative patches rather than a literal simulation of every fan in the stadium. "
    ) * 18
    reason = HtmlProcessorStrategy._detect_extraction_issue(  # pylint: disable=protected-access
        url="https://www.notboring.co/p/world-models",
        title="World Models: Computing the Uncomputable",
        text_content=(
            "#### Discussion about this post\n"
            "CommentsRestacks\n"
            f"{repeated_comment}\n"
            "This site requires JavaScript to run correctly. Please turn on JavaScript."
        ),
        html_content="<html><body>Discussion only payload</body></html>",
    )

    assert reason == "malformed extraction: discussion/comments block with javascript wall"


def test_detect_extraction_issue_ignores_explicit_comment_urls() -> None:
    """Direct comment pages should not be treated as malformed article extractions."""
    reason = HtmlProcessorStrategy._detect_extraction_issue(  # pylint: disable=protected-access
        url="https://www.notboring.co/p/world-models/comments",
        title="World Models discussion",
        text_content="#### Discussion about this post\nCommentsRestacks\nThread text only.",
        html_content="<html><body>Discussion page</body></html>",
    )

    assert reason is None


def test_detect_extraction_issue_for_placeholder_paywall_title() -> None:
    """Short paywalled placeholder pages should be treated as malformed."""
    reason = HtmlProcessorStrategy._detect_extraction_issue(  # pylint: disable=protected-access
        url="https://www.wsj.com/tech/ai/example-story",
        title="Subscribe to read",
        text_content="Subscribe to read. Sign in to continue reading this article.",
        html_content="<html><head><title>Subscribe to read</title></head></html>",
    )

    assert reason == "blocked/paywalled placeholder title"
