use std::path::{Component, Path, PathBuf};

use thiserror::Error;
use tokio::fs;

use super::model::SummaryBodyPointer;

const MAX_SUMMARY_SOURCE_BYTES: usize = 4_000_000;

#[derive(Debug, Clone)]
pub struct SummarizationBodyStore {
    root: PathBuf,
}

impl SummarizationBodyStore {
    /// Creates the reader for canonical content-body objects written by either runtime.
    ///
    /// # Errors
    ///
    /// Returns an error when a relative root cannot be made absolute.
    pub fn new(root: PathBuf) -> Result<Self, SummarizationBodyStoreError> {
        let root = if root.is_absolute() {
            root
        } else {
            std::env::current_dir()
                .map_err(SummarizationBodyStoreError::CurrentDirectory)?
                .join(root)
        };
        Ok(Self { root })
    }

    /// Reads one immutable source pointer without holding a `PostgreSQL` connection.
    ///
    /// # Errors
    ///
    /// Rejects non-local providers, unsafe keys, oversized objects, invalid UTF-8, and filesystem
    /// failures. A pointer whose object disappeared is a retryable error rather than a silent
    /// fallback to potentially stale JSON metadata.
    pub(super) async fn read_source(
        &self,
        pointer: &SummaryBodyPointer,
    ) -> Result<String, SummarizationBodyStoreError> {
        if pointer.storage_provider != "local" {
            return Err(SummarizationBodyStoreError::UnsupportedProvider(
                pointer.storage_provider.clone(),
            ));
        }
        let key = Path::new(&pointer.storage_key);
        validate_relative_path(key)?;
        let bytes = fs::read(self.root.join(key))
            .await
            .map_err(SummarizationBodyStoreError::Read)?;
        if bytes.len() > MAX_SUMMARY_SOURCE_BYTES {
            return Err(SummarizationBodyStoreError::BodyTooLarge(bytes.len()));
        }
        let body = String::from_utf8(bytes).map_err(SummarizationBodyStoreError::InvalidUtf8)?;
        if body.trim().is_empty() {
            return Err(SummarizationBodyStoreError::EmptyBody);
        }
        Ok(body)
    }
}

fn validate_relative_path(path: &Path) -> Result<(), SummarizationBodyStoreError> {
    if path.as_os_str().is_empty()
        || path.is_absolute()
        || path
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return Err(SummarizationBodyStoreError::UnsafeStorageKey);
    }
    Ok(())
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
    #[error("summarization source object has {0} bytes, exceeding the worker bound")]
    BodyTooLarge(usize),
    #[error("summarization source object is not valid UTF-8")]
    InvalidUtf8(#[source] std::string::FromUtf8Error),
    #[error("summarization source object is empty")]
    EmptyBody,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_parent_traversal() {
        assert!(validate_relative_path(Path::new("../secret")).is_err());
        assert!(validate_relative_path(Path::new("content/42/source.txt")).is_ok());
    }
}
