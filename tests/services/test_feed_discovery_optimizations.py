from types import SimpleNamespace

from app.models.llm.feed_discovery import DiscoveryCandidate
from app.services import feed_discovery


def test_sanitize_candidate_url_removes_markdown():
    raw = "[RSS](https://example.com/feed.xml))"
    assert feed_discovery._sanitize_candidate_url(raw) == "https://example.com/feed.xml"


def test_skip_candidate_on_known_host():
    candidate = DiscoveryCandidate(
        title="Tracking",
        site_url="https://link.chtbl.com/rss.xml",
        rationale="Skip tracking host",
    )

    assert feed_discovery._normalize_candidate(candidate) is None


def test_domain_attempt_cap_limits_validation(db_session, test_user, monkeypatch):
    class _StubDetector:
        def __init__(self, *args, **kwargs):
            pass

        def validate_feed_url(self, feed_url):
            return {"feed_url": feed_url, "title": "Test"}

        def classify_feed_type(self, **_kwargs):
            return SimpleNamespace(feed_type="atom")

        def detect_from_links(self, *args, **kwargs):
            return None

    monkeypatch.setattr(feed_discovery, "FeedDetector", _StubDetector)
    monkeypatch.setattr(feed_discovery, "DISCOVERY_DOMAIN_ATTEMPT_LIMIT", 1)

    candidates = [
        DiscoveryCandidate(
            title="One",
            site_url="https://example.com/a",
            feed_url="https://example.com/feed.xml",
            rationale="First candidate",
        ),
        DiscoveryCandidate(
            title="Two",
            site_url="https://example.com/b",
            feed_url="https://example.com/feed2.xml",
            rationale="Second candidate",
        ),
    ]

    validated = feed_discovery._validate_and_filter_candidates(
        db_session, test_user.id, candidates, model_spec="test"
    )

    assert len(validated) == 1
