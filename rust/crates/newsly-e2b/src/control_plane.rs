//! E2B public control-plane client.

use std::fmt;
use std::time::Duration;

use reqwest::{Method, RequestBuilder, Response, StatusCode};
use secrecy::{ExposeSecret, SecretString};
use serde::Deserialize;
use url::Url;

use crate::error::E2bError;
use crate::network::NetworkPolicy;
use crate::types::{SandboxHandle, SandboxId, SandboxRequest};

const ENVD_PORT: u16 = 49_983;
const DEFAULT_ERROR_BODY_LIMIT: usize = 16 * 1024;
const ROUTED_SANDBOX_DOMAINS: [&str; 4] = ["e2b.app", "e2b.dev", "e2b.pro", "e2b-staging.dev"];

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SandboxHealth {
    Running,
    Unavailable,
}

#[derive(Clone)]
pub struct ControlPlaneConfig {
    pub api_base: Url,
    pub sandbox_base_override: Option<Url>,
    pub api_key: SecretString,
    pub request_timeout: Duration,
    pub error_body_limit: usize,
    pub default_sandbox_domain: String,
    pub user_agent: String,
}

impl fmt::Debug for ControlPlaneConfig {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ControlPlaneConfig")
            .field("api_base", &self.api_base)
            .field("sandbox_base_override", &self.sandbox_base_override)
            .field("api_key", &"[REDACTED]")
            .field("request_timeout", &self.request_timeout)
            .field("error_body_limit", &self.error_body_limit)
            .field("default_sandbox_domain", &self.default_sandbox_domain)
            .field("user_agent", &self.user_agent)
            .finish()
    }
}

impl ControlPlaneConfig {
    pub fn production(api_key: SecretString) -> Result<Self, E2bError> {
        Ok(Self {
            api_base: Url::parse("https://api.e2b.app/")
                .map_err(|error| E2bError::Configuration(error.to_string()))?,
            sandbox_base_override: None,
            api_key,
            request_timeout: Duration::from_secs(30),
            error_body_limit: DEFAULT_ERROR_BODY_LIMIT,
            default_sandbox_domain: "e2b.app".to_owned(),
            user_agent: format!("newsly-e2b/{}", env!("CARGO_PKG_VERSION")),
        })
    }

    pub fn validate(&self) -> Result<(), E2bError> {
        if self.api_key.expose_secret().trim().is_empty() {
            return Err(E2bError::Configuration("E2B API key is empty".to_owned()));
        }
        if self.api_base.cannot_be_a_base() {
            return Err(E2bError::Configuration(
                "E2B API URL cannot be used as a base URL".to_owned(),
            ));
        }
        if !self.api_base.path().ends_with('/') {
            return Err(E2bError::Configuration(
                "E2B API base URL must end with a slash".to_owned(),
            ));
        }
        if !valid_dns_name(&self.default_sandbox_domain) {
            return Err(E2bError::Configuration(
                "E2B sandbox domain is invalid".to_owned(),
            ));
        }
        if self.request_timeout.is_zero() || self.error_body_limit == 0 {
            return Err(E2bError::Configuration(
                "E2B timeouts and body limits must be positive".to_owned(),
            ));
        }
        Ok(())
    }
}

#[derive(Clone, Debug)]
pub struct ControlPlaneClient {
    http: reqwest::Client,
    config: ControlPlaneConfig,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct SandboxWire {
    #[serde(rename = "sandboxID")]
    sandbox_id: String,
    #[serde(rename = "templateID")]
    template_id: String,
    #[serde(rename = "envdVersion")]
    envd_version: String,
    #[serde(default)]
    domain: Option<String>,
    #[serde(rename = "envdAccessToken", default)]
    envd_access_token: Option<String>,
    #[serde(rename = "trafficAccessToken", default)]
    traffic_access_token: Option<String>,
}

impl ControlPlaneClient {
    pub fn new(config: ControlPlaneConfig) -> Result<Self, E2bError> {
        config.validate()?;
        let http = reqwest::Client::builder()
            .connect_timeout(config.request_timeout)
            .timeout(config.request_timeout)
            .user_agent(&config.user_agent)
            .build()
            .map_err(|error| E2bError::Configuration(error.to_string()))?;
        Ok(Self { http, config })
    }

    #[must_use]
    pub fn config(&self) -> &ControlPlaneConfig {
        &self.config
    }

    pub async fn create(&self, request: &SandboxRequest) -> Result<SandboxHandle, E2bError> {
        request.validate()?;
        let response = self
            .send(
                self.api_request(Method::POST, "sandboxes")?.json(request),
                "create_sandbox",
                false,
                None,
            )
            .await?;
        self.decode_sandbox(response).await
    }

    pub async fn kill(&self, sandbox_id: &SandboxId) -> Result<bool, E2bError> {
        let path = format!("sandboxes/{}", encode_segment(sandbox_id.as_str()));
        Ok(self
            .send_allow_missing(
                self.api_request(Method::DELETE, &path)?,
                "kill_sandbox",
                true,
            )
            .await?
            .is_some())
    }

    pub async fn update_network(
        &self,
        sandbox_id: &SandboxId,
        policy: &NetworkPolicy,
    ) -> Result<(), E2bError> {
        policy.validate()?;
        let path = format!("sandboxes/{}/network", encode_segment(sandbox_id.as_str()));
        self.send(
            self.api_request(Method::PUT, &path)?.json(policy),
            "update_network",
            true,
            None,
        )
        .await?;
        Ok(())
    }

    pub async fn reset_network(&self, sandbox_id: &SandboxId) -> Result<(), E2bError> {
        self.update_network(sandbox_id, &NetworkPolicy::deny_all())
            .await
    }

    pub async fn check_envd_health(
        &self,
        handle: &SandboxHandle,
    ) -> Result<SandboxHealth, E2bError> {
        let mut url = self.envd_base_url(handle)?;
        url.set_path("health");
        url.set_query(None);
        let response = self
            .envd_request(Method::GET, url, handle)
            .send()
            .await
            .map_err(|error| transport_error(&error, "check_envd_health", true, None))?;
        match response.status() {
            status if status.is_success() => Ok(SandboxHealth::Running),
            StatusCode::NOT_FOUND
            | StatusCode::BAD_GATEWAY
            | StatusCode::SERVICE_UNAVAILABLE
            | StatusCode::GATEWAY_TIMEOUT => Ok(SandboxHealth::Unavailable),
            _ => Err(response_error(response, self.config.error_body_limit).await),
        }
    }

    pub fn envd_base_url(&self, handle: &SandboxHandle) -> Result<Url, E2bError> {
        if let Some(base) = &self.config.sandbox_base_override {
            return Ok(base.clone());
        }
        let domain = if handle.sandbox_domain.trim().is_empty() {
            &self.config.default_sandbox_domain
        } else {
            &handle.sandbox_domain
        };
        let url = if ROUTED_SANDBOX_DOMAINS.contains(&domain.as_str()) {
            format!("https://sandbox.{domain}/")
        } else {
            if !valid_dns_name(domain) || !valid_dns_label(handle.sandbox_id.as_str()) {
                return Err(E2bError::Configuration(
                    "custom E2B sandbox routing identifiers are invalid".to_owned(),
                ));
            }
            format!(
                "https://{ENVD_PORT}-{}.{domain}/",
                handle.sandbox_id.as_str()
            )
        };
        Url::parse(&url).map_err(|error| E2bError::Configuration(error.to_string()))
    }

    pub fn envd_request(&self, method: Method, url: Url, handle: &SandboxHandle) -> RequestBuilder {
        let mut request = self
            .http
            .request(method, url)
            .header("E2b-Sandbox-Id", handle.sandbox_id.as_str())
            .header("E2b-Sandbox-Port", ENVD_PORT.to_string());
        if let Some(token) = &handle.envd_access_token {
            request = request.header("X-Access-Token", token.expose_secret());
        }
        request
    }

    fn api_request(&self, method: Method, path: &str) -> Result<RequestBuilder, E2bError> {
        let url = self
            .config
            .api_base
            .join(path)
            .map_err(|error| E2bError::Configuration(error.to_string()))?;
        Ok(self
            .http
            .request(method, url)
            .header("X-API-Key", self.config.api_key.expose_secret()))
    }

    async fn decode_sandbox(&self, response: Response) -> Result<SandboxHandle, E2bError> {
        let wire = response
            .json::<SandboxWire>()
            .await
            .map_err(|error| E2bError::Protocol(error.to_string()))?;
        Ok(SandboxHandle {
            sandbox_id: SandboxId::parse(wire.sandbox_id)?,
            template_id: wire.template_id,
            envd_version: wire.envd_version,
            sandbox_domain: wire
                .domain
                .filter(|value| !value.trim().is_empty())
                .unwrap_or_else(|| self.config.default_sandbox_domain.clone()),
            envd_access_token: wire.envd_access_token.map(SecretString::from),
            traffic_access_token: wire.traffic_access_token.map(SecretString::from),
        })
    }

    async fn send(
        &self,
        request: RequestBuilder,
        operation: &str,
        idempotent: bool,
        execution_tag: Option<&str>,
    ) -> Result<Response, E2bError> {
        let response = request
            .send()
            .await
            .map_err(|error| transport_error(&error, operation, idempotent, execution_tag))?;
        if response.status().is_success() {
            return Ok(response);
        }
        Err(response_error(response, self.config.error_body_limit).await)
    }

    async fn send_allow_missing(
        &self,
        request: RequestBuilder,
        operation: &str,
        idempotent: bool,
    ) -> Result<Option<Response>, E2bError> {
        let response = request
            .send()
            .await
            .map_err(|error| transport_error(&error, operation, idempotent, None))?;
        if response.status() == StatusCode::NOT_FOUND {
            return Ok(None);
        }
        if response.status().is_success() {
            return Ok(Some(response));
        }
        Err(response_error(response, self.config.error_body_limit).await)
    }
}

fn encode_segment(value: &str) -> String {
    url::form_urlencoded::byte_serialize(value.as_bytes()).collect()
}

fn valid_dns_name(value: &str) -> bool {
    !value.is_empty() && value.len() <= 253 && value.split('.').all(valid_dns_label)
}

fn valid_dns_label(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 63
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
        && value
            .as_bytes()
            .first()
            .is_some_and(u8::is_ascii_alphanumeric)
        && value
            .as_bytes()
            .last()
            .is_some_and(u8::is_ascii_alphanumeric)
}

pub(crate) fn transport_error(
    error: &reqwest::Error,
    operation: &str,
    idempotent: bool,
    execution_tag: Option<&str>,
) -> E2bError {
    if error.is_connect() {
        E2bError::TransportBeforeDelivery {
            message: error.to_string(),
        }
    } else if idempotent {
        E2bError::RetryableTransport {
            operation: operation.to_owned(),
            message: error.to_string(),
        }
    } else {
        E2bError::AmbiguousDelivery {
            operation: operation.to_owned(),
            execution_tag: execution_tag.map(str::to_owned),
            message: error.to_string(),
        }
    }
}

pub(crate) async fn response_error(mut response: Response, limit: usize) -> E2bError {
    let status = response.status();
    let mut body = Vec::new();
    while body.len() < limit {
        match response.chunk().await {
            Ok(Some(chunk)) => {
                let remaining = limit - body.len();
                body.extend_from_slice(&chunk[..chunk.len().min(remaining)]);
            }
            Ok(None) | Err(_) => break,
        }
    }
    let body_text = String::from_utf8_lossy(&body);
    let parsed = serde_json::from_slice::<serde_json::Value>(&body).ok();
    let message = parsed
        .as_ref()
        .and_then(|value| value.get("message"))
        .and_then(serde_json::Value::as_str)
        .unwrap_or(&body_text)
        .trim()
        .to_owned();
    let code = parsed
        .as_ref()
        .and_then(|value| value.get("code"))
        .map(|value| {
            value
                .as_str()
                .map_or_else(|| value.to_string(), str::to_owned)
        });
    match status {
        StatusCode::UNAUTHORIZED | StatusCode::FORBIDDEN => E2bError::Authentication,
        StatusCode::NOT_FOUND => E2bError::NotFound { resource: message },
        StatusCode::TOO_MANY_REQUESTS | StatusCode::SERVICE_UNAVAILABLE => {
            E2bError::Quota { message }
        }
        _ => E2bError::Remote {
            status: status.as_u16(),
            code,
            message,
        },
    }
}

#[cfg(test)]
mod tests {
    use super::encode_segment;

    #[test]
    fn path_segments_are_percent_encoded() {
        assert_eq!(encode_segment("snapshot/name:tag"), "snapshot%2Fname%3Atag");
    }
}
