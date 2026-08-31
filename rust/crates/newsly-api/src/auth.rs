use std::fmt::{self, Debug, Formatter};
use std::sync::Arc;
use std::time::{Duration, Instant};

use axum::extract::FromRequestParts;
use axum::http::{StatusCode, header::AUTHORIZATION, request::Parts};
use base64::Engine as _;
use base64::engine::general_purpose::URL_SAFE;
use chrono::{DateTime, Utc};
use fernet::Fernet;
use jsonwebtoken::jwk::JwkSet;
use jsonwebtoken::{
    Algorithm, DecodingKey, EncodingKey, Header, Validation, decode, decode_header, encode,
};
use newsly_contracts::AccessTokenResponse;
use newsly_db::{find_user_by_api_key, find_user_by_id, is_api_key_token};
use secrecy::{ExposeSecret, SecretString};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use subtle::ConstantTimeEq;
use thiserror::Error;
use uuid::Uuid;

use crate::error::ApiError;
use crate::{AppState, request_id_from_headers};

const MAX_APPLE_JWKS_BYTES: usize = 64 * 1024;

#[derive(Clone)]
pub struct AuthConfig {
    jwt_secret: SecretString,
    admin_password: SecretString,
    admin_session_lifetime: Duration,
    access_token_lifetime: Duration,
    refresh_token_lifetime: Duration,
    apple_jwks_url: reqwest::Url,
    apple_signin_audiences: Arc<[String]>,
    apple_http: reqwest::Client,
    apple_jwks: Arc<tokio::sync::RwLock<Option<CachedAppleJwks>>>,
    apple_token_url: reqwest::Url,
    apple_revoke_url: reqwest::Url,
    apple_client_id: Arc<str>,
    apple_revocation: Option<AppleRevocationCredentials>,
}

impl Debug for AuthConfig {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("AuthConfig")
            .field("jwt_secret", &"[REDACTED]")
            .field("admin_password", &"[REDACTED]")
            .field("admin_session_lifetime", &self.admin_session_lifetime)
            .field("access_token_lifetime", &self.access_token_lifetime)
            .field("refresh_token_lifetime", &self.refresh_token_lifetime)
            .field("apple_jwks_url", &self.apple_jwks_url)
            .field("apple_signin_audiences", &self.apple_signin_audiences)
            .field("apple_token_url", &self.apple_token_url)
            .field("apple_revoke_url", &self.apple_revoke_url)
            .field("apple_client_id", &self.apple_client_id)
            .field(
                "apple_revocation",
                &self.apple_revocation.as_ref().map(|_| "[CONFIGURED]"),
            )
            // The HTTP client and mutable JWKS cache are implementation details, not
            // useful configuration diagnostics.
            .finish_non_exhaustive()
    }
}

pub(crate) struct AuthConfigInput {
    pub jwt_secret: SecretString,
    pub jwt_algorithm: String,
    pub admin_password: SecretString,
    pub admin_session_lifetime: Duration,
    pub access_token_lifetime: Duration,
    pub refresh_token_lifetime: Duration,
    pub apple_jwks_url: reqwest::Url,
    pub apple_signin_audiences: Vec<String>,
    pub apple_token_url: reqwest::Url,
    pub apple_revoke_url: reqwest::Url,
    pub apple_client_id: String,
    pub apple_team_id: Option<String>,
    pub apple_key_id: Option<String>,
    pub apple_private_key: Option<SecretString>,
}

impl AuthConfig {
    /// Build coexistence authentication configuration.
    ///
    /// # Errors
    ///
    /// Returns [`AuthConfigError::UnsupportedAlgorithm`] unless the configured
    /// Python signing algorithm is exactly `HS256`.
    pub(crate) fn new(input: AuthConfigInput) -> Result<Self, AuthConfigError> {
        let AuthConfigInput {
            jwt_secret,
            jwt_algorithm,
            admin_password,
            admin_session_lifetime,
            access_token_lifetime,
            refresh_token_lifetime,
            apple_jwks_url,
            apple_signin_audiences,
            apple_token_url,
            apple_revoke_url,
            apple_client_id,
            apple_team_id,
            apple_key_id,
            apple_private_key,
        } = input;
        if jwt_algorithm != "HS256" {
            return Err(AuthConfigError::UnsupportedAlgorithm(jwt_algorithm));
        }
        if admin_password.expose_secret().is_empty() {
            return Err(AuthConfigError::InvalidAdminPassword);
        }
        if admin_session_lifetime.is_zero()
            || access_token_lifetime.is_zero()
            || refresh_token_lifetime.is_zero()
        {
            return Err(AuthConfigError::InvalidLifetime);
        }
        if apple_signin_audiences.is_empty()
            || apple_signin_audiences
                .iter()
                .any(|audience| audience.trim().is_empty())
        {
            return Err(AuthConfigError::InvalidAppleAudience);
        }
        if apple_jwks_url.scheme() != "https" || apple_jwks_url.cannot_be_a_base() {
            return Err(AuthConfigError::InvalidAppleJwksUrl);
        }
        if apple_token_url.scheme() != "https"
            || apple_token_url.cannot_be_a_base()
            || apple_revoke_url.scheme() != "https"
            || apple_revoke_url.cannot_be_a_base()
        {
            return Err(AuthConfigError::InvalidAppleAccountUrl);
        }
        let apple_client_id = apple_client_id.trim().to_owned();
        if apple_client_id.is_empty() {
            return Err(AuthConfigError::InvalidAppleClientId);
        }
        let apple_revocation = match (apple_team_id, apple_key_id, apple_private_key) {
            (Some(team_id), Some(key_id), Some(private_key))
                if !team_id.trim().is_empty()
                    && !key_id.trim().is_empty()
                    && !private_key.expose_secret().trim().is_empty() =>
            {
                Some(AppleRevocationCredentials {
                    team_id: team_id.trim().into(),
                    key_id: key_id.trim().into(),
                    private_key,
                })
            }
            _ => None,
        };
        let apple_http = reqwest::Client::builder()
            .connect_timeout(Duration::from_secs(5))
            .timeout(Duration::from_secs(10))
            .redirect(reqwest::redirect::Policy::none())
            .build()?;
        Ok(Self {
            jwt_secret,
            admin_password,
            admin_session_lifetime,
            access_token_lifetime,
            refresh_token_lifetime,
            apple_jwks_url,
            apple_signin_audiences: apple_signin_audiences.into(),
            apple_http,
            apple_jwks: Arc::new(tokio::sync::RwLock::new(None)),
            apple_token_url,
            apple_revoke_url,
            apple_client_id: apple_client_id.into(),
            apple_revocation,
        })
    }

    fn decoding_key(&self) -> DecodingKey {
        DecodingKey::from_secret(self.jwt_secret.expose_secret().as_bytes())
    }

    fn encoding_key(&self) -> EncodingKey {
        EncodingKey::from_secret(self.jwt_secret.expose_secret().as_bytes())
    }

    pub(crate) fn issue_audio_episode_share_token(
        &self,
        audio_episode_id: i64,
        nonce: &str,
    ) -> Result<String, jsonwebtoken::errors::Error> {
        #[derive(Serialize)]
        struct AudioEpisodeShareClaims<'a> {
            #[serde(rename = "type")]
            token_type: &'static str,
            audio_episode_id: i64,
            nonce: &'a str,
        }

        encode(
            &Header::new(Algorithm::HS256),
            &AudioEpisodeShareClaims {
                token_type: "audio_episode_share",
                audio_episode_id,
                nonce,
            },
            &self.encoding_key(),
        )
    }

    pub(crate) fn decode_audio_episode_share_token(
        &self,
        token: &str,
    ) -> Result<(i64, String), jsonwebtoken::errors::Error> {
        #[derive(Deserialize)]
        struct AudioEpisodeShareClaims {
            #[serde(rename = "type")]
            token_type: String,
            audio_episode_id: i64,
            nonce: String,
        }

        let mut validation = Validation::new(Algorithm::HS256);
        validation.required_spec_claims.clear();
        validation.validate_exp = false;
        let claims =
            decode::<AudioEpisodeShareClaims>(token, &self.decoding_key(), &validation)?.claims;
        if claims.token_type != "audio_episode_share"
            || claims.audio_episode_id <= 0
            || claims.nonce.is_empty()
        {
            return Err(jsonwebtoken::errors::Error::from(
                jsonwebtoken::errors::ErrorKind::InvalidToken,
            ));
        }
        Ok((claims.audio_episode_id, claims.nonce))
    }

    pub(crate) fn is_valid_admin_session(&self, raw_token: &str) -> bool {
        let mut validation = Validation::new(Algorithm::HS256);
        validation.set_required_spec_claims(&["exp", "sub", "type"]);
        validation.leeway = 0;
        decode::<TokenClaims>(raw_token, &self.decoding_key(), &validation)
            .map(|token| {
                let now = Utc::now().timestamp();
                token.claims.sub == "admin"
                    && token.claims.token_type == "admin_session"
                    && token
                        .claims
                        .iat
                        .is_none_or(|issued_at| issued_at <= now && issued_at <= token.claims.exp)
            })
            .unwrap_or(false)
    }

    pub(crate) fn verify_admin_password(&self, presented: &str) -> bool {
        let presented = Sha256::digest(presented.as_bytes());
        let configured = Sha256::digest(self.admin_password.expose_secret().as_bytes());
        bool::from(presented.as_slice().ct_eq(configured.as_slice()))
    }

    pub(crate) fn issue_admin_session(
        &self,
        now: DateTime<Utc>,
    ) -> Result<String, AdminSessionError> {
        let lifetime = i64::try_from(self.admin_session_lifetime.as_secs())
            .map_err(|_| AdminSessionError::LifetimeOverflow)?;
        let issued_at = now.timestamp();
        let expires_at = issued_at
            .checked_add(lifetime)
            .ok_or(AdminSessionError::LifetimeOverflow)?;
        Ok(encode(
            &Header::new(Algorithm::HS256),
            &TokenClaims {
                sub: "admin".to_owned(),
                token_type: "admin_session".to_owned(),
                exp: expires_at,
                iat: Some(issued_at),
                jti: None,
            },
            &self.encoding_key(),
        )?)
    }

    pub(crate) fn admin_session_max_age_seconds(&self) -> u64 {
        self.admin_session_lifetime.as_secs()
    }

    pub(crate) fn decode_refresh_token(
        &self,
        raw_token: &str,
    ) -> Result<VerifiedRefreshToken, RefreshTokenError> {
        let mut validation = Validation::new(Algorithm::HS256);
        validation.set_required_spec_claims(&["exp", "sub", "type"]);
        validation.leeway = 0;
        let claims = decode::<TokenClaims>(raw_token, &self.decoding_key(), &validation)?.claims;
        if claims.token_type != "refresh" || claims.iat.is_some_and(|issued| issued > claims.exp) {
            return Err(RefreshTokenError::InvalidClaims);
        }
        let user_id = claims
            .sub
            .parse::<i64>()
            .ok()
            .filter(|value| *value > 0)
            .ok_or(RefreshTokenError::InvalidClaims)?;
        let expires_at =
            DateTime::from_timestamp(claims.exp, 0).ok_or(RefreshTokenError::InvalidClaims)?;
        Ok(VerifiedRefreshToken {
            user_id,
            expires_at,
        })
    }

    pub(crate) fn issue_token_pair(
        &self,
        user_id: i64,
        now: DateTime<Utc>,
    ) -> Result<AccessTokenResponse, RefreshTokenError> {
        let subject = user_id.to_string();
        let issued_at = now.timestamp();
        let access_expiry = issued_at
            .checked_add(duration_seconds(self.access_token_lifetime)?)
            .ok_or(RefreshTokenError::LifetimeOverflow)?;
        let refresh_expiry = issued_at
            .checked_add(duration_seconds(self.refresh_token_lifetime)?)
            .ok_or(RefreshTokenError::LifetimeOverflow)?;
        let access_token = encode(
            &Header::new(Algorithm::HS256),
            &TokenClaims {
                sub: subject.clone(),
                token_type: "access".to_owned(),
                exp: access_expiry,
                iat: Some(issued_at),
                jti: None,
            },
            &self.encoding_key(),
        )?;
        let refresh_token = encode(
            &Header::new(Algorithm::HS256),
            &TokenClaims {
                sub: subject,
                token_type: "refresh".to_owned(),
                exp: refresh_expiry,
                iat: Some(issued_at),
                jti: Some(Uuid::new_v4().to_string()),
            },
            &self.encoding_key(),
        )?;
        Ok(AccessTokenResponse {
            access_token,
            refresh_token,
            token_type: "bearer".to_owned(),
        })
    }

    pub(crate) fn encrypt_refresh_replay(
        &self,
        tokens: &AccessTokenResponse,
    ) -> Result<String, RefreshTokenError> {
        let payload = serde_json::to_vec(&ReplayPayload {
            access_token: &tokens.access_token,
            refresh_token: &tokens.refresh_token,
        })?;
        Ok(self.replay_cipher()?.encrypt(&payload))
    }

    pub(crate) fn decrypt_refresh_replay(
        &self,
        encrypted: &str,
    ) -> Result<AccessTokenResponse, RefreshTokenError> {
        let payload = self
            .replay_cipher()?
            .decrypt(encrypted)
            .map_err(|_| RefreshTokenError::InvalidReplay)?;
        let decoded: OwnedReplayPayload = serde_json::from_slice(&payload)?;
        if decoded.access_token.is_empty() || decoded.refresh_token.is_empty() {
            return Err(RefreshTokenError::InvalidReplay);
        }
        Ok(AccessTokenResponse {
            access_token: decoded.access_token,
            refresh_token: decoded.refresh_token,
            token_type: "bearer".to_owned(),
        })
    }

    fn replay_cipher(&self) -> Result<Fernet, RefreshTokenError> {
        const CONTEXT: &[u8] = b"newsly:refresh-token-replay:v1\0";
        let mut digest = Sha256::new();
        digest.update(CONTEXT);
        digest.update(self.jwt_secret.expose_secret().as_bytes());
        let encoded_key = URL_SAFE.encode(digest.finalize());
        Fernet::new(&encoded_key).ok_or(RefreshTokenError::InvalidReplayKey)
    }

    pub(crate) async fn verify_apple_identity(
        &self,
        id_token: &str,
    ) -> Result<VerifiedAppleIdentity, AppleIdentityError> {
        let header = decode_header(id_token)?;
        if header.alg != Algorithm::RS256 {
            return Err(AppleIdentityError::InvalidAlgorithm);
        }
        let key_id = header.kid.ok_or(AppleIdentityError::MissingKeyId)?;
        let decoding_key = self.apple_decoding_key(&key_id).await?;
        let mut validation = Validation::new(Algorithm::RS256);
        validation.set_audience(&self.apple_signin_audiences);
        validation.set_issuer(&["https://appleid.apple.com"]);
        validation.set_required_spec_claims(&["exp", "sub", "aud", "iss"]);
        validation.leeway = 0;
        let claims = decode::<AppleClaims>(id_token, &decoding_key, &validation)?.claims;
        if claims.sub.trim().is_empty() {
            return Err(AppleIdentityError::MissingSubject);
        }
        Ok(VerifiedAppleIdentity {
            subject: claims.sub,
            email: claims.email,
            name: claims.name,
        })
    }

    pub(crate) async fn exchange_and_revoke_apple_authorization(
        &self,
        authorization_code: &str,
    ) -> Result<(), AppleAccountError> {
        let authorization_code = authorization_code.trim();
        if authorization_code.is_empty() {
            return Err(AppleAccountError::MissingAuthorizationCode);
        }
        let credentials = self
            .apple_revocation
            .as_ref()
            .ok_or(AppleAccountError::NotConfigured)?;
        let now = Utc::now().timestamp();
        let mut header = Header::new(Algorithm::ES256);
        header.kid = Some(credentials.key_id.to_string());
        let private_key = credentials.private_key.expose_secret().replace("\\n", "\n");
        let signing_key = EncodingKey::from_ec_pem(private_key.as_bytes())?;
        let client_secret = encode(
            &header,
            &AppleClientSecretClaims {
                iss: credentials.team_id.as_ref(),
                iat: now,
                exp: now
                    .checked_add(5 * 60)
                    .ok_or(AppleAccountError::LifetimeOverflow)?,
                aud: "https://appleid.apple.com",
                sub: self.apple_client_id.as_ref(),
            },
            &signing_key,
        )?;
        let token_response = self
            .apple_http
            .post(self.apple_token_url.clone())
            .form(&AppleTokenExchangeForm {
                client_id: self.apple_client_id.as_ref(),
                client_secret: &client_secret,
                code: authorization_code,
                grant_type: "authorization_code",
            })
            .send()
            .await?;
        let token_status = token_response.status();
        let token_body = bounded_apple_body(token_response).await?;
        if !token_status.is_success() {
            return Err(AppleAccountError::ExchangeRejected(token_status));
        }
        let token_payload: AppleTokenExchangeResponse = serde_json::from_slice(&token_body)?;
        let (token, token_type_hint) = token_payload
            .refresh_token
            .as_deref()
            .filter(|value| !value.is_empty())
            .map(|token| (token, "refresh_token"))
            .or_else(|| {
                token_payload
                    .access_token
                    .as_deref()
                    .filter(|value| !value.is_empty())
                    .map(|token| (token, "access_token"))
            })
            .ok_or(AppleAccountError::MissingRevocableToken)?;
        let revoke_response = self
            .apple_http
            .post(self.apple_revoke_url.clone())
            .form(&AppleTokenRevokeForm {
                client_id: self.apple_client_id.as_ref(),
                client_secret: &client_secret,
                token,
                token_type_hint,
            })
            .send()
            .await?;
        let revoke_status = revoke_response.status();
        let _ = bounded_apple_body(revoke_response).await?;
        if !revoke_status.is_success() {
            return Err(AppleAccountError::RevokeRejected(revoke_status));
        }
        Ok(())
    }

    async fn apple_decoding_key(&self, key_id: &str) -> Result<DecodingKey, AppleIdentityError> {
        const JWKS_CACHE_TTL: Duration = Duration::from_secs(60 * 60);
        {
            let cached = self.apple_jwks.read().await;
            if let Some(cached) = cached.as_ref()
                && cached.fetched_at.elapsed() <= JWKS_CACHE_TTL
                && let Some(key) = cached.keys.find(key_id)
            {
                return DecodingKey::from_jwk(key).map_err(AppleIdentityError::Jwt);
            }
        }

        let response = self
            .apple_http
            .get(self.apple_jwks_url.clone())
            .send()
            .await?
            .error_for_status()?;
        let body = response.bytes().await?;
        if body.len() > MAX_APPLE_JWKS_BYTES {
            return Err(AppleIdentityError::JwksTooLarge);
        }
        let keys: JwkSet = serde_json::from_slice(&body)?;
        let decoding_key = keys
            .find(key_id)
            .ok_or_else(|| AppleIdentityError::UnknownKey(key_id.to_owned()))
            .and_then(|key| DecodingKey::from_jwk(key).map_err(AppleIdentityError::Jwt))?;
        *self.apple_jwks.write().await = Some(CachedAppleJwks {
            fetched_at: Instant::now(),
            keys,
        });
        Ok(decoding_key)
    }
}

#[derive(Debug, Clone)]
struct CachedAppleJwks {
    fetched_at: Instant,
    keys: JwkSet,
}

#[derive(Clone)]
struct AppleRevocationCredentials {
    team_id: Arc<str>,
    key_id: Arc<str>,
    private_key: SecretString,
}

impl Debug for AppleRevocationCredentials {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("AppleRevocationCredentials")
            .field("team_id", &self.team_id)
            .field("key_id", &self.key_id)
            .field("private_key", &"[REDACTED]")
            .finish()
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthenticatedUser {
    pub id: i64,
    pub apple_id: String,
}

#[derive(Debug, Deserialize)]
struct AccessTokenClaims {
    sub: String,
    #[serde(rename = "type")]
    token_type: String,
    exp: u64,
    iat: u64,
}

#[derive(Debug, Deserialize, Serialize)]
struct TokenClaims {
    sub: String,
    #[serde(rename = "type")]
    token_type: String,
    exp: i64,
    iat: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    jti: Option<String>,
}

#[derive(Debug, Serialize)]
struct ReplayPayload<'a> {
    access_token: &'a str,
    refresh_token: &'a str,
}

#[derive(Debug, Deserialize)]
struct OwnedReplayPayload {
    access_token: String,
    refresh_token: String,
}

#[derive(Debug, Deserialize)]
struct AppleClaims {
    sub: String,
    email: Option<String>,
    name: Option<Value>,
}

#[derive(Debug, Serialize)]
struct AppleClientSecretClaims<'a> {
    iss: &'a str,
    iat: i64,
    exp: i64,
    aud: &'a str,
    sub: &'a str,
}

#[derive(Debug, Serialize)]
struct AppleTokenExchangeForm<'a> {
    client_id: &'a str,
    client_secret: &'a str,
    code: &'a str,
    grant_type: &'a str,
}

#[derive(Debug, Deserialize)]
struct AppleTokenExchangeResponse {
    refresh_token: Option<String>,
    access_token: Option<String>,
}

#[derive(Debug, Serialize)]
struct AppleTokenRevokeForm<'a> {
    client_id: &'a str,
    client_secret: &'a str,
    token: &'a str,
    token_type_hint: &'a str,
}

#[derive(Debug, Clone)]
pub(crate) struct VerifiedAppleIdentity {
    pub subject: String,
    pub email: Option<String>,
    pub name: Option<Value>,
}

#[derive(Debug, Clone, Copy)]
pub(crate) struct VerifiedRefreshToken {
    pub user_id: i64,
    pub expires_at: DateTime<Utc>,
}

impl FromRequestParts<AppState> for AuthenticatedUser {
    type Rejection = ApiError;

    async fn from_request_parts(
        parts: &mut Parts,
        state: &AppState,
    ) -> Result<Self, Self::Rejection> {
        let request_id = request_id_from_headers(&parts.headers);
        let raw_header = parts.headers.get(AUTHORIZATION).ok_or_else(|| {
            ApiError::new(
                StatusCode::FORBIDDEN,
                "forbidden",
                "Not authenticated",
                request_id.clone(),
            )
        })?;
        let raw_header = raw_header
            .to_str()
            .map_err(|_| invalid_credentials(request_id.clone()))?;
        let token =
            bearer_token(raw_header).ok_or_else(|| invalid_credentials(request_id.clone()))?;

        let row = if is_api_key_token(token) {
            find_user_by_api_key(state.database.pool(), token)
                .await
                .map_err(|error| {
                    tracing::error!(error = %error, "API-key authentication lookup failed");
                    ApiError::new(
                        StatusCode::INTERNAL_SERVER_ERROR,
                        "internal_error",
                        "Internal server error",
                        request_id.clone(),
                    )
                    .with_retryable(true)
                })?
        } else {
            let mut validation = Validation::new(Algorithm::HS256);
            validation.set_required_spec_claims(&["exp", "sub", "type"]);
            let claims =
                decode::<AccessTokenClaims>(token, &state.auth.decoding_key(), &validation)
                    .map_err(|_| invalid_credentials(request_id.clone()))?
                    .claims;
            if claims.token_type != "access" || claims.iat > claims.exp {
                return Err(invalid_credentials(request_id));
            }
            let user_id = claims
                .sub
                .parse::<i64>()
                .ok()
                .filter(|value| *value > 0)
                .ok_or_else(|| invalid_credentials(request_id.clone()))?;
            find_user_by_id(state.database.pool(), user_id)
                .await
                .map_err(|error| {
                    tracing::error!(error = %error, "JWT user lookup failed");
                    ApiError::new(
                        StatusCode::INTERNAL_SERVER_ERROR,
                        "internal_error",
                        "Internal server error",
                        request_id.clone(),
                    )
                    .with_retryable(true)
                })?
        }
        .ok_or_else(|| invalid_credentials(request_id.clone()))?;

        if !row.is_active {
            return Err(ApiError::new(
                StatusCode::BAD_REQUEST,
                "bad_request",
                "Inactive user",
                request_id,
            ));
        }

        Ok(Self {
            id: row.id,
            apple_id: row.apple_id,
        })
    }
}

fn bearer_token(header: &str) -> Option<&str> {
    let mut parts = header.split_whitespace();
    let scheme = parts.next()?;
    let token = parts.next()?;
    if !scheme.eq_ignore_ascii_case("bearer") || token.is_empty() || parts.next().is_some() {
        return None;
    }
    Some(token)
}

fn invalid_credentials(request_id: String) -> ApiError {
    ApiError::new(
        StatusCode::UNAUTHORIZED,
        "authentication_required",
        "Could not validate credentials",
        request_id,
    )
    .bearer()
}

#[derive(Debug, Error)]
pub enum AuthConfigError {
    #[error("unsupported JWT_ALGORITHM {0:?}; Rust coexistence requires HS256")]
    UnsupportedAlgorithm(String),
    #[error("ADMIN_PASSWORD must be nonempty")]
    InvalidAdminPassword,
    #[error("admin, access, and refresh token lifetimes must be nonzero")]
    InvalidLifetime,
    #[error("APPLE_SIGNIN_AUDIENCES must contain at least one nonempty audience")]
    InvalidAppleAudience,
    #[error("APPLE_JWKS_URL must be an absolute HTTPS URL")]
    InvalidAppleJwksUrl,
    #[error("APPLE_TOKEN_URL and APPLE_REVOKE_URL must be absolute HTTPS URLs")]
    InvalidAppleAccountUrl,
    #[error("APPLE_CLIENT_ID must be nonempty")]
    InvalidAppleClientId,
    #[error("Apple JWKS HTTP client could not be built")]
    AppleHttp(#[from] reqwest::Error),
}

#[derive(Debug, Error)]
pub(crate) enum AdminSessionError {
    #[error("admin session token signing failed")]
    Jwt(#[from] jsonwebtoken::errors::Error),
    #[error("admin session lifetime exceeds the supported range")]
    LifetimeOverflow,
}

#[derive(Debug, Error)]
pub(crate) enum RefreshTokenError {
    #[error("refresh token signature or expiry is invalid")]
    Jwt(#[from] jsonwebtoken::errors::Error),
    #[error("refresh token claims are invalid")]
    InvalidClaims,
    #[error("token lifetime exceeds the supported range")]
    LifetimeOverflow,
    #[error("refresh replay key derivation failed")]
    InvalidReplayKey,
    #[error("stored refresh replay payload is invalid")]
    InvalidReplay,
    #[error("stored refresh replay JSON is invalid")]
    ReplayJson(#[from] serde_json::Error),
}

fn duration_seconds(duration: Duration) -> Result<i64, RefreshTokenError> {
    i64::try_from(duration.as_secs()).map_err(|_| RefreshTokenError::LifetimeOverflow)
}

#[derive(Debug, Error)]
pub(crate) enum AppleIdentityError {
    #[error("Apple identity token is invalid")]
    Jwt(#[from] jsonwebtoken::errors::Error),
    #[error("Apple identity token must use RS256")]
    InvalidAlgorithm,
    #[error("Apple identity token is missing a key id")]
    MissingKeyId,
    #[error("Apple identity token is missing its subject")]
    MissingSubject,
    #[error("Apple signing key {0:?} is unknown")]
    UnknownKey(String),
    #[error("Apple JWKS response exceeded its size limit")]
    JwksTooLarge,
    #[error("Apple JWKS request failed")]
    Http(#[from] reqwest::Error),
    #[error("Apple JWKS response was invalid")]
    JwksJson(#[from] serde_json::Error),
}

#[derive(Debug, Error)]
pub(crate) enum AppleAccountError {
    #[error("Apple authorization code is required")]
    MissingAuthorizationCode,
    #[error("Apple account revocation credentials are not configured")]
    NotConfigured,
    #[error("Apple client-secret lifetime overflowed")]
    LifetimeOverflow,
    #[error("Apple client-secret signing failed")]
    Jwt(#[from] jsonwebtoken::errors::Error),
    #[error("Apple authorization request failed")]
    Http(#[from] reqwest::Error),
    #[error("Apple authorization response exceeded its size limit")]
    ResponseTooLarge,
    #[error("Apple token exchange was rejected with {0}")]
    ExchangeRejected(reqwest::StatusCode),
    #[error("Apple token exchange returned invalid JSON")]
    ExchangeJson(#[from] serde_json::Error),
    #[error("Apple token exchange did not return a revocable token")]
    MissingRevocableToken,
    #[error("Apple authorization revocation was rejected with {0}")]
    RevokeRejected(reqwest::StatusCode),
}

async fn bounded_apple_body(response: reqwest::Response) -> Result<Vec<u8>, AppleAccountError> {
    const MAX_APPLE_RESPONSE_BYTES: u64 = 64 * 1024;
    if response
        .content_length()
        .is_some_and(|length| length > MAX_APPLE_RESPONSE_BYTES)
    {
        return Err(AppleAccountError::ResponseTooLarge);
    }
    let body = response.bytes().await?;
    if body.len() > usize::try_from(MAX_APPLE_RESPONSE_BYTES).expect("response bound fits usize") {
        return Err(AppleAccountError::ResponseTooLarge);
    }
    Ok(body.to_vec())
}

#[cfg(test)]
mod tests {
    use super::bearer_token;

    #[test]
    fn bearer_parser_is_case_insensitive_and_rejects_extra_parts() {
        assert_eq!(bearer_token("Bearer token"), Some("token"));
        assert_eq!(bearer_token("bearer token"), Some("token"));
        assert_eq!(bearer_token("Basic token"), None);
        assert_eq!(bearer_token("Bearer token extra"), None);
    }
}
