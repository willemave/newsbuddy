use std::fmt::{self, Debug, Formatter};
use std::time::Duration;

use chrono::{DateTime, Utc};
use futures_util::StreamExt;
use newsly_extraction::PublicUrl;
use reqwest::header::{AUTHORIZATION, CONTENT_TYPE, HeaderValue};
use reqwest::{Client, StatusCode};
use secrecy::{ExposeSecret, SecretString};
use serde::{Deserialize, Serialize};
use thiserror::Error;
use url::Url;

const MAX_FIRECRAWL_RESPONSE_BYTES: usize = 2_500_000;

#[derive(Clone)]
pub struct FirecrawlClient {
    endpoint: Url,
    api_key: Option<SecretString>,
    http: Client,
    cost_usd: Option<f64>,
}

impl Debug for FirecrawlClient {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("FirecrawlClient")
            .field("endpoint", &self.endpoint)
            .field("api_key", &self.api_key.as_ref().map(|_| "[REDACTED]"))
            .field("cost_usd", &self.cost_usd)
            .finish_non_exhaustive()
    }
}

#[derive(Debug, Clone)]
pub(super) struct FirecrawlResult {
    pub(super) final_url: PublicUrl,
    pub(super) markdown: String,
    pub(super) title: Option<String>,
    pub(super) published_at: Option<DateTime<Utc>>,
    pub(super) status_code: u16,
    pub(super) cost_usd: Option<f64>,
}

impl FirecrawlClient {
    /// Build the Rust-owned fallback adapter. Missing credentials remain representable so ordinary
    /// startup does not require Firecrawl until the extractor explicitly requests it.
    ///
    /// # Errors
    ///
    /// Returns an error for an unsafe endpoint, malformed secret header, or HTTP client failure.
    pub fn new(
        endpoint: Url,
        api_key: Option<SecretString>,
        timeout: Duration,
        cost_usd: Option<f64>,
    ) -> Result<Self, FirecrawlError> {
        if !matches!(endpoint.scheme(), "http" | "https")
            || endpoint.host().is_none()
            || !endpoint.username().is_empty()
            || endpoint.password().is_some()
            || endpoint.fragment().is_some()
        {
            return Err(FirecrawlError::InvalidConfiguration(
                "endpoint must be a credential-free absolute HTTP(S) URL",
            ));
        }
        if let Some(secret) = &api_key {
            HeaderValue::from_str(secret.expose_secret()).map_err(|_| {
                FirecrawlError::InvalidConfiguration("API key cannot be represented as a header")
            })?;
        }
        let http = Client::builder()
            .connect_timeout(Duration::from_secs(5))
            .timeout(timeout)
            .redirect(reqwest::redirect::Policy::limited(3))
            .no_proxy()
            .build()
            .map_err(FirecrawlError::Transport)?;
        Ok(Self {
            endpoint,
            api_key,
            http,
            cost_usd,
        })
    }

    /// Execute one bounded Firecrawl v2 scrape.
    ///
    /// # Errors
    ///
    /// Returns typed retryability for configuration, transport, HTTP, size, and response failures.
    pub(super) async fn scrape(&self, url: &PublicUrl) -> Result<FirecrawlResult, FirecrawlError> {
        let Some(api_key) = &self.api_key else {
            return Err(FirecrawlError::Unavailable);
        };
        url.validate_dns()
            .await
            .map_err(FirecrawlError::PublicUrl)?;
        let request = FirecrawlRequest {
            url: url.as_str(),
            formats: ["markdown"],
            only_main_content: true,
            remove_base64_images: true,
            block_ads: true,
            proxy: "auto",
            location: FirecrawlLocation {
                country: "US",
                languages: ["en-US"],
            },
        };
        let response = self
            .http
            .post(self.endpoint.clone())
            .header(AUTHORIZATION, format!("Bearer {}", api_key.expose_secret()))
            .header(CONTENT_TYPE, "application/json")
            .json(&request)
            .send()
            .await
            .map_err(FirecrawlError::Transport)?;
        let status = response.status();
        if response
            .content_length()
            .is_some_and(|length| length > MAX_FIRECRAWL_RESPONSE_BYTES as u64)
        {
            return Err(FirecrawlError::ResponseTooLarge);
        }
        let mut body = Vec::new();
        let mut stream = response.bytes_stream();
        while let Some(chunk) = stream.next().await {
            let chunk = chunk.map_err(FirecrawlError::Transport)?;
            if body.len().saturating_add(chunk.len()) > MAX_FIRECRAWL_RESPONSE_BYTES {
                return Err(FirecrawlError::ResponseTooLarge);
            }
            body.extend_from_slice(&chunk);
        }
        let payload: FirecrawlResponse = serde_json::from_slice(&body)
            .map_err(|source| FirecrawlError::InvalidResponse { status, source })?;
        let data = payload.data.unwrap_or_default();
        if !status.is_success() {
            return Err(FirecrawlError::HttpStatus {
                status,
                message: bounded_message(payload.error.or(data.error)),
            });
        }
        let markdown = data
            .markdown
            .filter(|value| !value.trim().is_empty())
            .ok_or(FirecrawlError::EmptyMarkdown)?
            .trim()
            .to_owned();
        let metadata = data.metadata.unwrap_or_default();
        let final_url = match metadata.source_url {
            Some(candidate) => {
                PublicUrl::parse(candidate.trim()).map_err(FirecrawlError::PublicUrl)?
            }
            None => url.clone(),
        };
        final_url
            .validate_dns()
            .await
            .map_err(FirecrawlError::PublicUrl)?;
        let published_at = metadata
            .published_time
            .as_deref()
            .and_then(|value| DateTime::parse_from_rfc3339(value).ok())
            .map(|value| value.with_timezone(&Utc));
        Ok(FirecrawlResult {
            final_url,
            markdown,
            title: clean_optional(metadata.title, 500),
            published_at,
            status_code: status.as_u16(),
            cost_usd: self.cost_usd,
        })
    }
}

fn bounded_message(message: Option<String>) -> String {
    message
        .unwrap_or_else(|| "Firecrawl request failed".to_owned())
        .chars()
        .take(500)
        .collect()
}

fn clean_optional(value: Option<String>, max_chars: usize) -> Option<String> {
    let value = value?.trim().chars().take(max_chars).collect::<String>();
    (!value.is_empty()).then_some(value)
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct FirecrawlRequest<'a> {
    url: &'a str,
    formats: [&'static str; 1],
    only_main_content: bool,
    remove_base64_images: bool,
    block_ads: bool,
    proxy: &'static str,
    location: FirecrawlLocation,
}

#[derive(Debug, Serialize)]
struct FirecrawlLocation {
    country: &'static str,
    languages: [&'static str; 1],
}

#[derive(Debug, Default, Deserialize)]
struct FirecrawlResponse {
    #[serde(default)]
    data: Option<FirecrawlData>,
    #[serde(default)]
    error: Option<String>,
}

#[derive(Debug, Default, Deserialize)]
struct FirecrawlData {
    #[serde(default)]
    markdown: Option<String>,
    #[serde(default)]
    metadata: Option<FirecrawlMetadata>,
    #[serde(default)]
    error: Option<String>,
}

#[derive(Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase")]
struct FirecrawlMetadata {
    #[serde(default)]
    title: Option<String>,
    #[serde(default, rename = "sourceURL")]
    source_url: Option<String>,
    #[serde(default)]
    published_time: Option<String>,
}

#[derive(Debug, Error)]
pub enum FirecrawlError {
    #[error("Firecrawl fallback is required but FIRECRAWL_API_KEY is not configured")]
    Unavailable,
    #[error("invalid Firecrawl configuration: {0}")]
    InvalidConfiguration(&'static str),
    #[error("Firecrawl transport failed")]
    Transport(#[source] reqwest::Error),
    #[error("Firecrawl returned HTTP {status}: {message}")]
    HttpStatus { status: StatusCode, message: String },
    #[error("Firecrawl response exceeded the configured size bound")]
    ResponseTooLarge,
    #[error("Firecrawl returned invalid JSON with HTTP {status}")]
    InvalidResponse {
        status: StatusCode,
        #[source]
        source: serde_json::Error,
    },
    #[error("Firecrawl returned no usable markdown")]
    EmptyMarkdown,
    #[error("Firecrawl returned an unsafe public URL")]
    PublicUrl(#[source] newsly_extraction::ExtractionClientError),
}

impl FirecrawlError {
    pub fn retryable(&self) -> bool {
        match self {
            Self::Transport(_) => true,
            Self::HttpStatus { status, .. } => {
                status.is_server_error()
                    || *status == StatusCode::REQUEST_TIMEOUT
                    || *status == StatusCode::TOO_MANY_REQUESTS
            }
            Self::Unavailable
            | Self::InvalidConfiguration(_)
            | Self::ResponseTooLarge
            | Self::InvalidResponse { .. }
            | Self::EmptyMarkdown
            | Self::PublicUrl(_) => false,
        }
    }
}
