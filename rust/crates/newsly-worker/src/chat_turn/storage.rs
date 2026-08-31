use std::env;
use std::path::{Component, Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use object_store::aws::AmazonS3Builder;
use object_store::path::Path as ObjectPath;
use object_store::{ClientOptions, Error as ObjectStoreError, ObjectStore, ObjectStoreExt};
use secrecy::{ExposeSecret, SecretString};
use thiserror::Error;

const MAX_CHAT_CONTEXT_BYTES: usize = 2_000_000;

/// Read-only resolver for source-body pointers accepted during the prepare transaction.
///
/// The key is immutable input. Reads happen only after that transaction commits, including for
/// S3-compatible production storage, so slow object storage can never pin a `PostgreSQL` snapshot.
#[derive(Clone)]
pub(super) struct ChatBodyStore {
    backend: ChatBodyBackend,
}

impl std::fmt::Debug for ChatBodyStore {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("ChatBodyStore")
            .field("backend", &self.backend)
            .finish()
    }
}

#[derive(Clone)]
enum ChatBodyBackend {
    Local { root: PathBuf },
    S3 { store: Arc<dyn ObjectStore> },
}

impl std::fmt::Debug for ChatBodyBackend {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Local { root } => formatter
                .debug_struct("LocalChatBodyBackend")
                .field("root", root)
                .finish(),
            Self::S3 { .. } => formatter.debug_struct("S3ChatBodyBackend").finish(),
        }
    }
}

impl ChatBodyStore {
    pub(super) fn from_env() -> Result<Self, ChatBodyStoreError> {
        let provider = env::var("CONTENT_BODY_STORAGE_PROVIDER")
            .unwrap_or_else(|_| "local".to_owned())
            .trim()
            .to_owned();
        let backend = match provider.as_str() {
            "local" => {
                let configured = env::var_os("CONTENT_BODY_LOCAL_ROOT")
                    .map_or_else(|| PathBuf::from("data/content_bodies"), PathBuf::from);
                let root = if configured.is_absolute() {
                    configured
                } else {
                    env::current_dir()?.join(configured)
                };
                validate_root(&root)?;
                ChatBodyBackend::Local { root }
            }
            "s3_compatible" => {
                let bucket = required("CONTENT_BODY_STORAGE_BUCKET")?;
                let endpoint = optional("CONTENT_BODY_STORAGE_ENDPOINT");
                let region = optional("CONTENT_BODY_STORAGE_REGION");
                let access_key =
                    optional("CONTENT_BODY_STORAGE_ACCESS_KEY").map(SecretString::from);
                let secret_key =
                    optional("CONTENT_BODY_STORAGE_SECRET_KEY").map(SecretString::from);
                if access_key.is_some() != secret_key.is_some() {
                    return Err(ChatBodyStoreError::IncompleteCredentials);
                }
                let timeout = bounded_u64("CONTENT_BODY_STORAGE_TIMEOUT_SECONDS", 30, 1, 300)?;
                let mut builder = AmazonS3Builder::from_env()
                    .with_bucket_name(bucket)
                    .with_disable_bulk_delete(true)
                    .with_client_options(
                        ClientOptions::new().with_timeout(Duration::from_secs(timeout)),
                    );
                if let Some(endpoint) = endpoint {
                    let url = reqwest::Url::parse(&endpoint)
                        .map_err(|_| ChatBodyStoreError::InvalidEndpoint)?;
                    builder = builder
                        .with_allow_http(url.scheme() == "http")
                        .with_endpoint(url.to_string());
                }
                if let Some(region) = region {
                    builder = builder.with_region(region);
                }
                if let (Some(access_key), Some(secret_key)) = (access_key, secret_key) {
                    builder = builder
                        .with_access_key_id(access_key.expose_secret())
                        .with_secret_access_key(secret_key.expose_secret());
                }
                let store = builder
                    .build()
                    .map_err(|error| ChatBodyStoreError::ObjectStore(error.to_string()))?;
                ChatBodyBackend::S3 {
                    store: Arc::new(store),
                }
            }
            _ => return Err(ChatBodyStoreError::UnsupportedProvider(provider)),
        };
        Ok(Self { backend })
    }

    pub(super) async fn get_text(
        &self,
        storage_key: &str,
    ) -> Result<Option<String>, ChatBodyStoreError> {
        validate_key(storage_key)?;
        let bytes = match &self.backend {
            ChatBodyBackend::Local { root } => {
                let path = safe_local_path(root, storage_key)?;
                match tokio::fs::read(path).await {
                    Ok(bytes) => Some(bytes),
                    Err(error) if error.kind() == std::io::ErrorKind::NotFound => None,
                    Err(error) => return Err(ChatBodyStoreError::Io(error)),
                }
            }
            ChatBodyBackend::S3 { store } => {
                let path = ObjectPath::parse(storage_key)
                    .map_err(|error| ChatBodyStoreError::UnsafeKey(error.to_string()))?;
                let result = match store.get(&path).await {
                    Ok(result) => result,
                    Err(ObjectStoreError::NotFound { .. }) => return Ok(None),
                    Err(error) => {
                        return Err(ChatBodyStoreError::ObjectStore(error.to_string()));
                    }
                };
                Some(
                    result
                        .bytes()
                        .await
                        .map_err(|error| ChatBodyStoreError::ObjectStore(error.to_string()))?
                        .to_vec(),
                )
            }
        };
        let Some(bytes) = bytes else { return Ok(None) };
        if bytes.len() > MAX_CHAT_CONTEXT_BYTES {
            return Err(ChatBodyStoreError::TooLarge(bytes.len()));
        }
        String::from_utf8(bytes)
            .map(Some)
            .map_err(ChatBodyStoreError::Utf8)
    }
}

fn safe_local_path(root: &Path, key: &str) -> Result<PathBuf, ChatBodyStoreError> {
    let relative = Path::new(key);
    if relative.is_absolute()
        || relative
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return Err(ChatBodyStoreError::UnsafeKey(key.to_owned()));
    }
    Ok(root.join(relative))
}

fn validate_key(key: &str) -> Result<(), ChatBodyStoreError> {
    if key.trim().is_empty() || key.len() > 1_024 || key.contains('\0') {
        return Err(ChatBodyStoreError::UnsafeKey(key.to_owned()));
    }
    let _ = safe_local_path(Path::new("/body-root"), key)?;
    Ok(())
}

fn validate_root(root: &Path) -> Result<(), ChatBodyStoreError> {
    if !root.is_absolute() || root == Path::new("/") {
        return Err(ChatBodyStoreError::UnsafeRoot);
    }
    Ok(())
}

fn required(name: &'static str) -> Result<String, ChatBodyStoreError> {
    optional(name).ok_or(ChatBodyStoreError::Missing(name))
}

fn optional(name: &'static str) -> Option<String> {
    env::var(name)
        .ok()
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
}

fn bounded_u64(
    name: &'static str,
    default: u64,
    minimum: u64,
    maximum: u64,
) -> Result<u64, ChatBodyStoreError> {
    let value = optional(name)
        .map_or(Ok(default), |value| value.parse::<u64>())
        .map_err(|_| ChatBodyStoreError::Invalid(name))?;
    if !(minimum..=maximum).contains(&value) {
        return Err(ChatBodyStoreError::Invalid(name));
    }
    Ok(value)
}

#[derive(Debug, Error)]
pub(super) enum ChatBodyStoreError {
    #[error("unsupported chat body storage provider {0}")]
    UnsupportedProvider(String),
    #[error("chat body storage configuration {0} is required")]
    Missing(&'static str),
    #[error("chat body storage configuration {0} is invalid")]
    Invalid(&'static str),
    #[error("chat body storage credentials are incomplete")]
    IncompleteCredentials,
    #[error("chat body storage endpoint is invalid")]
    InvalidEndpoint,
    #[error("chat body storage root must be an absolute non-root path")]
    UnsafeRoot,
    #[error("chat body storage key is unsafe: {0}")]
    UnsafeKey(String),
    #[error("chat body contains {0} bytes, exceeding the worker limit")]
    TooLarge(usize),
    #[error("chat body is not valid UTF-8")]
    Utf8(#[source] std::string::FromUtf8Error),
    #[error("chat body filesystem operation failed")]
    Io(#[from] std::io::Error),
    #[error("chat body object storage operation failed: {0}")]
    ObjectStore(String),
}
