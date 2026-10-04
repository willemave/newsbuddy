use std::env;
use std::fmt::{self, Debug, Formatter};
use std::ops::Deref;
use std::path::{Component, Path, PathBuf};

use newsly_providers::{ObjectStorageBackend, ObjectStorageConfigError};
use newsly_worker::queue_process_config::{
    QueueWorkerProcessConfig, QueueWorkerProcessConfigError, QueueWorkerProcessEnv,
};
use secrecy::SecretString;
use thiserror::Error;

const DEFAULT_X_TOKEN_URL: &str = "https://api.x.com/2/oauth2/token";

#[derive(Clone)]
pub struct AccountDeletionProcessConfig {
    pub process: QueueWorkerProcessConfig,
    pub media_audio_root: PathBuf,
    pub personal_markdown_root: PathBuf,
    pub artifact_storage: ObjectStorageBackend,
    pub x_client_id: Option<SecretString>,
    pub x_client_secret: Option<SecretString>,
    pub x_token_encryption_key: Option<SecretString>,
    pub x_oauth_token_url: String,
}

impl Debug for AccountDeletionProcessConfig {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("AccountDeletionProcessConfig")
            .field("process", &self.process)
            .field("media_audio_root", &self.media_audio_root)
            .field("personal_markdown_root", &self.personal_markdown_root)
            .field("artifact_storage", &self.artifact_storage)
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
        let process = QueueWorkerProcessConfig::from_env_with(
            "newsly-account-deletion",
            "rust-delete-account",
            QueueWorkerProcessEnv {
                database_max_connections: "NEWSLY_DELETE_WORKER_DATABASE_MAX_CONNECTIONS",
                database_min_connections: "NEWSLY_DELETE_WORKER_DATABASE_MIN_CONNECTIONS",
                database_acquire_timeout_ms: "NEWSLY_DELETE_WORKER_DATABASE_ACQUIRE_TIMEOUT_MS",
                database_bounds: "NEWSLY_DELETE_WORKER_DATABASE connection bounds",
                default_max_connections: 6,
                worker_id: "NEWSLY_DELETE_WORKER_ID",
                lease_seconds: "NEWSLY_DELETE_WORKER_LEASE_SECONDS",
                default_log_filter: "newsly_account_deletion_worker=info,newsly_worker=info,newsly_queue=info",
                trim_database_url: true,
                empty_database_url_is_missing: false,
            },
        )?;

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
        let artifact_storage = ObjectStorageBackend::from_environment()?;

        Ok(Self {
            process,
            media_audio_root,
            personal_markdown_root,
            artifact_storage,
            x_client_id: optional_secret("X_CLIENT_ID")?,
            x_client_secret: optional_secret("X_CLIENT_SECRET")?,
            x_token_encryption_key: optional_secret("X_TOKEN_ENCRYPTION_KEY")?,
            x_oauth_token_url: env::var("X_OAUTH_TOKEN_URL")
                .unwrap_or_else(|_| DEFAULT_X_TOKEN_URL.to_owned()),
        })
    }
}

impl Deref for AccountDeletionProcessConfig {
    type Target = QueueWorkerProcessConfig;

    fn deref(&self) -> &Self::Target {
        &self.process
    }
}

impl From<QueueWorkerProcessConfigError> for ProcessConfigError {
    fn from(error: QueueWorkerProcessConfigError) -> Self {
        match error {
            QueueWorkerProcessConfigError::Missing(name) => Self::Missing(name),
            QueueWorkerProcessConfigError::Empty(name) => Self::Empty(name),
            QueueWorkerProcessConfigError::InvalidUnicode(name) => Self::InvalidUnicode(name),
            QueueWorkerProcessConfigError::InvalidNumber(name, value) => {
                Self::InvalidNumber(name, value)
            }
            QueueWorkerProcessConfigError::Invalid(name) => Self::InvalidValue {
                name,
                value: env::var(name).unwrap_or_default(),
                expected: "a supported value",
            },
            QueueWorkerProcessConfigError::Range(name) => Self::InvalidRange(name),
        }
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

fn optional_secret(name: &'static str) -> Result<Option<SecretString>, ProcessConfigError> {
    match env::var(name) {
        Ok(value) if value.trim().is_empty() => Err(ProcessConfigError::Empty(name)),
        Ok(value) => Ok(Some(SecretString::from(value))),
        Err(env::VarError::NotPresent) => Ok(None),
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
    #[error("object-storage configuration is invalid")]
    ObjectStorage(#[from] ObjectStorageConfigError),
}
