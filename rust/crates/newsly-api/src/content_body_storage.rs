use futures_util::StreamExt as _;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use object_store::path::Path as ObjectPath;
use object_store::{Error as ObjectStoreError, ObjectStore, ObjectStoreExt};
use thiserror::Error;

#[derive(Clone)]
pub(super) enum ContentBodyStore {
    Local { root: PathBuf },
    S3 { store: Arc<dyn ObjectStore> },
}

impl std::fmt::Debug for ContentBodyStore {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Local { root } => formatter
                .debug_struct("LocalContentBodyStore")
                .field("root", root)
                .finish(),
            Self::S3 { .. } => formatter
                .debug_struct("S3ContentBodyStore")
                .finish_non_exhaustive(),
        }
    }
}

impl ContentBodyStore {
    pub(super) fn from_environment() -> Result<Self, ContentBodyStoreError> {
        Ok(
            match newsly_providers::ObjectStorageBackend::from_environment()? {
                newsly_providers::ObjectStorageBackend::Local { root } => Self::Local { root },
                newsly_providers::ObjectStorageBackend::S3 { store } => Self::S3 { store },
            },
        )
    }

    pub(super) async fn get_text(
        &self,
        key: &str,
    ) -> Result<Option<String>, ContentBodyStoreError> {
        validate_object_key(key)?;
        match self {
            Self::Local { root } => read_local(root, key).await,
            Self::S3 { store } => {
                let path = ObjectPath::parse(key)
                    .map_err(|error| ContentBodyStoreError::UnsafeObjectKey(error.to_string()))?;
                let result = match store.get(&path).await {
                    Ok(result) => result,
                    Err(ObjectStoreError::NotFound { .. }) => return Ok(None),
                    Err(error) => {
                        return Err(ContentBodyStoreError::ObjectStore(error.to_string()));
                    }
                };
                let maximum = newsly_providers::MAX_SOURCE_BODY_BYTES;
                if result.meta.size > maximum as u64 {
                    return Err(ContentBodyStoreError::TooLarge(maximum));
                }
                let mut bytes = Vec::with_capacity(
                    usize::try_from(result.meta.size)
                        .unwrap_or(maximum)
                        .min(maximum),
                );
                let mut stream = result.into_stream();
                while let Some(chunk) = stream.next().await {
                    let chunk = chunk
                        .map_err(|error| ContentBodyStoreError::ObjectStore(error.to_string()))?;
                    if bytes.len().saturating_add(chunk.len()) > maximum {
                        return Err(ContentBodyStoreError::TooLarge(maximum));
                    }
                    bytes.extend_from_slice(&chunk);
                }
                String::from_utf8(bytes)
                    .map(Some)
                    .map_err(ContentBodyStoreError::Utf8)
            }
        }
    }
}

async fn read_local(root: &Path, key: &str) -> Result<Option<String>, ContentBodyStoreError> {
    let maximum = newsly_providers::MAX_SOURCE_BODY_BYTES;
    let slice = newsly_providers::read_local_object(root, key, maximum)
        .await
        .map_err(|error| match error {
            newsly_providers::LocalStorageError::UnsafePath(path) => {
                ContentBodyStoreError::UnsafeLocalPath(path)
            }
            newsly_providers::LocalStorageError::File { path, source } => {
                ContentBodyStoreError::File { path, source }
            }
        })?;
    slice
        .map(|slice| {
            if slice.truncated {
                return Err(ContentBodyStoreError::TooLarge(maximum));
            }
            String::from_utf8(slice.bytes).map_err(ContentBodyStoreError::Utf8)
        })
        .transpose()
}

fn validate_object_key(key: &str) -> Result<(), ContentBodyStoreError> {
    if !newsly_providers::is_safe_object_key(key) {
        return Err(ContentBodyStoreError::UnsafeObjectKey(key.to_owned()));
    }
    Ok(())
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
