use std::path::{Component, Path, PathBuf};

use sha2::{Digest, Sha256};
use thiserror::Error;
use tokio::fs;
use uuid::Uuid;

use super::model::BodyPointer;

const MAX_NEWS_BODY_BYTES: usize = 2_000_000;

#[derive(Debug, Clone)]
pub struct NewsArticleBodyStore {
    root: PathBuf,
    prefix: PathBuf,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct StagedNewsBody {
    pub(super) storage_provider: &'static str,
    pub(super) storage_bucket: Option<String>,
    pub(super) storage_key: String,
    pub(super) content_format: &'static str,
    pub(super) sha256: String,
    pub(super) byte_size: i32,
    pub(super) char_count: i32,
}

impl NewsArticleBodyStore {
    /// Build a local, content-addressed body store compatible with Python body pointers.
    ///
    /// # Errors
    ///
    /// Returns an error when the storage prefix is unsafe or the current working directory cannot
    /// be resolved for a relative root.
    pub fn new(root: PathBuf, prefix: PathBuf) -> Result<Self, NewsBodyStoreError> {
        validate_relative_path(&prefix)?;
        let root = if root.is_absolute() {
            root
        } else {
            std::env::current_dir()
                .map_err(NewsBodyStoreError::CurrentDirectory)?
                .join(root)
        };
        Ok(Self { root, prefix })
    }

    pub(super) async fn stage(
        &self,
        news_item_id: i64,
        text: &str,
    ) -> Result<StagedNewsBody, NewsBodyStoreError> {
        let cleaned = text.trim();
        if cleaned.is_empty() {
            return Err(NewsBodyStoreError::EmptyBody);
        }
        let bytes = cleaned.as_bytes();
        if bytes.len() > MAX_NEWS_BODY_BYTES {
            return Err(NewsBodyStoreError::BodyTooLarge(bytes.len()));
        }
        let sha256 = sha256_hex(bytes);
        let relative = self
            .prefix
            .join("news-items")
            .join(news_item_id.to_string())
            .join(format!("source-{sha256}.txt"));
        validate_relative_path(&relative)?;
        let storage_key = path_to_storage_key(&relative)?;
        let destination = self.root.join(&relative);
        let parent = destination
            .parent()
            .ok_or(NewsBodyStoreError::UnsafeStorageKey)?;
        fs::create_dir_all(parent)
            .await
            .map_err(NewsBodyStoreError::Write)?;
        if !fs::try_exists(&destination)
            .await
            .map_err(NewsBodyStoreError::Write)?
        {
            let temporary = parent.join(format!(".{}.tmp", Uuid::new_v4()));
            fs::write(&temporary, bytes)
                .await
                .map_err(NewsBodyStoreError::Write)?;
            if let Err(error) = fs::rename(&temporary, &destination).await {
                let _ = fs::remove_file(&temporary).await;
                return Err(NewsBodyStoreError::Write(error));
            }
        }
        Ok(StagedNewsBody {
            storage_provider: "local",
            storage_bucket: None,
            storage_key,
            content_format: "text",
            sha256,
            byte_size: i32::try_from(bytes.len())
                .map_err(|_| NewsBodyStoreError::BodyTooLarge(bytes.len()))?,
            char_count: i32::try_from(cleaned.chars().count())
                .map_err(|_| NewsBodyStoreError::BodyTooLarge(bytes.len()))?,
        })
    }

    pub(super) async fn read(
        &self,
        pointer: &BodyPointer,
    ) -> Result<Option<String>, NewsBodyStoreError> {
        if pointer.storage_provider != "local" {
            return Err(NewsBodyStoreError::UnsupportedProvider(
                pointer.storage_provider.clone(),
            ));
        }
        let relative = Path::new(&pointer.storage_key);
        validate_relative_path(relative)?;
        let bytes = match fs::read(self.root.join(relative)).await {
            Ok(bytes) => bytes,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(NewsBodyStoreError::Read(error)),
        };
        if bytes.len() > MAX_NEWS_BODY_BYTES {
            return Err(NewsBodyStoreError::BodyTooLarge(bytes.len()));
        }
        String::from_utf8(bytes)
            .map(Some)
            .map_err(NewsBodyStoreError::InvalidUtf8)
    }
}

fn validate_relative_path(path: &Path) -> Result<(), NewsBodyStoreError> {
    if path.as_os_str().is_empty()
        || path.is_absolute()
        || path
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return Err(NewsBodyStoreError::UnsafeStorageKey);
    }
    Ok(())
}

fn path_to_storage_key(path: &Path) -> Result<String, NewsBodyStoreError> {
    path.components()
        .map(|component| match component {
            Component::Normal(part) => part
                .to_str()
                .map(str::to_owned)
                .ok_or(NewsBodyStoreError::UnsafeStorageKey),
            _ => Err(NewsBodyStoreError::UnsafeStorageKey),
        })
        .collect::<Result<Vec<_>, _>>()
        .map(|parts| parts.join("/"))
}

fn sha256_hex(value: &[u8]) -> String {
    Sha256::digest(value)
        .iter()
        .fold(String::with_capacity(64), |mut encoded, byte| {
            use std::fmt::Write as _;
            write!(encoded, "{byte:02x}").expect("writing to String cannot fail");
            encoded
        })
}

#[derive(Debug, Error)]
pub enum NewsBodyStoreError {
    #[error("news article body is empty")]
    EmptyBody,
    #[error("news article body has {0} bytes, exceeding the worker bound")]
    BodyTooLarge(usize),
    #[error("news article body storage key is unsafe")]
    UnsafeStorageKey,
    #[error("news article body storage provider {0:?} is not supported")]
    UnsupportedProvider(String),
    #[error("could not resolve current directory for news article storage")]
    CurrentDirectory(#[source] std::io::Error),
    #[error("could not write news article body")]
    Write(#[source] std::io::Error),
    #[error("could not read news article body")]
    Read(#[source] std::io::Error),
    #[error("stored news article body is not valid UTF-8")]
    InvalidUtf8(#[source] std::string::FromUtf8Error),
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn uses_python_compatible_news_item_storage_key() {
        let root = std::env::temp_dir().join(format!("newsly-news-body-{}", Uuid::new_v4()));
        let store = NewsArticleBodyStore::new(root.clone(), PathBuf::from("content")).unwrap();
        let staged = store.stage(42, "fixture body").await.unwrap();
        assert!(
            staged
                .storage_key
                .starts_with("content/news-items/42/source-")
        );
        let pointer = BodyPointer {
            storage_provider: "local".to_owned(),
            storage_key: staged.storage_key,
        };
        assert_eq!(
            store.read(&pointer).await.unwrap().as_deref(),
            Some("fixture body")
        );
        tokio::fs::remove_dir_all(root).await.unwrap();
    }
}
