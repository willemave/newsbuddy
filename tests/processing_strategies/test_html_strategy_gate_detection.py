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


def test_detect_access_gate_from_bloomberg_robot_page() -> None:
    """Bloomberg robot-check pages should not be treated as article text."""
    reason = HtmlProcessorStrategy._detect_access_gate(  # pylint: disable=protected-access
        title="Bloomberg",
        text_content=(
            "We've detected unusual activity from your computer network. "
            "To continue, please click the box below to let us know you're not a robot. "
            "Please make sure your browser supports JavaScript and cookies and that you "
            "are not blocking them from loading."
        ),
        html_content="<html><body>Bloomberg robot check</body></html>",
    )

    assert reason is not None
    assert reason.startswith("access gate detected")


def test_detect_access_gate_from_quick_check_robot_page() -> None:
    """Short generic bot checks should not be treated as article text."""
    reason = HtmlProcessorStrategy._detect_access_gate(  # pylint: disable=protected-access
        title="Just a quick check",
        text_content=("Just a quick check. Please click below to let us know you're not a robot."),
        html_content="<html><body>Just a quick check</body></html>",
    )

    assert reason is not None
    assert reason.startswith("access gate detected")


def test_detect_access_gate_from_short_forbidden_page() -> None:
    """A short 403 body should be retried or marked as blocked, not summarized."""
    reason = HtmlProcessorStrategy._detect_access_gate(  # pylint: disable=protected-access
        title="Silverback Imfura took a chance, and ended up alone",
        text_content="# 403 Forbidden",
        html_content="<html><body>403 Forbidden</body></html>",
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
            "<html><body><article>Long-form analysis about AI financing.</article></body></html>"
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


def test_detect_extraction_issue_for_short_paywall_prompt_with_real_title() -> None:
    """Short subscription prompts should fail even when metadata includes the article title."""
    reason = HtmlProcessorStrategy._detect_extraction_issue(  # pylint: disable=protected-access
        url="https://www.theinformation.com/articles/example-story",
        title="OpenAI Is Making Billions Just by Promising to Buy From Suppliers",
        text_content=(
            "Subscribe to read the full article. Join high-powered tech and business "
            "leaders who read The Information every day."
        ),
        html_content="<html><body>Subscribe to read the full article</body></html>",
    )

    assert reason == "access restricted: paywall/subscription prompt"
