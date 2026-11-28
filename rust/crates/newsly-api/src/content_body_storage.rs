use std::path::PathBuf;

use newsly_providers::{ObjectStorageBackend, ObjectStorageOpError};
use thiserror::Error;

#[derive(Debug, Clone)]
pub(super) struct ContentBodyStore {
    backend: ObjectStorageBackend,
}

impl ContentBodyStore {
    pub(super) fn from_environment() -> Result<Self, ContentBodyStoreError> {
        Ok(Self {
            backend: ObjectStorageBackend::from_environment()?,
        })
    }

    pub(super) fn backend(&self) -> ObjectStorageBackend {
        self.backend.clone()
    }

    pub(super) async fn get_text(
        &self,
        key: &str,
    ) -> Result<Option<String>, ContentBodyStoreError> {
        let Some(bytes) = self
            .backend
            .read_bounded(key, newsly_providers::MAX_SOURCE_BODY_BYTES)
            .await?
        else {
            return Ok(None);
        };
        String::from_utf8(bytes)
            .map(Some)
            .map_err(ContentBodyStoreError::Utf8)
    }
}

#[derive(Debug, Error)]
pub(super) enum ContentBodyStoreError {
    #[error("invalid content-body storage configuration")]
    Configuration(#[from] newsly_providers::ObjectStorageConfigError),
    #[error("content-body source exceeds the {0}-byte read limit")]
    TooLarge(usize),
    #[error("unsafe local content-body path {0:?}")]
    UnsafeLocalPath(PathBuf),
    #[error("unsafe content-body object key {0:?}")]
    UnsafeObjectKey(String),
    #[error("content-body object-store operation failed: {0}")]
    ObjectStore(String),
    #[error("content-body local file operation failed for {path:?}")]
    File {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
    #[error("content-body object is not valid UTF-8")]
    Utf8(#[from] std::string::FromUtf8Error),
}

impl From<ObjectStorageOpError> for ContentBodyStoreError {
    fn from(error: ObjectStorageOpError) -> Self {
        match error {
            ObjectStorageOpError::UnsafeKey(key) => Self::UnsafeObjectKey(key),
            ObjectStorageOpError::UnsafeLocalPath(path)
            | ObjectStorageOpError::UnsafeExistingObject(path) => Self::UnsafeLocalPath(path),
            ObjectStorageOpError::TooLarge { maximum_bytes, .. } => Self::TooLarge(maximum_bytes),
            ObjectStorageOpError::File { path, source } => Self::File { path, source },
            ObjectStorageOpError::ObjectStore(message) => Self::ObjectStore(message),
        }
    }
}
