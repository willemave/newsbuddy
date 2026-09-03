use newsly_contracts::{ShareActionAgentResult, ShareActionBriefingTarget};
use newsly_db::ShareActionAgentSnapshot;
use newsly_providers::{ValidatedFeed, ValidatedFeedFormat};
use serde_json::{Map, Value};

use super::{ShareActionHostInput, build_host_action, validated_scraper_type};

#[test]
fn add_feed_action_uses_the_host_validated_url_and_format() {
    let snapshot = snapshot("add_feed");
    let result = ShareActionAgentResult {
        action: "add_feed".to_owned(),
        primary_url: Some("https://this-week-in-rust.org/".to_owned()),
        feed_url: Some("https://untrusted.example/feed".to_owned()),
        content_urls: Vec::new(),
        presentation: None,
        chat: None,
        briefing_target: None,
        title: Some("This Week in Rust".to_owned()),
        platform: None,
        content_type: None,
        rationale: Some("The feed was validated".to_owned()),
        sources_used: Vec::new(),
        confidence: Some(1.0),
    };
    let validated = ValidatedFeed {
        effective_url: "https://this-week-in-rust.org/rss.xml".to_owned(),
        format: ValidatedFeedFormat::Rss,
        has_audio_entries: false,
    };

    let action = build_host_action(&snapshot, &result, Some(&validated))
        .expect("validated result should build")
        .expect("add_feed should produce a host action");
    let ShareActionHostInput::Feed(input) = action.typed_input else {
        panic!("add_feed should produce feed input");
    };
    assert_eq!(input.url, validated.effective_url);
    assert_eq!(input.feed_type.as_deref(), Some("atom"));
    assert_eq!(input.feed_format.as_deref(), Some("rss"));
    assert_eq!(
        action
            .action_input
            .get("feed_format")
            .and_then(Value::as_str),
        Some("rss")
    );
}

#[test]
fn add_feed_action_rejects_an_unvalidated_model_url() {
    let result = ShareActionAgentResult {
        action: "add_feed".to_owned(),
        primary_url: None,
        feed_url: Some("https://example.test/feed.xml".to_owned()),
        content_urls: Vec::new(),
        presentation: None,
        chat: None,
        briefing_target: None,
        title: None,
        platform: None,
        content_type: None,
        rationale: None,
        sources_used: Vec::new(),
        confidence: None,
    };
    let error = build_host_action(&snapshot("add_feed"), &result, None)
        .expect_err("model-only feed URL must not become a host action");
    assert!(error.to_string().contains("missing host validation"));
}

#[test]
fn add_to_briefing_feed_uses_host_validated_subscription_input() {
    let result = ShareActionAgentResult {
        action: "add_to_briefing".to_owned(),
        primary_url: Some("https://example.test/".to_owned()),
        feed_url: None,
        content_urls: Vec::new(),
        presentation: None,
        chat: None,
        briefing_target: Some(ShareActionBriefingTarget::Feed {
            url: "https://untrusted.example/feed".to_owned(),
            title: Some("Example briefing source".to_owned()),
            platform: Some("podcast_rss".to_owned()),
            rationale: None,
        }),
        title: None,
        platform: None,
        content_type: None,
        rationale: None,
        sources_used: Vec::new(),
        confidence: Some(1.0),
    };
    let validated = ValidatedFeed {
        effective_url: "https://example.test/canonical.xml".to_owned(),
        format: ValidatedFeedFormat::Rss,
        has_audio_entries: false,
    };

    let action = build_host_action(&snapshot("add_to_briefing"), &result, Some(&validated))
        .expect("validated briefing feed should build")
        .expect("feed target should produce a host action");
    let ShareActionHostInput::Briefing(super::BriefingActionInput::Feed(input)) =
        action.typed_input
    else {
        panic!("briefing feed should remain a typed feed target");
    };
    assert_eq!(input.url, validated.effective_url);
    assert_eq!(input.feed_type.as_deref(), Some("atom"));
    assert_eq!(input.feed_format.as_deref(), Some("rss"));

    let error = build_host_action(&snapshot("add_to_briefing"), &result, None)
        .expect_err("briefing feed must not trust the model URL without host validation");
    assert!(error.to_string().contains("missing host validation"));
}

#[test]
fn parsed_audio_evidence_wins_over_a_substack_host() {
    let validated = ValidatedFeed {
        effective_url: "https://podcast.substack.com/feed".to_owned(),
        format: ValidatedFeedFormat::Rss,
        has_audio_entries: true,
    };
    assert_eq!(validated_scraper_type(&validated), "podcast_rss");
}

fn snapshot(mode: &str) -> ShareActionAgentSnapshot {
    ShareActionAgentSnapshot {
        id: 7,
        user_id: 4,
        mode: mode.to_owned(),
        workflow_key: format!("share_action.{mode}.v1"),
        approval_policy: Map::new(),
        allowed_actions: vec!["subscribe_to_feed".to_owned()],
        tool_policy: Map::new(),
        input: Map::from_iter([(
            "url".to_owned(),
            Value::from("https://this-week-in-rust.org/"),
        )]),
        workspace_path: "/data/workspace/tasks/7".to_owned(),
    }
}
