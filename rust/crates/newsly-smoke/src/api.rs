use std::time::Duration;

use anyhow::{Context, Result, bail};
use reqwest::{Client, Method, StatusCode};
use serde::Serialize;
use serde::de::DeserializeOwned;
use serde_json::Value;
use url::Url;

const MAX_ERROR_BODY_CHARS: usize = 2_000;

#[derive(Debug, Clone)]
pub(crate) struct SmokeApi {
    base_url: Url,
    client: Client,
    token: Option<String>,
}

impl SmokeApi {
    pub(crate) fn new(base_url: Url, timeout: Duration) -> Result<Self> {
        let client = Client::builder()
            .timeout(timeout)
            .build()
            .context("could not build smoke HTTP client")?;
        Ok(Self {
            base_url,
            client,
            token: None,
        })
    }

    pub(crate) fn authenticated(&self, token: String) -> Self {
        let mut api = self.clone();
        api.token = Some(token);
        api
    }

    pub(crate) async fn get<T: DeserializeOwned>(&self, path: &str) -> Result<T> {
        self.send_json::<(), T>(Method::GET, path, None).await
    }

    pub(crate) async fn get_status(&self, path: &str) -> Result<StatusCode> {
        let response = self.request(Method::GET, path)?.send().await?;
        Ok(response.status())
    }

    pub(crate) async fn get_text(&self, absolute_url: &str) -> Result<(StatusCode, String)> {
        let response = self.client.get(absolute_url).send().await?;
        let status = response.status();
        let body = response.text().await?;
        Ok((status, body))
    }

    pub(crate) async fn post<B: Serialize, T: DeserializeOwned>(
        &self,
        path: &str,
        body: &B,
    ) -> Result<T> {
        self.send_json(Method::POST, path, Some(body)).await
    }

    pub(crate) async fn delete<T: DeserializeOwned>(&self, path: &str) -> Result<T> {
        self.send_json::<(), T>(Method::DELETE, path, None).await
    }

    pub(crate) async fn post_expect_status<B: Serialize>(
        &self,
        path: &str,
        body: &B,
        expected: StatusCode,
    ) -> Result<Value> {
        let response = self.request(Method::POST, path)?.json(body).send().await?;
        let status = response.status();
        let value = response.json::<Value>().await.unwrap_or(Value::Null);
        if status != expected {
            bail!("POST {path} returned {status}, expected {expected}: {value}");
        }
        Ok(value)
    }

    async fn send_json<B: Serialize, T: DeserializeOwned>(
        &self,
        method: Method,
        path: &str,
        body: Option<&B>,
    ) -> Result<T> {
        let mut request = self.request(method.clone(), path)?;
        if let Some(body) = body {
            request = request.json(body);
        }
        let response = request.send().await?;
        let status = response.status();
        let bytes = response.bytes().await?;
        if !status.is_success() {
            let body = String::from_utf8_lossy(&bytes);
            let bounded = body.chars().take(MAX_ERROR_BODY_CHARS).collect::<String>();
            bail!("{method} {path} returned {status}: {bounded}");
        }
        serde_json::from_slice(&bytes)
            .with_context(|| format!("{method} {path} returned invalid JSON with status {status}"))
    }

    fn request(&self, method: Method, path: &str) -> Result<reqwest::RequestBuilder> {
        let url = self
            .base_url
            .join(path.trim_start_matches('/'))
            .with_context(|| format!("invalid API path {path:?}"))?;
        let request = self.client.request(method, url);
        Ok(match &self.token {
            Some(token) => request.bearer_auth(token),
            None => request,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::MAX_ERROR_BODY_CHARS;

    #[test]
    fn diagnostic_bodies_remain_bounded() {
        let value = "x".repeat(MAX_ERROR_BODY_CHARS + 100);
        let bounded = value.chars().take(MAX_ERROR_BODY_CHARS).collect::<String>();
        assert_eq!(bounded.len(), MAX_ERROR_BODY_CHARS);
    }
}
