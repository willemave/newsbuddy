use std::env;
use std::path::{Component, Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use object_store::aws::AmazonS3Builder;
use object_store::path::Path as ObjectPath;
use object_store::{ClientOptions, Error as ObjectStoreError, ObjectStore, ObjectStoreExt};
use secrecy::{ExposeSecret, SecretString};
use thiserror::Error;
use tokio::io::AsyncReadExt;

const MAX_CHAT_CONTEXT_BYTES: usize = 2_000_000;

#[derive(Debug)]
pub(crate) struct ContentBodySlice {
    pub(crate) bytes: Vec<u8>,
    pub(crate) truncated: bool,
}

/// Read-only resolver for source-body pointers accepted during the prepare transaction.
///
/// The key is immutable input. Reads happen only after that transaction commits, including for
/// S3-compatible production storage, so slow object storage can never pin a `PostgreSQL` snapshot.
#[derive(Clone)]
pub(crate) struct ContentBodyStore {
    backend: ContentBodyBackend,
}

impl std::fmt::Debug for ContentBodyStore {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("ContentBodyStore")
            .field("backend", &self.backend)
            .finish()
    }
}

#[derive(Clone)]
enum ContentBodyBackend {
    Local { root: PathBuf },
    S3 { store: Arc<dyn ObjectStore> },
}

impl std::fmt::Debug for ContentBodyBackend {
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

impl ContentBodyStore {
    pub(crate) fn from_env() -> Result<Self, ContentBodyStoreError> {
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
                ContentBodyBackend::Local { root }
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
                    return Err(ContentBodyStoreError::IncompleteCredentials);
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
                        .map_err(|_| ContentBodyStoreError::InvalidEndpoint)?;
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
                    .map_err(|error| ContentBodyStoreError::ObjectStore(error.to_string()))?;
                ContentBodyBackend::S3 {
                    store: Arc::new(store),
                }
            }
            _ => return Err(ContentBodyStoreError::UnsupportedProvider(provider)),
        };
        Ok(Self { backend })
    }

    pub(crate) async fn get_text(
        &self,
        storage_key: &str,
    ) -> Result<Option<String>, ContentBodyStoreError> {
        self.get_bytes(storage_key)
            .await?
            .map(String::from_utf8)
            .transpose()
            .map_err(ContentBodyStoreError::Utf8)
    }

    pub(crate) async fn get_bytes(
        &self,
        storage_key: &str,
    ) -> Result<Option<Vec<u8>>, ContentBodyStoreError> {
        let Some(slice) = self
            .get_bytes_up_to(storage_key, MAX_CHAT_CONTEXT_BYTES)
            .await?
        else {
            return Ok(None);
        };
        if slice.truncated {
            return Err(ContentBodyStoreError::TooLarge(
                MAX_CHAT_CONTEXT_BYTES.saturating_add(1),
            ));
        }
        Ok(Some(slice.bytes))
    }

    pub(crate) async fn get_bytes_up_to(
        &self,
        storage_key: &str,
        maximum: usize,
    ) -> Result<Option<ContentBodySlice>, ContentBodyStoreError> {
        validate_key(storage_key)?;
        if maximum == 0 || maximum > MAX_CHAT_CONTEXT_BYTES {
            return Err(ContentBodyStoreError::InvalidReadLimit(maximum));
        }
        match &self.backend {
            ContentBodyBackend::Local { root } => {
                let path = safe_local_path(root, storage_key)?;
                let file = match tokio::fs::File::open(path).await {
                    Ok(file) => file,
                    Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
                    Err(error) => return Err(ContentBodyStoreError::Io(error)),
                };
                let length = usize::try_from(file.metadata().await?.len()).unwrap_or(usize::MAX);
                let mut bytes = Vec::with_capacity(length.min(maximum));
                file.take(u64::try_from(maximum).unwrap_or(u64::MAX))
                    .read_to_end(&mut bytes)
                    .await?;
                Ok(Some(ContentBodySlice {
                    bytes,
                    truncated: length > maximum,
                }))
            }
            ContentBodyBackend::S3 { store } => {
                let path = ObjectPath::parse(storage_key)
                    .map_err(|error| ContentBodyStoreError::UnsafeKey(error.to_string()))?;
                let metadata = match store.head(&path).await {
                    Ok(metadata) => metadata,
                    Err(ObjectStoreError::NotFound { .. }) => return Ok(None),
                    Err(error) => {
                        return Err(ContentBodyStoreError::ObjectStore(error.to_string()));
                    }
                };
                let maximum = u64::try_from(maximum).unwrap_or(u64::MAX);
                let end = metadata.size.min(maximum);
                let bytes = if end == 0 {
                    Vec::new()
                } else {
                    store
                        .get_range(&path, 0..end)
                        .await
                        .map_err(|error| ContentBodyStoreError::ObjectStore(error.to_string()))?
                        .to_vec()
                };
                Ok(Some(ContentBodySlice {
                    bytes,
                    truncated: metadata.size > maximum,
                }))
            }
        }
    }
}

fn safe_local_path(root: &Path, key: &str) -> Result<PathBuf, ContentBodyStoreError> {
    let relative = Path::new(key);
    if relative.is_absolute()
        || relative
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return Err(ContentBodyStoreError::UnsafeKey(key.to_owned()));
    }
    Ok(root.join(relative))
}

fn validate_key(key: &str) -> Result<(), ContentBodyStoreError> {
    if key.trim().is_empty() || key.len() > 1_024 || key.contains('\0') {
        return Err(ContentBodyStoreError::UnsafeKey(key.to_owned()));
    }
    let _ = safe_local_path(Path::new("/body-root"), key)?;
    Ok(())
}

fn validate_root(root: &Path) -> Result<(), ContentBodyStoreError> {
    if !root.is_absolute() || root == Path::new("/") {
        return Err(ContentBodyStoreError::UnsafeRoot);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::path::Path;

    use super::{ContentBodyBackend, ContentBodyStore, safe_local_path, validate_key};

    #[test]
    fn canonical_body_keys_cannot_escape_storage() {
        assert!(validate_key("content/42/source.txt").is_ok());
        assert!(validate_key("../secret").is_err());
        assert!(validate_key("/absolute").is_err());
        assert!(safe_local_path(Path::new("/body-root"), "a/../b").is_err());
    }

    #[tokio::test]
    async fn local_body_reads_only_the_requested_prefix() {
        let directory = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(directory.path().join("content/42")).unwrap();
        std::fs::write(
            directory.path().join("content/42/source.txt"),
            b"bounded body",
        )
        .unwrap();
        let store = ContentBodyStore {
            backend: ContentBodyBackend::Local {
                root: directory.path().to_path_buf(),
            },
        };

        let slice = store
            .get_bytes_up_to("content/42/source.txt", 7)
            .await
            .unwrap()
            .unwrap();
        assert_eq!(slice.bytes, b"bounded");
        assert!(slice.truncated);
        assert!(
            store
                .get_bytes_up_to("content/42/missing.txt", 7)
                .await
                .unwrap()
                .is_none()
        );
    }
}

fn required(name: &'static str) -> Result<String, ContentBodyStoreError> {
    optional(name).ok_or(ContentBodyStoreError::Missing(name))
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
) -> Result<u64, ContentBodyStoreError> {
    let value = optional(name)
        .map_or(Ok(default), |value| value.parse::<u64>())
        .map_err(|_| ContentBodyStoreError::Invalid(name))?;
    if !(minimum..=maximum).contains(&value) {
        return Err(ContentBodyStoreError::Invalid(name));
    }
    Ok(value)
}

#[derive(Debug, Error)]
pub enum ContentBodyStoreError {
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
    #[error("content body read limit {0} is invalid")]
    InvalidReadLimit(usize),
    #[error("chat body is not valid UTF-8")]
    Utf8(#[source] std::string::FromUtf8Error),
    #[error("chat body filesystem operation failed")]
    Io(#[from] std::io::Error),
    #[error("chat body object storage operation failed: {0}")]
    ObjectStore(String),
}
