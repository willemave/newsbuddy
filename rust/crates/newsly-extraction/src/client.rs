use std::fmt::{self, Debug, Formatter};
use std::time::Duration;

use futures_util::StreamExt;
use reqwest::header::{ACCEPT, CONTENT_TYPE, HeaderValue};
use secrecy::{ExposeSecret, SecretString};
use url::Url;

use crate::{EXTRACTION_SCHEMA_VERSION, ExtractRequest, ExtractResult, ExtractionClientError};

#[derive(Clone)]
pub struct DocumentExtractorConfig {
    pub base_url: Url,
    pub shared_secret: SecretString,
    pub connect_timeout: Duration,
    pub request_timeout: Duration,
    pub max_request_bytes: usize,
    pub max_response_bytes: usize,
}

impl Debug for DocumentExtractorConfig {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("DocumentExtractorConfig")
            .field("base_url", &self.base_url)
            .field("shared_secret", &"[REDACTED]")
            .field("connect_timeout", &self.connect_timeout)
            .field("request_timeout", &self.request_timeout)
            .field("max_request_bytes", &self.max_request_bytes)
            .field("max_response_bytes", &self.max_response_bytes)
            .finish()
    }
}

impl DocumentExtractorConfig {
    /// Build a configuration using conservative request and response bounds.
    ///
    /// # Errors
    ///
    /// Returns an error unless `base_url` is an absolute HTTP(S) service URL without credentials,
    /// query parameters, or a fragment. Private service hosts are allowed here; only document URLs
    /// use [`crate::PublicUrl`].
    pub fn new(base_url: Url, shared_secret: SecretString) -> Result<Self, ExtractionClientError> {
        if !matches!(base_url.scheme(), "http" | "https")
            || base_url.cannot_be_a_base()
            || base_url.host().is_none()
            || !base_url.username().is_empty()
            || base_url.password().is_some()
            || base_url.path() != "/"
            || base_url.query().is_some()
            || base_url.fragment().is_some()
        {
            return Err(ExtractionClientError::InvalidConfiguration(
                "base_url must be an absolute credential-free HTTP(S) service origin",
            ));
        }
        if shared_secret.expose_secret().is_empty() {
            return Err(ExtractionClientError::InvalidConfiguration(
                "shared_secret cannot be empty",
            ));
        }
        Ok(Self {
            base_url,
            shared_secret,
            connect_timeout: Duration::from_secs(5),
            request_timeout: Duration::from_secs(190),
            max_request_bytes: 32_768,
            max_response_bytes: 2_500_000,
        })
    }

    fn validate(&self) -> Result<(), ExtractionClientError> {
        if self.connect_timeout.is_zero() || self.connect_timeout > Duration::from_secs(30) {
            return Err(ExtractionClientError::InvalidConfiguration(
                "connect_timeout must be greater than zero and at most 30 seconds",
            ));
        }
        if self.request_timeout < Duration::from_secs(1)
            || self.request_timeout > Duration::from_secs(300)
        {
            return Err(ExtractionClientError::InvalidConfiguration(
                "request_timeout must be between 1 and 300 seconds",
            ));
        }
        if !(4_096..=262_144).contains(&self.max_request_bytes) {
            return Err(ExtractionClientError::InvalidConfiguration(
                "max_request_bytes must be between 4096 and 262144",
            ));
        }
        if !(65_536..=5_000_000).contains(&self.max_response_bytes) {
            return Err(ExtractionClientError::InvalidConfiguration(
                "max_response_bytes must be between 65536 and 5000000",
            ));
        }
        Ok(())
    }
}

#[derive(Clone, Debug)]
pub struct DocumentExtractorClient {
    config: DocumentExtractorConfig,
    http: reqwest::Client,
    endpoint: Url,
}

impl DocumentExtractorClient {
    /// Create a bounded, proxy-independent private service client.
    ///
    /// # Errors
    ///
    /// Returns a configuration error when the endpoint or authentication header is invalid, or a
    /// transport error when the HTTP client cannot be constructed.
    pub fn new(config: DocumentExtractorConfig) -> Result<Self, ExtractionClientError> {
        config.validate()?;
        let endpoint = config
            .base_url
            .join("v1/extract")
            .map_err(|_| ExtractionClientError::InvalidConfiguration("invalid extract endpoint"))?;
        HeaderValue::from_str(config.shared_secret.expose_secret()).map_err(|_| {
            ExtractionClientError::InvalidConfiguration(
                "shared_secret cannot be represented as an HTTP header",
            )
        })?;
        let http = reqwest::Client::builder()
            .connect_timeout(config.connect_timeout)
            .timeout(config.request_timeout)
            .no_proxy()
            .build()
            .map_err(ExtractionClientError::Transport)?;
        Ok(Self {
            config,
            http,
            endpoint,
        })
    }

    /// Extract one public document through the Python policy service.
    ///
    /// # Errors
    ///
    /// Returns stable errors for invalid public URLs, deadlines, transport failures, non-success
    /// HTTP status, oversized bodies, schema drift, request-ID mismatch, or invalid response bounds.
    pub async fn extract(
        &self,
        request: &ExtractRequest,
    ) -> Result<ExtractResult, ExtractionClientError> {
        request.validate()?;
        request.url.validate_dns().await?;
        let remaining = (request.absolute_deadline - chrono::Utc::now())
            .to_std()
            .map_err(|_| ExtractionClientError::InvalidRequest("absolute_deadline has elapsed"))?;
        let timeout = remaining.min(self.config.request_timeout);
        tokio::time::timeout(timeout, self.extract_inner(request))
            .await
            .map_err(|_| ExtractionClientError::Timeout)?
    }

    async fn extract_inner(
        &self,
        request: &ExtractRequest,
    ) -> Result<ExtractResult, ExtractionClientError> {
        let body = serde_json::to_vec(request).map_err(ExtractionClientError::InvalidResponse)?;
        if body.len() > self.config.max_request_bytes {
            return Err(ExtractionClientError::InvalidRequest(
                "serialized request exceeds the configured byte limit",
            ));
        }

        let mut builder = self
            .http
            .post(self.endpoint.clone())
            .header(ACCEPT, "application/json")
            .header(CONTENT_TYPE, "application/json")
            .body(body);
        builder = builder.header(
            "X-Document-Extractor-Token",
            self.config.shared_secret.expose_secret(),
        );
        let response = builder
            .send()
            .await
            .map_err(ExtractionClientError::Transport)?;
        let status = response.status();
        if response
            .content_length()
            .is_some_and(|length| length > self.config.max_response_bytes as u64)
        {
            return Err(ExtractionClientError::ResponseTooLarge {
                limit: self.config.max_response_bytes,
            });
        }

        let mut response_body = Vec::new();
        let mut stream = response.bytes_stream();
        while let Some(chunk) = stream.next().await {
            let chunk = chunk.map_err(ExtractionClientError::Transport)?;
            if response_body.len().saturating_add(chunk.len()) > self.config.max_response_bytes {
                return Err(ExtractionClientError::ResponseTooLarge {
                    limit: self.config.max_response_bytes,
                });
            }
            response_body.extend_from_slice(&chunk);
        }

        if !status.is_success() {
            let message = String::from_utf8_lossy(&response_body)
                .chars()
                .take(500)
                .collect();
            return Err(ExtractionClientError::HttpStatus { status, message });
        }

        let result: ExtractResult = serde_json::from_slice(&response_body)
            .map_err(ExtractionClientError::InvalidResponse)?;
        if result.schema_version() != EXTRACTION_SCHEMA_VERSION {
            return Err(ExtractionClientError::SchemaVersion {
                expected: EXTRACTION_SCHEMA_VERSION,
                actual: result.schema_version(),
            });
        }
        if result.request_id() != request.request_id {
            return Err(ExtractionClientError::RequestIdMismatch);
        }
        result.validate_bounds()?;
        result.validate_public_urls().await?;
        Ok(result)
    }
}

#[cfg(test)]
mod tests {
    use secrecy::SecretString;
    use url::Url;

    use super::{DocumentExtractorClient, DocumentExtractorConfig};

    #[test]
    fn configuration_requires_a_non_empty_secret_and_service_origin() {
        let origin = Url::parse("http://document-extractor:8200").expect("valid origin");
        assert!(
            DocumentExtractorConfig::new(origin.clone(), SecretString::from(String::new()))
                .is_err()
        );
        assert!(
            DocumentExtractorConfig::new(
                origin.join("nested").expect("nested URL"),
                SecretString::from("fixture-secret".to_owned()),
            )
            .is_err()
        );
    }

    #[test]
    fn client_revalidates_mutable_size_bounds() {
        let mut config = DocumentExtractorConfig::new(
            Url::parse("http://document-extractor:8200").expect("valid origin"),
            SecretString::from("fixture-secret".to_owned()),
        )
        .expect("valid config");
        config.max_response_bytes = 1;

        assert!(DocumentExtractorClient::new(config).is_err());
    }

    #[test]
    fn configuration_debug_output_redacts_the_secret() {
        let config = DocumentExtractorConfig::new(
            Url::parse("http://document-extractor:8200").expect("valid origin"),
            SecretString::from("fixture-secret".to_owned()),
        )
        .expect("valid config");
        let rendered = format!("{config:?}");

        assert!(rendered.contains("[REDACTED]"));
        assert!(!rendered.contains("fixture-secret"));
    }
}
