"""Tests for expanded admin dashboard readouts."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

from app.core.deps import require_admin
from app.main import app
from app.models.db import Content, OnboardingDiscoveryRun, ProcessingTask, VendorUsageRecord
from app.models.db.users import User


def test_admin_dashboard_shows_operational_readouts(client, db_session, test_user) -> None:
    """Dashboard should render queue, scraper, and user lifecycle readout sections."""

    def override_require_admin():
        return test_user

    app.dependency_overrides[require_admin] = override_require_admin
    try:
        now = datetime.now(UTC)

        other_user = User(
            apple_id="test_apple_other",
            email="other@example.com",
            full_name="Other User",
            is_active=False,
        )
        db_session.add(other_user)
        db_session.flush()

        db_session.add(
            Content(
                content_type="article",
                url="https://example.com/missing-summary",
                title="Missing Summary",
                source="test",
                status="failed",
                error_message="summary generation failed",
                content_metadata={},
            )
        )

        db_session.add_all(
            [
                ProcessingTask(
                    task_type="analyze_url",
                    queue_name="content",
                    status="pending",
                    created_at=now,
                ),
                ProcessingTask(
                    task_type="onboarding_discover",
                    queue_name="onboarding",
                    status="processing",
                    created_at=now,
                ),
                ProcessingTask(
                    task_type="summarize",
                    queue_name="content",
                    status="failed",
                    created_at=now,
                    completed_at=now,
                    error_message="Timeout while calling model",
                ),
            ]
        )

        db_session.add_all(
            [
                OnboardingDiscoveryRun(
                    user_id=test_user.id,
                    status="completed",
                    topic_summary="AI news",
                    inferred_topics=["ai"],
                    lane_summary="done",
                    created_at=now,
                    completed_at=now,
                ),
                OnboardingDiscoveryRun(
                    user_id=other_user.id,
                    status="failed",
                    topic_summary="security",
                    inferred_topics=["security"],
                    lane_summary="failed",
                    created_at=now,
                ),
            ]
        )
        db_session.commit()

        response = client.get("/admin/")
        assert response.status_code == 200
        body = response.text

        assert "Queue Status" in body
        assert "Task Phases" in body
        assert "Recent Failures (24h)" in body
        assert "Scraper Health (24h)" in body
        assert "Queue Watchdog (24h)" in body
        assert "User Lifecycle" in body

        assert "onboarding" in body
        assert "Tutorial Done" in body
    finally:
        app.dependency_overrides.pop(require_admin, None)


def test_admin_dashboard_shows_cost_analysis(client, db_session, test_user) -> None:
    """Dashboard should render tracked LLM cost rollups and per-user costs."""

    def override_require_admin():
        return test_user

    app.dependency_overrides[require_admin] = override_require_admin
    try:
        now = datetime.now(UTC)
        other_user = User(
            apple_id="cost_other",
            email="cost-other@example.com",
            full_name="Cost Other",
            is_active=True,
        )
        db_session.add(other_user)
        db_session.flush()

        db_session.add_all(
            [
                VendorUsageRecord(
                    provider="openai",
                    model="gpt-5.4",
                    feature="chat",
                    operation="chat.async",
                    user_id=test_user.id,
                    cost_usd=cast(Any, Decimal("0.5")),
                    total_tokens=1000,
                    currency="USD",
                    metadata_json={},
                    created_at=now,
                ),
                VendorUsageRecord(
                    provider="openai",
                    model="gpt-5.4-mini",
                    feature="summarization",
                    operation="summarization.llm_summarization",
                    user_id=test_user.id,
                    cost_usd=cast(Any, Decimal("0.75")),
                    total_tokens=800,
                    currency="USD",
                    metadata_json={},
                    created_at=now,
                ),
                VendorUsageRecord(
                    provider="google",
                    model="gemini-3.1-flash-lite-preview",
                    feature="news_processing",
                    operation="news_processing.summarize_short_form",
                    user_id=other_user.id,
                    cost_usd=cast(Any, Decimal("0.25")),
                    total_tokens=400,
                    currency="USD",
                    metadata_json={},
                    created_at=now,
                ),
                VendorUsageRecord(
                    provider="google",
                    model="gemini-3.1-flash-image-preview",
                    feature="image_generation",
                    operation="image_generation.infographic",
                    cost_usd=cast(Any, Decimal("1.0")),
                    request_count=1,
                    currency="USD",
                    metadata_json={},
                    created_at=now,
                ),
                VendorUsageRecord(
                    provider="x",
                    model="posts.read",
                    feature="x_api",
                    operation="x_api.fetch_tweet_by_id",
                    cost_usd=cast(Any, Decimal("0.4")),
                    request_count=1,
                    resource_count=20,
                    currency="USD",
                    metadata_json={},
                    created_at=now,
                ),
                VendorUsageRecord(
                    provider="firecrawl",
                    model="scrape-v2",
                    feature="html_extraction",
                    operation="firecrawl_scrape",
                    cost_usd=cast(Any, Decimal("0.00083")),
                    request_count=1,
                    resource_count=1,
                    currency="USD",
                    metadata_json={},
                    created_at=now,
                ),
            ]
        )
        db_session.commit()

        response = client.get("/admin/?cost_bucket=week")
        assert response.status_code == 200
        body = response.text

        assert "Cost Analysis" in body
        assert "Tracked LLM areas: chat, summarization, and image generation." in body
        assert "Cost By Area" in body
        assert "Top User Cost" in body
        assert "Recent Weekly Cost" in body
        assert "External API Providers" in body
        assert "Avg / Week" in body
        assert "Chat" in body
        assert "Summarization" in body
        assert "Image Generation" in body
        assert "X API" in body
        assert "Firecrawl" in body
        assert "cost-other@example.com" in body
        assert "$2.500000" in body
        assert "$1.500000" in body
        assert "$1.000000" in body
        assert "$0.000830" in body
    finally:
        app.dependency_overrides.pop(require_admin, None)
