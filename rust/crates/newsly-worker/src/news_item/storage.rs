use std::path::PathBuf;

use newsly_domain::sha256_hex;
use newsly_providers::{
    ObjectStorageBackend, ObjectStorageConfigError, ObjectStorageOpError, is_safe_object_key,
    path_to_object_key,
};
use thiserror::Error;

use super::model::BodyPointer;

const MAX_NEWS_BODY_BYTES: usize = 2_000_000;

#[derive(Debug, Clone)]
pub struct NewsArticleBodyStore {
    backend: ObjectStorageBackend,
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
    /// Builds the content-addressed store for persisted news article bodies over local or S3
    /// storage.
    ///
    /// # Errors
    ///
    /// Returns an error when the storage prefix is unsafe.
    pub fn from_backend(
        backend: ObjectStorageBackend,
        prefix: PathBuf,
    ) -> Result<Self, NewsBodyStoreError> {
        if path_to_object_key(&prefix).is_none() {
            return Err(NewsBodyStoreError::UnsafeStorageKey);
        }
        Ok(Self { backend, prefix })
    }

    /// Builds a local content-addressed store for persisted news article bodies.
    ///
    /// # Errors
    ///
    /// Returns an error when the storage prefix is unsafe or the current working directory cannot
    /// be resolved for a relative root.
    pub fn new(root: PathBuf, prefix: PathBuf) -> Result<Self, NewsBodyStoreError> {
        let backend = ObjectStorageBackend::local(root).map_err(|error| match error {
            ObjectStorageConfigError::CurrentDirectory(source) => {
                NewsBodyStoreError::CurrentDirectory(source)
            }
            _ => NewsBodyStoreError::UnsafeStorageKey,
        })?;
        Self::from_backend(backend, prefix)
    }

    pub(super) const fn provider_name(&self) -> &'static str {
        self.backend.provider_name()
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
        let storage_key =
            path_to_object_key(&relative).ok_or(NewsBodyStoreError::UnsafeStorageKey)?;
        self.backend
            .put_immutable(&storage_key, bytes, Some("text/plain; charset=utf-8"))
            .await
            .map_err(map_write_error)?;
        Ok(StagedNewsBody {
            storage_provider: self.backend.provider_name(),
            storage_bucket: self.backend.bucket().map(str::to_owned),
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
        if pointer.storage_provider != self.backend.provider_name() {
            return Err(NewsBodyStoreError::UnsupportedProvider(
                pointer.storage_provider.clone(),
            ));
        }
        if !is_safe_object_key(&pointer.storage_key) {
            return Err(NewsBodyStoreError::UnsafeStorageKey);
        }
        let Some(bytes) = self
            .backend
            .read_bounded(&pointer.storage_key, MAX_NEWS_BODY_BYTES)
            .await
            .map_err(map_read_error)?
        else {
            return Ok(None);
        };
        String::from_utf8(bytes)
            .map(Some)
            .map_err(NewsBodyStoreError::InvalidUtf8)
    }
}

fn map_write_error(error: ObjectStorageOpError) -> NewsBodyStoreError {
    match error {
        ObjectStorageOpError::UnsafeKey(_) | ObjectStorageOpError::UnsafeLocalPath(_) => {
            NewsBodyStoreError::UnsafeStorageKey
        }
        ObjectStorageOpError::UnsafeExistingObject(_) => NewsBodyStoreError::UnsafeExistingBody,
        ObjectStorageOpError::TooLarge { maximum_bytes, .. } => {
            NewsBodyStoreError::BodyTooLarge(maximum_bytes.saturating_add(1))
        }
        ObjectStorageOpError::File { source, .. } => NewsBodyStoreError::Write(source),
        ObjectStorageOpError::ObjectStore(message) => NewsBodyStoreError::ObjectStore(message),
    }
}

fn map_read_error(error: ObjectStorageOpError) -> NewsBodyStoreError {
    match error {
        ObjectStorageOpError::UnsafeKey(_) => NewsBodyStoreError::UnsafeStorageKey,
        ObjectStorageOpError::UnsafeLocalPath(_)
        | ObjectStorageOpError::UnsafeExistingObject(_) => NewsBodyStoreError::UnsafeExistingBody,
        ObjectStorageOpError::TooLarge { maximum_bytes, .. } => {
            NewsBodyStoreError::BodyTooLarge(maximum_bytes.saturating_add(1))
        }
        ObjectStorageOpError::File { source, .. } => NewsBodyStoreError::Read(source),
        ObjectStorageOpError::ObjectStore(message) => NewsBodyStoreError::ObjectStore(message),
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
    #[error("news article object-store operation failed: {0}")]
    ObjectStore(String),
    #[error("an existing content-addressed news article body is unsafe or has different bytes")]
    UnsafeExistingBody,
    #[error("stored news article body is not valid UTF-8")]
    InvalidUtf8(#[source] std::string::FromUtf8Error),
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use object_store::memory::InMemory;
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

    #[tokio::test]
    async fn stages_and_reads_s3_compatible_news_body() {
        let backend = ObjectStorageBackend::s3(Arc::new(InMemory::new()), "newsly-bucket");
        let store = NewsArticleBodyStore::from_backend(backend, PathBuf::from("content")).unwrap();
        let staged = store.stage(42, "  s3 article \n").await.unwrap();
        assert_eq!(staged.storage_provider, "s3_compatible");
        assert_eq!(staged.storage_bucket.as_deref(), Some("newsly-bucket"));
        let pointer = BodyPointer {
            storage_provider: staged.storage_provider.to_owned(),
            storage_key: staged.storage_key,
        };
        assert_eq!(
            store.read(&pointer).await.unwrap().as_deref(),
            Some("s3 article")
        );
    }
}
