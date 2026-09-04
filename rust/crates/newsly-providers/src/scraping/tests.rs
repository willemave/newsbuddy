use super::{
    AggregatorKey, FeedScrapeTarget, ScrapeGatewayError, ScrapedItem, exceeds_response_limit,
    is_external_reddit_url, normalize_feed_document, normalize_http_url,
};

#[test]
fn aggregator_names_accept_legacy_display_aliases() {
    assert_eq!(
        AggregatorKey::parse("Hacker News"),
        Some(AggregatorKey::HackerNews)
    );
    assert_eq!(
        AggregatorKey::parse("BrutalistReport"),
        Some(AggregatorKey::Brutalist)
    );
    assert_eq!(AggregatorKey::parse("missing"), None);
}

#[test]
fn result_urls_are_normalized_and_fragments_removed() {
    assert_eq!(
        normalize_http_url("http://example.com/post#section").as_deref(),
        Some("https://example.com/post")
    );
}

#[test]
fn reddit_internal_links_do_not_enter_news_ingestion() {
    assert!(!is_external_reddit_url(
        "https://www.reddit.com/r/rust/comments/1"
    ));
    assert!(is_external_reddit_url("https://blog.rust-lang.org/post"));
}

#[test]
fn configured_feed_fetch_can_disable_only_the_response_size_limit() {
    assert!(!exceeds_response_limit(u64::MAX, None));
    assert!(exceeds_response_limit(21, Some(20)));
}

fn podcast_target() -> FeedScrapeTarget {
    FeedScrapeTarget {
        config_id: 42,
        user_id: 7,
        scraper_type: "podcast_rss".to_owned(),
        display_name: Some("Test show".to_owned()),
        feed_url: "https://feeds.example.test/show.xml".to_owned(),
        limit: 10,
        fingerprint: "fixture".to_owned(),
    }
}

#[test]
fn podcast_feed_normalization_keeps_audio_and_episode_identity() {
    let document = br#"<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
          <channel>
            <title>Test show</title>
            <description>A fixture podcast</description>
            <item>
              <guid>episode-1</guid>
              <title>Episode one</title>
              <link>https://example.test/episodes/1</link>
              <enclosure url="https://cdn.example.test/episodes/1.mp3" type="audio/mpeg" />
              <description>Episode notes</description>
            </item>
          </channel>
        </rss>"#;

    let outcome =
        normalize_feed_document(&podcast_target(), document).expect("feed should normalize");

    assert!(outcome.item_errors.is_empty());
    let [ScrapedItem::Content(item)] = outcome.items.as_slice() else {
        panic!("expected one podcast item: {outcome:#?}");
    };
    assert_eq!(item.url, "https://example.test/episodes/1");
    assert_eq!(item.content_type, "podcast");
    assert_eq!(item.platform, "podcast");
    assert_eq!(item.config_id, 42);
    assert_eq!(
        item.metadata
            .get("audio_url")
            .and_then(|value| value.as_str()),
        Some("https://cdn.example.test/episodes/1.mp3")
    );
}

#[test]
fn podcast_feed_reports_entries_without_audio() {
    let document = br#"<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
          <channel>
            <title>Test show</title>
            <item>
              <guid>episode-without-audio</guid>
              <title>Episode without audio</title>
              <link>https://example.test/episodes/missing</link>
            </item>
          </channel>
        </rss>"#;

    let outcome =
        normalize_feed_document(&podcast_target(), document).expect("feed should normalize");

    assert!(outcome.items.is_empty());
    assert_eq!(outcome.item_errors.len(), 1);
    assert!(outcome.item_errors[0].contains("no usable audio enclosure"));
}

#[test]
fn invalid_feed_has_a_stable_diagnostic_code() {
    let error = normalize_feed_document(&podcast_target(), b"not a feed")
        .expect_err("invalid bytes should fail parsing");

    assert_eq!(error.diagnostic_code(), "invalid_feed");
    assert_eq!(error.http_status(), None);
    assert!(error.retryable());
    assert!(matches!(error, ScrapeGatewayError::Feed(_)));
}
