use std::path::{Component, Path, PathBuf};

use newsly_db::MediaTranscriptPointer;
use sha2::{Digest, Sha256};
use thiserror::Error;
use tokio::fs;
use uuid::Uuid;

const MAX_TRANSCRIPT_BYTES: usize = 2_000_000;

#[derive(Debug, Clone)]
pub struct MediaFileStore {
    scratch_root: PathBuf,
    tweet_media_root: PathBuf,
    body_root: PathBuf,
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
    /// Creates bounded local media/body storage. All roots are made absolute once so durable
    /// metadata never depends on a later process working directory.
    pub fn new(
        scratch_root: PathBuf,
        tweet_media_root: PathBuf,
        body_root: PathBuf,
        body_prefix: PathBuf,
        max_media_bytes: u64,
    ) -> Result<Self, MediaFileStoreError> {
        if max_media_bytes == 0 {
            return Err(MediaFileStoreError::InvalidMediaLimit);
        }
        validate_relative_path(&body_prefix)?;
        let current_directory = std::env::current_dir().map_err(MediaFileStoreError::CurrentDir)?;
        Ok(Self {
            scratch_root: absolute_path(scratch_root, &current_directory),
            tweet_media_root: absolute_path(tweet_media_root, &current_directory),
            body_root: absolute_path(body_root, &current_directory),
            body_prefix,
            max_media_bytes,
        })
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
        validate_relative_path(&storage_key_path)?;
        let storage_key = storage_key(&storage_key_path)?;
        let destination = self.body_root.join(&storage_key_path);
        let parent = destination
            .parent()
            .ok_or(MediaFileStoreError::UnsafeStorageKey)?;
        fs::create_dir_all(parent)
            .await
            .map_err(MediaFileStoreError::Write)?;
        match fs::symlink_metadata(&destination).await {
            Ok(metadata) => {
                if metadata.file_type().is_symlink()
                    || !metadata.is_file()
                    || metadata.len() != encoded.len() as u64
                    || fs::read(&destination)
                        .await
                        .map_err(MediaFileStoreError::Read)?
                        != encoded
                {
                    return Err(MediaFileStoreError::UnsafeExistingBody);
                }
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                let temporary = parent.join(format!(".{}.tmp", Uuid::new_v4()));
                fs::write(&temporary, encoded)
                    .await
                    .map_err(MediaFileStoreError::Write)?;
                if let Err(error) = fs::rename(&temporary, &destination).await {
                    let _ = fs::remove_file(&temporary).await;
                    return Err(MediaFileStoreError::Write(error));
                }
            }
            Err(error) => return Err(MediaFileStoreError::Read(error)),
        }
        Ok(MediaTranscriptPointer {
            storage_provider: "local".to_owned(),
            storage_bucket: None,
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

fn validate_relative_path(path: &Path) -> Result<(), MediaFileStoreError> {
    if path.as_os_str().is_empty()
        || path.is_absolute()
        || path
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return Err(MediaFileStoreError::UnsafeStorageKey);
    }
    Ok(())
}

fn storage_key(path: &Path) -> Result<String, MediaFileStoreError> {
    path.components()
        .map(|component| match component {
            Component::Normal(part) => part
                .to_str()
                .map(str::to_owned)
                .ok_or(MediaFileStoreError::UnsafeStorageKey),
            _ => Err(MediaFileStoreError::UnsafeStorageKey),
        })
        .collect::<Result<Vec<_>, _>>()
        .map(|parts| parts.join("/"))
}

fn sha256_hex(value: &[u8]) -> String {
    let digest = Sha256::digest(value);
    let mut encoded = String::with_capacity(digest.len() * 2);
    for byte in digest {
        encoded.push(
            char::from_digit(u32::from(byte >> 4), 16)
                .expect("a four-bit nibble is always hexadecimal"),
        );
        encoded.push(
            char::from_digit(u32::from(byte & 0x0f), 16)
                .expect("a four-bit nibble is always hexadecimal"),
        );
    }
    encoded
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
}

#[cfg(test)]
mod tests {
    use std::path::PathBuf;

    use uuid::Uuid;

    use super::MediaFileStore;

    #[tokio::test]
    async fn stages_python_compatible_content_body_pointer() {
        let root = std::env::temp_dir().join(format!("newsly-media-{}", Uuid::new_v4()));
        let store = MediaFileStore::new(
            root.join("scratch"),
            root.join("tweet"),
            root.join("body"),
            PathBuf::from("content"),
            1_000_000,
        )
        .unwrap();
        let pointer = store.stage_transcript(42, "hello podcast").await.unwrap();
        assert_eq!(pointer.storage_provider, "local");
        assert!(pointer.storage_key.starts_with("content/42/source-"));
        assert_eq!(pointer.char_count, 13);
        let _ = tokio::fs::remove_dir_all(root).await;
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
