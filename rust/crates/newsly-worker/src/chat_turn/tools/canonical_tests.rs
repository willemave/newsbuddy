use std::collections::BTreeSet;

use newsly_db::FeedSubscriptionMutation;

use super::super::actions::{
    ConfirmedAction, ResolvedSource, fallback_summary, mark_subscribed_options,
};
use super::super::catalog::{ChatTool, HostTool, SandboxTool};
use super::*;
#[test]
fn empty_ledger_cannot_claim_completed_actions() {
    assert!(fallback_summary(&[]).is_none());
}
#[test]
fn fallback_preserves_already_existing_outcome() {
    let action = ConfirmedAction::Inbox {
        url: "https://example.test/item".into(),
        already_exists: true,
    };
    let text = fallback_summary(&[action]).unwrap();
    assert!(text.contains("already in your inbox"));
    assert!(!text.contains("42"));
    assert!(!text.contains('{'));
    assert!(!text.contains("subscribed"));
}
#[test]
fn canonical_schema_has_no_retired_aliases() {
    let names = ChatToolExecutor::definitions()
        .into_iter()
        .map(|d| d.name)
        .collect::<BTreeSet<_>>();
    for name in [
        "search_web",
        "read_web_page",
        "resolve_source",
        "search_content",
        "read_content",
        "set_read_state",
        "set_saved_state",
        "add_to_inbox",
        "subscribe_to_source",
        "execute_bash",
    ] {
        assert!(names.contains(name));
    }
    for name in [
        "exa_web_search",
        "find_feed_options",
        "search_knowledge",
        "search_news",
        "list_unread_news_items",
        "start_deep_research_handoff",
    ] {
        assert!(!names.contains(name));
    }
}
#[test]
fn content_and_news_ids_are_distinct_and_closed() {
    let content: Reference = serde_json::from_value(json!({"kind":"content","id":1})).unwrap();
    let news: Reference = serde_json::from_value(json!({"kind":"news_item","id":1})).unwrap();
    assert_ne!(
        serde_json::to_value(content).unwrap(),
        serde_json::to_value(news).unwrap()
    );
    assert!(
        serde_json::from_value::<Reference>(
            json!({"kind":"content","id":1,"url":"https://untrusted.test"})
        )
        .is_err()
    );
}

#[test]
fn catalog_preserves_identity_and_execution_policy() {
    let definitions = ChatToolExecutor::definitions();
    let names = definitions
        .iter()
        .map(|definition| definition.name.as_str())
        .collect::<BTreeSet<_>>();
    assert_eq!(names.len(), definitions.len());
    for tool in ChatTool::all() {
        assert!(names.contains(tool.name()));
        assert_eq!(ChatTool::parse(tool.name()), Some(tool));
        assert!(!tool.cacheable() || tool.read_only());
    }
    for host in [
        HostTool::SubscribeToSource,
        HostTool::SetReadState,
        HostTool::SetSavedState,
        HostTool::AddToInbox,
    ] {
        let tool = ChatTool::Host(host);
        assert!(!tool.read_only());
        assert!(!tool.cacheable());
    }
    for host in [
        HostTool::SearchWeb,
        HostTool::ReadWebPage,
        HostTool::ResolveSource,
    ] {
        let tool = ChatTool::Host(host);
        assert!(tool.read_only());
        assert!(tool.cacheable());
    }
    assert_eq!(
        SandboxTool::CopyContentToWorkspace.transport_name(),
        "write_knowledge_items"
    );
    assert_eq!(
        ChatTool::parse("copy_content_to_workspace"),
        Some(ChatTool::Sandbox(SandboxTool::CopyContentToWorkspace))
    );
    assert!(ChatTool::parse("write_knowledge_items").is_none());
    assert!(ChatTool::parse("unknown_tool").is_none());
}

#[test]
fn fallback_uses_source_names_and_links_without_database_ids() {
    let aggregator = ConfirmedAction::Subscribed {
        source: ResolvedSource::Aggregator(AggregatorKey::Arxiv),
        mutation: FeedSubscriptionMutation::AlreadySubscribed,
    };
    let feed = ConfirmedAction::Subscribed {
        source: ResolvedSource::Feed {
            url: "https://example.test/feed".into(),
            title: Some("Example".into()),
            kind: None,
            reference: None,
        },
        mutation: FeedSubscriptionMutation::Created,
    };
    let summary = fallback_summary(&[aggregator, feed]).unwrap();
    assert!(summary.contains("already follow arXiv"));
    assert!(summary.contains("https://example.test/feed"));
    assert!(!summary.contains("987"));
    assert!(!summary.contains("subscription"));
}

#[test]
fn committed_subscriptions_update_the_review_cards() {
    let key = AggregatorKey::ALL[0];
    let mut options = vec![aggregator_feed_option(key)];
    assert!(!options[0].is_subscribed);
    mark_subscribed_options(&mut options, &ResolvedSource::Aggregator(key));
    assert!(options[0].is_subscribed);
    options[0].feed_url = "https://example.test/Feed".into();
    options[0].is_subscribed = false;
    mark_subscribed_options(
        &mut options,
        &ResolvedSource::Feed {
            url: "HTTPS://EXAMPLE.TEST:443/Feed/".into(),
            title: None,
            kind: None,
            reference: None,
        },
    );
    assert!(options[0].is_subscribed);
}

#[test]
fn updated_aggregator_topics_are_reported_as_a_committed_change() {
    let action = ConfirmedAction::Subscribed {
        source: ResolvedSource::Aggregator(AggregatorKey::Arxiv),
        mutation: FeedSubscriptionMutation::TopicsUpdated,
    };
    let summary = fallback_summary(&[action]).unwrap();
    assert!(summary.contains("Updated the topic filter for arXiv."));
    assert!(!summary.contains("already follow"));
    assert!(!summary.contains("now follow"));
}
