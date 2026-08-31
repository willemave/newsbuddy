use std::env;
use std::path::{Component, Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use futures_util::StreamExt as _;
use object_store::aws::AmazonS3Builder;
use object_store::path::Path as ObjectPath;
use object_store::{ClientOptions, Error as ObjectStoreError};
use object_store::{ObjectStore, ObjectStoreExt};
use reqwest::Url;
use secrecy::{ExposeSecret, SecretString};
use thiserror::Error;
use tokio::io::AsyncReadExt as _;

#[derive(Clone)]
pub(super) enum LearningDeckArtifactStore {
    Local { root: PathBuf },
    S3 { store: Arc<dyn ObjectStore> },
}

impl std::fmt::Debug for LearningDeckArtifactStore {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Local { root } => formatter
                .debug_struct("LocalLearningDeckArtifactStore")
                .field("root", root)
                .finish(),
            Self::S3 { .. } => formatter
                .debug_struct("S3LearningDeckArtifactStore")
                .finish_non_exhaustive(),
        }
    }
}

impl LearningDeckArtifactStore {
    /// Builds the object-storage boundary shared with Python Learning Deck artifacts.
    ///
    /// # Errors
    ///
    /// Returns an error for unsafe local roots or incomplete S3-compatible configuration.
    pub(super) fn from_environment() -> Result<Self, LearningDeckArtifactError> {
        let provider =
            env::var("CONTENT_BODY_STORAGE_PROVIDER").unwrap_or_else(|_| "local".to_owned());
        match provider.as_str() {
            "local" => {
                let configured = env::var_os("CONTENT_BODY_LOCAL_ROOT")
                    .map_or_else(|| PathBuf::from("data/content_bodies"), PathBuf::from);
                let root = if configured.is_absolute() {
                    configured
                } else {
                    env::current_dir()?.join(configured)
                };
                validate_root(&root)?;
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
                    return Err(LearningDeckArtifactError::IncompleteCredentials);
                }
                let timeout_seconds = env::var("CONTENT_BODY_STORAGE_TIMEOUT_SECONDS")
                    .ok()
                    .map(|value| {
                        value
                            .parse::<u64>()
                            .map_err(|_| LearningDeckArtifactError::InvalidTimeout(value.clone()))
                    })
                    .transpose()?
                    .unwrap_or(30);
                if !(1..=300).contains(&timeout_seconds) {
                    return Err(LearningDeckArtifactError::InvalidTimeout(
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
                    .map_err(|error| LearningDeckArtifactError::ObjectStore(error.to_string()))?;
                Ok(Self::S3 {
                    store: Arc::new(store),
                })
            }
            _ => Err(LearningDeckArtifactError::UnsupportedProvider(provider)),
        }
    }

    /// Deletes every immutable object key after the database deletion has committed.
    ///
    /// Missing objects are idempotent success.
    pub(super) async fn delete_many(
        &self,
        object_keys: &[String],
    ) -> Result<(), LearningDeckArtifactError> {
        for key in object_keys {
            self.delete(key).await?;
        }
        Ok(())
    }

    /// Reads an immutable artifact without allowing the object to exceed the route-specific cap.
    pub(super) async fn read_bounded(
        &self,
        key: &str,
        maximum_bytes: usize,
    ) -> Result<Option<Vec<u8>>, LearningDeckArtifactError> {
        validate_object_key(key)?;
        match self {
            Self::Local { root } => read_local_bounded(root, key, maximum_bytes).await,
            Self::S3 { store } => {
                let path = ObjectPath::parse(key).map_err(|error| {
                    LearningDeckArtifactError::UnsafeObjectKey(error.to_string())
                })?;
                let result = match store.get(&path).await {
                    Ok(result) => result,
                    Err(ObjectStoreError::NotFound { .. }) => return Ok(None),
                    Err(error) => {
                        return Err(LearningDeckArtifactError::ObjectStore(error.to_string()));
                    }
                };
                if result.meta.size > maximum_bytes as u64 {
                    return Err(LearningDeckArtifactError::TooLarge {
                        key: key.to_owned(),
                        maximum_bytes,
                    });
                }
                let initial_capacity = usize::try_from(result.meta.size)
                    .expect("artifact size is bounded by maximum_bytes");
                let mut bytes = Vec::with_capacity(initial_capacity);
                let mut stream = result.into_stream();
                while let Some(chunk) = stream.next().await {
                    let chunk = chunk.map_err(|error| {
                        LearningDeckArtifactError::ObjectStore(error.to_string())
                    })?;
                    if bytes.len().saturating_add(chunk.len()) > maximum_bytes {
                        return Err(LearningDeckArtifactError::TooLarge {
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

    async fn delete(&self, key: &str) -> Result<(), LearningDeckArtifactError> {
        validate_object_key(key)?;
        match self {
            Self::Local { root } => delete_local(root, key).await,
            Self::S3 { store } => {
                let path = ObjectPath::parse(key).map_err(|error| {
                    LearningDeckArtifactError::UnsafeObjectKey(error.to_string())
                })?;
                match store.delete(&path).await {
                    Ok(()) | Err(ObjectStoreError::NotFound { .. }) => Ok(()),
                    Err(error) => Err(LearningDeckArtifactError::ObjectStore(error.to_string())),
                }
            }
        }
    }
}

async fn read_local_bounded(
    root: &Path,
    key: &str,
    maximum_bytes: usize,
) -> Result<Option<Vec<u8>>, LearningDeckArtifactError> {
    let canonical_root = match tokio::fs::canonicalize(root).await {
        Ok(root) => root,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(source) => {
            return Err(LearningDeckArtifactError::File {
                path: root.to_path_buf(),
                source,
            });
        }
    };
    let requested = root.join(key);
    let canonical_path = match tokio::fs::canonicalize(&requested).await {
        Ok(path) => path,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(source) => {
            return Err(LearningDeckArtifactError::File {
                path: requested,
                source,
            });
        }
    };
    if !canonical_path.starts_with(&canonical_root) {
        return Err(LearningDeckArtifactError::UnsafeLocalPath(canonical_path));
    }
    let file = match tokio::fs::File::open(&canonical_path).await {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(source) => {
            return Err(LearningDeckArtifactError::File {
                path: canonical_path,
                source,
            });
        }
    };
    let metadata = file
        .metadata()
        .await
        .map_err(|source| LearningDeckArtifactError::File {
            path: canonical_path.clone(),
            source,
        })?;
    if !metadata.is_file() {
        return Ok(None);
    }
    if metadata.len() > maximum_bytes as u64 {
        return Err(LearningDeckArtifactError::TooLarge {
            key: key.to_owned(),
            maximum_bytes,
        });
    }
    let read_limit = u64::try_from(maximum_bytes)
        .unwrap_or(u64::MAX)
        .saturating_add(1);
    let initial_capacity =
        usize::try_from(metadata.len()).expect("artifact size is bounded by maximum_bytes");
    let mut bytes = Vec::with_capacity(initial_capacity);
    file.take(read_limit)
        .read_to_end(&mut bytes)
        .await
        .map_err(|source| LearningDeckArtifactError::File {
            path: canonical_path,
            source,
        })?;
    if bytes.len() > maximum_bytes {
        return Err(LearningDeckArtifactError::TooLarge {
            key: key.to_owned(),
            maximum_bytes,
        });
    }
    Ok(Some(bytes))
}

async fn delete_local(root: &Path, key: &str) -> Result<(), LearningDeckArtifactError> {
    let path = root.join(key);
    match tokio::fs::symlink_metadata(&path).await {
        Ok(_) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(()),
        Err(source) => return Err(LearningDeckArtifactError::File { path, source }),
    }
    let canonical_root =
        tokio::fs::canonicalize(root)
            .await
            .map_err(|source| LearningDeckArtifactError::File {
                path: root.to_path_buf(),
                source,
            })?;
    let parent = path
        .parent()
        .ok_or_else(|| LearningDeckArtifactError::UnsafeLocalPath(path.clone()))?;
    let canonical_parent = tokio::fs::canonicalize(parent).await.map_err(|source| {
        LearningDeckArtifactError::File {
            path: parent.to_path_buf(),
            source,
        }
    })?;
    if !canonical_parent.starts_with(&canonical_root) {
        return Err(LearningDeckArtifactError::UnsafeLocalPath(path));
    }
    match tokio::fs::remove_file(&path).await {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(source) => Err(LearningDeckArtifactError::File { path, source }),
    }
}

fn validate_root(root: &Path) -> Result<(), LearningDeckArtifactError> {
    if root == Path::new("/")
        || !root.is_absolute()
        || root.components().any(|component| {
            matches!(
                component,
                Component::ParentDir | Component::CurDir | Component::Prefix(_)
            )
        })
    {
        return Err(LearningDeckArtifactError::UnsafeRoot(root.to_path_buf()));
    }
    Ok(())
}

fn validate_object_key(key: &str) -> Result<(), LearningDeckArtifactError> {
    let path = Path::new(key);
    if key.is_empty()
        || key.len() > 2_048
        || key.contains(['\0', '\\'])
        || path.is_absolute()
        || path
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return Err(LearningDeckArtifactError::UnsafeObjectKey(key.to_owned()));
    }
    Ok(())
}

fn required(name: &'static str) -> Result<String, LearningDeckArtifactError> {
    optional(name).ok_or(LearningDeckArtifactError::Missing(name))
}

fn optional(name: &'static str) -> Option<String> {
    env::var(name)
        .ok()
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
}

fn optional_url(name: &'static str) -> Result<Option<Url>, LearningDeckArtifactError> {
    optional(name)
        .map(|value| {
            let url = value
                .parse::<Url>()
                .map_err(|_| LearningDeckArtifactError::InvalidUrl(name, value.clone()))?;
            if !matches!(url.scheme(), "http" | "https") || url.cannot_be_a_base() {
                return Err(LearningDeckArtifactError::InvalidUrl(name, value));
            }
            Ok(url)
        })
        .transpose()
}

#[derive(Debug, Error)]
pub(super) enum LearningDeckArtifactError {
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
    #[error("unsafe local artifact root {0:?}")]
    UnsafeRoot(PathBuf),
    #[error("unsafe local artifact path {0:?}")]
    UnsafeLocalPath(PathBuf),
    #[error("unsafe Learning Deck object key {0:?}")]
    UnsafeObjectKey(String),
    #[error("Learning Deck object {key:?} exceeds the {maximum_bytes}-byte response limit")]
    TooLarge { key: String, maximum_bytes: usize },
    #[error("Learning Deck object-store operation failed: {0}")]
    ObjectStore(String),
    #[error("Learning Deck local artifact operation failed for {path:?}")]
    File {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
    #[error("could not resolve the current directory")]
    CurrentDirectory(#[from] std::io::Error),
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn local_reads_are_bounded_and_missing_objects_are_absent() {
        let temporary = tempfile::tempdir().unwrap();
        let root = temporary.path().to_path_buf();
        tokio::fs::create_dir_all(root.join("learning/deck"))
            .await
            .unwrap();
        tokio::fs::write(root.join("learning/deck/index.html"), b"deck")
            .await
            .unwrap();
        let store = LearningDeckArtifactStore::Local { root };

        assert_eq!(
            store
                .read_bounded("learning/deck/index.html", 4)
                .await
                .unwrap(),
            Some(b"deck".to_vec())
        );
        assert!(matches!(
            store.read_bounded("learning/deck/index.html", 3).await,
            Err(LearningDeckArtifactError::TooLarge { .. })
        ));
        assert_eq!(
            store
                .read_bounded("learning/deck/missing.html", 4)
                .await
                .unwrap(),
            None
        );
    }

    #[tokio::test]
    async fn local_reads_reject_parent_components() {
        let temporary = tempfile::tempdir().unwrap();
        let store = LearningDeckArtifactStore::Local {
            root: temporary.path().to_path_buf(),
        };
        assert!(matches!(
            store.read_bounded("../outside", 10).await,
            Err(LearningDeckArtifactError::UnsafeObjectKey(_))
        ));
    }
}
