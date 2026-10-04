//! Validation shared by content-body and immutable artifact storage boundaries.

use object_store::aws::AmazonS3Builder;
use object_store::{ClientOptions, ObjectStore};
use reqwest::Url;
use secrecy::{ExposeSecret, SecretString};
use std::path::{Component, Path, PathBuf};
use std::{env, sync::Arc, time::Duration};

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

/// Storage backend built from validated process configuration; consumers retain product limits.
#[derive(Clone)]
pub enum ObjectStorageBackend {
    Local { root: PathBuf },
    S3 { store: Arc<dyn ObjectStore> },
}

impl std::fmt::Debug for ObjectStorageBackend {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Local { root } => formatter
                .debug_struct("LocalObjectStorage")
                .field("root", root)
                .finish(),
            Self::S3 { .. } => formatter
                .debug_struct("S3ObjectStorage")
                .finish_non_exhaustive(),
        }
    }
}

impl ObjectStorageBackend {
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
                let root = if configured.is_absolute() {
                    configured
                } else {
                    env::current_dir()?.join(configured)
                };
                if !is_safe_storage_root(&root) {
                    return Err(ObjectStorageConfigError::UnsafeRoot(root));
                }
                Ok(Self::Local { root })
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
                    .with_bucket_name(bucket)
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
                })
            }
            _ => Err(ObjectStorageConfigError::UnsupportedProvider(provider)),
        }
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

/// Read a regular object file within a canonical root, retaining at most the requested bytes.
///
/// # Errors
/// Returns an error for escaping paths, non-files or filesystem failures.
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
    use super::{is_safe_object_key, is_safe_storage_root};
    use std::path::Path;

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
