use super::{WeeklySeed, short_digest, weekly_message};

#[test]
fn weekly_seed_without_signal_is_still_actionable() {
    let message = weekly_message(&WeeklySeed {
        local_date: "2026-08-30".to_owned(),
        week_key: "weekly:2026-08-30".to_owned(),
        week_label: "2026-08-30".to_owned(),
        topic_summary: None,
        inferred_topics: Vec::new(),
        recent_reads: Vec::new(),
        feed_options: Vec::new(),
    });
    assert!(message.contains("Ask me for a topic"));
}

#[test]
fn feed_option_ids_are_short_and_stable() {
    assert_eq!(
        short_digest("https://example.com/feed"),
        short_digest("https://example.com/feed")
    );
    assert_eq!(short_digest("https://example.com/feed").len(), 16);
}
