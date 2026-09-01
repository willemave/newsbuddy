use std::path::{Path, PathBuf};

use thiserror::Error;

use crate::local_object::{
    LocalObjectError, absolute_root, publish, read_optional, sha256_hex, storage_key,
    validate_relative_path,
};

use super::model::ContentBodyPointer;

const MAX_SOURCE_BODY_BYTES: usize = 2_000_000;

#[derive(Debug, Clone)]
pub struct LocalContentBodyStore {
    root: PathBuf,
    prefix: PathBuf,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct StagedContentBody {
    pub(super) storage_provider: &'static str,
    pub(super) storage_key: String,
    pub(super) content_format: &'static str,
    pub(super) sha256: String,
    pub(super) byte_size: i32,
    pub(super) char_count: i32,
}

impl LocalContentBodyStore {
    /// Creates the content-addressed local body store for persisted source bodies.
    ///
    /// # Errors
    ///
    /// Returns an error for an unsafe prefix or a root path that cannot be made absolute.
    pub fn new(root: PathBuf, prefix: PathBuf) -> Result<Self, ContentBodyStoreError> {
        validate_relative_path(&prefix)?;
        let root = absolute_root(root)?;
        Ok(Self { root, prefix })
    }

    /// Stage a source body outside the database transaction. The deterministic object may be
    /// orphaned after lease loss, but no durable pointer can be published without the fence.
    ///
    /// # Errors
    ///
    /// Rejects empty or oversized text and propagates filesystem failures.
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
        validate_relative_path(&storage_key_path)?;
        let storage_key = storage_key(&storage_key_path)?;
        publish(&self.root, &storage_key_path, encoded).await?;
        Ok(StagedContentBody {
            storage_provider: "local",
            storage_key,
            content_format: "text",
            sha256: digest,
            byte_size: i32::try_from(encoded.len())
                .map_err(|_| ContentBodyStoreError::BodyTooLarge(encoded.len()))?,
            char_count: i32::try_from(text.chars().count())
                .map_err(|_| ContentBodyStoreError::BodyTooLarge(encoded.len()))?,
        })
    }

    /// Resolve an existing local source body for the pre-extracted analyze fast path.
    ///
    /// # Errors
    ///
    /// Returns an error for unsafe metadata, unsupported providers, invalid UTF-8, oversized files,
    /// or filesystem failures. A missing pointer object returns `Ok(None)`.
    pub(super) async fn read_source(
        &self,
        pointer: &ContentBodyPointer,
    ) -> Result<Option<String>, ContentBodyStoreError> {
        if pointer.storage_provider != "local" {
            return Err(ContentBodyStoreError::UnsupportedProvider(
                pointer.storage_provider.clone(),
            ));
        }
        let key = Path::new(&pointer.storage_key);
        validate_relative_path(key)?;
        let Some(bytes) = read_optional(&self.root, key).await? else {
            return Ok(None);
        };
        if bytes.len() > MAX_SOURCE_BODY_BYTES {
            return Err(ContentBodyStoreError::BodyTooLarge(bytes.len()));
        }
        String::from_utf8(bytes)
            .map(Some)
            .map_err(ContentBodyStoreError::InvalidUtf8)
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
    #[error("an existing content-addressed body is unsafe or has different bytes")]
    UnsafeExistingBody,
    #[error("stored content body is not valid UTF-8")]
    InvalidUtf8(#[source] std::string::FromUtf8Error),
}

impl From<LocalObjectError> for ContentBodyStoreError {
    fn from(error: LocalObjectError) -> Self {
        match error {
            LocalObjectError::CurrentDirectory(error) => Self::CurrentDirectory(error),
            LocalObjectError::UnsafePath => Self::UnsafeStorageKey,
            LocalObjectError::Write(error) => Self::Write(error),
            LocalObjectError::Read(error) => Self::Read(error),
            LocalObjectError::UnsafeExistingObject => Self::UnsafeExistingBody,
        }
    }
}

#[cfg(test)]
mod tests {
    use std::path::PathBuf;

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
}
