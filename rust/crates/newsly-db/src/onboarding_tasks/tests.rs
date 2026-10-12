#[path = "feed_tests.rs"]
mod feed_tests;

use sqlx::PgPool;

use super::{WeeklySeed, short_digest, weekly_message};

#[sqlx::test]
async fn stopped_legacy_writes_have_safe_schema_defaults(pool: PgPool) {
    let columns = sqlx::query_as::<_, (String, String, Option<String>)>(
        r#"
        SELECT table_name, column_name, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND (table_name, column_name) IN (
              ('feed_discovery_runs', 'seed_content_ids'),
              ('feed_discovery_suggestions', 'metadata'),
              ('briefing_segments', 'warnings')
          )
        ORDER BY table_name, column_name
        "#,
    )
    .fetch_all(&pool)
    .await
    .expect("legacy-column defaults should be queryable");

    assert_eq!(columns.len(), 3);
    assert!(columns.iter().any(|(table, column, default)| {
        table == "feed_discovery_runs"
            && column == "seed_content_ids"
            && default.as_deref().is_some_and(|value| value.contains("[]"))
    }));
    assert!(columns.iter().any(|(table, column, default)| {
        table == "feed_discovery_suggestions"
            && column == "metadata"
            && default.as_deref().is_some_and(|value| value.contains("{}"))
    }));
    assert!(columns.iter().any(|(table, column, default)| {
        table == "briefing_segments" && column == "warnings" && default.is_none()
    }));
}

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
