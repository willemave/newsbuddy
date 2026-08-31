//! Bounded streaming file transfer through envd's documented REST API.

use std::pin::Pin;
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;

use bytes::Bytes;
use futures_core::Stream;
use futures_util::StreamExt;
use reqwest::Method;
use semver::Version;

use crate::control_plane::{ControlPlaneClient, response_error, transport_error};
use crate::error::E2bError;
use crate::types::{SandboxHandle, SandboxPath, WorkspacePath};

pub type BoxByteStream = Pin<Box<dyn Stream<Item = Result<Bytes, E2bError>> + Send + 'static>>;

const ENVD_OCTET_STREAM_UPLOAD: &str = "0.5.7";

#[derive(Clone, Copy, Debug)]
pub struct FileLimits {
    pub upload_bytes: u64,
    pub download_bytes: u64,
}

impl Default for FileLimits {
    fn default() -> Self {
        Self {
            upload_bytes: 20 * 1024 * 1024,
            download_bytes: 20 * 1024 * 1024,
        }
    }
}

impl FileLimits {
    pub fn validate(self) -> Result<(), E2bError> {
        if self.upload_bytes == 0 || self.download_bytes == 0 {
            return Err(E2bError::InvalidInput(
                "file transfer limits must be greater than zero".to_owned(),
            ));
        }
        Ok(())
    }
}

#[derive(Clone, Debug)]
pub struct EnvdFileClient {
    control: ControlPlaneClient,
    limits: FileLimits,
}

impl EnvdFileClient {
    pub fn new(control: ControlPlaneClient, limits: FileLimits) -> Result<Self, E2bError> {
        limits.validate()?;
        Ok(Self { control, limits })
    }

    pub async fn upload(
        &self,
        sandbox: &SandboxHandle,
        path: &WorkspacePath,
        username: &str,
        content_length: Option<u64>,
        source: BoxByteStream,
    ) -> Result<(), E2bError> {
        self.upload_path(
            sandbox,
            path.as_str(),
            username,
            content_length,
            source,
            None,
        )
        .await
    }

    /// Upload to a trusted absolute sandbox path with an operation-specific deadline.
    pub async fn upload_sandbox_path(
        &self,
        sandbox: &SandboxHandle,
        path: &SandboxPath,
        username: &str,
        content_length: u64,
        source: BoxByteStream,
        request_timeout: Duration,
    ) -> Result<(), E2bError> {
        if request_timeout.is_zero() {
            return Err(E2bError::Deadline);
        }
        self.upload_path(
            sandbox,
            path.as_str(),
            username,
            Some(content_length),
            source,
            Some(request_timeout),
        )
        .await
    }

    #[allow(clippy::too_many_arguments)]
    async fn upload_path(
        &self,
        sandbox: &SandboxHandle,
        path: &str,
        username: &str,
        content_length: Option<u64>,
        mut source: BoxByteStream,
        request_timeout: Option<Duration>,
    ) -> Result<(), E2bError> {
        require_octet_stream_upload(sandbox)?;
        if let Some(length) = content_length
            && length > self.limits.upload_bytes
        {
            return Err(E2bError::FileTooLarge {
                limit_bytes: usize::try_from(self.limits.upload_bytes).unwrap_or(usize::MAX),
                observed_bytes: length,
            });
        }
        validate_username(username)?;
        let mut url = self.control.envd_base_url(sandbox)?;
        url.set_path("files");
        url.query_pairs_mut()
            .append_pair("path", path)
            .append_pair("username", username);

        let observed = Arc::new(AtomicU64::new(0));
        let observed_stream = Arc::clone(&observed);
        let limit = self.limits.upload_bytes;
        let bounded: BoxByteStream = Box::pin(async_stream::try_stream! {
            while let Some(chunk) = source.next().await {
                let chunk = chunk?;
                let next = observed_stream
                    .fetch_add(u64::try_from(chunk.len()).unwrap_or(u64::MAX), Ordering::Relaxed)
                    .saturating_add(u64::try_from(chunk.len()).unwrap_or(u64::MAX));
                if next > limit {
                    Err(E2bError::FileTooLarge {
                        limit_bytes: usize::try_from(limit).unwrap_or(usize::MAX),
                        observed_bytes: next,
                    })?;
                }
                yield chunk;
            }
        });

        let body = reqwest::Body::wrap_stream(bounded);
        let mut request = self
            .control
            .envd_request(Method::POST, url, sandbox)
            .header("Content-Type", "application/octet-stream")
            .body(body);
        if let Some(timeout) = request_timeout {
            request = request.timeout(timeout);
        }
        if let Some(length) = content_length {
            request = request.header("Content-Length", length);
        }
        let response = match request.send().await {
            Ok(response) => response,
            Err(error) => {
                let count = observed.load(Ordering::Relaxed);
                if count > limit {
                    return Err(E2bError::FileTooLarge {
                        limit_bytes: usize::try_from(limit).unwrap_or(usize::MAX),
                        observed_bytes: count,
                    });
                }
                return Err(transport_error(&error, "upload_file", false, None));
            }
        };
        if !response.status().is_success() {
            return Err(response_error(response, self.control.config().error_body_limit).await);
        }
        if let Some(expected) = content_length {
            let count = observed.load(Ordering::Relaxed);
            if count != expected {
                return Err(E2bError::Protocol(format!(
                    "file upload produced {count} bytes but declared {expected}"
                )));
            }
        }
        Ok(())
    }

    pub async fn download(
        &self,
        sandbox: &SandboxHandle,
        path: &WorkspacePath,
        username: &str,
    ) -> Result<BoxByteStream, E2bError> {
        self.download_path(sandbox, path.as_str(), username, None)
            .await
    }

    /// Download from a trusted absolute sandbox path with an operation-specific deadline.
    pub async fn download_sandbox_path(
        &self,
        sandbox: &SandboxHandle,
        path: &SandboxPath,
        username: &str,
        request_timeout: Duration,
    ) -> Result<BoxByteStream, E2bError> {
        if request_timeout.is_zero() {
            return Err(E2bError::Deadline);
        }
        self.download_path(sandbox, path.as_str(), username, Some(request_timeout))
            .await
    }

    async fn download_path(
        &self,
        sandbox: &SandboxHandle,
        path: &str,
        username: &str,
        request_timeout: Option<Duration>,
    ) -> Result<BoxByteStream, E2bError> {
        validate_username(username)?;
        let mut url = self.control.envd_base_url(sandbox)?;
        url.set_path("files");
        url.query_pairs_mut()
            .append_pair("path", path)
            .append_pair("username", username);
        let mut request = self.control.envd_request(Method::GET, url, sandbox);
        if let Some(timeout) = request_timeout {
            request = request.timeout(timeout);
        }
        let response = request
            .send()
            .await
            .map_err(|error| transport_error(&error, "download_file", true, None))?;
        if !response.status().is_success() {
            return Err(response_error(response, self.control.config().error_body_limit).await);
        }
        if let Some(length) = response.content_length()
            && length > self.limits.download_bytes
        {
            return Err(E2bError::FileTooLarge {
                limit_bytes: usize::try_from(self.limits.download_bytes).unwrap_or(usize::MAX),
                observed_bytes: length,
            });
        }

        let mut source = response.bytes_stream();
        let limit = self.limits.download_bytes;
        let stream = async_stream::try_stream! {
            let mut observed = 0_u64;
            while let Some(chunk) = source.next().await {
                let chunk = chunk.map_err(|error| E2bError::StreamInterrupted {
                    operation: "download_file".to_owned(),
                    message: error.to_string(),
                })?;
                observed = observed.saturating_add(u64::try_from(chunk.len()).unwrap_or(u64::MAX));
                if observed > limit {
                    Err(E2bError::FileTooLarge {
                        limit_bytes: usize::try_from(limit).unwrap_or(usize::MAX),
                        observed_bytes: observed,
                    })?;
                }
                yield chunk;
            }
        };
        Ok(Box::pin(stream))
    }
}

fn require_octet_stream_upload(sandbox: &SandboxHandle) -> Result<(), E2bError> {
    let version =
        Version::parse(sandbox.envd_version.trim().trim_start_matches('v')).map_err(|_| {
            E2bError::UnsupportedCapability {
                capability: "octet_stream_file_upload".to_owned(),
                version: sandbox.envd_version.clone(),
            }
        })?;
    let minimum = Version::parse(ENVD_OCTET_STREAM_UPLOAD)
        .expect("the built-in octet-stream upload version is valid");
    if version < minimum {
        return Err(E2bError::UnsupportedCapability {
            capability: "octet_stream_file_upload".to_owned(),
            version: sandbox.envd_version.clone(),
        });
    }
    Ok(())
}

fn validate_username(username: &str) -> Result<(), E2bError> {
    let valid = !username.is_empty()
        && username.len() <= 64
        && username
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_' || byte == b'-');
    if !valid {
        return Err(E2bError::InvalidInput(
            "sandbox username contains unsupported characters".to_owned(),
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::FileLimits;

    #[test]
    fn zero_file_limits_are_rejected() {
        assert!(
            FileLimits {
                upload_bytes: 0,
                download_bytes: 1,
            }
            .validate()
            .is_err()
        );
    }
}
