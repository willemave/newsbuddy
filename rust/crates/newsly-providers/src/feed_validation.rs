//! Fast, bounded validation for untrusted RSS and Atom candidates.
//!
//! Candidate documents are fetched as inert bytes through a public-network-only HTTP client,
//! then parsed by Rust. No candidate content is executed, so validation does not require a VM.

use std::{collections::HashSet, time::Duration};

use crate::public_http::{
    MAX_SOURCE_RESPONSE_BYTES, PublicDocument, PublicHttpError, fetch_public,
};
use chrono::{DateTime, Utc};
use feed_rs::model::{Entry, Feed, FeedType, Text};
use futures_util::{StreamExt as _, stream};
use newsly_extraction::{ExtractionClientError, PublicUrl};
use reqwest::StatusCode;
use thiserror::Error;

const REQUEST_TIMEOUT: Duration = Duration::from_secs(15);
const MAX_PARALLEL_VALIDATIONS: usize = 8;
const USER_AGENT: &str = "newsly-feed-validator/1.0 (+https://newsly.app)";
const ACCEPT: &str =
    "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.1";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ValidatedFeedFormat {
    Rss,
    Atom,
}

impl ValidatedFeedFormat {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Rss => "rss",
            Self::Atom => "atom",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ValidatedFeed {
    pub effective_url: String,
    pub format: ValidatedFeedFormat,
    pub has_audio_entries: bool,
    /// All feed publications, including articles in feeds that also contain audio.
    pub publishing_activity: FeedPublishingActivity,
    /// Audio episode publications, selected by the caller for podcast subscriptions.
    pub podcast_publishing_activity: FeedPublishingActivity,
}

/// Publication evidence from the fetched feed snapshot, not a complete archive count.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FeedPublishingActivity {
    pub checked_at: DateTime<Utc>,
    pub window_start: DateTime<Utc>,
    pub year_window_start: DateTime<Utc>,
    pub window_end: DateTime<Utc>,
    pub latest_item: Option<FeedPublishedItem>,
    /// Unique dated publications observed within the inclusive trailing 90-day window.
    /// Unknown when the feed has no non-future publication dates.
    pub observed_count: Option<i64>,
    /// The same observed lower bound over the trailing 365 days.
    pub observed_year_count: Option<i64>,
    pub undated_count: i64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FeedPublishedItem {
    pub title: Option<String>,
    pub url: Option<String>,
    pub published_at: DateTime<Utc>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ValidatedSharedItem {
    pub effective_url: String,
    pub content_type: &'static str,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ValidatedSharedTarget {
    Feed(Box<ValidatedFeed>),
    Item(ValidatedSharedItem),
}

#[derive(Clone, Debug, Default)]
pub struct FeedValidator;

impl FeedValidator {
    #[must_use]
    pub const fn new() -> Self {
        Self
    }

    /// Prove that a shared URL names an article or episode before recovering a rejected feed choice.
    ///
    /// # Errors
    /// Returns transient transport errors, exactly as feed validation does.
    pub async fn validate_shared_item(
        &self,
        url: &str,
    ) -> Result<Option<ValidatedSharedItem>, FeedValidationError> {
        let Some(document) = self.fetch(url).await? else {
            return Ok(None);
        };
        Ok(validated_shared_item_document(&document))
    }

    /// Validate one feed candidate and return its redirect-resolved URL.
    ///
    /// # Errors
    ///
    /// Returns an operational error when public DNS or HTTP transport is unavailable. Invalid or
    /// unavailable candidate documents return `Ok(None)`.
    pub async fn validate_feed_url(
        &self,
        url: &str,
    ) -> Result<Option<String>, FeedValidationError> {
        Ok(self
            .validate_feed(url)
            .await?
            .map(|feed| feed.effective_url))
    }

    /// Validate one feed candidate without executing any downloaded content.
    ///
    /// # Errors
    ///
    /// Returns an operational error when public DNS or HTTP transport is unavailable. Invalid or
    /// unavailable candidate documents return `Ok(None)`.
    pub async fn validate_feed(
        &self,
        url: &str,
    ) -> Result<Option<ValidatedFeed>, FeedValidationError> {
        let Some(document) = self.fetch(url).await? else {
            return Ok(None);
        };
        Ok(validated_feed_document(&document))
    }

    /// Classify a directly shared feed or item before using model-backed discovery.
    ///
    /// # Errors
    /// Returns an operational error when public DNS or HTTP transport is unavailable.
    pub async fn validate_shared_target(
        &self,
        url: &str,
    ) -> Result<Option<ValidatedSharedTarget>, FeedValidationError> {
        let Some(document) = self.fetch(url).await? else {
            return Ok(None);
        };
        Ok(validated_feed_document(&document)
            .map(Box::new)
            .map(ValidatedSharedTarget::Feed)
            .or_else(|| validated_shared_item_document(&document).map(ValidatedSharedTarget::Item)))
    }

    /// Validate a candidate set concurrently while preserving input order and per-candidate
    /// failures. Callers can retain successful feeds even when another host is unavailable.
    pub async fn validate_feeds(
        &self,
        urls: &[String],
    ) -> Vec<Result<Option<ValidatedFeed>, FeedValidationError>> {
        let validator = self.clone();
        stream::iter(urls.to_vec())
            .map(move |url| {
                let validator = validator.clone();
                async move { validator.validate_feed(&url).await }
            })
            .buffered(MAX_PARALLEL_VALIDATIONS)
            .collect()
            .await
    }

    async fn fetch(&self, raw_url: &str) -> Result<Option<PublicDocument>, FeedValidationError> {
        match fetch_public(
            raw_url,
            REQUEST_TIMEOUT,
            Some(MAX_SOURCE_RESPONSE_BYTES),
            USER_AGENT,
            ACCEPT,
        )
        .await
        {
            Ok(document) => Ok(Some(document)),
            Err(PublicHttpError::Url(ExtractionClientError::DnsResolution { .. })) => {
                Err(FeedValidationError::PublicUrlResolution)
            }
            Err(PublicHttpError::Status { status, .. }) => {
                if status.is_server_error() || matches!(status.as_u16(), 408 | 429) {
                    Err(FeedValidationError::HttpStatus(status))
                } else {
                    Ok(None)
                }
            }
            Err(PublicHttpError::Deadline) => Err(FeedValidationError::Deadline),
            Err(PublicHttpError::Http(error)) => Err(FeedValidationError::Http(error)),
            Err(_) => Ok(None),
        }
    }
}

fn has_feed_semantics(feed: &feed_rs::model::Feed) -> bool {
    feed.title.is_some()
        || feed.description.is_some()
        || !feed.links.is_empty()
        || !feed.entries.is_empty()
        || !feed.authors.is_empty()
        || feed.language.is_some()
}

fn has_audio_entries(feed: &feed_rs::model::Feed) -> bool {
    feed.entries
        .iter()
        .any(|entry| entry_audio_url(entry).is_some())
}

pub(crate) fn entry_audio_url(entry: &feed_rs::model::Entry) -> Option<String> {
    entry
        .content
        .as_ref()
        .and_then(|content| content.src.as_ref())
        .filter(|link| is_audio_link(link.media_type.as_deref(), &link.href))
        .map(|link| link.href.clone())
        .or_else(|| {
            entry
                .media
                .iter()
                .flat_map(|media| media.content.iter())
                .find_map(|content| {
                    let url = content.url.as_ref()?;
                    let media_type = content.content_type.as_ref().map(ToString::to_string);
                    is_audio_link(media_type.as_deref(), url.as_str()).then(|| url.to_string())
                })
        })
        .or_else(|| {
            entry.links.iter().find_map(|link| {
                let is_enclosure = link.rel.as_deref() == Some("enclosure");
                let has_no_declared_type = link.media_type.is_none();
                (is_audio_link(link.media_type.as_deref(), &link.href)
                    || (is_enclosure && has_no_declared_type))
                    .then(|| link.href.clone())
            })
        })
}

fn is_audio_link(media_type: Option<&str>, href: &str) -> bool {
    media_type.is_some_and(|value| value.to_ascii_lowercase().starts_with("audio/"))
        || href.split(['?', '#']).next().is_some_and(|path| {
            let path = path.to_ascii_lowercase();
            [
                ".aac", ".flac", ".m4a", ".mp3", ".mpga", ".oga", ".ogg", ".opus", ".wav", ".webm",
            ]
            .iter()
            .any(|extension| path.ends_with(extension))
        })
}

const fn validated_feed_format(feed_type: &FeedType) -> Option<ValidatedFeedFormat> {
    match feed_type {
        FeedType::Atom => Some(ValidatedFeedFormat::Atom),
        FeedType::RSS0 | FeedType::RSS1 | FeedType::RSS2 => Some(ValidatedFeedFormat::Rss),
        FeedType::JSON => None,
    }
}

fn validated_shared_item_document(document: &PublicDocument) -> Option<ValidatedSharedItem> {
    if document
        .effective_url
        .as_url()
        .path()
        .trim_matches('/')
        .is_empty()
    {
        return None;
    }
    shared_item_kind(&document.body).map(|content_type| ValidatedSharedItem {
        effective_url: document.effective_url.to_string(),
        content_type,
    })
}

fn validated_feed_document(document: &PublicDocument) -> Option<ValidatedFeed> {
    if !has_feed_document_root(&document.body) {
        return None;
    }
    let normalized = crate::feed_document::preserve_direct_media_urls(&document.body).ok()?;
    // Missing IDs must remain missing: generated title hashes are not identity evidence.
    let parsed = match feed_rs::parser::Builder::new()
        .id_generator(|_, _, _| String::new())
        .base_uri(Some(document.effective_url.as_str()))
        .build()
        .parse(normalized.as_slice())
    {
        Ok(parsed) => parsed,
        Err(error) => {
            tracing::debug!(
                error = %error,
                url = %document.effective_url,
                "feed parser rejected candidate"
            );
            return None;
        }
    };
    let format = validated_feed_format(&parsed.feed_type)?;
    if !has_feed_semantics(&parsed) {
        return None;
    }
    let has_audio_entries = has_audio_entries(&parsed);
    let checked_at = Utc::now();
    Some(ValidatedFeed {
        effective_url: document.effective_url.to_string(),
        format,
        has_audio_entries,
        publishing_activity: publishing_activity(&parsed, false, checked_at),
        podcast_publishing_activity: publishing_activity(&parsed, true, checked_at),
    })
}

fn publishing_activity(
    feed: &Feed,
    audio_only: bool,
    checked_at: DateTime<Utc>,
) -> FeedPublishingActivity {
    let window_start = checked_at - chrono::Duration::days(90);
    let year_window_start = checked_at - chrono::Duration::days(365);
    let mut entries: Vec<_> = feed
        .entries
        .iter()
        .filter(|entry| !audio_only || entry_audio_url(entry).is_some())
        .collect();
    // Prefer actual, current publication evidence when duplicate entries disagree.
    entries
        .sort_by_key(|entry| std::cmp::Reverse(entry.published.filter(|date| *date <= checked_at)));
    let mut identities = HashSet::new();
    let mut latest_item = None;
    let mut observed_count = None;
    let mut observed_year_count = None;
    let mut undated_count = 0;
    for entry in entries {
        // Explicit IDs are authoritative: publishers may reuse a homepage or audio URL.
        // Without an ID, links are the strongest available evidence. Fully anonymous
        // entries remain distinct rather than inventing identity from their title.
        let identity = if entry.id.is_empty() {
            entry_item_url(entry)
                .or_else(|| entry_audio_url(entry).and_then(|url| safe_item_url(&url)))
                .map(|url| format!("url:{url}"))
        } else {
            Some(format!("id:{}", entry.id))
        };
        if identity.is_some_and(|identity| !identities.insert(identity)) {
            continue;
        }
        let Some(published_at) = entry.published else {
            undated_count += 1;
            continue;
        };
        if published_at > checked_at {
            continue;
        }
        let count = observed_count.get_or_insert(0);
        if published_at >= window_start {
            *count += 1;
        }
        let year_count = observed_year_count.get_or_insert(0);
        if published_at >= year_window_start {
            *year_count += 1;
        }
        if latest_item.is_none() {
            latest_item = Some(FeedPublishedItem {
                title: entry.title.as_ref().and_then(readable_title),
                url: entry_item_url(entry),
                published_at,
            });
        }
    }
    FeedPublishingActivity {
        checked_at,
        window_start,
        year_window_start,
        window_end: checked_at,
        latest_item,
        observed_count,
        observed_year_count,
        undated_count,
    }
}

fn safe_item_url(raw_url: &str) -> Option<String> {
    if raw_url.len() > 2_048 {
        return None;
    }
    let url = PublicUrl::parse(raw_url).ok()?.to_string();
    (url.len() <= 2_048).then_some(url)
}

fn entry_item_url(entry: &Entry) -> Option<String> {
    entry
        .links
        .iter()
        .filter(|link| link.rel.as_deref().is_none_or(|rel| rel == "alternate"))
        .find_map(|link| safe_item_url(&link.href))
        .or_else(|| safe_item_url(&entry.id))
}

fn readable_title(title: &Text) -> Option<String> {
    let plain = if title.content_type.as_str() == "text/plain" {
        title.content.clone()
    } else {
        scraper::Html::parse_fragment(&title.content)
            .root_element()
            .text()
            .collect::<Vec<_>>()
            .join(" ")
    };
    let plain: String = plain
        .chars()
        .map(|character| {
            if character.is_control() {
                ' '
            } else {
                character
            }
        })
        .collect();
    let title: String = plain
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .chars()
        .take(300)
        .collect();
    (!title.is_empty()).then_some(title)
}

fn has_feed_document_root(body: &[u8]) -> bool {
    let prefix = &body[..body.len().min(4_000)];
    let lower = prefix
        .iter()
        .map(u8::to_ascii_lowercase)
        .collect::<Vec<_>>();
    lower
        .iter()
        .enumerate()
        .filter(|(_, byte)| **byte == b'<')
        .any(|(index, _)| feed_root_at(&lower[index + 1..]))
}

fn feed_root_at(mut value: &[u8]) -> bool {
    let name_end = value
        .iter()
        .position(|byte| !(byte.is_ascii_alphanumeric() || b"_.:-".contains(byte)))
        .unwrap_or(value.len());
    if name_end == 0 {
        return false;
    }
    value = &value[..name_end];
    let local_name = value
        .iter()
        .rposition(|byte| *byte == b':')
        .map_or(value, |index| &value[index + 1..]);
    matches!(local_name, b"rss" | b"feed" | b"rdf")
}

fn shared_item_kind(bytes: &[u8]) -> Option<&'static str> {
    let html = scraper::Html::parse_document(&String::from_utf8_lossy(bytes));
    let scripts =
        scraper::Selector::parse("script[type='application/ld+json']").expect("static selector");
    for script in html.select(&scripts) {
        if let Ok(value) = serde_json::from_str::<serde_json::Value>(&script.inner_html())
            && let Some(kind) = schema_item_kind(&value)
        {
            return Some(kind);
        }
    }
    let selector = scraper::Selector::parse("meta[property='og:type']").expect("static selector");
    html.select(&selector)
        .any(|node| {
            node.value()
                .attr("content")
                .is_some_and(|value| value.eq_ignore_ascii_case("article"))
        })
        .then_some("article")
}

fn schema_item_kind(value: &serde_json::Value) -> Option<&'static str> {
    if let Some(values) = value.as_array() {
        return values.iter().find_map(schema_item_kind);
    }
    let kinds = value.get("@type");
    let contains = |expected: &str| {
        kinds.is_some_and(|kind| {
            kind.as_str() == Some(expected)
                || kind
                    .as_array()
                    .is_some_and(|values| values.iter().any(|v| v.as_str() == Some(expected)))
        })
    };
    if contains("PodcastEpisode") {
        return Some("podcast");
    }
    if ["Article", "NewsArticle", "BlogPosting"]
        .into_iter()
        .any(contains)
    {
        return Some("article");
    }
    value.get("@graph").and_then(schema_item_kind)
}

#[derive(Debug, Error)]
pub enum FeedValidationError {
    #[error("feed validation deadline exceeded")]
    Deadline,
    #[error("feed validator HTTP client failed")]
    Http(#[from] reqwest::Error),
    #[error("feed host could not be resolved on the public network")]
    PublicUrlResolution,
    #[error("feed host returned retryable HTTP status {0}")]
    HttpStatus(StatusCode),
}

#[cfg(test)]
mod tests {
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
}
