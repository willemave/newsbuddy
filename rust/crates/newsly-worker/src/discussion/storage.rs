use std::path::PathBuf;

use chrono::Utc;
use newsly_domain::sha256_hex;
use newsly_providers::{
    ObjectStorageBackend, ObjectStorageConfigError, ObjectStorageOpError, path_to_object_key,
};
use serde_json::Value;
use thiserror::Error;

use super::model::RawDiscussionPointer;

const MAX_RAW_DISCUSSION_BYTES: usize = 16 * 1024 * 1024;

#[derive(Debug, Clone)]
pub struct DiscussionObjectStore {
    backend: ObjectStorageBackend,
    prefix: PathBuf,
}

impl DiscussionObjectStore {
    /// Creates the canonical object-store boundary for immutable comment trees over local or S3
    /// storage.
    ///
    /// # Errors
    ///
    /// Rejects an absolute or traversing storage prefix.
    pub fn from_backend(
        backend: ObjectStorageBackend,
        prefix: PathBuf,
    ) -> Result<Self, DiscussionObjectStoreError> {
        if path_to_object_key(&prefix).is_none() {
            return Err(DiscussionObjectStoreError::UnsafeStorageKey);
        }
        Ok(Self { backend, prefix })
    }

    /// Creates a local object-store boundary for immutable comment trees.
    ///
    /// # Errors
    ///
    /// Rejects an absolute or traversing storage prefix and failure to resolve a relative root.
    pub fn new(root: PathBuf, prefix: PathBuf) -> Result<Self, DiscussionObjectStoreError> {
        let backend = ObjectStorageBackend::local(root).map_err(|error| match error {
            ObjectStorageConfigError::CurrentDirectory(source) => {
                DiscussionObjectStoreError::CurrentDirectory(source)
            }
            _ => DiscussionObjectStoreError::UnsafeStorageKey,
        })?;
        Self::from_backend(backend, prefix)
    }

    /// Writes a content-addressed raw-comment object without a live database connection.
    ///
    /// Existing local objects must be regular files with identical contents. A losing task attempt
    /// may leave an unreferenced object, but the exact queue and row fences control pointer
    /// publication.
    pub(super) async fn stage(
        &self,
        news_item_id: i64,
        raw_payload: &Value,
        comment_count: usize,
    ) -> Result<RawDiscussionPointer, DiscussionObjectStoreError> {
        let canonical = serde_json::to_vec(raw_payload)?;
        if canonical.len() > MAX_RAW_DISCUSSION_BYTES {
            return Err(DiscussionObjectStoreError::PayloadTooLarge(canonical.len()));
        }
        let sha256 = sha256_hex(&canonical);
        let storage_path = self
            .prefix
            .join("news-item-discussions")
            .join(news_item_id.to_string())
            .join(format!("comments-{sha256}.json"));
        let storage_key = path_to_object_key(&storage_path)
            .ok_or(DiscussionObjectStoreError::UnsafeStorageKey)?;
        let pretty = serde_json::to_vec_pretty(raw_payload)?;
        self.backend
            .put_immutable(&storage_key, &pretty, Some("application/json"))
            .await?;
        Ok(RawDiscussionPointer {
            storage_provider: self.backend.provider_name(),
            storage_bucket: self.backend.bucket().map(str::to_owned),
            storage_key,
            content_format: "json",
            sha256,
            byte_size: i32::try_from(pretty.len())
                .map_err(|_| DiscussionObjectStoreError::PayloadTooLarge(pretty.len()))?,
            comment_count: i32::try_from(comment_count).unwrap_or(i32::MAX),
            updated_at: Utc::now(),
        })
    }
}

#[derive(Debug, Error)]
pub enum DiscussionObjectStoreError {
    #[error("could not resolve the current directory for discussion storage")]
    CurrentDirectory(#[source] std::io::Error),
    #[error("discussion storage key is unsafe")]
    UnsafeStorageKey,
    #[error("raw discussion payload has {0} bytes, exceeding the worker bound")]
    PayloadTooLarge(usize),
    #[error("raw discussion payload could not be serialized")]
    Serialize(#[from] serde_json::Error),
    #[error("raw discussion object could not be written")]
    Write(#[source] std::io::Error),
    #[error("raw discussion object-store operation failed: {0}")]
    ObjectStore(String),
    #[error("an existing content-addressed discussion object is unsafe or has different bytes")]
    UnsafeExistingObject,
}

impl From<ObjectStorageOpError> for DiscussionObjectStoreError {
    fn from(error: ObjectStorageOpError) -> Self {
        match error {
            ObjectStorageOpError::UnsafeKey(_) | ObjectStorageOpError::UnsafeLocalPath(_) => {
                Self::UnsafeStorageKey
            }
            ObjectStorageOpError::UnsafeExistingObject(_) => Self::UnsafeExistingObject,
            ObjectStorageOpError::TooLarge { maximum_bytes, .. } => {
                Self::PayloadTooLarge(maximum_bytes.saturating_add(1))
            }
            ObjectStorageOpError::File { source, .. } => Self::Write(source),
            ObjectStorageOpError::ObjectStore(message) => Self::ObjectStore(message),
        }
    }
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use object_store::memory::InMemory;
    use serde_json::json;
    use tokio::fs;
    use uuid::Uuid;

    use super::*;

    #[tokio::test]
    async fn stages_canonical_immutable_pointer() {
        let root = std::env::temp_dir().join(format!("newsly-discussion-{}", Uuid::new_v4()));
        let store = DiscussionObjectStore::new(root.clone(), PathBuf::from("content")).unwrap();
        let payload = json!({"comments": [{"comment_id": "1", "text": "hello"}]});
        let first = store.stage(42, &payload, 1).await.unwrap();
        let second = store.stage(42, &payload, 1).await.unwrap();
        assert_eq!(first.sha256, second.sha256);
        assert_eq!(first.storage_key, second.storage_key);
        assert!(
            first
                .storage_key
                .starts_with("content/news-item-discussions/42/")
        );
        let stored = fs::read(root.join(&first.storage_key)).await.unwrap();
        assert_eq!(first.byte_size, i32::try_from(stored.len()).unwrap());
        assert_eq!(first.to_json()["comment_count"], 1);
        let _ = fs::remove_dir_all(root).await;
    }

    #[tokio::test]
    async fn stages_s3_compatible_discussion_pointer() {
        let backend = ObjectStorageBackend::s3(Arc::new(InMemory::new()), "newsly-bucket");
        let store =
            DiscussionObjectStore::from_backend(backend.clone(), PathBuf::from("content")).unwrap();
        let payload = json!({"comments": []});
        let staged = store.stage(42, &payload, 0).await.unwrap();
        assert_eq!(staged.storage_provider, "s3_compatible");
        assert_eq!(staged.storage_bucket.as_deref(), Some("newsly-bucket"));
        assert!(backend.exists(&staged.storage_key).await.unwrap());
    }

    #[test]
    fn rejects_parent_traversal() {
        assert!(
            DiscussionObjectStore::new(PathBuf::from("data"), PathBuf::from("../escape")).is_err()
        );
    }
}
