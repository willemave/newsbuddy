use std::path::{Component, Path, PathBuf};

use chrono::Utc;
use serde_json::Value;
use sha2::{Digest, Sha256};
use thiserror::Error;
use tokio::fs;
use uuid::Uuid;

use super::model::RawDiscussionPointer;

const MAX_RAW_DISCUSSION_BYTES: usize = 16 * 1024 * 1024;

#[derive(Debug, Clone)]
pub struct DiscussionObjectStore {
    root: PathBuf,
    prefix: PathBuf,
}

impl DiscussionObjectStore {
    /// Creates the Python-compatible local object-store boundary for immutable comment trees.
    ///
    /// # Errors
    ///
    /// Rejects an absolute or traversing storage prefix and failure to resolve a relative root.
    pub fn new(root: PathBuf, prefix: PathBuf) -> Result<Self, DiscussionObjectStoreError> {
        validate_relative_path(&prefix)?;
        let root = if root.is_absolute() {
            root
        } else {
            std::env::current_dir()
                .map_err(DiscussionObjectStoreError::CurrentDirectory)?
                .join(root)
        };
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
        let destination = self.root.join(&storage_path);
        let parent = destination
            .parent()
            .ok_or(DiscussionObjectStoreError::UnsafeStorageKey)?;
        fs::create_dir_all(parent)
            .await
            .map_err(DiscussionObjectStoreError::Write)?;
        let pretty = serde_json::to_vec_pretty(raw_payload)?;
        match fs::symlink_metadata(&destination).await {
            Ok(metadata) => {
                if metadata.file_type().is_symlink()
                    || !metadata.is_file()
                    || fs::read(&destination)
                        .await
                        .map_err(DiscussionObjectStoreError::Read)?
                        != pretty
                {
                    return Err(DiscussionObjectStoreError::UnsafeExistingObject);
                }
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                let temporary = parent.join(format!(".{}.tmp", Uuid::new_v4()));
                fs::write(&temporary, &pretty)
                    .await
                    .map_err(DiscussionObjectStoreError::Write)?;
                if let Err(error) = fs::rename(&temporary, &destination).await {
                    let _ = fs::remove_file(&temporary).await;
                    return Err(DiscussionObjectStoreError::Write(error));
                }
            }
            Err(error) => return Err(DiscussionObjectStoreError::Read(error)),
        }
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

fn validate_relative_path(path: &Path) -> Result<(), DiscussionObjectStoreError> {
    if path.as_os_str().is_empty()
        || path.is_absolute()
        || path
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return Err(DiscussionObjectStoreError::UnsafeStorageKey);
    }
    Ok(())
}

fn storage_key(path: &Path) -> Result<String, DiscussionObjectStoreError> {
    path.components()
        .map(|component| match component {
            Component::Normal(part) => part
                .to_str()
                .map(str::to_owned)
                .ok_or(DiscussionObjectStoreError::UnsafeStorageKey),
            _ => Err(DiscussionObjectStoreError::UnsafeStorageKey),
        })
        .collect::<Result<Vec<_>, _>>()
        .map(|parts| parts.join("/"))
}

fn sha256_hex(bytes: &[u8]) -> String {
    let digest = Sha256::digest(bytes);
    let mut output = String::with_capacity(64);
    for byte in digest {
        use std::fmt::Write as _;
        let _ = write!(output, "{byte:02x}");
    }
    output
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

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[tokio::test]
    async fn stages_python_compatible_immutable_pointer() {
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
