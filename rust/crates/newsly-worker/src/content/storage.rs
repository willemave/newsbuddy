use std::path::PathBuf;

use newsly_domain::sha256_hex;
use newsly_providers::{
    ObjectStorageBackend, ObjectStorageConfigError, ObjectStorageOpError, is_safe_object_key,
    path_to_object_key,
};
use thiserror::Error;

use super::model::ContentBodyPointer;

const MAX_SOURCE_BODY_BYTES: usize = 2_000_000;

#[derive(Debug, Clone)]
pub struct LocalContentBodyStore {
    backend: ObjectStorageBackend,
    prefix: PathBuf,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct StagedContentBody {
    pub(super) storage_provider: &'static str,
    pub(super) storage_bucket: Option<String>,
    pub(super) storage_key: String,
    pub(super) content_format: &'static str,
    pub(super) sha256: String,
    pub(super) byte_size: i32,
    pub(super) char_count: i32,
}

impl LocalContentBodyStore {
    /// Creates a content-addressed body store over the configured local or S3-compatible backend.
    ///
    /// # Errors
    ///
    /// Returns an error for an unsafe storage prefix.
    pub fn from_backend(
        backend: ObjectStorageBackend,
        prefix: PathBuf,
    ) -> Result<Self, ContentBodyStoreError> {
        if path_to_object_key(&prefix).is_none() {
            return Err(ContentBodyStoreError::UnsafeStorageKey);
        }
        Ok(Self { backend, prefix })
    }

    /// Creates a local content-addressed body store for persisted source bodies.
    ///
    /// # Errors
    ///
    /// Returns an error for an unsafe prefix or a root path that cannot be made absolute.
    pub fn new(root: PathBuf, prefix: PathBuf) -> Result<Self, ContentBodyStoreError> {
        let backend = ObjectStorageBackend::local(root).map_err(|error| match error {
            ObjectStorageConfigError::CurrentDirectory(source) => {
                ContentBodyStoreError::CurrentDirectory(source)
            }
            _ => ContentBodyStoreError::UnsafeStorageKey,
        })?;
        Self::from_backend(backend, prefix)
    }

    /// Stage a source body outside the database transaction. The deterministic object may be
    /// orphaned after lease loss, but no durable pointer can be published without the fence.
    ///
    /// # Errors
    ///
    /// Rejects empty or oversized text and propagates storage failures.
    pub(super) async fn stage_source(
        &self,
        content_id: i64,
        text: &str,
    ) -> Result<StagedContentBody, ContentBodyStoreError> {
        if text.trim().is_empty() {
            return Err(ContentBodyStoreError::EmptyBody);
        }
        let encoded = text.as_bytes();
        if encoded.len() > MAX_SOURCE_BODY_BYTES {
            return Err(ContentBodyStoreError::BodyTooLarge(encoded.len()));
        }
        let digest = sha256_hex(encoded);
        let storage_key_path = self
            .prefix
            .join(content_id.to_string())
            .join(format!("source-{digest}.txt"));
        let storage_key =
            path_to_object_key(&storage_key_path).ok_or(ContentBodyStoreError::UnsafeStorageKey)?;
        self.backend
            .put_immutable(&storage_key, encoded, Some("text/plain; charset=utf-8"))
            .await
            .map_err(map_write_error)?;
        Ok(StagedContentBody {
            storage_provider: self.backend.provider_name(),
            storage_bucket: self.backend.bucket().map(str::to_owned),
            storage_key,
            content_format: "text",
            sha256: digest,
            byte_size: i32::try_from(encoded.len())
                .map_err(|_| ContentBodyStoreError::BodyTooLarge(encoded.len()))?,
            char_count: i32::try_from(text.chars().count())
                .map_err(|_| ContentBodyStoreError::BodyTooLarge(encoded.len()))?,
        })
    }

    /// Resolve an existing source body for the pre-extracted analyze fast path.
    ///
    /// # Errors
    ///
    /// Returns an error for unsafe metadata, mismatched providers, invalid UTF-8, oversized files,
    /// or storage failures. A missing pointer object returns `Ok(None)`.
    pub(super) async fn read_source(
        &self,
        pointer: &ContentBodyPointer,
    ) -> Result<Option<String>, ContentBodyStoreError> {
        if pointer.storage_provider != self.backend.provider_name() {
            return Err(ContentBodyStoreError::UnsupportedProvider(
                pointer.storage_provider.clone(),
            ));
        }
        if !is_safe_object_key(&pointer.storage_key) {
            return Err(ContentBodyStoreError::UnsafeStorageKey);
        }
        let Some(bytes) = self
            .backend
            .read_bounded(&pointer.storage_key, MAX_SOURCE_BODY_BYTES)
            .await
            .map_err(map_read_error)?
        else {
            return Ok(None);
        };
        String::from_utf8(bytes)
            .map(Some)
            .map_err(ContentBodyStoreError::InvalidUtf8)
    }
}

fn map_write_error(error: ObjectStorageOpError) -> ContentBodyStoreError {
    match error {
        ObjectStorageOpError::UnsafeKey(_) | ObjectStorageOpError::UnsafeLocalPath(_) => {
            ContentBodyStoreError::UnsafeStorageKey
        }
        ObjectStorageOpError::UnsafeExistingObject(_) => ContentBodyStoreError::UnsafeExistingBody,
        ObjectStorageOpError::TooLarge { maximum_bytes, .. } => {
            ContentBodyStoreError::BodyTooLarge(maximum_bytes.saturating_add(1))
        }
        ObjectStorageOpError::File { source, .. } => ContentBodyStoreError::Write(source),
        ObjectStorageOpError::ObjectStore(message) => ContentBodyStoreError::ObjectStore(message),
    }
}

fn map_read_error(error: ObjectStorageOpError) -> ContentBodyStoreError {
    match error {
        ObjectStorageOpError::UnsafeKey(_) => ContentBodyStoreError::UnsafeStorageKey,
        ObjectStorageOpError::UnsafeLocalPath(_)
        | ObjectStorageOpError::UnsafeExistingObject(_) => {
            ContentBodyStoreError::UnsafeExistingBody
        }
        ObjectStorageOpError::TooLarge { maximum_bytes, .. } => {
            ContentBodyStoreError::BodyTooLarge(maximum_bytes.saturating_add(1))
        }
        ObjectStorageOpError::File { source, .. } => ContentBodyStoreError::Read(source),
        ObjectStorageOpError::ObjectStore(message) => ContentBodyStoreError::ObjectStore(message),
    }
}

#[derive(Debug, Error)]
pub enum ContentBodyStoreError {
    #[error("content body text is empty")]
    EmptyBody,
    #[error("content body has {0} bytes, exceeding the Rust worker bound")]
    BodyTooLarge(usize),
    #[error("content body storage key is unsafe")]
    UnsafeStorageKey,
    #[error("content body storage provider {0:?} is not supported by this worker")]
    UnsupportedProvider(String),
    #[error("could not resolve the current directory for body storage")]
    CurrentDirectory(#[source] std::io::Error),
    #[error("could not write content body")]
    Write(#[source] std::io::Error),
    #[error("could not read content body")]
    Read(#[source] std::io::Error),
    #[error("content body object-store operation failed: {0}")]
    ObjectStore(String),
    #[error("an existing content-addressed body is unsafe or has different bytes")]
    UnsafeExistingBody,
    #[error("stored content body is not valid UTF-8")]
    InvalidUtf8(#[source] std::string::FromUtf8Error),
}

#[cfg(test)]
mod tests {
    use std::path::PathBuf;
    use std::sync::Arc;

    use newsly_providers::ObjectStorageBackend;
    use object_store::memory::InMemory;
    use uuid::Uuid;

    use super::{ContentBodyPointer, LocalContentBodyStore};

    #[test]
    fn rejects_parent_components_in_prefix() {
        assert!(
            LocalContentBodyStore::new(PathBuf::from("data"), PathBuf::from("../escape")).is_err()
        );
    }

    #[tokio::test]
    async fn stages_and_reads_canonical_local_source_pointer() {
        let root = std::env::temp_dir().join(format!("newsly-worker-{}", Uuid::new_v4()));
        let store = LocalContentBodyStore::new(root.clone(), PathBuf::from("content")).unwrap();
        let source = "  fixture body \n";
        let staged = store.stage_source(42, source).await.unwrap();
        assert_eq!(staged.storage_provider, "local");
        assert_eq!(staged.storage_bucket, None);
        assert_eq!(staged.byte_size, i32::try_from(source.len()).unwrap());
        assert_eq!(
            staged.char_count,
            i32::try_from(source.chars().count()).unwrap()
        );
        let pointer = ContentBodyPointer {
            storage_provider: staged.storage_provider.to_owned(),
            storage_key: staged.storage_key.clone(),
        };
        assert_eq!(
            store.read_source(&pointer).await.unwrap().as_deref(),
            Some(source)
        );
        tokio::fs::remove_dir_all(&root).await.unwrap();
    }

    #[tokio::test]
    async fn stages_and_reads_s3_compatible_source_pointer() {
        let backend = ObjectStorageBackend::s3(Arc::new(InMemory::new()), "newsly-bucket");
        let store = LocalContentBodyStore::from_backend(backend, PathBuf::from("content")).unwrap();
        let staged = store.stage_source(42, "s3 fixture body").await.unwrap();
        assert_eq!(staged.storage_provider, "s3_compatible");
        assert_eq!(staged.storage_bucket.as_deref(), Some("newsly-bucket"));
        let pointer = ContentBodyPointer {
            storage_provider: staged.storage_provider.to_owned(),
            storage_key: staged.storage_key.clone(),
        };
        assert_eq!(
            store.read_source(&pointer).await.unwrap().as_deref(),
            Some("s3 fixture body")
        );
    }
}
