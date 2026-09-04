//! Fast, bounded validation for untrusted RSS and Atom candidates.
//!
//! Candidate documents are fetched as inert bytes through a public-network-only HTTP client,
//! then parsed by Rust. No candidate content is executed, so validation does not require a VM.

use std::time::Duration;

use feed_rs::model::FeedType;
use futures_util::{StreamExt as _, stream};
use newsly_extraction::{ExtractionClientError, PublicUrl};
use reqwest::{StatusCode, header};
use thiserror::Error;

const CONNECT_TIMEOUT: Duration = Duration::from_secs(5);
const REQUEST_TIMEOUT: Duration = Duration::from_secs(15);
const MAX_RESPONSE_BYTES: usize = 2_000_000;
const MAX_REDIRECTS: usize = 5;
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
}

#[derive(Clone, Debug, Default)]
pub struct FeedValidator;

impl FeedValidator {
    #[must_use]
    pub const fn new() -> Self {
        Self
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
        if !has_feed_document_root(&document.body) {
            return Ok(None);
        }
        let parsed = match feed_rs::parser::parse(document.body.as_slice()) {
            Ok(parsed) => parsed,
            Err(error) => {
                tracing::debug!(
                    error = %error,
                    url = %document.effective_url,
                    "feed parser rejected candidate"
                );
                return Ok(None);
            }
        };
        let Some(format) = validated_feed_format(&parsed.feed_type) else {
            return Ok(None);
        };
        if !has_feed_semantics(&parsed) {
            return Ok(None);
        }
        Ok(Some(ValidatedFeed {
            effective_url: document.effective_url.to_string(),
            format,
            has_audio_entries: has_audio_entries(&parsed),
        }))
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

    async fn fetch(&self, raw_url: &str) -> Result<Option<FeedDocument>, FeedValidationError> {
        let Ok(mut current) = PublicUrl::parse(raw_url) else {
            return Ok(None);
        };
        for redirect_count in 0..=MAX_REDIRECTS {
            let addresses = match current.resolve_public_addresses().await {
                Ok(addresses) => addresses,
                Err(ExtractionClientError::DnsResolution { .. }) => {
                    return Err(FeedValidationError::PublicUrlResolution);
                }
                Err(_) => return Ok(None),
            };
            let host = current
                .as_url()
                .host_str()
                .ok_or(FeedValidationError::InvalidUrl)?;
            let mut builder = reqwest::Client::builder()
                .connect_timeout(CONNECT_TIMEOUT)
                .timeout(REQUEST_TIMEOUT)
                .redirect(reqwest::redirect::Policy::none())
                .no_proxy();
            if host.parse::<std::net::IpAddr>().is_err() {
                builder = builder.resolve_to_addrs(host, &addresses);
            }
            let client = builder.build()?;
            let response = client
                .get(current.as_url().clone())
                .header(header::USER_AGENT, USER_AGENT)
                .header(header::ACCEPT, ACCEPT)
                .send()
                .await?;
            if response.status().is_redirection() {
                if redirect_count == MAX_REDIRECTS {
                    return Ok(None);
                }
                let Some(location) = response
                    .headers()
                    .get(header::LOCATION)
                    .and_then(|value| value.to_str().ok())
                else {
                    return Ok(None);
                };
                let Ok(next) = current.as_url().join(location) else {
                    return Ok(None);
                };
                let Ok(next) = PublicUrl::parse(next.as_str()) else {
                    return Ok(None);
                };
                current = next;
                continue;
            }
            if response.status() == StatusCode::TOO_MANY_REQUESTS
                || response.status().is_server_error()
            {
                return Err(FeedValidationError::HttpStatus(response.status()));
            }
            if !response.status().is_success() {
                return Ok(None);
            }
            if response
                .content_length()
                .is_some_and(|length| length > MAX_RESPONSE_BYTES as u64)
            {
                return Ok(None);
            }
            let mut body = Vec::new();
            let mut chunks = response.bytes_stream();
            while let Some(chunk) = chunks.next().await {
                let chunk = chunk?;
                if body.len().saturating_add(chunk.len()) > MAX_RESPONSE_BYTES {
                    return Ok(None);
                }
                body.extend_from_slice(&chunk);
            }
            return Ok(Some(FeedDocument {
                effective_url: current,
                body,
            }));
        }
        Ok(None)
    }
}

#[derive(Debug)]
struct FeedDocument {
    effective_url: PublicUrl,
    body: Vec<u8>,
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
            [".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav"]
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

#[derive(Debug, Error)]
pub enum FeedValidationError {
    #[error("feed validator HTTP client failed")]
    Http(#[from] reqwest::Error),
    #[error("feed host could not be resolved on the public network")]
    PublicUrlResolution,
    #[error("feed URL is invalid")]
    InvalidUrl,
    #[error("feed host returned retryable HTTP status {0}")]
    HttpStatus(StatusCode),
}

#[cfg(test)]
mod tests {
    use feed_rs::model::FeedType;

    use super::{
        ValidatedFeedFormat, has_audio_entries, has_feed_document_root, validated_feed_format,
    };

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
}
