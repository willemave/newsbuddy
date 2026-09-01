use std::path::PathBuf;

use chrono::Utc;
use serde_json::Value;
use thiserror::Error;

use crate::local_object::{
    LocalObjectError, absolute_root, publish, sha256_hex, storage_key, validate_relative_path,
};

use super::model::RawDiscussionPointer;

const MAX_RAW_DISCUSSION_BYTES: usize = 16 * 1024 * 1024;

#[derive(Debug, Clone)]
pub struct DiscussionObjectStore {
    root: PathBuf,
    prefix: PathBuf,
}

impl DiscussionObjectStore {
    /// Creates the canonical local object-store boundary for immutable comment trees.
    ///
    /// # Errors
    ///
    /// Rejects an absolute or traversing storage prefix and failure to resolve a relative root.
    pub fn new(root: PathBuf, prefix: PathBuf) -> Result<Self, DiscussionObjectStoreError> {
        validate_relative_path(&prefix)?;
        let root = absolute_root(root)?;
        Ok(Self { root, prefix })
    }

    /// Writes a content-addressed raw-comment object without a live database connection.
    ///
    /// Existing objects must be regular files with identical contents. A losing task attempt may
    /// leave an unreferenced object, but the exact queue and row fences control pointer publication.
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
        validate_relative_path(&storage_path)?;
        let storage_key = storage_key(&storage_path)?;
        let pretty = serde_json::to_vec_pretty(raw_payload)?;
        publish(&self.root, &storage_path, &pretty).await?;
        Ok(RawDiscussionPointer {
            storage_provider: "local",
            storage_bucket: None,
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
    #[error("raw discussion object could not be read")]
    Read(#[source] std::io::Error),
    #[error("an existing content-addressed discussion object is unsafe or has different bytes")]
    UnsafeExistingObject,
}

impl From<LocalObjectError> for DiscussionObjectStoreError {
    fn from(error: LocalObjectError) -> Self {
        match error {
            LocalObjectError::CurrentDirectory(error) => Self::CurrentDirectory(error),
            LocalObjectError::UnsafePath => Self::UnsafeStorageKey,
            LocalObjectError::Write(error) => Self::Write(error),
            LocalObjectError::Read(error) => Self::Read(error),
            LocalObjectError::UnsafeExistingObject => Self::UnsafeExistingObject,
        }
    }
}

#[cfg(test)]
mod tests {
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

    #[test]
    fn rejects_parent_traversal() {
        assert!(
            DiscussionObjectStore::new(PathBuf::from("data"), PathBuf::from("../escape")).is_err()
        );
    }
}
