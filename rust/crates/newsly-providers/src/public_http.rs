//! One public-network dispatch path for candidate validation and accepted source downloads.
use std::net::SocketAddr;
use std::time::Duration;

use futures_util::StreamExt;
use newsly_extraction::{ExtractionClientError, PublicUrl};
use reqwest::{Client, header};
use thiserror::Error;

const MAX_REDIRECTS: usize = 5;

pub(crate) struct PublicDocument {
    pub effective_url: PublicUrl,
    pub body: Vec<u8>,
}

#[derive(Debug, Error)]
pub(crate) enum PublicHttpError {
    #[error("public source validation failed")]
    Url(#[from] ExtractionClientError),
    #[error("public source HTTP request failed")]
    Http(#[from] reqwest::Error),
    #[error("public source returned HTTP {status}")]
    Status {
        status: reqwest::StatusCode,
        retry_after: Option<i64>,
    },
    #[error("public source deadline exceeded")]
    Deadline,
    #[error("public source response exceeded its body limit")]
    TooLarge,
    #[error("public source redirect is invalid or exceeded its limit")]
    Redirect,
}

pub(crate) async fn fetch_public(
    url: &str,
    timeout: Duration,
    max_bytes: Option<usize>,
    user_agent: &str,
    accept: &str,
) -> Result<PublicDocument, PublicHttpError> {
    // Includes DNS, every redirect, and body streaming, rather than restarting per hop.
    tokio::time::timeout(timeout, async {
        let mut current = PublicUrl::parse(url)?;
        for hop in 0..=MAX_REDIRECTS {
            let addresses = current.resolve_public_addresses().await?;
            let client = pinned_client(&current, &addresses, timeout)?;
            let response = client
                .get(current.as_url().clone())
                .header(header::USER_AGENT, user_agent)
                .header(header::ACCEPT, accept)
                .send()
                .await?;
            if response.status().is_redirection() {
                if hop == MAX_REDIRECTS {
                    return Err(PublicHttpError::Redirect);
                }
                let location = response
                    .headers()
                    .get(header::LOCATION)
                    .and_then(|value| value.to_str().ok())
                    .ok_or(PublicHttpError::Redirect)?;
                current = redirect_target(&current, location)?;
                continue;
            }
            if response.status().is_client_error() || response.status().is_server_error() {
                return Err(PublicHttpError::Status {
                    status: response.status(),
                    retry_after: response
                        .headers()
                        .get(header::RETRY_AFTER)
                        .and_then(|value| value.to_str().ok())
                        .and_then(retry_after_seconds),
                });
            }
            if max_bytes.is_some_and(|limit| {
                response
                    .content_length()
                    .is_some_and(|size| exceeds_response_limit(size, Some(limit)))
            }) {
                return Err(PublicHttpError::TooLarge);
            }
            let mut body = Vec::new();
            let mut chunks = response.bytes_stream();
            while let Some(chunk) = chunks.next().await {
                let chunk = chunk?;
                if max_bytes.is_some_and(|limit| body.len().saturating_add(chunk.len()) > limit) {
                    return Err(PublicHttpError::TooLarge);
                }
                body.extend_from_slice(&chunk);
            }
            return Ok(PublicDocument {
                effective_url: current,
                body,
            });
        }
        Err(PublicHttpError::Redirect)
    })
    .await
    .map_err(|_| PublicHttpError::Deadline)?
}

fn redirect_target(current: &PublicUrl, location: &str) -> Result<PublicUrl, PublicHttpError> {
    let next = current
        .as_url()
        .join(location)
        .map_err(|_| PublicHttpError::Redirect)?;
    Ok(PublicUrl::parse(next.as_str())?)
}

fn pinned_client(
    url: &PublicUrl,
    addresses: &[SocketAddr],
    timeout: Duration,
) -> Result<Client, reqwest::Error> {
    let mut builder = Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .connect_timeout(Duration::from_secs(5).min(timeout))
        .timeout(timeout);
    if let Some(host) = url.as_url().host_str() {
        builder = builder.resolve_to_addrs(host, addresses);
    }
    builder.build()
}

pub(crate) fn exceeds_response_limit(size: u64, max_bytes: Option<usize>) -> bool {
    max_bytes.is_some_and(|limit| size > limit as u64)
}

fn retry_after_seconds(value: &str) -> Option<i64> {
    value
        .trim()
        .parse::<i64>()
        .ok()
        .filter(|seconds| *seconds >= 0)
        .or_else(|| {
            chrono::DateTime::parse_from_rfc2822(value)
                .ok()
                .map(|time| {
                    (time.with_timezone(&chrono::Utc) - chrono::Utc::now())
                        .num_seconds()
                        .max(0)
                })
        })
}

pub(crate) fn retryable_http_error(error: &reqwest::Error) -> bool {
    error.status().map_or_else(
        || !error.is_builder() && !error.is_decode(),
        |status| status.is_server_error() || matches!(status.as_u16(), 408 | 429),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    #[tokio::test]
    async fn dispatch_uses_supplied_addresses_without_a_second_dns_lookup() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let url = PublicUrl::parse(&format!(
            "http://not-in-dns.invalid:{}/feed",
            address.port()
        ))
        .unwrap();
        // Test only: the production caller obtains these from resolve_public_addresses.
        let client = pinned_client(&url, &[address], Duration::from_secs(2)).unwrap();
        let server = tokio::spawn(async move {
            let (mut socket, _) = listener.accept().await.unwrap();
            let mut request = [0; 2048];
            let size = socket.read(&mut request).await.unwrap();
            assert!(String::from_utf8_lossy(&request[..size]).contains("not-in-dns.invalid"));
            socket
                .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\nfeed")
                .await
                .unwrap();
        });
        assert_eq!(
            client
                .get(url.as_url().clone())
                .send()
                .await
                .unwrap()
                .text()
                .await
                .unwrap(),
            "feed"
        );
        server.await.unwrap();
    }

    #[test]
    fn private_redirect_cannot_be_dispatched() {
        let url = PublicUrl::parse("https://example.com/feed").unwrap();
        assert!(redirect_target(&url, "http://127.0.0.1/private").is_err());
        assert!(redirect_target(&url, "http://169.254.169.254/latest").is_err());
        assert_eq!(
            redirect_target(&url, "/new").unwrap().as_str(),
            "https://example.com/new"
        );
    }
    #[test]
    fn retry_after_accepts_seconds_and_http_dates_without_negative_delays() {
        assert_eq!(super::retry_after_seconds("120"), Some(120));
        assert_eq!(super::retry_after_seconds("-1"), None);
        assert_eq!(super::retry_after_seconds("invalid"), None);
        assert_eq!(
            super::retry_after_seconds("Wed, 21 Oct 2015 07:28:00 GMT"),
            Some(0)
        );
    }
}
