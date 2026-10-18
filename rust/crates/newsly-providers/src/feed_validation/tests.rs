use chrono::{DateTime, Utc};
use feed_rs::model::FeedType;
use std::fmt::Write as _;

use super::{
    ValidatedFeedFormat, has_audio_entries, has_feed_document_root, validated_feed_format,
};

fn checked_at() -> DateTime<Utc> {
    "2026-10-05T12:00:00Z".parse().unwrap()
}

fn activity(xml: &str) -> super::FeedPublishingActivity {
    activity_with_mode(xml, false)
}

fn activity_with_mode(xml: &str, audio_only: bool) -> super::FeedPublishingActivity {
    let feed = feed_rs::parser::Builder::new()
        .id_generator(|_, _, _| String::new())
        .build()
        .parse(xml.as_bytes())
        .expect("fixture parses");
    super::publishing_activity(&feed, audio_only, checked_at())
}

fn atom_activity(entries: &str) -> super::FeedPublishingActivity {
    activity(&format!(
        "<feed xmlns=\"http://www.w3.org/2005/Atom\"><title>Feed</title>{entries}</feed>"
    ))
}

#[test]
fn rss_activity_uses_latest_publication_and_deduplicates_unsorted_entries() {
    let activity = activity(
        r#"<rss version="2.0"><channel><title>News</title>
        <item><guid isPermaLink="false">old</guid><title>Old</title><pubDate>Sun, 05 Apr 2026 12:00:00 GMT</pubDate></item>
        <item><guid isPermaLink="false">new</guid><title>Latest</title><link>https://example.com/latest</link><pubDate>Mon, 05 Oct 2026 12:00:00 GMT</pubDate></item>
        <item><guid isPermaLink="false">middle</guid><title>Middle</title><pubDate>Thu, 01 Oct 2026 12:00:00 GMT</pubDate></item>
        <item><guid isPermaLink="false">new</guid><title>Duplicate</title><pubDate>Mon, 05 Oct 2026 12:00:00 GMT</pubDate></item>
        </channel></rss>"#,
    );
    assert_eq!(activity.observed_count, Some(2));
    assert_eq!(activity.observed_year_count, Some(3));
    assert_eq!(activity.undated_count, 0);
    let latest = activity.latest_item.unwrap();
    assert_eq!(latest.title.as_deref(), Some("Latest"));
    assert_eq!(latest.url.as_deref(), Some("https://example.com/latest"));
    assert_eq!(latest.published_at, checked_at());
}

#[test]
fn atom_activity_observes_inclusive_cutoff_without_updated_fallback() {
    let activity = atom_activity(
        r"
        <entry><id>before</id><published>2026-07-07T11:59:59Z</published></entry>
        <entry><id>cutoff</id><published>2026-07-07T12:00:00Z</published></entry>
        <entry><id>now</id><published>2026-10-05T12:00:00Z</published></entry>
        <entry><id>future</id><published>2026-10-05T12:00:01Z</published></entry>
        <entry><id>updated</id><updated>2026-10-05T11:00:00Z</updated></entry>
        <entry><id>missing</id></entry>",
    );
    assert_eq!(
        activity.window_start,
        "2026-07-07T12:00:00Z".parse::<DateTime<Utc>>().unwrap()
    );
    assert_eq!(activity.window_end, checked_at());
    assert_eq!(activity.observed_count, Some(2));
    assert_eq!(activity.undated_count, 2);
    assert_eq!(activity.latest_item.unwrap().published_at, checked_at());
}

#[test]
fn activity_distinguishes_unknown_dates_from_observed_zero() {
    for entries in [
        "",
        "<entry><id>undated</id></entry>",
        "<entry><id>future</id><published>2026-10-06T00:00:00Z</published></entry>",
        "<entry><id>updated-only</id><updated>2026-10-01T00:00:00Z</updated></entry>",
    ] {
        let activity = atom_activity(entries);
        assert_eq!(activity.observed_count, None);
        assert_eq!(activity.observed_year_count, None);
        assert_eq!(activity.latest_item, None);
    }
    let activity =
        atom_activity("<entry><id>old</id><published>2026-01-01T00:00:00Z</published></entry>");
    assert_eq!(activity.observed_count, Some(0));
    assert!(activity.latest_item.is_some());
}

#[test]
fn activity_uses_inclusive_year_window_and_excludes_malformed_dates() {
    let activity = atom_activity(
        r"
        <entry><id>before-year</id><published>2025-10-05T11:59:59Z</published></entry>
        <entry><id>year-cutoff</id><published>2025-10-05T12:00:00Z</published></entry>
        <entry><id>bad-date</id><published>not-a-date</published></entry>",
    );
    assert_eq!(
        activity.year_window_start,
        "2025-10-05T12:00:00Z".parse::<DateTime<Utc>>().unwrap()
    );
    assert_eq!(activity.observed_count, Some(0));
    assert_eq!(activity.observed_year_count, Some(1));
    assert_eq!(activity.undated_count, 1);
}

#[test]
fn latest_item_resolves_relative_links_and_strips_title_controls() {
    let mut feed = feed_rs::parser::Builder::new()
        .id_generator(|_, _, _| String::new())
        .base_uri(Some("https://example.com/feeds/news.xml"))
        .build()
        .parse(br#"<feed xmlns="http://www.w3.org/2005/Atom"><title>News</title><entry><id>relative</id><title>A title</title><link href="../latest"/><published>2026-10-01T00:00:00Z</published></entry></feed>"#.as_slice())
        .unwrap();
    feed.entries[0].title.as_mut().unwrap().content = "A\u{0007}\n title".to_owned();
    let latest = super::publishing_activity(&feed, false, checked_at())
        .latest_item
        .unwrap();
    assert_eq!(latest.url.as_deref(), Some("https://example.com/latest"));
    assert_eq!(latest.title.as_deref(), Some("A title"));
}

#[test]
fn activity_preserves_distinct_ids_and_does_not_invent_anonymous_ids() {
    let activity = atom_activity(
        r#"
        <entry><id>a</id><link href="https://example.com/a"/><published>2026-10-04T00:00:00Z</published></entry>
        <entry><id>b</id><link href="https://example.com/b"/><published>2026-10-03T00:00:00Z</published></entry>
        <entry><id>a</id><link href="https://example.com/b"/><published>2026-10-02T00:00:00Z</published></entry>
        <entry><id>a</id></entry>
        <entry><title>Same title</title><published>2026-10-01T00:00:00Z</published></entry>
        <entry><title>Same title</title><published>2026-09-30T00:00:00Z</published></entry>"#,
    );
    assert_eq!(activity.observed_count, Some(4));
    assert_eq!(activity.undated_count, 0);
}

#[test]
fn mixed_podcast_activity_counts_only_unique_audio_episodes() {
    let activity = activity_with_mode(
        r#"<rss version="2.0"><channel><title>Mixed</title>
        <item><guid isPermaLink="false">article</guid><pubDate>Mon, 05 Oct 2026 12:00:00 GMT</pubDate></item>
        <item><guid isPermaLink="false">episode</guid><title>Episode</title><enclosure url="https://cdn.example/episode.mp3" type="audio/mpeg"/><pubDate>Thu, 01 Oct 2026 12:00:00 GMT</pubDate></item>
        <item><guid isPermaLink="false">episode</guid><enclosure url="https://cdn.example/episode.mp3" type="audio/mpeg"/><pubDate>Thu, 01 Oct 2026 12:00:00 GMT</pubDate></item>
        <item><title>Undated episode</title><enclosure url="https://cdn.example/other.mp3" type="audio/mpeg"/></item>
        </channel></rss>"#,
        true,
    );
    assert_eq!(activity.observed_count, Some(1));
    assert_eq!(activity.undated_count, 1);
    assert_eq!(
        activity.latest_item.unwrap().title.as_deref(),
        Some("Episode")
    );
}

#[test]
fn podcast_activity_preserves_distinct_guids_with_a_shared_homepage_and_audio() {
    let mut entries = String::new();
    for index in 0..3 {
        write!(entries, r#"<item><guid isPermaLink="false">episode-{index}</guid><link>https://example.com/show</link><enclosure url="https://cdn.example/episode.mp3" type="audio/mpeg"/><pubDate>Thu, 01 Oct 2026 12:00:00 GMT</pubDate></item>"#).unwrap();
    }
    let activity = activity_with_mode(
        &format!("<rss version=\"2.0\"><channel><title>Show</title>{entries}</channel></rss>"),
        true,
    );
    assert_eq!(activity.observed_count, Some(3));
    assert_eq!(activity.observed_year_count, Some(3));
}

#[test]
fn anonymous_activity_deduplicates_by_article_link_then_audio_url() {
    let activity = activity(
        r#"<rss version="2.0"><channel><title>Anonymous</title>
        <item><link>https://example.com/article</link><pubDate>Thu, 01 Oct 2026 12:00:00 GMT</pubDate></item>
        <item><link>https://example.com/article</link><pubDate>Wed, 30 Sep 2026 12:00:00 GMT</pubDate></item>
        <item><enclosure url="https://cdn.example/episode.mp3" type="audio/mpeg"/><pubDate>Thu, 01 Oct 2026 12:00:00 GMT</pubDate></item>
        <item><enclosure url="https://cdn.example/episode.mp3" type="audio/mpeg"/><pubDate>Wed, 30 Sep 2026 12:00:00 GMT</pubDate></item>
        </channel></rss>"#,
    );
    assert_eq!(activity.observed_count, Some(2));
}

#[test]
fn mixed_newsletter_keeps_article_activity_separate_from_podcast_activity() {
    let xml = r#"<rss version="2.0"><channel><title>Mixed newsletter</title>
        <item><guid isPermaLink="false">article</guid><title>Latest article</title><pubDate>Mon, 05 Oct 2026 12:00:00 GMT</pubDate></item>
        <item><guid isPermaLink="false">episode</guid><title>Episode</title><enclosure url="https://cdn.example/episode.mp3" type="audio/mpeg"/><pubDate>Thu, 01 Oct 2026 12:00:00 GMT</pubDate></item>
        </channel></rss>"#;
    let all = activity(xml);
    let audio = activity_with_mode(xml, true);
    assert_eq!(all.observed_count, Some(2));
    assert_eq!(
        all.latest_item.unwrap().title.as_deref(),
        Some("Latest article")
    );
    assert_eq!(audio.observed_count, Some(1));
    assert_eq!(audio.latest_item.unwrap().title.as_deref(), Some("Episode"));
    let document = super::PublicDocument {
        effective_url: super::PublicUrl::parse("https://example.com/feed.xml").unwrap(),
        body: xml.as_bytes().to_vec(),
    };
    let validated = super::validated_feed_document(&document).unwrap();
    assert!(validated.has_audio_entries);
    assert_eq!(
        validated
            .publishing_activity
            .latest_item
            .unwrap()
            .title
            .as_deref(),
        Some("Latest article")
    );
    assert_eq!(
        validated
            .podcast_publishing_activity
            .latest_item
            .unwrap()
            .title
            .as_deref(),
        Some("Episode")
    );
    assert_eq!(
        validated.publishing_activity.checked_at,
        validated.podcast_publishing_activity.checked_at
    );
}

#[test]
fn latest_item_title_is_plain_bounded_text_and_url_is_safe() {
    let activity = atom_activity(
        r#"<entry><id>entry</id><title type="html">&lt;b&gt; A &amp;amp; B &lt;/b&gt;</title><link href="javascript:alert(1)"/><published>2026-10-01T00:00:00Z</published></entry>"#,
    );
    let latest = activity.latest_item.unwrap();
    assert_eq!(latest.title.as_deref(), Some("A & B"));
    assert_eq!(latest.url, None);
    assert_eq!(
        super::safe_item_url("https://user:secret@example.com/item"),
        None
    );
    assert_eq!(super::safe_item_url("http://127.0.0.1/item"), None);
    assert_eq!(
        super::safe_item_url(&format!("https://example.com/{}", "x".repeat(2_048))),
        None
    );
    let activity = atom_activity(&format!(
        "<entry><id>long</id><title>{}</title><published>2026-10-01T00:00:00Z</published></entry>",
        "é".repeat(500)
    ));
    assert_eq!(
        activity.latest_item.unwrap().title.unwrap().chars().count(),
        300
    );
}

#[test]
fn feed_root_check_accepts_namespaced_rss_and_atom_only() {
    assert!(has_feed_document_root(
        b"<?xml version='1.0'?><rss version='2.0'>"
    ));
    assert!(has_feed_document_root(b"<atom:feed xmlns:atom='urn:atom'>"));
    assert!(has_feed_document_root(b"<rdf:RDF xmlns:rdf='urn:rdf'>"));
    assert!(!has_feed_document_root(b"<html><body>rss</body></html>"));
}

#[test]
fn parsed_feed_format_preserves_rss_and_atom() {
    assert_eq!(
        validated_feed_format(&FeedType::Atom),
        Some(ValidatedFeedFormat::Atom)
    );
    for feed_type in [FeedType::RSS0, FeedType::RSS1, FeedType::RSS2] {
        assert_eq!(
            validated_feed_format(&feed_type),
            Some(ValidatedFeedFormat::Rss)
        );
    }
    assert_eq!(validated_feed_format(&FeedType::JSON), None);
}

#[tokio::test]
async fn slow_first_host_does_not_hold_later_validation_slots() {
    use std::sync::{
        Arc,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    };
    struct Active(Arc<AtomicUsize>);
    impl Drop for Active {
        fn drop(&mut self) {
            self.0.fetch_sub(1, Ordering::SeqCst);
        }
    }
    let release_first = Arc::new(tokio::sync::Notify::new());
    let first_finished = Arc::new(AtomicBool::new(false));
    let active = Arc::new(AtomicUsize::new(0));
    let peak = Arc::new(AtomicUsize::new(0));
    let urls = (0..12).map(|index| index.to_string()).collect::<Vec<_>>();
    let document = super::PublicDocument {
        effective_url: super::PublicUrl::parse("https://example.com/feed").unwrap(),
        body: br#"<rss version="2.0"><channel><title>Show</title></channel></rss>"#.to_vec(),
    };
    let feed = super::validated_feed_document(&document).unwrap();
    let validations = super::ordered_feed_validations(&urls, |url| {
        let (release, finished, active, peak, mut feed) = (
            release_first.clone(),
            first_finished.clone(),
            active.clone(),
            peak.clone(),
            feed.clone(),
        );
        async move {
            let count = active.fetch_add(1, Ordering::SeqCst) + 1;
            let _active = Active(active);
            peak.fetch_max(count, Ordering::SeqCst);
            if url == "0" {
                release.notified().await;
                finished.store(true, Ordering::SeqCst);
            } else if url == "8" {
                assert!(
                    !finished.load(Ordering::SeqCst),
                    "ninth request must start before slow first finishes"
                );
                release.notify_one();
            }
            tokio::task::yield_now().await;
            feed.effective_url = url;
            Ok(Some(feed))
        }
    });
    let results = tokio::time::timeout(std::time::Duration::from_secs(2), validations)
        .await
        .expect("ninth request must release the first without a blocked slot");
    assert_eq!(
        results
            .into_iter()
            .map(|result| result.unwrap().unwrap().effective_url)
            .collect::<Vec<_>>(),
        urls
    );
    assert!((2..=super::MAX_PARALLEL_VALIDATIONS).contains(&peak.load(Ordering::SeqCst)));
    assert_eq!(active.load(Ordering::SeqCst), 0);
}

#[test]
fn discovery_evidence_preserves_content_language_conflict_and_bounds_untrusted_text() {
    let document = super::PublicDocument {
        effective_url: super::PublicUrl::parse("https://example.com/feed").unwrap(),
        body: format!(r#"<rss version="2.0"><channel><title>Show</title><language>en</language><description>{}</description>
        <item><title>Robert forklarer kunstig intelligens innen helseforskning</title><pubDate>Thu, 01 Oct 2026 12:00:00 GMT</pubDate><enclosure url="https://example.com/a.mp3" type="audio/mpeg"/></item>
        <item><title>Inge forklarer sammenhengen mellom kunstig intelligens og proteinfolding</title><pubDate>Fri, 02 Oct 2026 12:00:00 GMT</pubDate><enclosure url="https://example.com/b.mp3" type="audio/mpeg"/></item>
        <item><title>An article should not be episode evidence</title></item>
        </channel></rss>"#, "x".repeat(500)).into_bytes(),
    };
    let (feed, evidence) = super::validated_feed_document_with_evidence(&document).unwrap();
    assert!(feed.has_audio_entries);
    assert_eq!(evidence.declared_language.as_deref(), Some("en"));
    assert_eq!(evidence.recent_episode_titles.len(), 2);
    assert!(evidence.recent_episode_titles[0].starts_with("Inge forklarer"));
    assert_eq!(evidence.description.unwrap().chars().count(), 200);
}

#[test]
fn podcast_classification_requires_host_parsed_audio_evidence() {
    let podcast = feed_rs::parser::parse(
        br#"<rss version="2.0"><channel><title>Podcast</title><item><title>Episode</title><enclosure url="https://cdn.example/episode.mp3" type="audio/mpeg" /></item></channel></rss>"#
            .as_slice(),
    )
    .expect("podcast fixture parses");
    assert!(has_audio_entries(&podcast));

    let publication = feed_rs::parser::parse(
        br#"<rss version="2.0"><channel><title>Publication</title><item><title>Post</title><link>https://example.com/post</link></item></channel></rss>"#
            .as_slice(),
    )
    .expect("publication fixture parses");
    assert!(!has_audio_entries(&publication));
}

#[test]
fn podcast_classification_accepts_legacy_untyped_audio_extensions() {
    for extension in ["flac", "mpga", "oga", "webm"] {
        let document = format!(
            "<rss version=\"2.0\"><channel><title>Podcast</title><item><title>Episode</title><enclosure url=\"https://cdn.example/episode.{extension}\" /></item></channel></rss>"
        );
        let feed = feed_rs::parser::parse(document.as_bytes()).expect("fixture parses");
        assert!(
            has_audio_entries(&feed),
            "extension {extension} was rejected"
        );
    }
}
#[test]
fn shared_item_evidence_distinguishes_episodes_articles_and_homepages() {
    assert_eq!(super::shared_item_kind(br#"<script type="application/ld+json">{"@graph":[{"@type":["CreativeWork","PodcastEpisode"]}]}</script>"#), Some("podcast"));
    assert_eq!(
        super::shared_item_kind(br#"<meta property="og:type" content="article">"#),
        Some("article")
    );
    assert_eq!(
        super::shared_item_kind(
            br#"<script type="application/ld+json">{"@type":"WebSite"}</script>"#
        ),
        None
    );
    assert_eq!(
        super::shared_item_kind(br#"<script type="application/ld+json">invalid</script>"#),
        None
    );
}
