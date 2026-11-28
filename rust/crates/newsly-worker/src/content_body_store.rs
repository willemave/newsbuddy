use newsly_providers::{ObjectStorageBackend, ObjectStorageOpError};
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
#[derive(Debug, Clone)]
pub(crate) struct ContentBodyStore {
    backend: ObjectStorageBackend,
}

impl ContentBodyStore {
    pub(crate) fn from_env() -> Result<Self, ContentBodyStoreError> {
        Ok(Self {
            backend: ObjectStorageBackend::from_environment()?,
        })
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
        if maximum == 0 || maximum > MAX_CHAT_CONTEXT_BYTES {
            return Err(ContentBodyStoreError::InvalidReadLimit(maximum));
        }
        let slice = self
            .backend
            .read_prefix(storage_key, maximum)
            .await
            .map_err(|error| map_storage_error(storage_key, error))?;
        Ok(slice.map(|slice| ContentBodySlice {
            bytes: slice.bytes,
            truncated: slice.truncated,
        }))
    }
}

fn map_storage_error(storage_key: &str, error: ObjectStorageOpError) -> ContentBodyStoreError {
    match error {
        ObjectStorageOpError::UnsafeKey(key) => ContentBodyStoreError::UnsafeKey(key),
        ObjectStorageOpError::UnsafeLocalPath(_)
        | ObjectStorageOpError::UnsafeExistingObject(_) => {
            ContentBodyStoreError::UnsafeKey(storage_key.to_owned())
        }
        ObjectStorageOpError::TooLarge { maximum_bytes, .. } => {
            ContentBodyStoreError::TooLarge(maximum_bytes.saturating_add(1))
        }
        ObjectStorageOpError::File { source, .. } => ContentBodyStoreError::Io(source),
        ObjectStorageOpError::ObjectStore(message) => ContentBodyStoreError::ObjectStore(message),
    }
}

#[cfg(test)]
mod tests {
    use newsly_providers::{ObjectStorageBackend, is_safe_object_key};

    use super::ContentBodyStore;

    #[test]
    fn canonical_body_keys_cannot_escape_storage() {
        assert!(is_safe_object_key("content/42/source.txt"));
        assert!(!is_safe_object_key("../secret"));
        assert!(!is_safe_object_key("/absolute"));
        assert!(!is_safe_object_key("a/../b"));
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
            backend: ObjectStorageBackend::local(directory.path().to_path_buf()).unwrap(),
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
            backend: ObjectStorageBackend::local(root).unwrap(),
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
