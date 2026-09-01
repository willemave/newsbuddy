use std::path::{Path, PathBuf};

use thiserror::Error;

use crate::local_object::{
    LocalObjectError, absolute_root, publish, read_optional, sha256_hex, storage_key,
    validate_relative_path,
};

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
    /// Builds the local content-addressed store for persisted news article bodies.
    ///
    /// # Errors
    ///
    /// Returns an error when the storage prefix is unsafe or the current working directory cannot
    /// be resolved for a relative root.
    pub fn new(root: PathBuf, prefix: PathBuf) -> Result<Self, NewsBodyStoreError> {
        validate_relative_path(&prefix)?;
        let root = absolute_root(root)?;
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
        let storage_key = storage_key(&relative)?;
        publish(&self.root, &relative, bytes).await?;
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
        let Some(bytes) = read_optional(&self.root, relative).await? else {
            return Ok(None);
        };
        if bytes.len() > MAX_NEWS_BODY_BYTES {
            return Err(NewsBodyStoreError::BodyTooLarge(bytes.len()));
        }
        String::from_utf8(bytes)
            .map(Some)
            .map_err(NewsBodyStoreError::InvalidUtf8)
    }
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
    #[error("an existing content-addressed news article body is unsafe or has different bytes")]
    UnsafeExistingBody,
    #[error("stored news article body is not valid UTF-8")]
    InvalidUtf8(#[source] std::string::FromUtf8Error),
}

impl From<LocalObjectError> for NewsBodyStoreError {
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
    use uuid::Uuid;

    use super::*;

    #[tokio::test]
    async fn uses_canonical_news_item_storage_key() {
        let root = std::env::temp_dir().join(format!("newsly-news-body-{}", Uuid::new_v4()));
        let store = NewsArticleBodyStore::new(root.clone(), PathBuf::from("content")).unwrap();
        let staged = store.stage(42, "  fixture body \n").await.unwrap();
        assert!(
            staged
                .storage_key
                .starts_with("content/news-items/42/source-")
        );
        assert_eq!(
            staged.byte_size,
            i32::try_from("fixture body".len()).unwrap()
        );
        assert_eq!(
            staged.char_count,
            i32::try_from("fixture body".chars().count()).unwrap()
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
