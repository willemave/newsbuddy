use std::time::Duration;

use reqwest::{Client, Url};
use secrecy::{ExposeSecret, SecretString};
use serde_json::{Map, Value};
use thiserror::Error;

pub const X_DEFAULT_SCOPES: [&str; 4] = [
    "tweet.read",
    "users.read",
    "bookmark.read",
    "offline.access",
];

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct XOAuthToken {
    pub access_token: String,
    pub refresh_token: Option<String>,
    pub expires_in: Option<i64>,
    pub scopes: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct XAuthenticatedUser {
    pub id: String,
    pub username: Option<String>,
}

#[derive(Debug, Clone)]
pub struct XOAuthGateway {
    client: Client,
    client_id: String,
    client_secret: Option<SecretString>,
    redirect_uri: String,
    authorize_url: Url,
    token_url: Url,
    api_base_url: Url,
}

impl XOAuthGateway {
    /// Creates an X OAuth gateway from validated client and endpoint configuration.
    ///
    /// # Errors
    ///
    /// Returns an error when required values or endpoint URLs are invalid, or the HTTP client
    /// cannot be constructed.
    pub fn new(
        client_id: &str,
        client_secret: Option<SecretString>,
        redirect_uri: &str,
        authorize_url: Url,
        token_url: Url,
        api_base_url: Url,
    ) -> Result<Self, XOAuthGatewayError> {
        if client_id.trim().is_empty() || redirect_uri.trim().is_empty() {
            return Err(XOAuthGatewayError::InvalidConfiguration);
        }
        for url in [&authorize_url, &token_url, &api_base_url] {
            if !matches!(url.scheme(), "http" | "https") || url.cannot_be_a_base() {
                return Err(XOAuthGatewayError::InvalidConfiguration);
            }
        }
        let client = Client::builder().timeout(Duration::from_secs(20)).build()?;
        Ok(Self {
            client,
            client_id: client_id.trim().to_owned(),
            client_secret,
            redirect_uri: redirect_uri.trim().to_owned(),
            authorize_url,
            token_url,
            api_base_url,
        })
    }

    pub fn authorize_url(&self, state: &str, code_challenge: &str, scopes: &[String]) -> String {
        let mut url = self.authorize_url.clone();
        url.query_pairs_mut()
            .append_pair("response_type", "code")
            .append_pair("client_id", &self.client_id)
            .append_pair("redirect_uri", &self.redirect_uri)
            .append_pair("scope", &scopes.join(" "))
            .append_pair("state", state)
            .append_pair("code_challenge", code_challenge)
            .append_pair("code_challenge_method", "S256");
        url.to_string()
    }

    /// Exchanges an authorization code and PKCE verifier for an X OAuth token.
    ///
    /// # Errors
    ///
    /// Returns an error for invalid input or when the token request or response cannot be
    /// processed.
    pub async fn exchange_code(
        &self,
        code: &str,
        code_verifier: &str,
    ) -> Result<XOAuthToken, XOAuthGatewayError> {
        if code.trim().is_empty() || code_verifier.trim().is_empty() {
            return Err(XOAuthGatewayError::InvalidRequest);
        }
        let form = [
            ("grant_type", "authorization_code"),
            ("client_id", self.client_id.as_str()),
            ("code", code.trim()),
            ("code_verifier", code_verifier.trim()),
            ("redirect_uri", self.redirect_uri.as_str()),
        ];
        let mut request = self
            .client
            .post(self.token_url.clone())
            .header("accept", "application/json")
            .form(&form);
        if let Some(secret) = &self.client_secret {
            request = request.basic_auth(&self.client_id, Some(secret.expose_secret()));
        }
        let payload = send_json(request).await?;
        parse_token(&payload)
    }

    /// Fetches the authenticated X user represented by an access token.
    ///
    /// # Errors
    ///
    /// Returns an error when the provider request fails or the response omits the user identity.
    pub async fn authenticated_user(
        &self,
        access_token: &str,
    ) -> Result<XAuthenticatedUser, XOAuthGatewayError> {
        let mut url = self.api_base_url.clone();
        url.set_path(&format!("{}/users/me", url.path().trim_end_matches('/')));
        url.query_pairs_mut()
            .append_pair("user.fields", "name,username");
        let payload = send_json(
            self.client
                .get(url)
                .header("accept", "application/json")
                .bearer_auth(access_token),
        )
        .await?;
        let data = payload.get("data").and_then(Value::as_object).ok_or(
            XOAuthGatewayError::MalformedResponse("X /users/me response is missing data"),
        )?;
        let id = optional_string(data.get("id")).ok_or(XOAuthGatewayError::MalformedResponse(
            "X /users/me response is missing user id",
        ))?;
        Ok(XAuthenticatedUser {
            id,
            username: optional_string(data.get("username")),
        })
    }

    /// Revokes an access or refresh token through the configured X OAuth endpoint.
    ///
    /// # Errors
    ///
    /// Returns an error for an invalid token hint or when the provider request fails.
    pub async fn revoke(
        &self,
        token: &str,
        token_type_hint: &str,
    ) -> Result<(), XOAuthGatewayError> {
        if token.trim().is_empty() || !matches!(token_type_hint, "access_token" | "refresh_token") {
            return Err(XOAuthGatewayError::InvalidRequest);
        }
        let mut revoke_url = self.token_url.clone();
        let path = revoke_url.path().trim_end_matches('/');
        let base = path.strip_suffix("/token").unwrap_or(path);
        revoke_url.set_path(&format!("{base}/revoke"));
        let form = [
            ("token", token.trim()),
            ("token_type_hint", token_type_hint),
            ("client_id", self.client_id.as_str()),
        ];
        let mut request = self
            .client
            .post(revoke_url)
            .header("accept", "application/json")
            .form(&form);
        if let Some(secret) = &self.client_secret {
            request = request.basic_auth(&self.client_id, Some(secret.expose_secret()));
        }
        let _ = send_json(request).await?;
        Ok(())
    }
}

async fn send_json(
    request: reqwest::RequestBuilder,
) -> Result<Map<String, Value>, XOAuthGatewayError> {
    let response = request.send().await?;
    let status = response.status();
    let bytes = response.bytes().await?;
    let value = if bytes.is_empty() {
        Value::Object(Map::new())
    } else {
        serde_json::from_slice(&bytes).map_err(|_| {
            XOAuthGatewayError::MalformedResponse("X provider response is not valid JSON")
        })?
    };
    let object = value
        .as_object()
        .cloned()
        .ok_or(XOAuthGatewayError::MalformedResponse(
            "X provider response is not a JSON object",
        ))?;
    if !status.is_success() {
        return Err(XOAuthGatewayError::Provider {
            status: status.as_u16(),
            detail: extract_error(&object),
        });
    }
    Ok(object)
}

fn parse_token(payload: &Map<String, Value>) -> Result<XOAuthToken, XOAuthGatewayError> {
    let access_token = optional_string(payload.get("access_token")).ok_or(
        XOAuthGatewayError::MalformedResponse("X token response is missing access_token"),
    )?;
    let expires_in = payload.get("expires_in").and_then(|value| {
        value
            .as_i64()
            .or_else(|| value.as_str().and_then(|raw| raw.parse().ok()))
    });
    let scopes = match payload.get("scope") {
        Some(Value::String(raw)) => raw
            .split_whitespace()
            .filter(|value| !value.is_empty())
            .map(ToOwned::to_owned)
            .collect(),
        Some(Value::Array(values)) => values
            .iter()
            .filter_map(Value::as_str)
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(ToOwned::to_owned)
            .collect(),
        _ => Vec::new(),
    };
    Ok(XOAuthToken {
        access_token,
        refresh_token: optional_string(payload.get("refresh_token")),
        expires_in,
        scopes,
    })
}

fn optional_string(value: Option<&Value>) -> Option<String> {
    value
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(ToOwned::to_owned)
}

fn extract_error(payload: &Map<String, Value>) -> String {
    if let (Some(title), Some(detail)) = (
        payload.get("title").and_then(Value::as_str),
        payload.get("detail").and_then(Value::as_str),
    ) {
        return format!("{title}: {detail}");
    }
    for key in ["detail", "error"] {
        if let Some(message) = payload.get(key).and_then(Value::as_str) {
            return message.chars().take(300).collect();
        }
    }
    "Unknown error".to_owned()
}

#[derive(Debug, Error)]
pub enum XOAuthGatewayError {
    #[error("X OAuth configuration is invalid")]
    InvalidConfiguration,
    #[error("X OAuth request is invalid")]
    InvalidRequest,
    #[error("X provider request failed with status {status}: {detail}")]
    Provider { status: u16, detail: String },
    #[error("{0}")]
    MalformedResponse(&'static str),
    #[error("X provider transport failed")]
    Transport(#[from] reqwest::Error),
}
