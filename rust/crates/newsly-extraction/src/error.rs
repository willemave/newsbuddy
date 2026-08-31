use std::net::IpAddr;

use reqwest::StatusCode;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum ExtractionClientError {
    #[error("invalid public extraction URL: {reason}")]
    InvalidPublicUrl {
        reason: &'static str,
        #[source]
        source: Option<url::ParseError>,
    },
    #[error("extraction URL resolved to non-public address {0}")]
    NonPublicAddress(IpAddr),
    #[error("DNS resolution failed for extraction host {host}")]
    DnsResolution {
        host: String,
        #[source]
        source: std::io::Error,
    },
    #[error("DNS resolution returned no addresses for extraction host {0}")]
    NoDnsAddresses(String),
    #[error("invalid document extractor configuration: {0}")]
    InvalidConfiguration(&'static str),
    #[error("invalid extraction request: {0}")]
    InvalidRequest(&'static str),
    #[error("document extractor request timed out")]
    Timeout,
    #[error("document extractor transport failed")]
    Transport(#[source] reqwest::Error),
    #[error("document extractor returned HTTP {status}: {message}")]
    HttpStatus { status: StatusCode, message: String },
    #[error("document extractor response exceeded {limit} bytes")]
    ResponseTooLarge { limit: usize },
    #[error("document extractor returned invalid JSON")]
    InvalidResponse(#[source] serde_json::Error),
    #[error("document extractor returned schema version {actual}, expected {expected}")]
    SchemaVersion { expected: u16, actual: u16 },
    #[error("document extractor response request id did not match the request")]
    RequestIdMismatch,
    #[error("document extractor returned an out-of-bounds field: {0}")]
    InvalidResponseBounds(&'static str),
}
