"""Tests for admin onboarding lane preview routes."""

from app.core.deps import require_admin
from app.main import app
from app.models.api.common import OnboardingAudioLanePreview, OnboardingAudioLanePreviewResponse


def test_admin_onboarding_lane_preview_page_requires_admin_session(client):
    """Preview page should redirect to admin login without admin session."""
    response = client.get("/admin/onboarding/lane-preview", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/auth/admin/login?next=")


def test_admin_onboarding_lane_preview_api_requires_admin_session(client):
    """Preview API should redirect to admin login without admin session."""
    response = client.post(
        "/admin/onboarding/lane-preview",
        json={"transcript": "AI policy and climate tech updates", "locale": "en-US"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/auth/admin/login?next=")


def test_admin_onboarding_lane_preview_api_returns_plan(client, test_user, monkeypatch):
    """Preview API should return generated lane plan for authenticated admin."""

    def override_require_admin():
        return test_user

    async def fake_preview_audio_lane_plan(_payload):
        return OnboardingAudioLanePreviewResponse(
            topic_summary="AI policy and climate tech",
            inferred_topics=["AI policy", "climate tech"],
            lanes=[
                OnboardingAudioLanePreview(
                    name="Reddit",
                    goal="Find active communities.",
                    target="reddit",
                    queries=["AI policy subreddit", "best subreddits for climate tech"],
                    include_social=True,
                    exa_results_per_query=3,
                )
            ],
            used_fallback=False,
            fallback_reason=None,
        )

    app.dependency_overrides[require_admin] = override_require_admin
    monkeypatch.setattr("app.routers.admin.preview_audio_lane_plan", fake_preview_audio_lane_plan)

    response = client.post(
        "/admin/onboarding/lane-preview",
        json={"transcript": "I want AI policy updates", "locale": "en-US"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["topic_summary"] == "AI policy and climate tech"
    assert data["used_fallback"] is False
    assert data["lanes"][0]["target"] == "reddit"
    assert data["lanes"][0]["include_social"] is True
