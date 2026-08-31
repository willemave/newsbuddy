use std::env;
use std::path::{Component, Path, PathBuf};

use thiserror::Error;
use tokio::fs::File;

#[derive(Debug, Clone)]
pub(super) struct AudioStorage {
    root: PathBuf,
}

impl AudioStorage {
    pub(super) fn from_environment() -> Result<Self, AudioStorageError> {
        let configured = env::var_os("MEDIA_BASE_DIR")
            .map_or_else(|| PathBuf::from("data/media"), PathBuf::from);
        let root = if configured.is_absolute() {
            configured
        } else {
            env::current_dir()?.join(configured)
        };
        validate_root(&root)?;
        Ok(Self { root })
    }

    pub(super) async fn open(&self, stored_path: &str) -> Result<File, AudioStorageError> {
        let path = self.resolve_existing_path(stored_path).await?;
        File::open(&path)
            .await
            .map_err(|source| AudioStorageError::File { path, source })
    }

    async fn resolve_existing_path(&self, stored_path: &str) -> Result<PathBuf, AudioStorageError> {
        let stored_path = Path::new(stored_path);
        if stored_path.as_os_str().is_empty()
            || stored_path.components().any(|component| {
                matches!(
                    component,
                    Component::ParentDir | Component::CurDir | Component::Prefix(_)
                )
            })
        {
            return Err(AudioStorageError::UnsafePath(stored_path.to_path_buf()));
        }
        let requested = if stored_path.is_absolute() {
            stored_path.to_path_buf()
        } else {
            self.root.join(stored_path)
        };
        let canonical_root = tokio::fs::canonicalize(&self.root)
            .await
            .map_err(|source| AudioStorageError::File {
                path: self.root.clone(),
                source,
            })?;
        let canonical_path = tokio::fs::canonicalize(&requested)
            .await
            .map_err(|source| AudioStorageError::File {
                path: requested,
                source,
            })?;
        if !canonical_path.starts_with(canonical_root) {
            return Err(AudioStorageError::UnsafePath(canonical_path));
        }
        Ok(canonical_path)
    }
}

fn validate_root(root: &Path) -> Result<(), AudioStorageError> {
    if root == Path::new("/")
        || !root.is_absolute()
        || root.components().any(|component| {
            matches!(
                component,
                Component::ParentDir | Component::CurDir | Component::Prefix(_)
            )
        })
    {
        return Err(AudioStorageError::UnsafeRoot(root.to_path_buf()));
    }
    Ok(())
}

#[derive(Debug, Error)]
pub(super) enum AudioStorageError {
    #[error("unsafe media root {0:?}")]
    UnsafeRoot(PathBuf),
    #[error("unsafe audio storage path {0:?}")]
    UnsafePath(PathBuf),
    #[error("audio storage file operation failed for {path:?}")]
    File {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
    #[error("could not resolve the current directory")]
    CurrentDirectory(#[from] std::io::Error),
}
