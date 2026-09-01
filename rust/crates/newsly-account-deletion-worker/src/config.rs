use std::env;
use std::fmt::{self, Debug, Formatter};
use std::path::{Component, Path, PathBuf};
use std::time::Duration;

use newsly_db::DatabaseConfig;
use newsly_worker::config::WorkerLogFormat;
use secrecy::SecretString;
use thiserror::Error;
use url::Url;

const DEFAULT_X_TOKEN_URL: &str = "https://api.x.com/2/oauth2/token";

#[derive(Clone)]
pub enum ArtifactStorageConfig {
    Local {
        root: PathBuf,
    },
    S3Compatible {
        bucket: String,
        endpoint: Option<Url>,
        region: Option<String>,
        access_key: Option<SecretString>,
        secret_key: Option<SecretString>,
        timeout: Duration,
    },
}

impl Debug for ArtifactStorageConfig {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        match self {
            Self::Local { root } => formatter
                .debug_struct("LocalArtifactStorage")
                .field("root", root)
                .finish(),
            Self::S3Compatible {
                bucket,
                endpoint,
                region,
                access_key,
                secret_key,
                timeout,
            } => formatter
                .debug_struct("S3CompatibleArtifactStorage")
                .field("bucket", bucket)
                .field("endpoint", endpoint)
                .field("region", region)
                .field("access_key", &access_key.as_ref().map(|_| "[REDACTED]"))
                .field("secret_key", &secret_key.as_ref().map(|_| "[REDACTED]"))
                .field("timeout", timeout)
                .finish(),
        }
    }
}

#[derive(Clone)]
pub struct AccountDeletionProcessConfig {
    database_url: SecretString,
    pub database: DatabaseConfig,
    pub worker_id: String,
    pub lease_duration: Duration,
    pub max_retries: i32,
    pub log_filter: String,
    pub log_format: WorkerLogFormat,
    pub media_audio_root: PathBuf,
    pub personal_markdown_root: PathBuf,
    pub agent_data_mirror_root: PathBuf,
    pub artifact_storage: ArtifactStorageConfig,
    pub e2b_api_key: Option<SecretString>,
    pub x_client_id: Option<SecretString>,
    pub x_client_secret: Option<SecretString>,
    pub x_token_encryption_key: Option<SecretString>,
    pub x_oauth_token_url: String,
}

impl Debug for AccountDeletionProcessConfig {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("AccountDeletionProcessConfig")
            .field("database_url", &"[REDACTED]")
            .field("database", &self.database)
            .field("worker_id", &self.worker_id)
            .field("lease_duration", &self.lease_duration)
            .field("max_retries", &self.max_retries)
            .field("log_filter", &self.log_filter)
            .field("log_format", &self.log_format)
            .field("media_audio_root", &self.media_audio_root)
            .field("personal_markdown_root", &self.personal_markdown_root)
            .field("agent_data_mirror_root", &self.agent_data_mirror_root)
            .field("artifact_storage", &self.artifact_storage)
            .field(
                "e2b_api_key",
                &self.e2b_api_key.as_ref().map(|_| "[REDACTED]"),
            )
            .field(
                "x_client_id",
                &self.x_client_id.as_ref().map(|_| "[REDACTED]"),
            )
            .field(
                "x_client_secret",
                &self.x_client_secret.as_ref().map(|_| "[REDACTED]"),
            )
            .field(
                "x_token_encryption_key",
                &self.x_token_encryption_key.as_ref().map(|_| "[REDACTED]"),
            )
            .field("x_oauth_token_url", &self.x_oauth_token_url)
            .finish()
    }
}

impl AccountDeletionProcessConfig {
    /// Loads the isolated account-deletion worker configuration without requiring content-worker
    /// provider settings.
    ///
    /// # Errors
    ///
    /// Returns a field-specific error for missing secrets, malformed bounds, or unsafe roots.
    pub fn from_env() -> Result<Self, ProcessConfigError> {
        let database_url = required_secret("DATABASE_URL")?;
        let mut database = DatabaseConfig::new(database_url.clone(), "newsly-account-deletion");
        database.max_connections = parse_u32("NEWSLY_DELETE_WORKER_DATABASE_MAX_CONNECTIONS", 6)?;
        database.min_connections = parse_u32("NEWSLY_DELETE_WORKER_DATABASE_MIN_CONNECTIONS", 0)?;
        database.acquire_timeout = Duration::from_millis(parse_u64(
            "NEWSLY_DELETE_WORKER_DATABASE_ACQUIRE_TIMEOUT_MS",
            5_000,
        )?);
        if database.max_connections == 0 || database.min_connections > database.max_connections {
            return Err(ProcessConfigError::InvalidRange(
                "NEWSLY_DELETE_WORKER_DATABASE connection bounds",
            ));
        }

        let worker_id = env::var("NEWSLY_DELETE_WORKER_ID").unwrap_or_else(|_| {
            let host = env::var("HOSTNAME").unwrap_or_else(|_| "local".to_owned());
            format!("rust-delete-account-{host}-{}", std::process::id())
        });
        if worker_id.trim().is_empty() || worker_id.len() > 100 {
            return Err(ProcessConfigError::InvalidRange("NEWSLY_DELETE_WORKER_ID"));
        }
        let lease_duration =
            Duration::from_secs(parse_u64("NEWSLY_DELETE_WORKER_LEASE_SECONDS", 300)?);
        if lease_duration.is_zero() {
            return Err(ProcessConfigError::InvalidRange(
                "NEWSLY_DELETE_WORKER_LEASE_SECONDS",
            ));
        }
        let max_retries = parse_i32("MAX_TASK_RETRIES", 3)?;
        if max_retries < 0 {
            return Err(ProcessConfigError::InvalidRange("MAX_TASK_RETRIES"));
        }

        let media_root = absolute_safe_root(
            "MEDIA_BASE_DIR",
            env::var_os("MEDIA_BASE_DIR")
                .map_or_else(|| PathBuf::from("./data/media"), PathBuf::from),
        )?;
        let media_audio_root = media_root.join("audio_episodes");
        let personal_markdown_root = absolute_safe_root(
            "PERSONAL_MARKDOWN_ROOT",
            env::var_os("PERSONAL_MARKDOWN_ROOT")
                .map_or_else(|| PathBuf::from("./data/personal_markdown"), PathBuf::from),
        )?;
        let agent_data_mirror_root = absolute_safe_root(
            "AGENT_DATA_MIRROR_ROOT",
            env::var_os("AGENT_DATA_MIRROR_ROOT")
                .map_or_else(|| PathBuf::from("./data/agent_user_data"), PathBuf::from),
        )?;
        let artifact_storage = artifact_storage_from_env()?;

        Ok(Self {
            database_url,
            database,
            worker_id,
            lease_duration,
            max_retries,
            log_filter: env::var("RUST_LOG").unwrap_or_else(|_| {
                "newsly_account_deletion_worker=info,newsly_worker=info,newsly_queue=info"
                    .to_owned()
            }),
            log_format: parse_log_format(
                &env::var("NEWSLY_RUST_LOG_FORMAT").unwrap_or_else(|_| "json".to_owned()),
            )?,
            media_audio_root,
            personal_markdown_root,
            agent_data_mirror_root,
            artifact_storage,
            e2b_api_key: optional_secret_alias("LLM_TASK_SANDBOX_E2B_API_KEY", "E2B_API_KEY")?,
            x_client_id: optional_secret("X_CLIENT_ID")?,
            x_client_secret: optional_secret("X_CLIENT_SECRET")?,
            x_token_encryption_key: optional_secret("X_TOKEN_ENCRYPTION_KEY")?,
            x_oauth_token_url: env::var("X_OAUTH_TOKEN_URL")
                .unwrap_or_else(|_| DEFAULT_X_TOKEN_URL.to_owned()),
        })
    }

    pub const fn database_url(&self) -> &SecretString {
        &self.database_url
    }
}

fn parse_log_format(value: &str) -> Result<WorkerLogFormat, ProcessConfigError> {
    match value.trim().to_ascii_lowercase().as_str() {
        "json" => Ok(WorkerLogFormat::Json),
        "pretty" => Ok(WorkerLogFormat::Pretty),
        _ => Err(ProcessConfigError::InvalidValue {
            name: "NEWSLY_RUST_LOG_FORMAT",
            value: value.to_owned(),
            expected: "json or pretty",
        }),
    }
}

fn artifact_storage_from_env() -> Result<ArtifactStorageConfig, ProcessConfigError> {
    let provider = env::var("CONTENT_BODY_STORAGE_PROVIDER").unwrap_or_else(|_| "local".to_owned());
    match provider.as_str() {
        "local" => Ok(ArtifactStorageConfig::Local {
            root: absolute_safe_root(
                "CONTENT_BODY_LOCAL_ROOT",
                env::var_os("CONTENT_BODY_LOCAL_ROOT")
                    .map_or_else(|| PathBuf::from("./data/content_bodies"), PathBuf::from),
            )?,
        }),
        "s3_compatible" => {
            let bucket = required_string("CONTENT_BODY_STORAGE_BUCKET")?;
            let endpoint = env::var("CONTENT_BODY_STORAGE_ENDPOINT")
                .ok()
                .map(|value| {
                    value
                        .parse::<Url>()
                        .map_err(|_| ProcessConfigError::InvalidValue {
                            name: "CONTENT_BODY_STORAGE_ENDPOINT",
                            value,
                            expected: "an absolute HTTP(S) URL",
                        })
                })
                .transpose()?;
            if endpoint
                .as_ref()
                .is_some_and(|url| !matches!(url.scheme(), "http" | "https"))
            {
                return Err(ProcessConfigError::InvalidRange(
                    "CONTENT_BODY_STORAGE_ENDPOINT",
                ));
            }
            let access_key = optional_secret("CONTENT_BODY_STORAGE_ACCESS_KEY")?;
            let secret_key = optional_secret("CONTENT_BODY_STORAGE_SECRET_KEY")?;
            if access_key.is_some() != secret_key.is_some() {
                return Err(ProcessConfigError::InvalidRange(
                    "CONTENT_BODY_STORAGE credential pair",
                ));
            }
            let timeout =
                Duration::from_secs(parse_u64("CONTENT_BODY_STORAGE_TIMEOUT_SECONDS", 30)?);
            if !(Duration::from_secs(1)..=Duration::from_secs(300)).contains(&timeout) {
                return Err(ProcessConfigError::InvalidRange(
                    "CONTENT_BODY_STORAGE_TIMEOUT_SECONDS",
                ));
            }
            Ok(ArtifactStorageConfig::S3Compatible {
                bucket,
                endpoint,
                region: env::var("CONTENT_BODY_STORAGE_REGION")
                    .ok()
                    .filter(|value| !value.trim().is_empty()),
                access_key,
                secret_key,
                timeout,
            })
        }
        _ => Err(ProcessConfigError::InvalidValue {
            name: "CONTENT_BODY_STORAGE_PROVIDER",
            value: provider,
            expected: "local or s3_compatible",
        }),
    }
}

fn absolute_safe_root(name: &'static str, value: PathBuf) -> Result<PathBuf, ProcessConfigError> {
    let absolute = if value.is_absolute() {
        value
    } else {
        env::current_dir()
            .map_err(ProcessConfigError::CurrentDirectory)?
            .join(value)
    };
    if absolute == Path::new("/")
        || absolute.components().any(|component| {
            matches!(
                component,
                Component::ParentDir | Component::CurDir | Component::Prefix(_)
            )
        })
    {
        return Err(ProcessConfigError::UnsafeRoot(name));
    }
    Ok(absolute)
}

fn required_string(name: &'static str) -> Result<String, ProcessConfigError> {
    let value = env::var(name).map_err(|_| ProcessConfigError::Missing(name))?;
    if value.trim().is_empty() {
        return Err(ProcessConfigError::Empty(name));
    }
    Ok(value)
}

fn required_secret(name: &'static str) -> Result<SecretString, ProcessConfigError> {
    required_string(name).map(SecretString::from)
}

fn optional_secret(name: &'static str) -> Result<Option<SecretString>, ProcessConfigError> {
    match env::var(name) {
        Ok(value) if value.trim().is_empty() => Err(ProcessConfigError::Empty(name)),
        Ok(value) => Ok(Some(SecretString::from(value))),
        Err(env::VarError::NotPresent) => Ok(None),
        Err(env::VarError::NotUnicode(_)) => Err(ProcessConfigError::InvalidUnicode(name)),
    }
}

fn optional_secret_alias(
    primary: &'static str,
    fallback: &'static str,
) -> Result<Option<SecretString>, ProcessConfigError> {
    match optional_secret(primary)? {
        Some(value) => Ok(Some(value)),
        None => optional_secret(fallback),
    }
}

fn parse_u32(name: &'static str, default: u32) -> Result<u32, ProcessConfigError> {
    parse_number(name, default)
}

fn parse_u64(name: &'static str, default: u64) -> Result<u64, ProcessConfigError> {
    parse_number(name, default)
}

fn parse_i32(name: &'static str, default: i32) -> Result<i32, ProcessConfigError> {
    parse_number(name, default)
}

fn parse_number<T>(name: &'static str, default: T) -> Result<T, ProcessConfigError>
where
    T: std::str::FromStr,
{
    match env::var(name) {
        Ok(value) => value
            .parse()
            .map_err(|_| ProcessConfigError::InvalidNumber(name, value)),
        Err(env::VarError::NotPresent) => Ok(default),
        Err(env::VarError::NotUnicode(_)) => Err(ProcessConfigError::InvalidUnicode(name)),
    }
}

#[derive(Debug, Error)]
pub enum ProcessConfigError {
    #[error("required environment variable {0} is missing")]
    Missing(&'static str),
    #[error("environment variable {0} cannot be empty")]
    Empty(&'static str),
    #[error("environment variable {0} is not valid Unicode")]
    InvalidUnicode(&'static str),
    #[error("{name} has invalid value {value:?}; expected {expected}")]
    InvalidValue {
        name: &'static str,
        value: String,
        expected: &'static str,
    },
    #[error("{0} is not a valid number")]
    InvalidNumber(&'static str, String),
    #[error("{0} is outside its supported range")]
    InvalidRange(&'static str),
    #[error("{0} must not resolve to a broad or traversing filesystem root")]
    UnsafeRoot(&'static str),
    #[error("the current working directory is unavailable")]
    CurrentDirectory(#[source] std::io::Error),
}
