use std::path::{Component, Path, PathBuf};

use sha2::{Digest, Sha256};
use thiserror::Error;
use tokio::fs;
use uuid::Uuid;

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
    /// Create the content-addressed local body store used by the existing Python resolver.
    ///
    /// # Errors
    ///
    /// Returns an error for an unsafe prefix or a root path that cannot be made absolute.
    pub fn new(root: PathBuf, prefix: PathBuf) -> Result<Self, ContentBodyStoreError> {
        validate_relative_path(&prefix)?;
        let root = if root.is_absolute() {
            root
        } else {
            std::env::current_dir()
                .map_err(ContentBodyStoreError::CurrentDirectory)?
                .join(root)
        };
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
        let storage_key = path_to_storage_key(&storage_key_path)?;
        let destination = self.root.join(&storage_key_path);
        let parent = destination
            .parent()
            .ok_or(ContentBodyStoreError::UnsafeStorageKey)?;
        fs::create_dir_all(parent)
            .await
            .map_err(ContentBodyStoreError::Write)?;
        if !fs::try_exists(&destination)
            .await
            .map_err(ContentBodyStoreError::Write)?
        {
            let temporary = parent.join(format!(".{}.tmp", Uuid::new_v4()));
            fs::write(&temporary, encoded)
                .await
                .map_err(ContentBodyStoreError::Write)?;
            if let Err(error) = fs::rename(&temporary, &destination).await {
                let _ = fs::remove_file(&temporary).await;
                return Err(ContentBodyStoreError::Write(error));
            }
        }
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
        let path = self.root.join(key);
        let bytes = match fs::read(path).await {
            Ok(bytes) => bytes,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(ContentBodyStoreError::Read(error)),
        };
        if bytes.len() > MAX_SOURCE_BODY_BYTES {
            return Err(ContentBodyStoreError::BodyTooLarge(bytes.len()));
        }
        String::from_utf8(bytes)
            .map(Some)
            .map_err(ContentBodyStoreError::InvalidUtf8)
    }
}

fn validate_relative_path(path: &Path) -> Result<(), ContentBodyStoreError> {
    if path.as_os_str().is_empty()
        || path.is_absolute()
        || path
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return Err(ContentBodyStoreError::UnsafeStorageKey);
    }
    Ok(())
}

fn sha256_hex(value: &[u8]) -> String {
    let digest = Sha256::digest(value);
    let mut encoded = String::with_capacity(digest.len() * 2);
    for byte in digest {
        encoded.push(
            char::from_digit(u32::from(byte >> 4), 16)
                .expect("a four-bit nibble is always a hexadecimal digit"),
        );
        encoded.push(
            char::from_digit(u32::from(byte & 0x0f), 16)
                .expect("a four-bit nibble is always a hexadecimal digit"),
        );
    }
    encoded
}

fn path_to_storage_key(path: &Path) -> Result<String, ContentBodyStoreError> {
    let parts = path
        .components()
        .map(|component| match component {
            Component::Normal(part) => part
                .to_str()
                .map(str::to_owned)
                .ok_or(ContentBodyStoreError::UnsafeStorageKey),
            _ => Err(ContentBodyStoreError::UnsafeStorageKey),
        })
        .collect::<Result<Vec<_>, _>>()?;
    Ok(parts.join("/"))
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
    #[error("stored content body is not valid UTF-8")]
    InvalidUtf8(#[source] std::string::FromUtf8Error),
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
    async fn stages_and_reads_python_compatible_source_pointer() {
        let root = std::env::temp_dir().join(format!("newsly-worker-{}", Uuid::new_v4()));
        let store = LocalContentBodyStore::new(root.clone(), PathBuf::from("content")).unwrap();
        let staged = store.stage_source(42, "fixture body").await.unwrap();
        let pointer = ContentBodyPointer {
            storage_provider: staged.storage_provider.to_owned(),
            storage_key: staged.storage_key.clone(),
        };
        assert_eq!(
            store.read_source(&pointer).await.unwrap().as_deref(),
            Some("fixture body")
        );
        tokio::fs::remove_dir_all(&root).await.unwrap();
    }
}
