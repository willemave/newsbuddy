use std::path::{Component, Path, PathBuf};

use thiserror::Error;

#[derive(Debug, Clone)]
pub struct AudioEpisodeFileStore {
    root: PathBuf,
}

impl AudioEpisodeFileStore {
    pub fn new(root: PathBuf) -> Result<Self, AudioEpisodeFileStoreError> {
        if !root.is_absolute()
            || root == Path::new("/")
            || root.components().any(|component| {
                matches!(
                    component,
                    Component::ParentDir | Component::CurDir | Component::Prefix(_)
                )
            })
        {
            return Err(AudioEpisodeFileStoreError::UnsafeRoot(root));
        }
        Ok(Self { root })
    }

    pub async fn write(
        &self,
        audio_episode_id: i64,
        task_id: i64,
        retry_count: i32,
        bytes: &[u8],
    ) -> Result<String, AudioEpisodeFileStoreError> {
        if audio_episode_id <= 0 || task_id <= 0 || retry_count < 0 || bytes.is_empty() {
            return Err(AudioEpisodeFileStoreError::InvalidWrite);
        }
        let directory = self.root.join("audio_episodes");
        tokio::fs::create_dir_all(&directory)
            .await
            .map_err(|source| AudioEpisodeFileStoreError::Io {
                path: directory.clone(),
                source,
            })?;
        let final_path = directory.join(format!(
            "audio-episode-{audio_episode_id}-task-{task_id}-attempt-{retry_count}.mp3"
        ));
        let temporary_path = directory.join(format!(
            ".audio-episode-{audio_episode_id}-task-{task_id}-attempt-{retry_count}.tmp"
        ));
        tokio::fs::write(&temporary_path, bytes)
            .await
            .map_err(|source| AudioEpisodeFileStoreError::Io {
                path: temporary_path.clone(),
                source,
            })?;
        tokio::fs::rename(&temporary_path, &final_path)
            .await
            .map_err(|source| AudioEpisodeFileStoreError::Io {
                path: final_path.clone(),
                source,
            })?;
        Ok(final_path.to_string_lossy().into_owned())
    }
}

#[derive(Debug, Error)]
pub enum AudioEpisodeFileStoreError {
    #[error("unsafe audio media root {0:?}")]
    UnsafeRoot(PathBuf),
    #[error("invalid audio file write request")]
    InvalidWrite,
    #[error("audio file operation failed for {path:?}")]
    Io {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
}

impl AudioEpisodeFileStoreError {
    pub const fn retryable(&self) -> bool {
        matches!(self, Self::Io { .. })
    }
}
