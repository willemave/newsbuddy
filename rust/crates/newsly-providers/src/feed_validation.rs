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
use serde::Serialize;
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

/// Bounded publisher text used as evidence, never as instructions or verified spoken language.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub(crate) struct FeedDiscoveryEvidence {
    pub(crate) title: Option<String>,
    pub(crate) authors: Vec<String>,
    description: Option<String>,
    declared_language: Option<String>,
    recent_episode_titles: Vec<String>,
    latest_episode_published_at: Option<DateTime<Utc>>,
    observed_episodes_90_days: Option<i64>,
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

    pub(crate) async fn validate_feed_with_evidence(
        &self,
        url: &str,
    ) -> Result<Option<(ValidatedFeed, FeedDiscoveryEvidence)>, FeedValidationError> {
        let Some(document) = self.fetch(url).await? else {
            return Ok(None);
        };
        Ok(validated_feed_document_with_evidence(&document))
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
        ordered_feed_validations(urls, move |url| {
            let validator = validator.clone();
            async move { validator.validate_feed(&url).await }
        })
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

/// Preserve caller order without letting a slow early host prevent later requests from starting.
async fn ordered_feed_validations<F, Fut>(
    urls: &[String],
    validate: F,
) -> Vec<Result<Option<ValidatedFeed>, FeedValidationError>>
where
    F: Fn(String) -> Fut + Send + Sync,
    Fut: std::future::Future<Output = Result<Option<ValidatedFeed>, FeedValidationError>> + Send,
{
    // Own the queued URLs so the concurrent futures do not retain a borrowed iterator.
    let owned_urls = urls.to_vec();
    let mut results = stream::iter(owned_urls.into_iter().enumerate())
        .map(|(index, url)| {
            let validate = &validate;
            async move { (index, validate(url).await) }
        })
        .buffer_unordered(MAX_PARALLEL_VALIDATIONS)
        .collect::<Vec<_>>()
        .await;
    results.sort_by_key(|(index, _)| *index);
    results.into_iter().map(|(_, result)| result).collect()
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
    parse_validated_feed_document(document).map(|(feed, _)| feed)
}

fn validated_feed_document_with_evidence(
    document: &PublicDocument,
) -> Option<(ValidatedFeed, FeedDiscoveryEvidence)> {
    let (validated, parsed) = parse_validated_feed_document(document)?;
    let evidence = podcast_discovery_evidence(&parsed, &validated.podcast_publishing_activity);
    Some((validated, evidence))
}

fn parse_validated_feed_document(document: &PublicDocument) -> Option<(ValidatedFeed, Feed)> {
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
    let validated = ValidatedFeed {
        effective_url: document.effective_url.to_string(),
        format,
        has_audio_entries,
        publishing_activity: publishing_activity(&parsed, false, checked_at),
        podcast_publishing_activity: publishing_activity(&parsed, true, checked_at),
    };
    Some((validated, parsed))
}

fn podcast_discovery_evidence(
    feed: &Feed,
    activity: &FeedPublishingActivity,
) -> FeedDiscoveryEvidence {
    let mut episodes = feed
        .entries
        .iter()
        .filter(|entry| entry_audio_url(entry).is_some())
        .collect::<Vec<_>>();
    episodes.sort_by_key(|entry| std::cmp::Reverse(entry.published));
    let mut seen = HashSet::new();
    let recent_episode_titles = episodes
        .into_iter()
        .filter_map(|entry| entry.title.as_ref().and_then(readable_title))
        .filter(|title| seen.insert(title.clone()))
        .take(3)
        .map(|title| title.chars().take(120).collect())
        .collect();
    FeedDiscoveryEvidence {
        authors: feed
            .authors
            .iter()
            .take(8)
            .map(|author| author.name.chars().take(240).collect())
            .collect(),
        title: feed
            .title
            .as_ref()
            .and_then(readable_title)
            .map(|value| value.chars().take(120).collect()),
        description: feed
            .description
            .as_ref()
            .and_then(readable_title)
            .map(|value| value.chars().take(200).collect()),
        declared_language: feed
            .language
            .as_ref()
            .map(|value| value.chars().take(40).collect()),
        recent_episode_titles,
        latest_episode_published_at: activity.latest_item.as_ref().map(|item| item.published_at),
        observed_episodes_90_days: activity.observed_count,
    }
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
mod tests;
