use std::collections::BTreeMap;
use std::fmt::{self, Debug, Formatter};
use std::sync::Arc;
use std::time::Duration;

use feed_rs::model::FeedType;
use secrecy::SecretString;
use thiserror::Error;
use tokio::time::Instant;
use tokio_util::sync::CancellationToken;

use crate::{
    ControlPlaneConfig, DirectE2bProvider, E2bError, FeedFetchRequest, FileLimits, NetworkPolicy,
    SandboxId, SandboxProvider, SandboxRequest, VmFeedProvider,
};

const CANDIDATE_CURL_EXIT_CODES: [i32; 16] =
    [3, 6, 7, 8, 16, 18, 22, 28, 35, 47, 52, 55, 56, 60, 61, 63];
const FEED_COMMAND_BUDGET: Duration = Duration::from_secs(35);

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

/// Validates feed candidates inside a short-lived, deny-by-default E2B sandbox.
///
/// The validator has no database dependency. Runtime ownership of the singleton feed namespace is
/// enforced by the caller before invoking it; each request still creates and destroys an isolated
/// secure sandbox so malformed or hostile feeds never run in an application process.
#[derive(Clone)]
pub struct FeedValidator {
    provider: Option<Arc<DirectE2bProvider>>,
    template_id: Arc<str>,
    sandbox_timeout_seconds: u32,
}

impl Debug for FeedValidator {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("FeedValidator")
            .field("provider", &self.provider.as_ref().map(|_| "[CONFIGURED]"))
            .field("template_id", &self.template_id)
            .field("sandbox_timeout_seconds", &self.sandbox_timeout_seconds)
            .finish()
    }
}

impl FeedValidator {
    pub fn new(
        api_key: Option<SecretString>,
        template_id: &str,
        sandbox_timeout: Duration,
    ) -> Result<Self, FeedValidationError> {
        let template_id = template_id.trim().to_owned();
        if template_id.is_empty() || template_id.len() > 256 {
            return Err(FeedValidationError::InvalidTemplate);
        }
        let sandbox_timeout_seconds = u32::try_from(sandbox_timeout.as_secs())
            .ok()
            .filter(|value| *value > 0)
            .ok_or(FeedValidationError::InvalidTimeout)?;
        let provider = api_key
            .map(|api_key| {
                let config = ControlPlaneConfig::production(api_key)?;
                DirectE2bProvider::new(config, FileLimits::default()).map(Arc::new)
            })
            .transpose()?;
        Ok(Self {
            provider,
            template_id: template_id.into(),
            sandbox_timeout_seconds,
        })
    }

    pub fn is_configured(&self) -> bool {
        self.provider.is_some()
    }

    /// Fetches, parses, and validates one candidate while guaranteeing sandbox cleanup is
    /// attempted on every provider outcome.
    pub async fn validate_feed_url(
        &self,
        url: &str,
    ) -> Result<Option<String>, FeedValidationError> {
        Ok(self
            .validate_feed(url)
            .await?
            .map(|feed| feed.effective_url))
    }

    /// Fetches and parses one candidate while retaining its actual RSS-versus-Atom format.
    pub async fn validate_feed(
        &self,
        url: &str,
    ) -> Result<Option<ValidatedFeed>, FeedValidationError> {
        let provider = self
            .provider
            .as_ref()
            .ok_or(FeedValidationError::MissingApiKey)?;
        let request = match FeedFetchRequest::parse(url) {
            Ok(request) => request,
            Err(E2bError::InvalidInput(_)) => return Ok(None),
            Err(error) => return Err(error.into()),
        };
        let sandbox_request = SandboxRequest {
            template_id: self.template_id.to_string(),
            timeout: self.sandbox_timeout_seconds,
            auto_pause: false,
            auto_pause_memory: false,
            secure: true,
            allow_internet_access: false,
            metadata: BTreeMap::from([
                ("feature".to_owned(), "feed_research".to_owned()),
                ("user_id".to_owned(), "0".to_owned()),
                ("vm_namespace".to_owned(), "user:0".to_owned()),
            ]),
            env_vars: BTreeMap::from([("NEWSLY_USER_ID".to_owned(), "0".to_owned())]),
            network: Some(NetworkPolicy::deny_all()),
        };
        let deadline = Instant::now()
            .checked_add(FEED_COMMAND_BUDGET)
            .ok_or(FeedValidationError::InvalidTimeout)?;
        let sandbox = provider.create_sandbox(&sandbox_request).await?;
        let mut cleanup =
            EphemeralSandboxCleanup::new(Arc::clone(provider), sandbox.sandbox_id.clone());
        let result = provider
            .fetch_feed(&sandbox, &request, deadline, CancellationToken::new())
            .await;
        cleanup.destroy().await;
        let result = result?;
        if result.curl_exit != 0 {
            if CANDIDATE_CURL_EXIT_CODES.contains(&result.curl_exit) {
                return Ok(None);
            }
            return Err(FeedValidationError::SandboxFetch {
                exit_code: result.curl_exit,
                diagnostic: result.stderr.chars().take(500).collect(),
            });
        }
        if result.status >= 400 || result.status == 0 {
            return Ok(None);
        }
        if !has_feed_document_root(&result.body) {
            return Ok(None);
        }
        let parsed = match feed_rs::parser::parse(result.body.as_ref()) {
            Ok(parsed) => parsed,
            Err(error) => {
                tracing::debug!(
                    error = %error,
                    url = %result.effective_url,
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
            effective_url: result.effective_url.to_string(),
            format,
            has_audio_entries: has_audio_entries(&parsed),
        }))
    }
}

/// Cancellation-safe owner for an ephemeral sandbox. Dropping an in-flight validation future
/// schedules destruction on the current Tokio runtime, while the ordinary completed path awaits
/// destruction before returning.
#[derive(Debug)]
struct EphemeralSandboxCleanup {
    provider: Arc<DirectE2bProvider>,
    sandbox_id: Option<SandboxId>,
}

impl EphemeralSandboxCleanup {
    fn new(provider: Arc<DirectE2bProvider>, sandbox_id: SandboxId) -> Self {
        Self {
            provider,
            sandbox_id: Some(sandbox_id),
        }
    }

    async fn destroy(&mut self) {
        let Some(sandbox_id) = self.sandbox_id.clone() else {
            return;
        };
        match self.provider.kill_sandbox(&sandbox_id).await {
            Ok(_) => self.sandbox_id = None,
            Err(error) => {
                tracing::warn!(
                    sandbox_id = %sandbox_id,
                    error = %error,
                    "unable to destroy ephemeral E2B feed-validation sandbox"
                );
                // Retain the exact ID so Drop schedules one cancellation-safe cleanup retry.
            }
        }
    }
}

impl Drop for EphemeralSandboxCleanup {
    fn drop(&mut self) {
        let Some(sandbox_id) = self.sandbox_id.take() else {
            return;
        };
        let provider = Arc::clone(&self.provider);
        let Ok(runtime) = tokio::runtime::Handle::try_current() else {
            tracing::error!(
                sandbox_id = %sandbox_id,
                "unable to schedule ephemeral E2B sandbox cleanup outside a Tokio runtime"
            );
            return;
        };
        runtime.spawn(async move {
            if let Err(error) = provider.kill_sandbox(&sandbox_id).await {
                tracing::warn!(
                    sandbox_id = %sandbox_id,
                    error = %error,
                    "unable to destroy cancelled E2B feed-validation sandbox"
                );
            }
        });
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
    feed.entries.iter().any(|entry| {
        entry
            .content
            .as_ref()
            .and_then(|content| content.src.as_ref())
            .is_some_and(|link| is_audio_link(link.media_type.as_deref(), &link.href))
            || entry
                .links
                .iter()
                .any(|link| is_audio_link(link.media_type.as_deref(), &link.href))
            || entry.media.iter().any(|media| {
                media.content.iter().any(|content| {
                    let media_type = content.content_type.as_ref().map(ToString::to_string);
                    let url = content.url.as_ref().map(ToString::to_string);
                    url.as_deref()
                        .is_some_and(|url| is_audio_link(media_type.as_deref(), url))
                })
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
    #[error("E2B_API_KEY is required for scraper feed validation")]
    MissingApiKey,
    #[error("the E2B feed-validation template id is invalid")]
    InvalidTemplate,
    #[error("the E2B feed-validation timeout is invalid")]
    InvalidTimeout,
    #[error("sandbox feed fetch failed with curl exit {exit_code}: {diagnostic}")]
    SandboxFetch { exit_code: i32, diagnostic: String },
    #[error(transparent)]
    E2b(#[from] E2bError),
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
