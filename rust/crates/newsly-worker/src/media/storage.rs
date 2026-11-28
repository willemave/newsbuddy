use std::path::{Path, PathBuf};

use newsly_db::MediaTranscriptPointer;
use newsly_domain::sha256_hex;
use newsly_providers::{
    ObjectStorageBackend, ObjectStorageConfigError, ObjectStorageOpError, path_to_object_key,
};
use thiserror::Error;
use tokio::fs;

const MAX_TRANSCRIPT_BYTES: usize = 2_000_000;

#[derive(Debug, Clone)]
pub struct MediaFileStore {
    scratch_root: PathBuf,
    tweet_media_root: PathBuf,
    body_backend: ObjectStorageBackend,
    body_prefix: PathBuf,
    max_media_bytes: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ValidatedMediaFile {
    pub path: PathBuf,
    pub filename: String,
    pub size_bytes: u64,
}

impl MediaFileStore {
    /// Creates bounded local scratch/media roots paired with a local or S3-compatible transcript
    /// body storage backend.
    pub fn from_backend(
        scratch_root: PathBuf,
        tweet_media_root: PathBuf,
        body_backend: ObjectStorageBackend,
        body_prefix: PathBuf,
        max_media_bytes: u64,
    ) -> Result<Self, MediaFileStoreError> {
        if max_media_bytes == 0 {
            return Err(MediaFileStoreError::InvalidMediaLimit);
        }
        if path_to_object_key(&body_prefix).is_none() {
            return Err(MediaFileStoreError::UnsafeStorageKey);
        }
        let current_directory = std::env::current_dir().map_err(MediaFileStoreError::CurrentDir)?;
        Ok(Self {
            scratch_root: absolute_path(scratch_root, &current_directory),
            tweet_media_root: absolute_path(tweet_media_root, &current_directory),
            body_backend,
            body_prefix,
            max_media_bytes,
        })
    }

    /// Creates bounded local media/body storage. All roots are made absolute once so durable
    /// metadata never depends on a later process working directory.
    pub fn new(
        scratch_root: PathBuf,
        tweet_media_root: PathBuf,
        body_root: PathBuf,
        body_prefix: PathBuf,
        max_media_bytes: u64,
    ) -> Result<Self, MediaFileStoreError> {
        let body_backend = ObjectStorageBackend::local(body_root).map_err(|error| match error {
            ObjectStorageConfigError::CurrentDirectory(source) => {
                MediaFileStoreError::CurrentDir(source)
            }
            _ => MediaFileStoreError::UnsafeStorageKey,
        })?;
        Self::from_backend(
            scratch_root,
            tweet_media_root,
            body_backend,
            body_prefix,
            max_media_bytes,
        )
    }

    pub const fn max_media_bytes(&self) -> u64 {
        self.max_media_bytes
    }

    /// Creates a task-generation-specific podcast scratch directory. It is safe to delete after a
    /// transcript has been staged because no database row ever points at this directory.
    pub async fn podcast_attempt_dir(
        &self,
        content_id: i64,
        task_id: i64,
    ) -> Result<PathBuf, MediaFileStoreError> {
        let path = self
            .scratch_root
            .join(format!("content-{content_id}"))
            .join(format!("task-{task_id}"));
        fs::create_dir_all(&path)
            .await
            .map_err(MediaFileStoreError::Write)?;
        Ok(path)
    }

    /// Creates the durable tweet-media directory. The transcription task receives the exact file
    /// path through content metadata, while path validation still confines reads to this root.
    pub async fn tweet_attempt_dir(
        &self,
        content_id: i64,
        task_id: i64,
    ) -> Result<PathBuf, MediaFileStoreError> {
        let path = self
            .tweet_media_root
            .join(format!("content-{content_id}"))
            .join(format!("task-{task_id}"));
        fs::create_dir_all(&path)
            .await
            .map_err(MediaFileStoreError::Write)?;
        Ok(path)
    }

    /// Validates a persisted tweet audio pointer against the configured media root and size bound.
    /// Symlinks and non-regular files are rejected before an `OpenAI` upload can begin.
    pub async fn validate_tweet_audio(
        &self,
        raw_path: &str,
    ) -> Result<ValidatedMediaFile, MediaFileStoreError> {
        let raw_path = Path::new(raw_path.trim());
        if raw_path.as_os_str().is_empty() {
            return Err(MediaFileStoreError::MissingTweetAudioPath);
        }
        let metadata = fs::symlink_metadata(raw_path)
            .await
            .map_err(MediaFileStoreError::Read)?;
        if metadata.file_type().is_symlink() || !metadata.is_file() {
            return Err(MediaFileStoreError::UnsafeMediaPath);
        }
        if metadata.len() == 0 {
            return Err(MediaFileStoreError::EmptyMedia);
        }
        if metadata.len() > self.max_media_bytes {
            return Err(MediaFileStoreError::MediaTooLarge {
                limit: self.max_media_bytes,
            });
        }
        let canonical_root = fs::canonicalize(&self.tweet_media_root)
            .await
            .map_err(MediaFileStoreError::Read)?;
        let canonical_path = fs::canonicalize(raw_path)
            .await
            .map_err(MediaFileStoreError::Read)?;
        if !canonical_path.starts_with(&canonical_root) {
            return Err(MediaFileStoreError::UnsafeMediaPath);
        }
        let filename = canonical_path
            .file_name()
            .and_then(|value| value.to_str())
            .ok_or(MediaFileStoreError::UnsafeMediaPath)?
            .to_owned();
        Ok(ValidatedMediaFile {
            path: canonical_path,
            filename,
            size_bytes: metadata.len(),
        })
    }

    /// Publishes a content-addressed canonical podcast body outside the database transaction.
    /// Lease loss can leave an unreferenced object, but can never publish an unfenced DB pointer.
    pub async fn stage_transcript(
        &self,
        content_id: i64,
        transcript: &str,
    ) -> Result<MediaTranscriptPointer, MediaFileStoreError> {
        let transcript = transcript.trim();
        if transcript.is_empty() {
            return Err(MediaFileStoreError::EmptyTranscript);
        }
        let encoded = transcript.as_bytes();
        if encoded.len() > MAX_TRANSCRIPT_BYTES {
            return Err(MediaFileStoreError::TranscriptTooLarge(encoded.len()));
        }
        let sha256 = sha256_hex(encoded);
        let storage_key_path = self
            .body_prefix
            .join(content_id.to_string())
            .join(format!("source-{sha256}.txt"));
        let storage_key =
            path_to_object_key(&storage_key_path).ok_or(MediaFileStoreError::UnsafeStorageKey)?;
        self.body_backend
            .put_immutable(&storage_key, encoded, Some("text/plain; charset=utf-8"))
            .await?;
        Ok(MediaTranscriptPointer {
            storage_provider: self.body_backend.provider_name().to_owned(),
            storage_bucket: self.body_backend.bucket().map(str::to_owned),
            storage_key,
            content_format: "text".to_owned(),
            sha256,
            byte_size: i32::try_from(encoded.len())
                .map_err(|_| MediaFileStoreError::TranscriptTooLarge(encoded.len()))?,
            char_count: i32::try_from(transcript.chars().count())
                .map_err(|_| MediaFileStoreError::TranscriptTooLarge(encoded.len()))?,
        })
    }

    pub async fn cleanup_podcast_attempt(&self, path: &Path) {
        if path.starts_with(&self.scratch_root) {
            let _ = fs::remove_dir_all(path).await;
        }
    }

    pub async fn cleanup_tweet_attempt(&self, path: &Path) {
        if path.starts_with(&self.tweet_media_root) {
            let _ = fs::remove_dir_all(path).await;
        }
    }
}

fn absolute_path(path: PathBuf, current_directory: &Path) -> PathBuf {
    if path.is_absolute() {
        path
    } else {
        current_directory.join(path)
    }
}

#[derive(Debug, Error)]
pub enum MediaFileStoreError {
    #[error("media byte limit must be greater than zero")]
    InvalidMediaLimit,
    #[error("could not resolve current directory")]
    CurrentDir(#[source] std::io::Error),
    #[error("media path is outside the configured root or is not a regular file")]
    UnsafeMediaPath,
    #[error("tweet audio path is missing")]
    MissingTweetAudioPath,
    #[error("media file is empty")]
    EmptyMedia,
    #[error("media file exceeded the {limit}-byte limit")]
    MediaTooLarge { limit: u64 },
    #[error("podcast transcript is empty")]
    EmptyTranscript,
    #[error("podcast transcript has {0} bytes, exceeding the storage bound")]
    TranscriptTooLarge(usize),
    #[error("content body storage key is unsafe")]
    UnsafeStorageKey,
    #[error("existing content-addressed body is not the expected regular file")]
    UnsafeExistingBody,
    #[error("media file read failed")]
    Read(#[source] std::io::Error),
    #[error("media file write failed")]
    Write(#[source] std::io::Error),
    #[error("transcript object-store operation failed: {0}")]
    ObjectStore(String),
}

impl MediaFileStoreError {
    /// Only filesystem and remote object-store I/O can succeed on a later attempt; validation
    /// outcomes repeat for the same input, so retrying would only repeat the download and paid
    /// transcription.
    pub(crate) const fn is_retryable(&self) -> bool {
        matches!(
            self,
            Self::CurrentDir(_) | Self::Read(_) | Self::Write(_) | Self::ObjectStore(_)
        )
    }
}

impl From<ObjectStorageOpError> for MediaFileStoreError {
    fn from(error: ObjectStorageOpError) -> Self {
        match error {
            ObjectStorageOpError::UnsafeKey(_) | ObjectStorageOpError::UnsafeLocalPath(_) => {
                Self::UnsafeStorageKey
            }
            ObjectStorageOpError::UnsafeExistingObject(_) => Self::UnsafeExistingBody,
            ObjectStorageOpError::TooLarge { maximum_bytes, .. } => {
                Self::TranscriptTooLarge(maximum_bytes.saturating_add(1))
            }
            ObjectStorageOpError::File { source, .. } => Self::Write(source),
            ObjectStorageOpError::ObjectStore(message) => Self::ObjectStore(message),
        }
    }
}

#[cfg(test)]
mod tests {
    use std::path::PathBuf;
    use std::sync::Arc;

    use newsly_providers::ObjectStorageBackend;
    use object_store::memory::InMemory;
    use uuid::Uuid;

    use super::{MediaFileStore, MediaFileStoreError};

    #[test]
    fn only_storage_io_failures_are_retryable() {
        assert!(MediaFileStoreError::Write(std::io::Error::other("disk")).is_retryable());
        assert!(MediaFileStoreError::ObjectStore("timeout".to_owned()).is_retryable());
        assert!(!MediaFileStoreError::EmptyTranscript.is_retryable());
        assert!(!MediaFileStoreError::TranscriptTooLarge(3_000_000).is_retryable());
        assert!(!MediaFileStoreError::UnsafeStorageKey.is_retryable());
    }

    #[tokio::test]
    async fn stages_canonical_content_body_pointer() {
        let root = std::env::temp_dir().join(format!("newsly-media-{}", Uuid::new_v4()));
        let store = MediaFileStore::new(
            root.join("scratch"),
            root.join("tweet"),
            root.join("body"),
            PathBuf::from("content"),
            1_000_000,
        )
        .unwrap();
        let pointer = store
            .stage_transcript(42, "  hello podcast \n")
            .await
            .unwrap();
        assert_eq!(pointer.storage_provider, "local");
        assert!(pointer.storage_key.starts_with("content/42/source-"));
        assert_eq!(pointer.char_count, 13);
        assert_eq!(pointer.byte_size, 13);
        assert_eq!(
            tokio::fs::read(root.join("body").join(&pointer.storage_key))
                .await
                .unwrap(),
            b"hello podcast"
        );
        let _ = tokio::fs::remove_dir_all(root).await;
    }

    #[tokio::test]
    async fn stages_s3_compatible_transcript_pointer() {
        let root = std::env::temp_dir().join(format!("newsly-media-s3-{}", Uuid::new_v4()));
        let backend = ObjectStorageBackend::s3(Arc::new(InMemory::new()), "newsly-bucket");
        let store = MediaFileStore::from_backend(
            root.join("scratch"),
            root.join("tweet"),
            backend.clone(),
            PathBuf::from("content"),
            1_000_000,
        )
        .unwrap();
        let pointer = store
            .stage_transcript(42, "  hello s3 podcast \n")
            .await
            .unwrap();
        assert_eq!(pointer.storage_provider, "s3_compatible");
        assert_eq!(pointer.storage_bucket.as_deref(), Some("newsly-bucket"));
        assert_eq!(
            backend
                .read_bounded(&pointer.storage_key, 1024)
                .await
                .unwrap(),
            Some(b"hello s3 podcast".to_vec())
        );
    }

    #[test]
    fn rejects_unsafe_body_prefix() {
        assert!(
            MediaFileStore::new(
                PathBuf::from("scratch"),
                PathBuf::from("tweet"),
                PathBuf::from("body"),
                PathBuf::from("../escape"),
                1,
            )
            .is_err()
        );
    }
}
