use super::*;
use chrono::{TimeZone, Utc};

fn content_source(content_type: &str, metadata: Value) -> ContentBriefingSourceProjection {
    ContentBriefingSourceProjection {
        id: 7,
        content_type: content_type.into(),
        url: "https://example.com/item".into(),
        source_url: None,
        title: Some("A Work".into()),
        source: Some("  Latent Space ".into()),
        metadata,
        source_char_count: Some(14_000),
        created_at: Utc.with_ymd_and_hms(2026, 9, 25, 12, 0, 0).unwrap(),
        publication_date: None,
    }
}

#[test]
fn article_sources_carry_publisher_and_reading_minutes() {
    let source = present_content_source(
        "content:7",
        &content_source("article", json!({})),
        false,
        "r",
    )
    .unwrap();
    assert_eq!(source.publisher.as_deref(), Some("Latent Space"));
    assert_eq!(source.reading_minutes, Some(12));
    assert_eq!(source.duration_seconds, None);
}

#[test]
fn content_publication_date_never_falls_back_to_import_time() {
    for metadata in [json!({}), json!({"tweet_created_at": "invalid"})] {
        let mut projection = content_source("article", metadata);
        projection.url = "https://x.com/author/status/123".into();
        let source = present_content_source("content:7", &projection, false, "r").unwrap();
        assert_eq!(source.published_at, None);
        assert!(serde_json::to_value(source).unwrap()["published_at"].is_null());
    }
    let mut projection = content_source(
        "article",
        json!({"tweet_created_at": "2023-04-01T12:00:00Z"}),
    );
    projection.url = "https://x.com/author/status/123".into();
    let original = Utc.with_ymd_and_hms(2023, 4, 1, 12, 0, 0).unwrap();
    assert_eq!(
        present_content_source("content:7", &projection, false, "r")
            .unwrap()
            .published_at,
        Some(original)
    );
    projection.publication_date = Some(original + chrono::Duration::days(1));
    assert_eq!(
        present_content_source("content:7", &projection, false, "r")
            .unwrap()
            .published_at,
        Some(original)
    );
}

#[test]
fn synthetic_sql_dates_and_inherited_tweet_metadata_do_not_mislabel_articles() {
    let mut projection = content_source(
        "article",
        json!({
            "tweet_id":"123", "tweet_author":"X Author", "tweet_created_at":"2023-04-01T12:00:00Z"
        }),
    );
    projection.source_url = Some("https://x.com/author/status/123".into());
    projection.publication_date = Some(projection.created_at);
    let source = present_content_source("content:7", &projection, false, "r").unwrap();
    assert_eq!(source.published_at, None);
    assert_eq!(source.publisher.as_deref(), Some("Latent Space"));
    projection.publication_date = Some(Utc.with_ymd_and_hms(2025, 12, 1, 12, 0, 0).unwrap());
    assert_eq!(
        present_content_source("content:7", &projection, false, "r")
            .unwrap()
            .published_at,
        projection.publication_date
    );
    projection.metadata["publication_date"] = json!(projection.created_at.to_rfc3339());
    assert_eq!(
        present_content_source("content:7", &projection, false, "r")
            .unwrap()
            .published_at,
        Some(projection.created_at)
    );
    projection.url = "https://x.com/author/status/123".into();
    assert_eq!(
        present_content_source("content:7", &projection, false, "r")
            .unwrap()
            .published_at,
        Some(Utc.with_ymd_and_hms(2023, 4, 1, 12, 0, 0).unwrap())
    );
    projection.metadata = json!({});
    assert_eq!(
        present_content_source("content:7", &projection, false, "r")
            .unwrap()
            .published_at,
        None
    );
}

#[test]
fn x_sources_use_author_metadata_instead_of_submission_provenance() {
    for (metadata, expected) in [
        (
            json!({"tweet_author":"Author", "tweet_author_username":"writer"}),
            "Author (@writer)",
        ),
        (json!({"tweet_author_username":"@writer"}), "@writer"),
        (json!({"tweet_author":"Author"}), "Author"),
        (json!({}), "X"),
        (
            json!({"tweet_snapshot":{"author_name":"Author", "author_username":"writer"}}),
            "Author (@writer)",
        ),
    ] {
        let mut projection = content_source("article", metadata);
        projection.url = "https://x.com/writer/status/123".into();
        projection.source = Some("self submission".into());
        let source = present_content_source("content:7", &projection, false, "r").unwrap();
        assert_eq!(source.publisher.as_deref(), Some(expected));
    }
}

#[test]
fn x_publication_dates_accept_native_snapshots_and_legacy_utc() {
    for metadata in [
        json!({"tweet_created_at":"2023-04-01T12:00:00"}),
        json!({"tweet_created_at":"2023-04-01T14:00:00+02:00"}),
        json!({"tweet_snapshot":{"created_at":"2023-04-01T12:00:00Z"}}),
    ] {
        let mut projection = content_source("article", metadata);
        projection.url = "https://x.com/author/status/123".into();
        let source = present_content_source("content:7", &projection, false, "r").unwrap();
        assert_eq!(
            source.published_at,
            Some(Utc.with_ymd_and_hms(2023, 4, 1, 12, 0, 0).unwrap())
        );
    }
}

#[test]
fn podcast_sources_carry_duration_but_not_reading_minutes() {
    let projection = content_source("podcast", json!({"duration_seconds": "3939"}));
    let source = present_content_source("content:7", &projection, false, "r").unwrap();
    assert_eq!(source.duration_seconds, Some(3939));
    assert_eq!(source.reading_minutes, None);

    let projection = content_source("podcast", json!({"duration_seconds": 0}));
    let source = present_content_source("content:7", &projection, false, "r").unwrap();
    assert_eq!(source.duration_seconds, None);
}

#[test]
fn briefing_and_content_views_share_generated_headlines_without_changing_provenance() {
    for kind in ["article", "podcast"] {
        for (summary, expected) in [
            (
                json!({"title":"Reader headline (with a meaningful qualifier)", "feed_preview":{"title":"Alternate preview"}}),
                "Reader headline (with a meaningful qualifier)",
            ),
            (
                json!({"title":" ", "feed_preview":{"title":"Preview headline"}}),
                "Preview headline",
            ),
            (json!({"title":"Access denied"}), "A Work"),
        ] {
            let projection = content_source(kind, json!({"domain":{"summary":summary}}));
            let briefing = present_content_source("content:7", &projection, false, "r").unwrap();
            assert_eq!(briefing.title, expected);
            assert_eq!(projection.title.as_deref(), Some("A Work"));
        }
    }
}
