use std::path::PathBuf;

use newsly_providers::{
    ObjectStorageBackend, ObjectStorageConfigError, ObjectStorageOpError, is_safe_object_key,
};
use thiserror::Error;

use super::model::SummaryBodyPointer;

const MAX_SUMMARY_SOURCE_BYTES: usize = 4_000_000;

#[derive(Debug, Clone)]
pub struct SummarizationBodyStore {
    backend: ObjectStorageBackend,
}

impl SummarizationBodyStore {
    /// Creates the reader backed by the configured local or S3-compatible object store.
    #[must_use]
    pub const fn from_backend(backend: ObjectStorageBackend) -> Self {
        Self { backend }
    }

    /// Creates a local reader for canonical content-body objects.
    ///
    /// # Errors
    ///
    /// Returns an error when a relative root cannot be made absolute or is unsafe.
    pub fn new(root: PathBuf) -> Result<Self, SummarizationBodyStoreError> {
        let backend = ObjectStorageBackend::local(root).map_err(|error| match error {
            ObjectStorageConfigError::CurrentDirectory(source) => {
                SummarizationBodyStoreError::CurrentDirectory(source)
            }
            _ => SummarizationBodyStoreError::UnsafeStorageKey,
        })?;
        Ok(Self { backend })
    }

    /// Reads one immutable source pointer without holding a `PostgreSQL` connection.
    ///
    /// # Errors
    ///
    /// Rejects mismatched providers, unsafe keys, oversized objects, invalid UTF-8, and backend
    /// failures. A pointer whose object disappeared is a retryable error rather than a silent
    /// fallback to potentially stale JSON metadata.
    pub(super) async fn read_source(
        &self,
        pointer: &SummaryBodyPointer,
    ) -> Result<String, SummarizationBodyStoreError> {
        if pointer.storage_provider != self.backend.provider_name() {
            return Err(SummarizationBodyStoreError::UnsupportedProvider(
                pointer.storage_provider.clone(),
            ));
        }
        if !is_safe_object_key(&pointer.storage_key) {
            return Err(SummarizationBodyStoreError::UnsafeStorageKey);
        }
        let bytes = self
            .backend
            .read_bounded(&pointer.storage_key, MAX_SUMMARY_SOURCE_BYTES)
            .await
            .map_err(|error| match error {
                ObjectStorageOpError::UnsafeKey(_)
                | ObjectStorageOpError::UnsafeLocalPath(_)
                | ObjectStorageOpError::UnsafeExistingObject(_) => {
                    SummarizationBodyStoreError::UnsafeStorageKey
                }
                ObjectStorageOpError::TooLarge { maximum_bytes, .. } => {
                    SummarizationBodyStoreError::BodyTooLarge(maximum_bytes.saturating_add(1))
                }
                ObjectStorageOpError::File { source, .. } => {
                    SummarizationBodyStoreError::Read(source)
                }
                ObjectStorageOpError::ObjectStore(message) => {
                    SummarizationBodyStoreError::ObjectStore(message)
                }
            })?
            .ok_or_else(|| {
                SummarizationBodyStoreError::Read(std::io::Error::new(
                    std::io::ErrorKind::NotFound,
                    "source object not found",
                ))
            })?;
        let body = String::from_utf8(bytes).map_err(SummarizationBodyStoreError::InvalidUtf8)?;
        if body.trim().is_empty() {
            return Err(SummarizationBodyStoreError::EmptyBody);
        }
        Ok(body)
    }
}

#[derive(Debug, Error)]
pub enum SummarizationBodyStoreError {
    #[error("could not resolve the current directory for body storage")]
    CurrentDirectory(#[source] std::io::Error),
    #[error("summarization body storage key is unsafe")]
    UnsafeStorageKey,
    #[error("summarization does not support content body provider {0:?}")]
    UnsupportedProvider(String),
    #[error("summarization source object could not be read")]
    Read(#[source] std::io::Error),
    #[error("summarization source object-store read failed: {0}")]
    ObjectStore(String),
    #[error("summarization source object has {0} bytes, exceeding the worker bound")]
    BodyTooLarge(usize),
    #[error("summarization source object is not valid UTF-8")]
    InvalidUtf8(#[source] std::string::FromUtf8Error),
    #[error("summarization source object is empty")]
    EmptyBody,
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use object_store::memory::InMemory;

    use super::*;

    #[test]
    fn rejects_parent_traversal() {
        assert!(!is_safe_object_key("../secret"));
        assert!(is_safe_object_key("content/42/source.txt"));
    }

    #[tokio::test]
    async fn reads_s3_compatible_source_pointer() {
        let backend = ObjectStorageBackend::s3(Arc::new(InMemory::new()), "bucket");
        backend
            .put_immutable("content/42/source.txt", b"s3 summary source", None)
            .await
            .unwrap();
        let store = SummarizationBodyStore::from_backend(backend);
        let pointer = SummaryBodyPointer {
            storage_provider: "s3_compatible".to_owned(),
            storage_key: "content/42/source.txt".to_owned(),
            sha256: newsly_domain::sha256_hex(b"s3 summary source"),
        };
        assert_eq!(
            store.read_source(&pointer).await.unwrap(),
            "s3 summary source"
        );
    }
}
