//! Validation and unified local / S3-compatible operations for content-body and artifact storage.

use futures_util::StreamExt as _;
use object_store::aws::AmazonS3Builder;
use object_store::path::Path as ObjectPath;
use object_store::{
    Attribute, AttributeValue, Attributes, ClientOptions, Error as ObjectStoreError, ObjectStore,
    ObjectStoreExt as _, PutOptions,
};
use reqwest::Url;
use secrecy::{ExposeSecret, SecretString};
use std::path::{Component, Path, PathBuf};
use std::{env, sync::Arc, time::Duration};
use tokio::io::AsyncWriteExt as _;
use uuid::Uuid;

/// Whether a configured absolute storage root excludes parent traversal and filesystem roots.
#[must_use]
pub fn is_safe_storage_root(root: &Path) -> bool {
    root.is_absolute()
        && root != Path::new("/")
        && !root.components().any(|component| {
            matches!(
                component,
                Component::ParentDir | Component::CurDir | Component::Prefix(_)
            )
        })
}

/// Whether an immutable object key is a bounded relative path with unambiguous segments.
#[must_use]
pub fn is_safe_object_key(key: &str) -> bool {
    !key.trim().is_empty()
        && key.len() <= 2_048
        && !key.contains(['\0', '\\'])
        && !Path::new(key).is_absolute()
        && key
            .split('/')
            .all(|segment| !matches!(segment, "" | "." | ".."))
        && Path::new(key)
            .components()
            .all(|component| matches!(component, Component::Normal(_)))
}

/// Convert a validated relative `Path` into a canonical `/`-separated object key.
#[must_use]
pub fn path_to_object_key(path: &Path) -> Option<String> {
    if !path.to_str().is_some_and(is_safe_object_key) {
        return None;
    }
    path.components()
        .map(|component| match component {
            Component::Normal(part) => part.to_str().map(str::to_owned),
            _ => None,
        })
        .collect::<Option<Vec<_>>>()
        .map(|parts| parts.join("/"))
}

/// Remove an object entry after verifying its parent stays within the canonical root.
/// A symlink entry is unlinked without deleting its target; missing entries are successful.
///
/// # Errors
/// Returns an error for unsafe paths or filesystem failures.
pub async fn delete_local_object(root: &Path, key: &str) -> Result<(), LocalStorageError> {
    let path = root.join(key);
    if !is_safe_object_key(key) {
        return Err(LocalStorageError::UnsafePath(path));
    }
    match tokio::fs::symlink_metadata(&path).await {
        Ok(_) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(()),
        Err(source) => return Err(LocalStorageError::File { path, source }),
    }
    let canonical_root =
        tokio::fs::canonicalize(root)
            .await
            .map_err(|source| LocalStorageError::File {
                path: root.to_path_buf(),
                source,
            })?;
    let parent = path
        .parent()
        .ok_or_else(|| LocalStorageError::UnsafePath(path.clone()))?;
    let canonical_parent =
        tokio::fs::canonicalize(parent)
            .await
            .map_err(|source| LocalStorageError::File {
                path: parent.to_path_buf(),
                source,
            })?;
    if !canonical_parent.starts_with(&canonical_root) {
        return Err(LocalStorageError::UnsafePath(path));
    }
    match tokio::fs::remove_file(&path).await {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(source) => Err(LocalStorageError::File { path, source }),
    }
}

#[derive(Debug, thiserror::Error)]
pub enum LocalStorageError {
    #[error("unsafe local object path {0:?}")]
    UnsafePath(std::path::PathBuf),
    #[error("local object operation failed for {path:?}")]
    File {
        path: std::path::PathBuf,
        #[source]
        source: std::io::Error,
    },
}

/// Unified error for local and S3-compatible object operations.
#[derive(Debug, thiserror::Error)]
pub enum ObjectStorageOpError {
    #[error("unsafe object key {0:?}")]
    UnsafeKey(String),
    #[error("unsafe local object path {0:?}")]
    UnsafeLocalPath(PathBuf),
    #[error("existing local object at {0:?} is unsafe or has different bytes")]
    UnsafeExistingObject(PathBuf),
    #[error("object {key:?} exceeds the {maximum_bytes}-byte read limit")]
    TooLarge { key: String, maximum_bytes: usize },
    #[error("local object operation failed for {path:?}")]
    File {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
    #[error("object-store operation failed: {0}")]
    ObjectStore(String),
}

impl From<LocalStorageError> for ObjectStorageOpError {
    fn from(error: LocalStorageError) -> Self {
        match error {
            LocalStorageError::UnsafePath(path) => Self::UnsafeLocalPath(path),
            LocalStorageError::File { path, source } => Self::File { path, source },
        }
    }
}

/// Storage backend built from validated process configuration; consumers retain product limits.
#[derive(Clone)]
pub enum ObjectStorageBackend {
    Local {
        root: PathBuf,
    },
    S3 {
        store: Arc<dyn ObjectStore>,
        bucket: String,
    },
}

impl std::fmt::Debug for ObjectStorageBackend {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Local { root } => formatter
                .debug_struct("LocalObjectStorage")
                .field("root", root)
                .finish(),
            Self::S3 { bucket, .. } => formatter
                .debug_struct("S3ObjectStorage")
                .field("bucket", bucket)
                .finish_non_exhaustive(),
        }
    }
}

impl ObjectStorageBackend {
    /// Build a validated local storage backend, resolving relative paths against the current
    /// working directory once.
    ///
    /// # Errors
    /// Returns an error if the current directory cannot be resolved or the root is unsafe.
    pub fn local(root: PathBuf) -> Result<Self, ObjectStorageConfigError> {
        let root = if root.is_absolute() {
            root
        } else {
            env::current_dir()?.join(root)
        };
        if !is_safe_storage_root(&root) {
            return Err(ObjectStorageConfigError::UnsafeRoot(root));
        }
        Ok(Self::Local { root })
    }

    /// Build an S3-compatible backend from an explicit `ObjectStore` (useful in tests).
    #[must_use]
    pub fn s3(store: Arc<dyn ObjectStore>, bucket: impl Into<String>) -> Self {
        Self::S3 {
            store,
            bucket: bucket.into(),
        }
    }

    /// Canonical `storage_provider` value written to database pointers.
    #[must_use]
    pub const fn provider_name(&self) -> &'static str {
        match self {
            Self::Local { .. } => "local",
            Self::S3 { .. } => "s3_compatible",
        }
    }

    /// Configured bucket name for S3-compatible storage, or `None` for local storage.
    #[must_use]
    pub fn bucket(&self) -> Option<&str> {
        match self {
            Self::Local { .. } => None,
            Self::S3 { bucket, .. } => Some(bucket.as_str()),
        }
    }

    /// Build a local or S3-compatible backend from `CONTENT_BODY_STORAGE` settings.
    ///
    /// # Errors
    /// Returns an error for unsafe roots or invalid/incomplete remote configuration.
    pub fn from_environment() -> Result<Self, ObjectStorageConfigError> {
        let provider =
            env::var("CONTENT_BODY_STORAGE_PROVIDER").unwrap_or_else(|_| "local".to_owned());
        match provider.trim() {
            "local" => {
                let configured = env::var_os("CONTENT_BODY_LOCAL_ROOT")
                    .map_or_else(|| PathBuf::from("data/content_bodies"), PathBuf::from);
                Self::local(configured)
            }
            "s3_compatible" => {
                let bucket = required("CONTENT_BODY_STORAGE_BUCKET")?;
                let endpoint = optional_url("CONTENT_BODY_STORAGE_ENDPOINT")?;
                let region = optional("CONTENT_BODY_STORAGE_REGION");
                let access_key =
                    optional("CONTENT_BODY_STORAGE_ACCESS_KEY").map(SecretString::from);
                let secret_key =
                    optional("CONTENT_BODY_STORAGE_SECRET_KEY").map(SecretString::from);
                if access_key.is_some() != secret_key.is_some() {
                    return Err(ObjectStorageConfigError::IncompleteCredentials);
                }
                let timeout_seconds = env::var("CONTENT_BODY_STORAGE_TIMEOUT_SECONDS")
                    .ok()
                    .map(|value| {
                        value
                            .parse::<u64>()
                            .map_err(|_| ObjectStorageConfigError::InvalidTimeout(value.clone()))
                    })
                    .transpose()?
                    .unwrap_or(30);
                if !(1..=300).contains(&timeout_seconds) {
                    return Err(ObjectStorageConfigError::InvalidTimeout(
                        timeout_seconds.to_string(),
                    ));
                }
                let mut builder = AmazonS3Builder::from_env()
                    .with_bucket_name(bucket.clone())
                    .with_disable_bulk_delete(true)
                    .with_client_options(
                        ClientOptions::new().with_timeout(Duration::from_secs(timeout_seconds)),
                    );
                if let Some(endpoint) = endpoint {
                    builder = builder
                        .with_allow_http(endpoint.scheme() == "http")
                        .with_endpoint(endpoint.to_string());
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
                    .map_err(|error| ObjectStorageConfigError::ObjectStore(error.to_string()))?;
                Ok(Self::S3 {
                    store: Arc::new(store),
                    bucket,
                })
            }
            _ => Err(ObjectStorageConfigError::UnsupportedProvider(provider)),
        }
    }

    /// Read at most `maximum_bytes` of an object, indicating whether the stored object was larger.
    ///
    /// # Errors
    /// Returns an error for unsafe keys/paths or backend I/O failures.
    pub async fn read_prefix(
        &self,
        key: &str,
        maximum_bytes: usize,
    ) -> Result<Option<LocalObjectSlice>, ObjectStorageOpError> {
        validate_key(key)?;
        match self {
            Self::Local { root } => Ok(read_local_object(root, key, maximum_bytes).await?),
            Self::S3 { store, .. } => {
                let path = parse_object_path(key)?;
                let metadata = match store.head(&path).await {
                    Ok(metadata) => metadata,
                    Err(ObjectStoreError::NotFound { .. }) => return Ok(None),
                    Err(error) => return Err(ObjectStorageOpError::ObjectStore(error.to_string())),
                };
                let maximum_u64 = u64::try_from(maximum_bytes).unwrap_or(u64::MAX);
                let end = metadata.size.min(maximum_u64);
                let bytes = if end == 0 {
                    Vec::new()
                } else {
                    match store.get_range(&path, 0..end).await {
                        Ok(bytes) => bytes.to_vec(),
                        Err(ObjectStoreError::NotFound { .. }) => return Ok(None),
                        Err(error) => {
                            return Err(ObjectStorageOpError::ObjectStore(error.to_string()));
                        }
                    }
                };
                Ok(Some(LocalObjectSlice {
                    bytes,
                    truncated: metadata.size > maximum_u64,
                }))
            }
        }
    }

    /// Read a complete object, rejecting any object larger than `maximum_bytes`.
    ///
    /// # Errors
    /// Returns `ObjectStorageOpError::TooLarge` if the object exceeds `maximum_bytes`, or an error
    /// for unsafe keys/paths or backend failures.
    pub async fn read_bounded(
        &self,
        key: &str,
        maximum_bytes: usize,
    ) -> Result<Option<Vec<u8>>, ObjectStorageOpError> {
        validate_key(key)?;
        match self {
            Self::Local { root } => {
                let Some(slice) = read_local_object(root, key, maximum_bytes).await? else {
                    return Ok(None);
                };
                if slice.truncated {
                    return Err(ObjectStorageOpError::TooLarge {
                        key: key.to_owned(),
                        maximum_bytes,
                    });
                }
                Ok(Some(slice.bytes))
            }
            Self::S3 { store, .. } => {
                let path = parse_object_path(key)?;
                let result = match store.get(&path).await {
                    Ok(result) => result,
                    Err(ObjectStoreError::NotFound { .. }) => return Ok(None),
                    Err(error) => return Err(ObjectStorageOpError::ObjectStore(error.to_string())),
                };
                if result.meta.size > maximum_bytes as u64 {
                    return Err(ObjectStorageOpError::TooLarge {
                        key: key.to_owned(),
                        maximum_bytes,
                    });
                }
                let initial_capacity = usize::try_from(result.meta.size)
                    .unwrap_or(maximum_bytes)
                    .min(maximum_bytes);
                let mut bytes = Vec::with_capacity(initial_capacity);
                let mut stream = result.into_stream();
                while let Some(chunk) = stream.next().await {
                    let chunk = chunk
                        .map_err(|error| ObjectStorageOpError::ObjectStore(error.to_string()))?;
                    if bytes.len().saturating_add(chunk.len()) > maximum_bytes {
                        return Err(ObjectStorageOpError::TooLarge {
                            key: key.to_owned(),
                            maximum_bytes,
                        });
                    }
                    bytes.extend_from_slice(&chunk);
                }
                Ok(Some(bytes))
            }
        }
    }

    /// Check whether an object exists without reading its payload.
    ///
    /// # Errors
    /// Returns an error for unsafe keys/paths or backend failures.
    pub async fn exists(&self, key: &str) -> Result<bool, ObjectStorageOpError> {
        validate_key(key)?;
        match self {
            Self::Local { root } => Ok(read_local_object(root, key, 0).await?.is_some()),
            Self::S3 { store, .. } => {
                let path = parse_object_path(key)?;
                match store.head(&path).await {
                    Ok(_) => Ok(true),
                    Err(ObjectStoreError::NotFound { .. }) => Ok(false),
                    Err(error) => Err(ObjectStorageOpError::ObjectStore(error.to_string())),
                }
            }
        }
    }

    /// Publish immutable bytes under `key`.
    ///
    /// On local storage, the temporary file is created beside the destination and hard-linked into
    /// place so concurrent writers never clobber a winner; an existing destination is accepted
    /// only when it is a regular non-symlink file with identical bytes.
    ///
    /// # Errors
    /// Returns an error for unsafe keys/paths, mismatched existing local objects, or backend I/O
    /// failures.
    pub async fn put_immutable(
        &self,
        key: &str,
        bytes: &[u8],
        content_type: Option<&str>,
    ) -> Result<(), ObjectStorageOpError> {
        validate_key(key)?;
        match self {
            Self::Local { root } => publish_local_immutable(root, key, bytes).await,
            Self::S3 { store, .. } => {
                let path = parse_object_path(key)?;
                let mut options = PutOptions::default();
                if let Some(content_type) = content_type {
                    options.attributes = Attributes::from_iter([(
                        Attribute::ContentType,
                        AttributeValue::from(content_type.to_owned()),
                    )]);
                }
                store
                    .put_opts(&path, bytes.to_vec().into(), options)
                    .await
                    .map(|_| ())
                    .map_err(|error| ObjectStorageOpError::ObjectStore(error.to_string()))
            }
        }
    }

    /// Delete an object; missing objects are idempotent success.
    ///
    /// # Errors
    /// Returns an error for unsafe keys/paths or backend failures.
    pub async fn delete(&self, key: &str) -> Result<(), ObjectStorageOpError> {
        validate_key(key)?;
        match self {
            Self::Local { root } => Ok(delete_local_object(root, key).await?),
            Self::S3 { store, .. } => {
                let path = parse_object_path(key)?;
                match store.delete(&path).await {
                    Ok(()) | Err(ObjectStoreError::NotFound { .. }) => Ok(()),
                    Err(error) => Err(ObjectStorageOpError::ObjectStore(error.to_string())),
                }
            }
        }
    }
}

fn validate_key(key: &str) -> Result<(), ObjectStorageOpError> {
    if !is_safe_object_key(key) {
        return Err(ObjectStorageOpError::UnsafeKey(key.to_owned()));
    }
    Ok(())
}

fn parse_object_path(key: &str) -> Result<ObjectPath, ObjectStorageOpError> {
    ObjectPath::parse(key).map_err(|error| ObjectStorageOpError::UnsafeKey(error.to_string()))
}

async fn publish_local_immutable(
    root: &Path,
    key: &str,
    bytes: &[u8],
) -> Result<(), ObjectStorageOpError> {
    let destination = root.join(key);
    let parent = destination
        .parent()
        .ok_or_else(|| ObjectStorageOpError::UnsafeLocalPath(destination.clone()))?;
    tokio::fs::create_dir_all(root)
        .await
        .map_err(|source| ObjectStorageOpError::File {
            path: root.to_path_buf(),
            source,
        })?;
    let canonical_root =
        tokio::fs::canonicalize(root)
            .await
            .map_err(|source| ObjectStorageOpError::File {
                path: root.to_path_buf(),
                source,
            })?;
    tokio::fs::create_dir_all(parent)
        .await
        .map_err(|source| ObjectStorageOpError::File {
            path: parent.to_path_buf(),
            source,
        })?;
    let canonical_parent =
        tokio::fs::canonicalize(parent)
            .await
            .map_err(|source| ObjectStorageOpError::File {
                path: parent.to_path_buf(),
                source,
            })?;
    if !canonical_parent.starts_with(&canonical_root) {
        return Err(ObjectStorageOpError::UnsafeLocalPath(destination));
    }

    let temporary = canonical_parent.join(format!(".{}.tmp", Uuid::new_v4()));
    if let Err(error) = write_new_file(&temporary, bytes).await {
        let _ = tokio::fs::remove_file(&temporary).await;
        return Err(error);
    }
    match tokio::fs::hard_link(&temporary, &destination).await {
        Ok(()) => remove_temporary(&temporary).await,
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
            remove_temporary(&temporary).await?;
            validate_existing_local(&destination, bytes).await
        }
        Err(source) => {
            let _ = tokio::fs::remove_file(&temporary).await;
            Err(ObjectStorageOpError::File {
                path: destination,
                source,
            })
        }
    }
}

async fn write_new_file(path: &Path, bytes: &[u8]) -> Result<(), ObjectStorageOpError> {
    let mut file = tokio::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .await
        .map_err(|source| ObjectStorageOpError::File {
            path: path.to_path_buf(),
            source,
        })?;
    file.write_all(bytes)
        .await
        .map_err(|source| ObjectStorageOpError::File {
            path: path.to_path_buf(),
            source,
        })?;
    file.flush()
        .await
        .map_err(|source| ObjectStorageOpError::File {
            path: path.to_path_buf(),
            source,
        })
}

async fn remove_temporary(path: &Path) -> Result<(), ObjectStorageOpError> {
    tokio::fs::remove_file(path)
        .await
        .map_err(|source| ObjectStorageOpError::File {
            path: path.to_path_buf(),
            source,
        })
}

async fn validate_existing_local(path: &Path, expected: &[u8]) -> Result<(), ObjectStorageOpError> {
    let metadata =
        tokio::fs::symlink_metadata(path)
            .await
            .map_err(|source| ObjectStorageOpError::File {
                path: path.to_path_buf(),
                source,
            })?;
    if metadata.file_type().is_symlink()
        || !metadata.is_file()
        || metadata.len() != expected.len() as u64
    {
        return Err(ObjectStorageOpError::UnsafeExistingObject(
            path.to_path_buf(),
        ));
    }
    let actual = tokio::fs::read(path)
        .await
        .map_err(|source| ObjectStorageOpError::File {
            path: path.to_path_buf(),
            source,
        })?;
    if actual == expected {
        Ok(())
    } else {
        Err(ObjectStorageOpError::UnsafeExistingObject(
            path.to_path_buf(),
        ))
    }
}

fn required(name: &'static str) -> Result<String, ObjectStorageConfigError> {
    optional(name).ok_or(ObjectStorageConfigError::Missing(name))
}

fn optional(name: &'static str) -> Option<String> {
    env::var(name)
        .ok()
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
}

fn optional_url(name: &'static str) -> Result<Option<Url>, ObjectStorageConfigError> {
    optional(name)
        .map(|value| {
            let url = value
                .parse::<Url>()
                .map_err(|_| ObjectStorageConfigError::InvalidUrl(name, value.clone()))?;
            if !matches!(url.scheme(), "http" | "https") || url.cannot_be_a_base() {
                return Err(ObjectStorageConfigError::InvalidUrl(name, value));
            }
            Ok(url)
        })
        .transpose()
}

#[derive(Debug, thiserror::Error)]
pub enum ObjectStorageConfigError {
    #[error("missing required object-storage setting {0}")]
    Missing(&'static str),
    #[error("unsupported CONTENT_BODY_STORAGE_PROVIDER {0:?}")]
    UnsupportedProvider(String),
    #[error(
        "CONTENT_BODY_STORAGE_ACCESS_KEY and CONTENT_BODY_STORAGE_SECRET_KEY must be set together"
    )]
    IncompleteCredentials,
    #[error("invalid object-storage timeout {0:?}")]
    InvalidTimeout(String),
    #[error("invalid URL for {0}: {1:?}")]
    InvalidUrl(&'static str, String),
    #[error("unsafe local object root {0:?}")]
    UnsafeRoot(PathBuf),
    #[error("object-storage client construction failed: {0}")]
    ObjectStore(String),
    #[error("could not resolve the current directory")]
    CurrentDirectory(#[from] std::io::Error),
}

/// Source text reads use the same 20 MiB budget as public document acquisition.
pub const MAX_SOURCE_BODY_BYTES: usize = 20 * 1024 * 1024;

#[derive(Debug)]
pub struct LocalObjectSlice {
    pub bytes: Vec<u8>,
    pub truncated: bool,
}

/// Read a regular non-symlink object file within a canonical root, retaining at most the requested
/// bytes.
///
/// # Errors
/// Returns an error for escaping paths, symlinks, non-files or filesystem failures.
pub async fn read_local_object(
    root: &Path,
    key: &str,
    maximum_bytes: usize,
) -> Result<Option<LocalObjectSlice>, LocalStorageError> {
    use tokio::io::AsyncReadExt as _;
    let requested = root.join(key);
    if !is_safe_object_key(key) {
        return Err(LocalStorageError::UnsafePath(requested));
    }
    let link_meta = match tokio::fs::symlink_metadata(&requested).await {
        Ok(meta) => meta,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(source) => {
            return Err(LocalStorageError::File {
                path: requested,
                source,
            });
        }
    };
    if link_meta.file_type().is_symlink() || !link_meta.is_file() {
        return Err(LocalStorageError::UnsafePath(requested));
    }
    let canonical_root = match tokio::fs::canonicalize(root).await {
        Ok(path) => path,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(source) => {
            return Err(LocalStorageError::File {
                path: root.to_path_buf(),
                source,
            });
        }
    };
    let path = match tokio::fs::canonicalize(&requested).await {
        Ok(path) => path,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(source) => {
            return Err(LocalStorageError::File {
                path: requested,
                source,
            });
        }
    };
    if !path.starts_with(&canonical_root) {
        return Err(LocalStorageError::UnsafePath(path));
    }
    let file = match tokio::fs::File::open(&path).await {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(source) => return Err(LocalStorageError::File { path, source }),
    };
    let metadata = file
        .metadata()
        .await
        .map_err(|source| LocalStorageError::File {
            path: path.clone(),
            source,
        })?;
    if !metadata.is_file() {
        return Err(LocalStorageError::UnsafePath(path));
    }
    let length = usize::try_from(metadata.len()).unwrap_or(usize::MAX);
    let mut bytes = Vec::with_capacity(length.min(maximum_bytes));
    file.take(u64::try_from(maximum_bytes.saturating_add(1)).unwrap_or(u64::MAX))
        .read_to_end(&mut bytes)
        .await
        .map_err(|source| LocalStorageError::File { path, source })?;
    let truncated = length > maximum_bytes || bytes.len() > maximum_bytes;
    bytes.truncate(maximum_bytes);
    Ok(Some(LocalObjectSlice { bytes, truncated }))
}

#[cfg(test)]
mod tests {
    use std::path::Path;
    use std::sync::Arc;

    use object_store::memory::InMemory;

    use super::{
        ObjectStorageBackend, ObjectStorageOpError, is_safe_object_key, is_safe_storage_root,
    };

    #[test]
    fn ambiguous_and_escaping_keys_are_rejected() {
        assert!(is_safe_object_key("learning/deck/index.html"));
        assert!(is_safe_object_key(&"a".repeat(2_048)));
        for key in [
            "",
            " ",
            "/absolute",
            "../secret",
            "a/../b",
            "a/./b",
            "a//b",
            "a/",
            "a\\b",
            "a\0b",
        ] {
            assert!(!is_safe_object_key(key), "{key:?}");
        }
        assert!(!is_safe_object_key(&"a".repeat(2_049)));
    }

    #[test]
    fn roots_must_be_absolute_and_cannot_escape() {
        assert!(is_safe_storage_root(Path::new("/body-root")));
        for root in ["/", "relative", "/body/../secret"] {
            assert!(!is_safe_storage_root(Path::new(root)));
        }
    }

    #[tokio::test]
    async fn local_backend_immutable_publish_and_bounded_reads() {
        let temporary = tempfile::tempdir().unwrap();
        let backend = ObjectStorageBackend::local(temporary.path().join("objects")).unwrap();
        assert_eq!(backend.provider_name(), "local");
        assert_eq!(backend.bucket(), None);

        backend
            .put_immutable("content/42/source.txt", b"expected", Some("text/plain"))
            .await
            .unwrap();
        // Idempotent re-publish with identical bytes succeeds.
        backend
            .put_immutable("content/42/source.txt", b"expected", None)
            .await
            .unwrap();
        // Mismatched bytes fail without overwriting.
        let mismatch = backend
            .put_immutable("content/42/source.txt", b"different", None)
            .await
            .unwrap_err();
        assert!(matches!(
            mismatch,
            ObjectStorageOpError::UnsafeExistingObject(_)
        ));
        assert_eq!(
            backend
                .read_bounded("content/42/source.txt", 16)
                .await
                .unwrap(),
            Some(b"expected".to_vec())
        );
        assert!(matches!(
            backend.read_bounded("content/42/source.txt", 4).await,
            Err(ObjectStorageOpError::TooLarge { .. })
        ));
        let prefix = backend
            .read_prefix("content/42/source.txt", 4)
            .await
            .unwrap()
            .unwrap();
        assert_eq!(prefix.bytes, b"expe");
        assert!(prefix.truncated);
        assert!(backend.exists("content/42/source.txt").await.unwrap());
        backend.delete("content/42/source.txt").await.unwrap();
        assert!(!backend.exists("content/42/source.txt").await.unwrap());
    }

    #[tokio::test]
    async fn s3_backend_supports_put_read_exists_and_delete() {
        let backend = ObjectStorageBackend::s3(Arc::new(InMemory::new()), "newsly-test-bucket");
        assert_eq!(backend.provider_name(), "s3_compatible");
        assert_eq!(backend.bucket(), Some("newsly-test-bucket"));
        assert!(!backend.exists("content/42/source.txt").await.unwrap());

        backend
            .put_immutable(
                "content/42/source.txt",
                b"hello s3",
                Some("text/plain; charset=utf-8"),
            )
            .await
            .unwrap();
        assert!(backend.exists("content/42/source.txt").await.unwrap());
        assert_eq!(
            backend
                .read_bounded("content/42/source.txt", 16)
                .await
                .unwrap(),
            Some(b"hello s3".to_vec())
        );
        assert!(matches!(
            backend.read_bounded("content/42/source.txt", 4).await,
            Err(ObjectStorageOpError::TooLarge { .. })
        ));
        let prefix = backend
            .read_prefix("content/42/source.txt", 5)
            .await
            .unwrap()
            .unwrap();
        assert_eq!(prefix.bytes, b"hello");
        assert!(prefix.truncated);

        backend.delete("content/42/source.txt").await.unwrap();
        backend.delete("content/42/source.txt").await.unwrap();
        assert!(!backend.exists("content/42/source.txt").await.unwrap());
        assert_eq!(
            backend
                .read_bounded("content/42/source.txt", 16)
                .await
                .unwrap(),
            None
        );
    }

    #[tokio::test]
    async fn concurrent_identical_local_writers_both_succeed() {
        let temporary = tempfile::tempdir().unwrap();
        let backend = ObjectStorageBackend::local(temporary.path().join("objects")).unwrap();
        let (first, second) = tokio::join!(
            backend.put_immutable("objects/value.txt", b"expected", None),
            backend.put_immutable("objects/value.txt", b"expected", None),
        );
        first.unwrap();
        second.unwrap();
    }
}

#[cfg(all(test, unix))]
mod deletion_tests {
    use super::delete_local_object;

    #[tokio::test]
    async fn deleting_symlink_unlinks_entry_and_preserves_target() {
        let temporary = tempfile::tempdir().unwrap();
        let root = temporary.path().join("objects");
        tokio::fs::create_dir_all(&root).await.unwrap();
        let target = temporary.path().join("source.txt");
        tokio::fs::write(&target, b"source").await.unwrap();
        std::os::unix::fs::symlink(&target, root.join("link.txt")).unwrap();
        delete_local_object(&root, "link.txt").await.unwrap();
        assert!(
            tokio::fs::symlink_metadata(root.join("link.txt"))
                .await
                .is_err()
        );
        assert_eq!(tokio::fs::read(&target).await.unwrap(), b"source");
        delete_local_object(&root, "link.txt").await.unwrap();
    }

    #[tokio::test]
    async fn deleting_through_escaping_parent_fails_closed() {
        let temporary = tempfile::tempdir().unwrap();
        let root = temporary.path().join("objects");
        let outside = temporary.path().join("outside");
        tokio::fs::create_dir_all(&root).await.unwrap();
        tokio::fs::create_dir_all(&outside).await.unwrap();
        tokio::fs::write(outside.join("source.txt"), b"source")
            .await
            .unwrap();
        std::os::unix::fs::symlink(&outside, root.join("escape")).unwrap();
        assert!(
            delete_local_object(&root, "escape/source.txt")
                .await
                .is_err()
        );
        assert_eq!(
            tokio::fs::read(outside.join("source.txt")).await.unwrap(),
            b"source"
        );
    }
}

#[cfg(all(test, unix))]
mod read_tests {
    use super::read_local_object;

    #[tokio::test]
    async fn reads_are_bounded_and_escape_or_directory_is_rejected() {
        let temporary = tempfile::tempdir().unwrap();
        let root = temporary.path().join("objects");
        tokio::fs::create_dir_all(root.join("folder"))
            .await
            .unwrap();
        tokio::fs::write(root.join("source.txt"), b"bounded source")
            .await
            .unwrap();
        let slice = read_local_object(&root, "source.txt", 7)
            .await
            .unwrap()
            .unwrap();
        assert_eq!(slice.bytes, b"bounded");
        assert!(slice.truncated);
        assert!(
            read_local_object(&root, "missing.txt", 7)
                .await
                .unwrap()
                .is_none()
        );
        assert!(read_local_object(&root, "folder", 7).await.is_err());
        let outside = temporary.path().join("secret.txt");
        tokio::fs::write(&outside, b"secret").await.unwrap();
        std::os::unix::fs::symlink(&outside, root.join("escape.txt")).unwrap();
        assert!(read_local_object(&root, "escape.txt", 7).await.is_err());
    }
}
