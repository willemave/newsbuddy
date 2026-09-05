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
    assert_eq!(
        normalize_http_url("https://example.com/post/?page=1#section").as_deref(),
        Some("https://example.com/post?page=1")
    );
    assert_eq!(
        normalize_http_url("https://example.com/").as_deref(),
        Some("https://example.com/")
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
        known_urls: std::collections::BTreeSet::new(),
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
            <title>Raw feed title</title>
            <description>A fixture podcast</description>
            <itunes:author xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">Feed author</itunes:author>
            <item>
              <guid>episode-1</guid>
              <title>Episode one</title>
              <link>https://example.test/episodes/1</link>
              <enclosure url="https://cdn.example.test/episodes/1.mp3" type="audio/mpeg" />
              <itunes:episode xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">7</itunes:episode>
              <itunes:duration xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">20:34</itunes:duration>
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
    assert_eq!(item.source.as_deref(), Some("Test show"));
    assert_eq!(item.metadata["feed_name"], "Test show");
    assert_eq!(item.metadata["feed_title"], "Raw feed title");
    assert_eq!(item.metadata["author"], "Feed author");
    assert_eq!(
        item.metadata
            .get("audio_url")
            .and_then(|value| value.as_str()),
        Some("https://cdn.example.test/episodes/1.mp3")
    );
    assert_eq!(item.metadata["description"], "Episode notes");
    assert_eq!(item.metadata["word_count"], 2);
    assert_eq!(item.metadata["episode_number"], 7);
    assert_eq!(item.metadata["duration"], 1_234);
    assert_eq!(item.metadata["duration_seconds"], 1_234);
}

#[test]
fn podcast_feed_uses_unique_enclosures_when_entries_share_a_homepage() {
    let document = br#"<rss version="2.0">
        <channel>
          <title>Test show</title>
          <item>
            <guid>episode-2</guid>
            <title>Episode two</title>
            <link>https://example.test/shows/test-show</link>
            <enclosure url="https://cdn.example.test/episodes/2.mp3" type="audio/mpeg" />
          </item>
          <item>
            <guid>episode-1</guid>
            <title>Episode one</title>
            <link>https://example.test/shows/test-show</link>
            <enclosure url="https://cdn.example.test/episodes/1.mp3" type="audio/mpeg" />
          </item>
        </channel>
      </rss>"#;

    let outcome =
        normalize_feed_document(&podcast_target(), document).expect("feed should normalize");

    assert!(outcome.item_errors.is_empty());
    let urls = outcome
        .items
        .iter()
        .map(|item| match item {
            ScrapedItem::Content(item) => item.url.as_str(),
            ScrapedItem::News(_) => panic!("expected podcast content"),
        })
        .collect::<Vec<_>>();
    assert_eq!(
        urls,
        [
            "https://cdn.example.test/episodes/2.mp3",
            "https://cdn.example.test/episodes/1.mp3"
        ]
    );
}

#[test]
fn podcast_feed_uses_enclosure_when_entry_has_no_page_link() {
    let document = br#"<rss version="2.0">
        <channel>
          <title>Test show</title>
          <item>
            <guid>episode-1</guid>
            <title>Episode one</title>
            <enclosure url="https://cdn.example.test/episodes/1.mp3" type="audio/mpeg" />
          </item>
        </channel>
      </rss>"#;

    let outcome =
        normalize_feed_document(&podcast_target(), document).expect("feed should normalize");

    let [ScrapedItem::Content(item)] = outcome.items.as_slice() else {
        panic!("expected one podcast item: {outcome:#?}");
    };
    assert_eq!(item.url, "https://cdn.example.test/episodes/1.mp3");
    assert_eq!(item.metadata["audio_url"], item.url);
}

#[test]
fn substack_feed_filters_audio_posts_by_title() {
    let target = FeedScrapeTarget {
        known_urls: std::collections::BTreeSet::new(),
        config_id: 42,
        user_id: 7,
        scraper_type: "substack".to_owned(),
        display_name: Some("Test publication".to_owned()),
        feed_url: "https://example.substack.com/feed".to_owned(),
        limit: 10,
        fingerprint: "fixture".to_owned(),
    };
    let document = br#"<rss version="2.0"><channel><title>Test publication</title>
        <item><guid>article</guid><title>An article</title><link>https://example.substack.com/p/article</link></item>
        <item><guid>audio</guid><title>Podcast: an interview</title><link>https://example.substack.com/p/audio</link></item>
      </channel></rss>"#;

    let outcome = normalize_feed_document(&target, document).expect("feed should normalize");

    assert_eq!(outcome.items.len(), 1);
    let ScrapedItem::Content(item) = &outcome.items[0] else {
        panic!("expected article content");
    };
    assert_eq!(item.url, "https://example.substack.com/p/article");
}

#[test]
fn feed_body_preserves_internal_whitespace() {
    let target = FeedScrapeTarget {
        known_urls: std::collections::BTreeSet::new(),
        config_id: 42,
        user_id: 7,
        scraper_type: "atom".to_owned(),
        display_name: Some("Test publication".to_owned()),
        feed_url: "https://example.test/feed.xml".to_owned(),
        limit: 10,
        fingerprint: "fixture".to_owned(),
    };
    let document = br#"<feed xmlns="http://www.w3.org/2005/Atom">
        <title>Test publication</title>
        <entry>
          <id>article</id><title>An article</title>
          <link href="https://example.test/article" />
          <content type="html">  &lt;pre&gt;first
    second&lt;/pre&gt;  </content>
        </entry>
      </feed>"#;

    let outcome = normalize_feed_document(&target, document).expect("feed should normalize");
    let [ScrapedItem::Content(item)] = outcome.items.as_slice() else {
        panic!("expected one article: {outcome:#?}");
    };
    assert_eq!(item.metadata["rss_content"], "<pre>first\n    second</pre>");
}

#[test]
fn feed_source_falls_back_to_feed_domain() {
    let target = FeedScrapeTarget {
        known_urls: std::collections::BTreeSet::new(),
        config_id: 42,
        user_id: 7,
        scraper_type: "atom".to_owned(),
        display_name: None,
        feed_url: "https://www.example.test/feeds/main.xml".to_owned(),
        limit: 10,
        fingerprint: "fixture".to_owned(),
    };
    let document = br#"<feed xmlns="http://www.w3.org/2005/Atom">
        <entry>
          <id>article</id><title>An article</title>
          <link href="https://example.test/article" />
        </entry>
      </feed>"#;

    let outcome = normalize_feed_document(&target, document).expect("feed should normalize");
    let [ScrapedItem::Content(item)] = outcome.items.as_slice() else {
        panic!("expected one article: {outcome:#?}");
    };
    assert_eq!(item.source.as_deref(), Some("example.test"));
    assert_eq!(item.metadata["source"], "example.test");
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

#[test]
fn retained_feed_catchup_skips_known_and_malformed_entries_before_limiting() {
    let mut target = podcast_target();
    target.limit = 2;
    target
        .known_urls
        .insert("https://example.com/one.mp3".to_owned());
    let document = br#"<rss version="2.0"><channel><title>Show</title><link>https://example.com</link><description>Show</description>
    <item><guid>broken</guid><title>Missing enclosure</title></item>
    <item><guid>one</guid><enclosure url="https://example.com/one.mp3" type="audio/mpeg" length="10"/></item>
    <item><guid>two</guid><enclosure url="https://example.com/two.mp3" type="audio/mpeg" length="10"/></item>
    <item><guid>three</guid><enclosure url="https://example.com/three.mp3" type="audio/mpeg" length="10"/></item>
    </channel></rss>"#;
    let result = normalize_feed_document(&target, document).unwrap();
    let urls = result
        .items
        .iter()
        .filter_map(|item| match item {
            ScrapedItem::Content(item) => Some(item.url.as_str()),
            ScrapedItem::News(_) => None,
        })
        .collect::<Vec<_>>();
    assert_eq!(
        urls,
        vec![
            "https://example.com/two.mp3",
            "https://example.com/three.mp3"
        ]
    );
    assert_eq!(result.item_errors.len(), 1);
}

#[test]
fn an_oversized_url_does_not_starve_the_usable_feed_tail() {
    let mut target = podcast_target();
    target.limit = 1;
    let document = format!(
        r#"<rss version="2.0"><channel><title>Show</title><item><title>Poison</title><enclosure url="https://cdn.example/{}.mp3" type="audio/mpeg"/></item><item><title>Good</title><enclosure url="https://cdn.example/good.mp3" type="audio/mpeg"/></item></channel></rss>"#,
        "x".repeat(2050)
    );
    let outcome = normalize_feed_document(&target, document.as_bytes()).unwrap();
    assert_eq!(outcome.items.len(), 1);
    assert_eq!(outcome.item_errors.len(), 1);
    let ScrapedItem::Content(item) = &outcome.items[0] else {
        panic!("content expected")
    };
    assert_eq!(item.title.as_deref(), Some("Good"));
}
