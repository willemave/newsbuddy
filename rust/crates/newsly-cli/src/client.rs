use std::time::Duration;

use newsly_contracts::{BulkMarkReadRequest, CliLinkStartRequest, ErrorEnvelope};
use reqwest::{Method, StatusCode, Url};
use serde::Serialize;
use serde_json::Value;
use thiserror::Error;

use crate::config::RuntimeConfig;

const MAX_ERROR_BODY_BYTES: usize = 1 << 20;
const CLIENT_NAME: &str = "rust_cli";

pub type QueryParameters = Vec<(String, String)>;

#[derive(Debug, Clone, Error, PartialEq)]
#[error("{message}")]
pub struct ApiError {
    pub message: String,
    pub status_code: Option<u16>,
    pub code: Option<String>,
    pub details: Option<Box<Value>>,
    pub retryable: Option<bool>,
    pub request_id: Option<String>,
}

impl ApiError {
    pub fn local(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
            status_code: None,
            code: None,
            details: None,
            retryable: None,
            request_id: None,
        }
    }

    pub fn local_with_details(message: impl Into<String>, details: Value) -> Self {
        Self {
            details: Some(Box::new(details)),
            ..Self::local(message)
        }
    }

    pub fn is_status(&self, status: StatusCode) -> bool {
        self.status_code == Some(status.as_u16())
    }

    fn replace_message_for_status(mut self, status: StatusCode, message: &'static str) -> Self {
        if self.is_status(status) {
            message.clone_into(&mut self.message);
        }
        self
    }
}

#[derive(Debug, Clone)]
pub struct Client {
    base_url: Url,
    api_key: Option<String>,
    version: Option<String>,
    http: reqwest::Client,
}

impl Client {
    /// Build a client from the resolved runtime configuration.
    ///
    /// # Errors
    ///
    /// Returns an error when the server URL or HTTP client configuration is invalid.
    pub fn new(
        config: &RuntimeConfig,
        timeout: Duration,
        client_version: impl Into<String>,
    ) -> Result<Self, ApiError> {
        let server_url = format!("{}/", config.server_url.trim().trim_end_matches('/'));
        let base_url = Url::parse(&server_url)
            .map_err(|error| ApiError::local(format!("invalid server URL: {error}")))?;
        if !matches!(base_url.scheme(), "http" | "https") || base_url.host_str().is_none() {
            return Err(ApiError::local(
                "invalid server URL: expected an absolute http or https URL",
            ));
        }

        let http = reqwest::Client::builder()
            .timeout(timeout)
            .build()
            .map_err(|error| ApiError::local(format!("build HTTP client: {error}")))?;
        let api_key = non_empty(&config.api_key);
        let client_version = client_version.into();
        let version = non_empty(&client_version);

        Ok(Self {
            base_url,
            api_key,
            version,
            http,
        })
    }

    /// Fetch one durable job.
    ///
    /// # Errors
    ///
    /// Returns an error when the request fails or the response is invalid.
    pub async fn get_job(&self, job_id: i64) -> Result<Value, ApiError> {
        self.request_json(Method::GET, &format!("/api/jobs/{job_id}"), true, &[], None)
            .await
    }

    /// Run provider-backed agent search.
    ///
    /// # Errors
    ///
    /// Returns an error when request encoding, transport, or response decoding fails.
    pub async fn search_agent<T>(&self, request: &T) -> Result<Value, ApiError>
    where
        T: Serialize + ?Sized,
    {
        self.post_authenticated("/api/agent/search", request).await
    }

    /// Start an onboarding discovery run.
    ///
    /// # Errors
    ///
    /// Returns an error when request encoding, transport, or response decoding fails.
    pub async fn start_onboarding<T>(&self, request: &T) -> Result<Value, ApiError>
    where
        T: Serialize + ?Sized,
    {
        self.post_authenticated("/api/agent/onboarding", request)
            .await
    }

    /// Fetch an onboarding discovery run.
    ///
    /// # Errors
    ///
    /// Returns an error when the request fails or the response is invalid.
    pub async fn get_onboarding(&self, run_id: i64) -> Result<Value, ApiError> {
        self.request_json(
            Method::GET,
            &format!("/api/agent/onboarding/{run_id}"),
            true,
            &[],
            None,
        )
        .await
    }

    /// Complete an onboarding run with the selected suggestions.
    ///
    /// # Errors
    ///
    /// Returns an error when request encoding, transport, or response decoding fails.
    pub async fn complete_onboarding<T>(&self, run_id: i64, request: &T) -> Result<Value, ApiError>
    where
        T: Serialize + ?Sized,
    {
        self.post_authenticated(&format!("/api/agent/onboarding/{run_id}/complete"), request)
            .await
    }

    /// List visible content with the supplied query parameters.
    ///
    /// # Errors
    ///
    /// Returns an error when the request fails or the response is invalid.
    pub async fn list_content(&self, query: &[(String, String)]) -> Result<Value, ApiError> {
        self.request_json(Method::GET, "/api/content/", true, query, None)
            .await
            .map_err(|error| {
                error.replace_message_for_status(StatusCode::NOT_FOUND, "content route not found")
            })
    }

    /// Fetch one canonical content item.
    ///
    /// # Errors
    ///
    /// Returns an error when the request fails or the response is invalid.
    pub async fn get_content(&self, content_id: i64) -> Result<Value, ApiError> {
        self.request_json(
            Method::GET,
            &format!("/api/content/{content_id}"),
            true,
            &[],
            None,
        )
        .await
        .map_err(|error| {
            error.replace_message_for_status(StatusCode::NOT_FOUND, "content not found")
        })
    }

    /// Submit a URL for processing.
    ///
    /// # Errors
    ///
    /// Returns an error when request encoding, transport, or response decoding fails.
    pub async fn submit_content<T>(&self, request: &T) -> Result<Value, ApiError>
    where
        T: Serialize + ?Sized,
    {
        self.post_authenticated("/api/content/submit", request)
            .await
            .map_err(|error| {
                error.replace_message_for_status(StatusCode::NOT_FOUND, "submit route not found")
            })
    }

    /// List visible content submission statuses.
    ///
    /// # Errors
    ///
    /// Returns an error when the request fails or the response is invalid.
    pub async fn list_content_submission_statuses(
        &self,
        query: &[(String, String)],
    ) -> Result<Value, ApiError> {
        self.request_json(
            Method::GET,
            "/api/content/submissions/list",
            true,
            query,
            None,
        )
        .await
        .map_err(|error| {
            error.replace_message_for_status(
                StatusCode::NOT_FOUND,
                "submission status route not found",
            )
        })
    }

    /// List visible news items.
    ///
    /// # Errors
    ///
    /// Returns an error when the request fails or the response is invalid.
    pub async fn list_news_items(&self, query: &[(String, String)]) -> Result<Value, ApiError> {
        self.request_json(Method::GET, "/api/news/items", true, query, None)
            .await
            .map_err(|error| {
                error.replace_message_for_status(StatusCode::NOT_FOUND, "news route not found")
            })
    }

    /// Fetch one canonical news item.
    ///
    /// # Errors
    ///
    /// Returns an error when the request fails or the response is invalid.
    pub async fn get_news_item(&self, news_item_id: i64) -> Result<Value, ApiError> {
        self.request_json(
            Method::GET,
            &format!("/api/news/items/{news_item_id}"),
            true,
            &[],
            None,
        )
        .await
        .map_err(|error| {
            error.replace_message_for_status(StatusCode::NOT_FOUND, "news item not found")
        })
    }

    /// Convert a news item into an article.
    ///
    /// # Errors
    ///
    /// Returns an error when the request fails or the response is invalid.
    pub async fn convert_news_item_to_article(&self, news_item_id: i64) -> Result<Value, ApiError> {
        self.request_json(
            Method::POST,
            &format!("/api/news/items/{news_item_id}/convert-to-article"),
            true,
            &[],
            None,
        )
        .await
        .map_err(|error| {
            error.replace_message_for_status(StatusCode::NOT_FOUND, "news item not found")
        })
    }

    /// Mark news items as read.
    ///
    /// # Errors
    ///
    /// Returns an error when the request fails or the response is invalid.
    pub async fn mark_news_items_read(
        &self,
        request: &BulkMarkReadRequest,
    ) -> Result<Value, ApiError> {
        self.post_authenticated("/api/news/items/mark-read", request)
            .await
            .map_err(|error| {
                error.replace_message_for_status(StatusCode::NOT_FOUND, "news items not found")
            })
    }

    /// List configured scraper sources.
    ///
    /// # Errors
    ///
    /// Returns an error when the request fails or the response is invalid.
    pub async fn list_sources(&self, query: &[(String, String)]) -> Result<Value, ApiError> {
        self.request_json(Method::GET, "/api/scrapers/", true, query, None)
            .await
    }

    /// Subscribe to a feed source.
    ///
    /// # Errors
    ///
    /// Returns an error when request encoding, transport, or response decoding fails.
    pub async fn subscribe_source<T>(&self, request: &T) -> Result<Value, ApiError>
    where
        T: Serialize + ?Sized,
    {
        self.post_authenticated("/api/scrapers/subscribe", request)
            .await
    }

    /// Start an unauthenticated CLI-link session.
    ///
    /// # Errors
    ///
    /// Returns an error when the request fails or the response is invalid.
    pub async fn start_cli_link(&self, device_name: Option<&str>) -> Result<Value, ApiError> {
        let request = serde_json::to_value(CliLinkStartRequest {
            device_name: device_name
                .filter(|name| !name.trim().is_empty())
                .map(str::to_owned),
        })
        .map_err(|error| {
            ApiError::local(format!(
                "POST /api/agent/cli/link/start encode request: {error}"
            ))
        })?;
        self.request_json(
            Method::POST,
            "/api/agent/cli/link/start",
            false,
            &[],
            Some(&request),
        )
        .await
    }

    /// Poll an unauthenticated CLI-link session.
    ///
    /// # Errors
    ///
    /// Returns an error when the request fails or the response is invalid.
    pub async fn poll_cli_link(
        &self,
        session_id: &str,
        poll_token: &str,
    ) -> Result<Value, ApiError> {
        let query = vec![("poll_token".to_owned(), poll_token.to_owned())];
        self.request_json(
            Method::GET,
            &format!("/api/agent/cli/link/{}", encode_path_segment(session_id)),
            false,
            &query,
            None,
        )
        .await
    }

    /// Fetch the desired local-library manifest.
    ///
    /// # Errors
    ///
    /// Returns an error when the request fails or the response is invalid.
    pub async fn get_library_manifest(&self, include_source: bool) -> Result<Value, ApiError> {
        let query = vec![("include_source".to_owned(), include_source.to_string())];
        self.request_json(
            Method::GET,
            "/api/agent/library/manifest",
            true,
            &query,
            None,
        )
        .await
    }

    /// Fetch one local-library document.
    ///
    /// # Errors
    ///
    /// Returns an error when the request fails or the response is invalid.
    pub async fn get_library_file(&self, relative_path: &str) -> Result<Value, ApiError> {
        let query = vec![("path".to_owned(), relative_path.to_owned())];
        self.request_json(Method::GET, "/api/agent/library/file", true, &query, None)
            .await
    }

    async fn post_authenticated<T>(&self, path: &str, body: &T) -> Result<Value, ApiError>
    where
        T: Serialize + ?Sized,
    {
        let body = serde_json::to_value(body)
            .map_err(|error| ApiError::local(format!("POST {path} encode request: {error}")))?;
        self.request_json(Method::POST, path, true, &[], Some(&body))
            .await
    }

    async fn request_json(
        &self,
        method: Method,
        path: &str,
        include_auth: bool,
        query: &[(String, String)],
        body: Option<&Value>,
    ) -> Result<Value, ApiError> {
        let endpoint = self
            .base_url
            .join(path.trim_start_matches('/'))
            .map_err(|error| ApiError::local(format!("{method} {path} build request: {error}")))?;
        let mut request = self
            .http
            .request(method.clone(), endpoint)
            .header(reqwest::header::ACCEPT, "application/json")
            .header("X-Newsly-Client", CLIENT_NAME);

        if let Some(version) = self.version.as_deref() {
            request = request.header("X-Newsly-Client-Version", version);
        }
        if include_auth && let Some(api_key) = self.api_key.as_deref() {
            request = request.bearer_auth(api_key);
        }
        if !query.is_empty() {
            request = request.query(query);
        }
        if let Some(body) = body {
            request = request.json(body);
        }

        let mut response = request
            .send()
            .await
            .map_err(|error| ApiError::local(format!("{method} {path} request failed: {error}")))?;
        let status = response.status();
        if !status.is_success() {
            let payload = read_limited_error_body(&mut response)
                .await
                .map_err(|error| {
                    ApiError::local(format!("{method} {path} read error response: {error}"))
                })?;
            return Err(decode_api_error(status, &payload));
        }

        let payload = response
            .bytes()
            .await
            .map_err(|error| ApiError::local(format!("{method} {path} read response: {error}")))?;
        if payload.is_empty() {
            return Ok(Value::Null);
        }
        serde_json::from_slice(&payload)
            .map_err(|error| ApiError::local(format!("{method} {path} decode response: {error}")))
    }
}

async fn read_limited_error_body(response: &mut reqwest::Response) -> reqwest::Result<Vec<u8>> {
    let mut body = Vec::new();
    while body.len() < MAX_ERROR_BODY_BYTES {
        let Some(chunk) = response.chunk().await? else {
            break;
        };
        let remaining = MAX_ERROR_BODY_BYTES - body.len();
        body.extend_from_slice(&chunk[..chunk.len().min(remaining)]);
    }
    Ok(body)
}

fn decode_api_error(status: StatusCode, body: &[u8]) -> ApiError {
    let fallback_message = format!("request failed with status {}", status.as_u16());
    let Ok(envelope) = serde_json::from_slice::<ErrorEnvelope>(body) else {
        return ApiError {
            message: fallback_message,
            status_code: Some(status.as_u16()),
            code: None,
            details: None,
            retryable: None,
            request_id: None,
        };
    };
    if envelope.code.trim().is_empty() {
        return ApiError {
            message: fallback_message,
            status_code: Some(status.as_u16()),
            code: None,
            details: None,
            retryable: None,
            request_id: None,
        };
    }

    ApiError {
        message: if envelope.message.trim().is_empty() {
            fallback_message
        } else {
            envelope.message
        },
        status_code: Some(status.as_u16()),
        code: non_empty(&envelope.code),
        details: envelope
            .details
            .map(|details| Box::new(Value::Object(details))),
        retryable: Some(envelope.retryable),
        request_id: non_empty(&envelope.request_id),
    }
}

fn non_empty(value: &str) -> Option<String> {
    let trimmed = value.trim();
    (!trimmed.is_empty()).then(|| trimmed.to_owned())
}

fn encode_path_segment(value: &str) -> String {
    let mut encoded = String::with_capacity(value.len());
    for byte in value.bytes() {
        if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'.' | b'_' | b'~') {
            encoded.push(char::from(byte));
        } else {
            use std::fmt::Write as _;
            let _ = write!(encoded, "%{byte:02X}");
        }
    }
    encoded
}

#[cfg(test)]
mod tests {
    use super::{decode_api_error, encode_path_segment};
    use reqwest::StatusCode;
    use serde_json::json;

    #[test]
    fn canonical_error_fields_are_preserved() {
        let body = serde_json::to_vec(&json!({
            "code": "conflict",
            "message": "already exists",
            "details": {"content_id": 42},
            "retryable": false,
            "request_id": "request-1"
        }))
        .unwrap();

        let error = decode_api_error(StatusCode::CONFLICT, &body);

        assert_eq!(error.message, "already exists");
        assert_eq!(error.status_code, Some(409));
        assert_eq!(error.code.as_deref(), Some("conflict"));
        assert_eq!(error.details.as_deref(), Some(&json!({"content_id": 42})));
        assert_eq!(error.retryable, Some(false));
        assert_eq!(error.request_id.as_deref(), Some("request-1"));
    }

    #[test]
    fn malformed_error_uses_status_fallback() {
        let error = decode_api_error(StatusCode::BAD_GATEWAY, b"not json");

        assert_eq!(error.message, "request failed with status 502");
        assert_eq!(error.status_code, Some(502));
        assert_eq!(error.code, None);
        assert_eq!(error.retryable, None);
    }

    #[test]
    fn path_segments_are_percent_encoded() {
        assert_eq!(encode_path_segment("session /?"), "session%20%2F%3F");
        assert_eq!(encode_path_segment("abc-123_~"), "abc-123_~");
    }
}
