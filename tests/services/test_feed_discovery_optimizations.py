from contextlib import contextmanager
from types import SimpleNamespace

from app.models.db import UserScraperConfig
from app.models.llm.feed_discovery import DiscoveryCandidate
from app.services import feed_discovery, feed_discovery_candidates


def test_sanitize_candidate_url_removes_markdown():
    raw = "[RSS](https://example.com/feed.xml))"
    assert feed_discovery_candidates._sanitize_candidate_url(raw) == "https://example.com/feed.xml"


def test_skip_candidate_on_known_host():
    candidate = DiscoveryCandidate(
        title="Tracking",
        site_url="https://link.chtbl.com/rss.xml",
        rationale="Skip tracking host",
    )

    assert feed_discovery_candidates._normalize_candidate(candidate) is None


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
        db_session,
        test_user.id,
        candidates,
        model_spec="test",
        detector=_StubDetector(),
    )

    assert len(validated) == 1


def test_default_candidate_validation_uses_feed_sandbox_runtime(
    db_session,
    test_user,
    monkeypatch,
) -> None:
    class _StubDetector:
        def validate_feed_url(self, feed_url):
            return {"feed_url": feed_url, "feed_format": "rss", "title": "Test"}

        def classify_feed_type(self, **_kwargs):
            return SimpleNamespace(feed_type="atom")

    runtime_user_ids: list[int] = []

    @contextmanager
    def _runtime(*, user_id: int):
        runtime_user_ids.append(user_id)
        yield SimpleNamespace(detector=_StubDetector())

    monkeypatch.setattr(feed_discovery, "feed_research_runtime", _runtime)
    candidate = DiscoveryCandidate(
        title="Example",
        site_url="https://example.com",
        feed_url="https://example.com/feed.xml",
        rationale="Validated sandbox candidate",
    )

    validated = feed_discovery._validate_candidates_in_feed_sandbox(
        db_session,
        test_user.id,
        [candidate],
        model_spec="test",
    )

    assert runtime_user_ids == [test_user.id]
    assert [item.feed_url for item in validated] == ["https://example.com/feed.xml"]


def test_subscribed_candidate_is_returned_without_revalidating_network(
    db_session,
    test_user,
) -> None:
    feed_url = "https://example.com/feed.xml"
    db_session.add(
        UserScraperConfig(
            user_id=test_user.id,
            scraper_type="atom",
            display_name="Existing",
            config={"feed_url": feed_url},
            feed_url=feed_url,
            is_active=True,
        )
    )
    db_session.commit()

    detector = SimpleNamespace(
        validate_feed_url=lambda *_args: (_ for _ in ()).throw(
            AssertionError("subscribed feeds should not be fetched again")
        )
    )
    validated = feed_discovery._validate_and_filter_candidates(
        db_session,
        test_user.id,
        [
            DiscoveryCandidate(
                title="Existing",
                site_url="https://example.com",
                feed_url=feed_url,
                suggestion_type="atom",
                rationale="Already subscribed",
            )
        ],
        model_spec="test",
        detector=detector,
    )

    assert [candidate.feed_url for candidate in validated] == [feed_url]
