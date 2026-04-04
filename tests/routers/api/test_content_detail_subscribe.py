"""Tests for can_subscribe behavior in content detail response."""

import pytest

from app.constants import SELF_SUBMISSION_SOURCE
from app.models.metadata import ContentStatus, ContentType
from app.services.scraper_configs import CreateUserScraperConfig, create_user_scraper_config

pytestmark = pytest.mark.usefixtures("stub_valid_feed_url")


def test_can_subscribe_self_submission_true_when_missing_config(
    client,
    content_factory,
    status_entry_factory,
    test_user,
):
    metadata = {
        "source": SELF_SUBMISSION_SOURCE,
        "content_type": "html",
        "content": "Test",
        "detected_feed": {
            "url": "https://example.com/feed",
            "type": "atom",
            "title": "Example Feed",
            "format": "rss",
        },
    }
    content = content_factory(
        content_type=ContentType.ARTICLE.value,
        url="https://example.com/article",
        title="Example",
        source=SELF_SUBMISSION_SOURCE,
        status=ContentStatus.COMPLETED.value,
        content_metadata=metadata,
    )
    status_entry_factory(user=test_user, content=content, status="inbox")

    response = client.get(f"/api/content/{content.id}")
    assert response.status_code == 200
    data = response.json()
    assert data["can_subscribe"] is True


def test_can_subscribe_false_when_already_subscribed(
    client,
    db_session,
    content_factory,
    status_entry_factory,
    test_user,
):
    payload = CreateUserScraperConfig(
        scraper_type="atom",
        display_name="Example Feed",
        config={"feed_url": "https://example.com/feed"},
        is_active=True,
    )
    create_user_scraper_config(db_session, test_user.id, payload)

    metadata = {
        "source": SELF_SUBMISSION_SOURCE,
        "content_type": "html",
        "content": "Test",
        "detected_feed": {
            "url": "https://example.com/feed",
            "type": "atom",
            "title": "Example Feed",
            "format": "rss",
        },
    }
    content = content_factory(
        content_type=ContentType.ARTICLE.value,
        url="https://example.com/article",
        title="Example",
        source=SELF_SUBMISSION_SOURCE,
        status=ContentStatus.COMPLETED.value,
        content_metadata=metadata,
    )
    status_entry_factory(user=test_user, content=content, status="inbox")

    response = client.get(f"/api/content/{content.id}")
    assert response.status_code == 200
    data = response.json()
    assert data["can_subscribe"] is False


def test_can_subscribe_false_for_non_news_non_self_submission(
    client,
    content_factory,
    status_entry_factory,
    test_user,
):
    metadata = {
        "source": "web",
        "content_type": "html",
        "content": "Test",
        "detected_feed": {
            "url": "https://example.com/feed",
            "type": "atom",
            "title": "Example Feed",
            "format": "rss",
        },
    }
    content = content_factory(
        content_type=ContentType.ARTICLE.value,
        url="https://example.com/article",
        title="Example",
        source="web",
        status=ContentStatus.COMPLETED.value,
        content_metadata=metadata,
    )
    status_entry_factory(user=test_user, content=content, status="inbox")

    response = client.get(f"/api/content/{content.id}")
    assert response.status_code == 200
    data = response.json()
    assert data["can_subscribe"] is False


def test_can_subscribe_true_for_news_content(
    client,
    content_factory,
    status_entry_factory,
    test_user,
):
    metadata = {
        "source": "hackernews",
        "platform": "hackernews",
        "article": {
            "url": "https://example.com/story",
            "title": "Example Story",
            "source_domain": "example.com",
        },
        "detected_feed": {
            "url": "https://example.com/feed",
            "type": "atom",
            "title": "Example Feed",
            "format": "rss",
        },
    }
    content = content_factory(
        content_type=ContentType.NEWS.value,
        url="https://example.com/article",
        title="Example",
        source="hackernews",
        status=ContentStatus.COMPLETED.value,
        content_metadata=metadata,
    )
    status_entry_factory(user=test_user, content=content, status="inbox")

    response = client.get(f"/api/content/{content.id}")
    assert response.status_code == 200
    data = response.json()
    assert data["can_subscribe"] is True
