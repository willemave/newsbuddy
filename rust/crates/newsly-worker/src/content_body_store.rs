use std::path::PathBuf;
use std::sync::Arc;

use object_store::path::Path as ObjectPath;
use object_store::{Error as ObjectStoreError, ObjectStore, ObjectStoreExt};
use thiserror::Error;

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
        let backend = match newsly_providers::ObjectStorageBackend::from_environment()? {
            newsly_providers::ObjectStorageBackend::Local { root } => {
                ContentBodyBackend::Local { root }
            }
            newsly_providers::ObjectStorageBackend::S3 { store } => {
                ContentBodyBackend::S3 { store }
            }
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
                newsly_providers::read_local_object(root, storage_key, maximum)
                    .await
                    .map(|slice| {
                        slice.map(|slice| ContentBodySlice {
                            bytes: slice.bytes,
                            truncated: slice.truncated,
                        })
                    })
                    .map_err(|error| match error {
                        newsly_providers::LocalStorageError::UnsafePath(_) => {
                            ContentBodyStoreError::UnsafeKey(storage_key.to_owned())
                        }
                        newsly_providers::LocalStorageError::File { source, .. } => {
                            ContentBodyStoreError::Io(source)
                        }
                    })
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

fn validate_key(key: &str) -> Result<(), ContentBodyStoreError> {
    if !newsly_providers::is_safe_object_key(key) {
        return Err(ContentBodyStoreError::UnsafeKey(key.to_owned()));
    }
    Ok(())
}

#[cfg(test)]
mod tests {

    use super::{ContentBodyBackend, ContentBodyStore, validate_key};

    #[test]
    fn canonical_body_keys_cannot_escape_storage() {
        assert!(validate_key("content/42/source.txt").is_ok());
        assert!(validate_key("../secret").is_err());
        assert!(validate_key("/absolute").is_err());
        assert!(validate_key("a/../b").is_err());
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
    #[cfg(unix)]
    #[tokio::test]
    async fn local_reads_reject_symlink_escape_and_non_files() {
        let temporary = tempfile::tempdir().unwrap();
        let root = temporary.path().join("body-root");
        tokio::fs::create_dir_all(&root).await.unwrap();
        let outside = temporary.path().join("secret.txt");
        tokio::fs::write(&outside, b"secret").await.unwrap();
        std::os::unix::fs::symlink(&outside, root.join("escape.txt")).unwrap();
        let store = ContentBodyStore {
            backend: ContentBodyBackend::Local { root },
        };
        assert!(store.get_bytes("escape.txt").await.is_err());
        assert!(store.get_bytes(".").await.is_err());
    }
}

#[derive(Debug, Error)]
pub enum ContentBodyStoreError {
    #[error("invalid content-body storage configuration")]
    Configuration(#[from] newsly_providers::ObjectStorageConfigError),
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
