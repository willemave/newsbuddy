from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = REPO_ROOT / "client/newsly/newsly"


def test_assistant_feed_cards_honor_live_server_subscription_state() -> None:
    model_source = (APP_ROOT / "Models/ChatMessage.swift").read_text()
    action_source = (APP_ROOT / "Views/Chat/AssistantFeedOptionsSection.swift").read_text()

    assert "isSubscribed = response.isSubscribed" in model_source
    assert "option.isSubscribed || subscribedOptionIds.contains(option.id)" in action_source
    assert ".disabled(actionModel.isSubscribed(option)" in action_source
    assert 'option.isSubscribed ? "Already subscribed" : "Added"' in action_source
    assert "config.subscriptionOutcome == .reactivated" in action_source
    assert 'subscriptionLabels[option.id] = "Re-enabled"' in action_source


def test_assistant_feed_subscription_state_is_backward_compatible() -> None:
    generated_source = (APP_ROOT / "Models/Generated/APIModels.generated.swift").read_text()
    mixed_search_model = generated_source.split(
        "struct APIMixedSearchFeedResultResponse: Codable {",
        1,
    )[1].split("\nstruct ", 1)[0]

    expected_fallback = (
        "isSubscribed = try container.decodeIfPresent(Bool.self, forKey: .isSubscribed) ?? false"
    )
    assert expected_fallback in mixed_search_model
