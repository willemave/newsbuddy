//! Bounded host adapter for feed retrieval inside an E2B sandbox.

use std::collections::BTreeMap;
use std::fmt;
use std::time::Duration;

use async_trait::async_trait;
use base64::Engine as _;
use base64::engine::general_purpose::STANDARD as BASE64;
use bytes::Bytes;
use futures_util::stream;
use http::{HeaderName, HeaderValue};
use serde::{Deserialize, Serialize};
use tokio::time::Instant;
use tokio_util::sync::CancellationToken;
use url::Url;
use uuid::Uuid;

use crate::bootstrap::{VM_BOOTSTRAP_EXECUTABLE, VmBootstrapClient};
use crate::error::E2bError;
use crate::files::BoxByteStream;
use crate::network::NetworkPolicy;
use crate::session::DirectE2bProvider;
use crate::types::{OutputLimits, SandboxHandle, SandboxPath, SandboxUser};

pub const MAX_FEED_RESPONSE_BYTES: usize = 2_000_000;

const MAX_FEED_REQUEST_BYTES: usize = 256 * 1024;
const MAX_FEED_URL_BYTES: usize = 8 * 1024;
const MAX_FEED_HEADERS: usize = 64;
const MAX_HEADER_NAME_BYTES: usize = 128;
const MAX_HEADER_VALUE_BYTES: usize = 8 * 1024;
const MAX_TOTAL_HEADER_BYTES: usize = 64 * 1024;
const MAX_RAW_RESPONSE_HEADER_BYTES: usize = 256 * 1024;
const MAX_CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
const MAX_REQUEST_TIMEOUT: Duration = Duration::from_secs(30);
const COMMAND_TIMEOUT_OVERHEAD: Duration = Duration::from_secs(5);
const CLEANUP_TIMEOUT: Duration = Duration::from_secs(5);

#[derive(Clone)]
pub struct FeedFetchRequest {
    pub url: Url,
    pub headers: BTreeMap<String, String>,
    pub connect_timeout: Duration,
    pub request_timeout: Duration,
    pub max_response_bytes: usize,
}

impl FeedFetchRequest {
    pub fn parse(url: &str) -> Result<Self, E2bError> {
        let url = Url::parse(url)
            .map_err(|error| E2bError::InvalidInput(format!("invalid feed URL: {error}")))?;
        Self::new(url)
    }

    pub fn new(url: Url) -> Result<Self, E2bError> {
        let request = Self {
            url,
            headers: BTreeMap::new(),
            connect_timeout: MAX_CONNECT_TIMEOUT,
            request_timeout: MAX_REQUEST_TIMEOUT,
            max_response_bytes: MAX_FEED_RESPONSE_BYTES,
        };
        request.validate()?;
        Ok(request)
    }

    pub fn validate(&self) -> Result<(), E2bError> {
        validate_feed_url(&self.url)?;
        if self.connect_timeout.is_zero()
            || self.connect_timeout > MAX_CONNECT_TIMEOUT
            || self.request_timeout.is_zero()
            || self.request_timeout > MAX_REQUEST_TIMEOUT
            || self.connect_timeout > self.request_timeout
        {
            return Err(E2bError::InvalidInput(
                "feed timeouts must be positive, connect <= request, connect <= 10s, and request <= 30s"
                    .to_owned(),
            ));
        }
        if self.max_response_bytes == 0 || self.max_response_bytes > MAX_FEED_RESPONSE_BYTES {
            return Err(E2bError::InvalidInput(format!(
                "feed response limit must be between 1 and {MAX_FEED_RESPONSE_BYTES} bytes"
            )));
        }
        validate_headers(&self.headers)
    }

    fn network_policy(&self) -> Result<NetworkPolicy, E2bError> {
        let host = self.url.host_str().ok_or_else(|| {
            E2bError::InvalidInput("feed URL must include a network host".to_owned())
        })?;
        NetworkPolicy::allow_hosts([host.to_owned()])
    }

    fn encode(&self) -> Result<Vec<u8>, E2bError> {
        self.validate()?;
        let payload = serde_json::to_vec(&FeedBatchRequestWire {
            urls: [self.url.as_str()],
            headers: &self.headers,
            connect_timeout: self.connect_timeout.as_secs_f64(),
            max_time: self.request_timeout.as_secs_f64(),
            max_bytes: self.max_response_bytes,
        })
        .map_err(|error| E2bError::Protocol(format!("unable to encode feed request: {error}")))?;
        if payload.len() > MAX_FEED_REQUEST_BYTES {
            return Err(E2bError::FileTooLarge {
                limit_bytes: MAX_FEED_REQUEST_BYTES,
                observed_bytes: u64::try_from(payload.len()).unwrap_or(u64::MAX),
            });
        }
        Ok(payload)
    }
}

impl fmt::Debug for FeedFetchRequest {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("FeedFetchRequest")
            .field("url", &self.url)
            .field("header_names", &self.headers.keys().collect::<Vec<_>>())
            .field("header_values", &"[REDACTED]")
            .field("connect_timeout", &self.connect_timeout)
            .field("request_timeout", &self.request_timeout)
            .field("max_response_bytes", &self.max_response_bytes)
            .finish()
    }
}

#[derive(Clone, Eq, PartialEq)]
pub struct FeedFetchResult {
    pub requested_url: Url,
    pub effective_url: Url,
    pub status: u16,
    pub raw_headers: Bytes,
    pub body: Bytes,
    pub curl_exit: i32,
    pub stderr: String,
}

impl fmt::Debug for FeedFetchResult {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("FeedFetchResult")
            .field("requested_url", &self.requested_url)
            .field("effective_url", &self.effective_url)
            .field("status", &self.status)
            .field("raw_header_bytes", &self.raw_headers.len())
            .field("body_bytes", &self.body.len())
            .field("curl_exit", &self.curl_exit)
            .field("stderr", &self.stderr)
            .finish()
    }
}

/// DB-independent feed-fetch boundary. The caller owns sandbox acquisition and lifetime.
#[async_trait]
pub trait VmFeedProvider: Send + Sync {
    async fn fetch_feed(
        &self,
        sandbox: &SandboxHandle,
        request: &FeedFetchRequest,
        absolute_deadline: Instant,
        cancellation: CancellationToken,
    ) -> Result<FeedFetchResult, E2bError>;
}

#[async_trait]
impl VmFeedProvider for DirectE2bProvider {
    async fn fetch_feed(
        &self,
        sandbox: &SandboxHandle,
        request: &FeedFetchRequest,
        absolute_deadline: Instant,
        cancellation: CancellationToken,
    ) -> Result<FeedFetchResult, E2bError> {
        request.validate()?;
        let deadline = bounded_feed_deadline(absolute_deadline, request.request_timeout)?;
        let policy = request.network_policy()?;
        let payload = request.encode()?;
        let payload_len = payload.len();
        let remote_request = SandboxPath::parse(format!(
            "/tmp/newsly-feed-request-{}.json",
            Uuid::new_v4().simple()
        ))?;
        let source: BoxByteStream =
            Box::pin(stream::once(std::future::ready(Ok(Bytes::from(payload)))));
        let root = SandboxUser::root();
        let upload = self.file_client().upload_sandbox_path(
            sandbox,
            &remote_request,
            root.as_str(),
            u64::try_from(payload_len).unwrap_or(u64::MAX),
            source,
            remaining(deadline)?,
        );
        let upload_result = tokio::select! {
            () = cancellation.cancelled() => Err(E2bError::Cancelled),
            result = tokio::time::timeout_at(deadline, upload) => {
                result.map_err(|_| E2bError::Deadline)?
            }
        };
        if let Err(error) = upload_result {
            cleanup_feed_request(self.vm_bootstrap_client(), sandbox, &remote_request).await;
            return Err(error);
        }

        let command_result = self
            .with_network_policy(&sandbox.sandbox_id, &policy, || {
                self.vm_bootstrap_client().run_command(
                    sandbox,
                    "fetch_feed",
                    "/bin/sh",
                    vec![
                        "-c".to_owned(),
                        "exec \"$1\" feed fetch-batch < \"$2\"".to_owned(),
                        "newsly-feed-fetch".to_owned(),
                        VM_BOOTSTRAP_EXECUTABLE.to_owned(),
                        remote_request.as_str().to_owned(),
                    ],
                    Some(SandboxUser::root()),
                    deadline,
                    feed_output_limits(request.max_response_bytes),
                    cancellation,
                )
            })
            .await;
        cleanup_feed_request(self.vm_bootstrap_client(), sandbox, &remote_request).await;
        let command_result = command_result?;
        parse_feed_result(&command_result.output.stdout, request)
    }
}

#[derive(Debug, Serialize)]
struct FeedBatchRequestWire<'a> {
    urls: [&'a str; 1],
    headers: &'a BTreeMap<String, String>,
    connect_timeout: f64,
    max_time: f64,
    max_bytes: usize,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct FeedBatchRowWire {
    index: usize,
    url: String,
    effective_url: String,
    status: u16,
    headers_b64: String,
    body_b64: String,
    curl_exit: i32,
    stderr: String,
}

fn parse_feed_result(
    stdout: &str,
    request: &FeedFetchRequest,
) -> Result<FeedFetchResult, E2bError> {
    let mut lines = stdout.lines().filter(|line| !line.trim().is_empty());
    let line = lines
        .next()
        .ok_or_else(|| E2bError::Protocol("feed helper returned no result row".to_owned()))?;
    if lines.next().is_some() {
        return Err(E2bError::Protocol(
            "feed helper returned more than one result row".to_owned(),
        ));
    }
    let row: FeedBatchRowWire = serde_json::from_str(line)
        .map_err(|error| E2bError::Protocol(format!("invalid feed helper JSONL: {error}")))?;
    if row.index != 0 || row.url != request.url.as_str() {
        return Err(E2bError::Protocol(
            "feed helper result does not match the requested URL".to_owned(),
        ));
    }
    if row.status > 599 || !(0..=255).contains(&row.curl_exit) {
        return Err(E2bError::Protocol(
            "feed helper returned an invalid HTTP status or curl exit code".to_owned(),
        ));
    }
    let effective_url = Url::parse(&row.effective_url)
        .map_err(|error| E2bError::Protocol(format!("invalid effective feed URL: {error}")))?;
    validate_feed_url(&effective_url)
        .map_err(|error| E2bError::Protocol(format!("invalid effective feed URL: {error}")))?;
    let raw_headers = decode_bounded(
        "feed response headers",
        &row.headers_b64,
        MAX_RAW_RESPONSE_HEADER_BYTES,
    )?;
    let body = decode_bounded(
        "feed response body",
        &row.body_b64,
        request.max_response_bytes,
    )?;
    if row.stderr.len() > 8 * 1024 {
        return Err(E2bError::Protocol(
            "feed helper diagnostic exceeded its output bound".to_owned(),
        ));
    }
    Ok(FeedFetchResult {
        requested_url: request.url.clone(),
        effective_url,
        status: row.status,
        raw_headers: Bytes::from(raw_headers),
        body: Bytes::from(body),
        curl_exit: row.curl_exit,
        stderr: row.stderr,
    })
}

fn validate_feed_url(url: &Url) -> Result<(), E2bError> {
    if url.as_str().len() > MAX_FEED_URL_BYTES
        || !matches!(url.scheme(), "http" | "https")
        || url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
    {
        return Err(E2bError::InvalidInput(
            "feed URL must be bounded HTTP(S), include a host, and omit credentials".to_owned(),
        ));
    }
    Ok(())
}

fn validate_headers(headers: &BTreeMap<String, String>) -> Result<(), E2bError> {
    if headers.len() > MAX_FEED_HEADERS {
        return Err(E2bError::InvalidInput(format!(
            "feed request contains more than {MAX_FEED_HEADERS} headers"
        )));
    }
    let mut total_bytes = 0_usize;
    for (name, value) in headers {
        if name.len() > MAX_HEADER_NAME_BYTES
            || value.len() > MAX_HEADER_VALUE_BYTES
            || HeaderName::from_bytes(name.as_bytes()).is_err()
            || HeaderValue::from_str(value).is_err()
        {
            return Err(E2bError::InvalidInput(format!(
                "feed request contains invalid header {name:?}"
            )));
        }
        total_bytes = total_bytes.saturating_add(name.len() + value.len() + 4);
    }
    if total_bytes > MAX_TOTAL_HEADER_BYTES {
        return Err(E2bError::InvalidInput(format!(
            "feed request headers exceed {MAX_TOTAL_HEADER_BYTES} bytes"
        )));
    }
    Ok(())
}

fn decode_bounded(label: &str, encoded: &str, limit: usize) -> Result<Vec<u8>, E2bError> {
    let maximum_encoded = limit.div_ceil(3).saturating_mul(4);
    if encoded.len() > maximum_encoded {
        return Err(E2bError::FileTooLarge {
            limit_bytes: limit,
            observed_bytes: u64::try_from(encoded.len()).unwrap_or(u64::MAX),
        });
    }
    let decoded = BASE64
        .decode(encoded)
        .map_err(|error| E2bError::Protocol(format!("invalid base64 {label}: {error}")))?;
    if decoded.len() > limit {
        return Err(E2bError::FileTooLarge {
            limit_bytes: limit,
            observed_bytes: u64::try_from(decoded.len()).unwrap_or(u64::MAX),
        });
    }
    Ok(decoded)
}

fn bounded_feed_deadline(
    requested: Instant,
    request_timeout: Duration,
) -> Result<Instant, E2bError> {
    let now = Instant::now();
    if requested <= now {
        return Err(E2bError::Deadline);
    }
    let maximum = now
        .checked_add(request_timeout.saturating_add(COMMAND_TIMEOUT_OVERHEAD))
        .ok_or_else(|| E2bError::InvalidInput("feed timeout is too large".to_owned()))?;
    Ok(requested.min(maximum))
}

fn remaining(deadline: Instant) -> Result<Duration, E2bError> {
    deadline
        .checked_duration_since(Instant::now())
        .filter(|duration| !duration.is_zero())
        .ok_or(E2bError::Deadline)
}

fn feed_output_limits(response_bytes: usize) -> OutputLimits {
    let encoded_body = response_bytes.div_ceil(3).saturating_mul(4);
    let encoded_headers = MAX_RAW_RESPONSE_HEADER_BYTES.div_ceil(3).saturating_mul(4);
    let stdout_bytes = encoded_body
        .saturating_add(encoded_headers)
        .saturating_add(MAX_FEED_URL_BYTES * 4)
        .saturating_add(64 * 1024);
    OutputLimits {
        stdout_bytes,
        stderr_bytes: 64 * 1024,
        combined_bytes: stdout_bytes.saturating_add(64 * 1024),
        event_bytes: stdout_bytes,
        channel_capacity: 16,
    }
}

async fn cleanup_feed_request(
    bootstrap: &VmBootstrapClient,
    sandbox: &SandboxHandle,
    remote_request: &SandboxPath,
) {
    let Some(deadline) = Instant::now().checked_add(CLEANUP_TIMEOUT) else {
        return;
    };
    if let Err(error) = bootstrap
        .run_command(
            sandbox,
            "cleanup_feed_request",
            "/bin/rm",
            vec![
                "-f".to_owned(),
                "--".to_owned(),
                remote_request.as_str().to_owned(),
            ],
            Some(SandboxUser::root()),
            deadline,
            OutputLimits {
                stdout_bytes: 4 * 1024,
                stderr_bytes: 4 * 1024,
                combined_bytes: 8 * 1024,
                event_bytes: 8 * 1024,
                channel_capacity: 4,
            },
            CancellationToken::new(),
        )
        .await
    {
        tracing::warn!(
            sandbox_id = %sandbox.sandbox_id,
            path = remote_request.as_str(),
            error = %error,
            "unable to remove staged E2B feed request"
        );
    }
}

#[cfg(test)]
mod tests {
    use base64::Engine as _;
    use base64::engine::general_purpose::STANDARD as BASE64;

    use super::{FeedFetchRequest, parse_feed_result};

    #[test]
    fn request_rejects_non_http_urls_and_header_injection() {
        assert!(FeedFetchRequest::parse("file:///etc/passwd").is_err());
        let mut request = FeedFetchRequest::parse("https://example.com/feed.xml").unwrap();
        request
            .headers
            .insert("X-Test".to_owned(), "ok\r\nInjected: yes".to_owned());
        assert!(request.validate().is_err());
    }

    #[test]
    fn result_parser_decodes_one_matching_bounded_row() {
        let request = FeedFetchRequest::parse("https://example.com/feed.xml").unwrap();
        let stdout = format!(
            r#"{{"index":0,"url":"https://example.com/feed.xml","effective_url":"https://example.com/final.xml","status":200,"headers_b64":"{}","body_b64":"{}","curl_exit":0,"stderr":""}}"#,
            BASE64.encode(b"HTTP/1.1 200 OK\r\n\r\n"),
            BASE64.encode(b"<rss/>")
        );
        let result = parse_feed_result(&stdout, &request).unwrap();
        assert_eq!(result.status, 200);
        assert_eq!(result.body.as_ref(), b"<rss/>");
        assert_eq!(
            result.effective_url.as_str(),
            "https://example.com/final.xml"
        );
    }

    #[test]
    fn result_parser_rejects_wrong_order_or_extra_rows() {
        let request = FeedFetchRequest::parse("https://example.com/feed.xml").unwrap();
        let wrong = r#"{"index":1,"url":"https://example.com/feed.xml","effective_url":"https://example.com/feed.xml","status":200,"headers_b64":"","body_b64":"","curl_exit":0,"stderr":""}"#;
        assert!(parse_feed_result(wrong, &request).is_err());
        assert!(parse_feed_result(&format!("{wrong}\n{wrong}"), &request).is_err());
    }
}
