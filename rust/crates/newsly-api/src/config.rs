use std::env;
use std::fmt::{self, Debug, Formatter};
use std::net::SocketAddr;
use std::time::Duration;

use newsly_db::DatabaseConfig;
use newsly_domain::{ApplicationSha, ReplicaId};
use reqwest::Url;
use secrecy::SecretString;
use thiserror::Error;

use crate::auth::AuthConfigInput;
use crate::{AuthConfig, AuthConfigError};

const DEFAULT_BIND_ADDRESS: &str = "0.0.0.0:8100";
const DEFAULT_SERVICE_NAME: &str = "Newsly Rust API";
const DEFAULT_ENVIRONMENT: &str = "development";
const DEFAULT_LOG_FILTER: &str = "newsly_api=info,tower_http=info";
const DEFAULT_APPLE_JWKS_URL: &str = "https://appleid.apple.com/auth/keys";
const DEFAULT_APPLE_SIGNIN_AUDIENCE: &str = "org.willemaw.newsly";
const DEFAULT_APPLE_TOKEN_URL: &str = "https://appleid.apple.com/auth/token";
const DEFAULT_APPLE_REVOKE_URL: &str = "https://appleid.apple.com/auth/revoke";
const DEFAULT_APPLE_CLIENT_ID: &str = "org.willemaw.newsly";
const DEFAULT_X_OAUTH_AUTHORIZE_URL: &str = "https://x.com/i/oauth2/authorize";
const DEFAULT_X_OAUTH_TOKEN_URL: &str = "https://api.x.com/2/oauth2/token";
const DEFAULT_X_API_BASE_URL: &str = "https://api.x.com/2";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LogFormat {
    Json,
    Pretty,
}

impl LogFormat {
    fn parse(value: &str) -> Result<Self, ConfigError> {
        match value.trim().to_ascii_lowercase().as_str() {
            "json" => Ok(Self::Json),
            "pretty" | "text" => Ok(Self::Pretty),
            _ => Err(ConfigError::InvalidValue {
                name: "NEWSLY_RUST_LOG_FORMAT",
                value: value.to_owned(),
                expected: "json or pretty",
            }),
        }
    }
}

#[derive(Clone)]
pub struct ServerConfig {
    pub bind_address: SocketAddr,
    pub service_name: String,
    pub environment: String,
    pub debug: bool,
    pub log_filter: String,
    pub log_format: LogFormat,
    pub readiness_timeout: Duration,
    pub checkout_timeout: Duration,
    pub database: DatabaseConfig,
    pub auth: AuthConfig,
    pub openai_api_key: Option<SecretString>,
    pub openai_api_base: Option<String>,
    pub openai_transcription_timeout: Duration,
    pub integration_token_encryption_key: Option<SecretString>,
    pub x_client_id: Option<String>,
    pub x_client_secret: Option<SecretString>,
    pub x_oauth_redirect_uri: Option<String>,
    pub x_oauth_authorize_url: Url,
    pub x_oauth_token_url: Url,
    pub x_api_base_url: Url,
    pub replica_id: ReplicaId,
    pub application_sha: ApplicationSha,
}

impl Debug for ServerConfig {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ServerConfig")
            .field("bind_address", &self.bind_address)
            .field("service_name", &self.service_name)
            .field("environment", &self.environment)
            .field("debug", &self.debug)
            .field("log_filter", &self.log_filter)
            .field("log_format", &self.log_format)
            .field("readiness_timeout", &self.readiness_timeout)
            .field("checkout_timeout", &self.checkout_timeout)
            .field("database", &self.database)
            .field("auth", &self.auth)
            .field("openai_api_key_configured", &self.openai_api_key.is_some())
            .field("openai_api_base", &self.openai_api_base)
            .field(
                "openai_transcription_timeout",
                &self.openai_transcription_timeout,
            )
            .field(
                "integration_token_encryption_key_configured",
                &self.integration_token_encryption_key.is_some(),
            )
            .field("x_client_id_configured", &self.x_client_id.is_some())
            .field(
                "x_client_secret_configured",
                &self.x_client_secret.is_some(),
            )
            .field(
                "x_oauth_redirect_uri_configured",
                &self.x_oauth_redirect_uri.is_some(),
            )
            .field("x_oauth_authorize_url", &self.x_oauth_authorize_url)
            .field("x_oauth_token_url", &self.x_oauth_token_url)
            .field("x_api_base_url", &self.x_api_base_url)
            .field("replica_id", &self.replica_id)
            .field("application_sha", &self.application_sha)
            .finish()
    }
}

impl ServerConfig {
    /// Loads and validates process configuration from the environment.
    ///
    /// # Errors
    ///
    /// Returns [`ConfigError`] when a required value is absent or a value is malformed.
    #[expect(
        clippy::too_many_lines,
        reason = "startup configuration is a single linear parse-and-validate boundary"
    )]
    pub fn from_env() -> Result<Self, ConfigError> {
        let bind_address = value_or_default("NEWSLY_RUST_BIND_ADDR", DEFAULT_BIND_ADDRESS)
            .parse()
            .map_err(|source| ConfigError::InvalidSocketAddress {
                name: "NEWSLY_RUST_BIND_ADDR",
                source,
            })?;
        let service_name = value_or_default("NEWSLY_RUST_SERVICE_NAME", DEFAULT_SERVICE_NAME);
        let environment = value_or_default("ENVIRONMENT", DEFAULT_ENVIRONMENT);
        let debug = parse_bool("DEBUG", false)?;
        let log_filter = env::var("RUST_LOG").unwrap_or_else(|_| DEFAULT_LOG_FILTER.to_owned());
        let log_format = LogFormat::parse(
            &env::var("NEWSLY_RUST_LOG_FORMAT").unwrap_or_else(|_| "json".to_owned()),
        )?;
        let readiness_timeout =
            Duration::from_millis(parse_u64("NEWSLY_RUST_READINESS_TIMEOUT_MS", 2_000)?);
        let checkout_timeout =
            Duration::from_secs(parse_u64("CHECKOUT_TIMEOUT_MINUTES", 30)?.saturating_mul(60));

        let database_url = env::var("DATABASE_URL")
            .map(SecretString::from)
            .map_err(|_| ConfigError::Missing("DATABASE_URL"))?;
        let mut database = DatabaseConfig::new(database_url, "newsly-api");
        database.max_connections = parse_u32("NEWSLY_RUST_DATABASE_MAX_CONNECTIONS", 5)?;
        database.min_connections = parse_u32("NEWSLY_RUST_DATABASE_MIN_CONNECTIONS", 0)?;
        database.acquire_timeout =
            Duration::from_millis(parse_u64("NEWSLY_RUST_DATABASE_ACQUIRE_TIMEOUT_MS", 5_000)?);
        let jwt_secret = env::var("JWT_SECRET_KEY")
            .map(SecretString::from)
            .map_err(|_| ConfigError::Missing("JWT_SECRET_KEY"))?;
        let jwt_algorithm = value_or_default("JWT_ALGORITHM", "HS256");
        let admin_password = env::var("ADMIN_PASSWORD")
            .map(SecretString::from)
            .map_err(|_| ConfigError::Missing("ADMIN_PASSWORD"))?;
        let admin_session_lifetime = Duration::from_secs(
            parse_u64("ADMIN_SESSION_EXPIRE_MINUTES", 10_080)?.saturating_mul(60),
        );
        let access_token_lifetime = Duration::from_secs(
            parse_u64("ACCESS_TOKEN_EXPIRE_MINUTES", 43_200)?.saturating_mul(60),
        );
        let refresh_token_lifetime = Duration::from_secs(
            parse_u64("REFRESH_TOKEN_EXPIRE_DAYS", 90)?.saturating_mul(24 * 60 * 60),
        );
        let apple_jwks_url_value = value_or_default("APPLE_JWKS_URL", DEFAULT_APPLE_JWKS_URL);
        let apple_jwks_url: Url =
            apple_jwks_url_value
                .parse()
                .map_err(|_| ConfigError::InvalidValue {
                    name: "APPLE_JWKS_URL",
                    value: apple_jwks_url_value,
                    expected: "an absolute HTTPS URL",
                })?;
        let apple_signin_audiences =
            parse_string_list("APPLE_SIGNIN_AUDIENCES", DEFAULT_APPLE_SIGNIN_AUDIENCE)?;
        let apple_token_url = parse_https_url("APPLE_TOKEN_URL", DEFAULT_APPLE_TOKEN_URL)?;
        let apple_revoke_url = parse_https_url("APPLE_REVOKE_URL", DEFAULT_APPLE_REVOKE_URL)?;
        let apple_client_id = value_or_default("APPLE_CLIENT_ID", DEFAULT_APPLE_CLIENT_ID);
        let apple_team_id = optional_string("APPLE_TEAM_ID");
        let apple_key_id = optional_string("APPLE_KEY_ID");
        let apple_private_key = optional_string("APPLE_PRIVATE_KEY").map(SecretString::from);
        let auth = AuthConfig::new(AuthConfigInput {
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
        })?;
        let openai_api_key = optional_string("OPENAI_API_KEY").map(SecretString::from);
        let openai_api_base = optional_string("OPENAI_BASE_URL");
        let openai_transcription_timeout =
            Duration::from_secs(parse_u64("OPENAI_TRANSCRIPTION_TIMEOUT_SECONDS", 600)?);
        let integration_token_encryption_key =
            optional_string("X_TOKEN_ENCRYPTION_KEY").map(SecretString::from);
        let x_client_id = optional_string("X_CLIENT_ID");
        let x_client_secret = optional_string("X_CLIENT_SECRET").map(SecretString::from);
        let x_oauth_redirect_uri = optional_string("X_OAUTH_REDIRECT_URI");
        let x_oauth_authorize_url =
            parse_http_url("X_OAUTH_AUTHORIZE_URL", DEFAULT_X_OAUTH_AUTHORIZE_URL)?;
        let x_oauth_token_url = parse_http_url("X_OAUTH_TOKEN_URL", DEFAULT_X_OAUTH_TOKEN_URL)?;
        let x_api_base_url = parse_http_url("X_API_BASE_URL", DEFAULT_X_API_BASE_URL)?;
        let replica_id = ReplicaId::new(
            env::var("NEWSLY_REPLICA_ID")
                .or_else(|_| env::var("HOSTNAME"))
                .unwrap_or_else(|_| format!("local-{}", std::process::id())),
        )?;
        let application_sha = ApplicationSha::new(
            env::var("NEWSLY_APPLICATION_SHA")
                .ok()
                .unwrap_or_else(|| "0".repeat(40)),
        )?;

        if database.max_connections == 0 {
            return Err(ConfigError::OutOfRange {
                name: "NEWSLY_RUST_DATABASE_MAX_CONNECTIONS",
                requirement: "must be greater than zero",
            });
        }
        if database.min_connections > database.max_connections {
            return Err(ConfigError::OutOfRange {
                name: "NEWSLY_RUST_DATABASE_MIN_CONNECTIONS",
                requirement: "must not exceed NEWSLY_RUST_DATABASE_MAX_CONNECTIONS",
            });
        }
        if readiness_timeout.is_zero() {
            return Err(ConfigError::OutOfRange {
                name: "NEWSLY_RUST_READINESS_TIMEOUT_MS",
                requirement: "must be greater than zero",
            });
        }
        if checkout_timeout.is_zero() {
            return Err(ConfigError::OutOfRange {
                name: "CHECKOUT_TIMEOUT_MINUTES",
                requirement: "must be greater than zero",
            });
        }
        if openai_transcription_timeout.is_zero() {
            return Err(ConfigError::OutOfRange {
                name: "OPENAI_TRANSCRIPTION_TIMEOUT_SECONDS",
                requirement: "must be greater than zero",
            });
        }

        Ok(Self {
            bind_address,
            service_name,
            environment,
            debug,
            log_filter,
            log_format,
            readiness_timeout,
            checkout_timeout,
            database,
            auth,
            openai_api_key,
            openai_api_base,
            openai_transcription_timeout,
            integration_token_encryption_key,
            x_client_id,
            x_client_secret,
            x_oauth_redirect_uri,
            x_oauth_authorize_url,
            x_oauth_token_url,
            x_api_base_url,
            replica_id,
            application_sha,
        })
    }
}

fn value_or_default(name: &'static str, default: &'static str) -> String {
    env::var(name).unwrap_or_else(|_| default.to_owned())
}

fn optional_string(name: &'static str) -> Option<String> {
    env::var(name)
        .ok()
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
}

fn parse_https_url(name: &'static str, default: &'static str) -> Result<Url, ConfigError> {
    let value = value_or_default(name, default);
    let url: Url = value.parse().map_err(|_| ConfigError::InvalidValue {
        name,
        value: value.clone(),
        expected: "an absolute HTTPS URL",
    })?;
    if url.scheme() != "https" || url.cannot_be_a_base() {
        return Err(ConfigError::InvalidValue {
            name,
            value,
            expected: "an absolute HTTPS URL",
        });
    }
    Ok(url)
}

fn parse_http_url(name: &'static str, default: &'static str) -> Result<Url, ConfigError> {
    let value = value_or_default(name, default);
    let url: Url = value.parse().map_err(|_| ConfigError::InvalidValue {
        name,
        value: value.clone(),
        expected: "an absolute http or https URL",
    })?;
    if !matches!(url.scheme(), "http" | "https") || url.cannot_be_a_base() {
        return Err(ConfigError::InvalidValue {
            name,
            value,
            expected: "an absolute http or https URL",
        });
    }
    Ok(url)
}

fn parse_u32(name: &'static str, default: u32) -> Result<u32, ConfigError> {
    parse_number(name, default)
}

fn parse_bool(name: &'static str, default: bool) -> Result<bool, ConfigError> {
    let Ok(value) = env::var(name) else {
        return Ok(default);
    };
    match value.trim().to_ascii_lowercase().as_str() {
        "1" | "true" | "yes" | "on" => Ok(true),
        "0" | "false" | "no" | "off" => Ok(false),
        _ => Err(ConfigError::InvalidValue {
            name,
            value,
            expected: "a boolean",
        }),
    }
}

fn parse_u64(name: &'static str, default: u64) -> Result<u64, ConfigError> {
    parse_number(name, default)
}

fn parse_number<T>(name: &'static str, default: T) -> Result<T, ConfigError>
where
    T: std::str::FromStr,
{
    match env::var(name) {
        Ok(value) => value
            .parse()
            .map_err(|_| ConfigError::InvalidNumber { name, value }),
        Err(_) => Ok(default),
    }
}

fn parse_string_list(
    name: &'static str,
    default: &'static str,
) -> Result<Vec<String>, ConfigError> {
    let value = env::var(name).unwrap_or_else(|_| default.to_owned());
    let trimmed = value.trim();
    if trimmed.starts_with('[') {
        let values: Vec<serde_json::Value> =
            serde_json::from_str(trimmed).map_err(|_| ConfigError::InvalidValue {
                name,
                value: value.clone(),
                expected: "a comma-separated list or JSON string list",
            })?;
        let mut parsed = Vec::with_capacity(values.len());
        for item in values {
            let Some(item) = item.as_str() else {
                return Err(ConfigError::InvalidValue {
                    name,
                    value,
                    expected: "a comma-separated list or JSON string list",
                });
            };
            let item = item.trim();
            if !item.is_empty() {
                parsed.push(item.to_owned());
            }
        }
        return Ok(parsed);
    }
    Ok(trimmed
        .split(',')
        .map(str::trim)
        .filter(|item| !item.is_empty())
        .map(str::to_owned)
        .collect())
}

#[derive(Debug, Error)]
pub enum ConfigError {
    #[error("required environment variable {0} is missing")]
    Missing(&'static str),
    #[error("{name} is not a valid socket address")]
    InvalidSocketAddress {
        name: &'static str,
        #[source]
        source: std::net::AddrParseError,
    },
    #[error("{name} must be a number, got {value:?}")]
    InvalidNumber { name: &'static str, value: String },
    #[error("invalid {name} value {value:?}; expected {expected}")]
    InvalidValue {
        name: &'static str,
        value: String,
        expected: &'static str,
    },
    #[error("{name} {requirement}")]
    OutOfRange {
        name: &'static str,
        requirement: &'static str,
    },
    #[error(transparent)]
    Auth(#[from] AuthConfigError),
    #[error(transparent)]
    OwnershipIdentity(#[from] newsly_domain::InvalidOwnershipValue),
}
