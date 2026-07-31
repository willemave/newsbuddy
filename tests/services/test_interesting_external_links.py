"""Tests for article interesting external link curation."""

from app.models.metadata.summaries import InterestingExternalLink
from app.services import interesting_external_links as links


class FakeResult:
    def __init__(self, output: object) -> None:
        self.output = output

    @property
    def usage(self) -> None:
        return None


class FakeAgent:
    def __init__(self, output: object) -> None:
        self.output = output
        self.prompt: str | None = None
        self.model_settings: dict[str, object] | None = None

    def run_sync(self, prompt: str, model_settings=None) -> FakeResult:  # noqa: ANN001
        self.prompt = prompt
        self.model_settings = model_settings
        return FakeResult(self.output)


def test_extract_interesting_link_candidates_filters_same_site_share_and_tracking_links() -> None:
    text = """
    This article cites [the paper](https://papers.example.org/model?utm_source=newsletter)
    and [the source repo](https://github.com/example/project).
    Ignore [about us](https://example.com/about) and
    [share](https://twitter.com/intent/tweet?url=https://example.com/post).
    """

    candidates = links.extract_interesting_link_candidates(
        text,
        source_url="https://example.com/post",
    )

    assert [candidate.url for candidate in candidates] == [
        "https://papers.example.org/model",
        "https://github.com/example/project",
    ]
    assert candidates[0].title == "the paper"


def test_select_interesting_external_links_rejects_non_candidate_model_urls(
    monkeypatch,
) -> None:
    output = links.InterestingExternalLinksSelection(
        links=[
            InterestingExternalLink(
                url="https://papers.example.org/model",
                title="Original model paper",
                reason="Primary source for the article's core claim.",
                category="primary_source",
                confidence=0.94,
            ),
            InterestingExternalLink(
                url="https://hallucinated.example.com",
                title="Not in article",
                reason="The model should not be allowed to invent this.",
                category="other",
                confidence=0.8,
            ),
        ]
    )
    fake_agent = FakeAgent(output)

    monkeypatch.setattr(links, "resolve_model", lambda *_args: ("openai", "openai:test"))
    monkeypatch.setattr(links, "get_basic_agent", lambda *_args: fake_agent)

    selected = links.select_interesting_external_links(
        "See [the paper](https://papers.example.org/model) for details.",
        source_url="https://example.com/post",
        title="Example article",
    )

    assert [link.url for link in selected] == ["https://papers.example.org/model"]
    assert selected[0].title == "Original model paper"
    assert fake_agent.prompt is not None
    assert "https://papers.example.org/model" in fake_agent.prompt
    assert fake_agent.model_settings == {"timeout": links.LINK_SELECTION_TIMEOUT_SECONDS}


def test_select_interesting_external_links_fails_closed_on_timeout(monkeypatch) -> None:
    class TimeoutAgent:
        def run_sync(self, prompt: str, model_settings=None) -> FakeResult:  # noqa: ANN001
            del model_settings
            raise TimeoutError("selection timed out")

    monkeypatch.setattr(links, "resolve_model", lambda *_args: ("openai", "openai:test"))
    monkeypatch.setattr(links, "get_basic_agent", lambda *_args: TimeoutAgent())

    selected = links.select_interesting_external_links(
        "See [the paper](https://papers.example.org/model) for details.",
        source_url="https://example.com/post",
        title="Example article",
    )

    assert selected == []
