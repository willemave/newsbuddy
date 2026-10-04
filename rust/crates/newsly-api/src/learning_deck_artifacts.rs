use std::path::{Path, PathBuf};
use std::sync::Arc;

use futures_util::StreamExt as _;
use object_store::Error as ObjectStoreError;
use object_store::path::Path as ObjectPath;
use object_store::{ObjectStore, ObjectStoreExt};
use thiserror::Error;

#[derive(Clone)]
pub(super) enum LearningDeckArtifactStore {
    Local { root: PathBuf },
    S3 { store: Arc<dyn ObjectStore> },
}

impl std::fmt::Debug for LearningDeckArtifactStore {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Local { root } => formatter
                .debug_struct("LocalLearningDeckArtifactStore")
                .field("root", root)
                .finish(),
            Self::S3 { .. } => formatter
                .debug_struct("S3LearningDeckArtifactStore")
                .finish_non_exhaustive(),
        }
    }
}

impl LearningDeckArtifactStore {
    pub(super) fn from_content_store(
        store: &crate::content_body_storage::ContentBodyStore,
    ) -> Self {
        match store {
            crate::content_body_storage::ContentBodyStore::Local { root } => {
                Self::Local { root: root.clone() }
            }
            crate::content_body_storage::ContentBodyStore::S3 { store } => Self::S3 {
                store: Arc::clone(store),
            },
        }
    }

    /// Deletes every immutable object key after the database deletion has committed.
    ///
    /// Missing objects are idempotent success.
    pub(super) async fn delete_many(
        &self,
        object_keys: &[String],
    ) -> Result<(), LearningDeckArtifactError> {
        for key in object_keys {
            self.delete(key).await?;
        }
        Ok(())
    }

    /// Reads an immutable artifact without allowing the object to exceed the route-specific cap.
    pub(super) async fn read_bounded(
        &self,
        key: &str,
        maximum_bytes: usize,
    ) -> Result<Option<Vec<u8>>, LearningDeckArtifactError> {
        validate_object_key(key)?;
        match self {
            Self::Local { root } => read_local_bounded(root, key, maximum_bytes).await,
            Self::S3 { store } => {
                let path = ObjectPath::parse(key).map_err(|error| {
                    LearningDeckArtifactError::UnsafeObjectKey(error.to_string())
                })?;
                let result = match store.get(&path).await {
                    Ok(result) => result,
                    Err(ObjectStoreError::NotFound { .. }) => return Ok(None),
                    Err(error) => {
                        return Err(LearningDeckArtifactError::ObjectStore(error.to_string()));
                    }
                };
                if result.meta.size > maximum_bytes as u64 {
                    return Err(LearningDeckArtifactError::TooLarge {
                        key: key.to_owned(),
                        maximum_bytes,
                    });
                }
                let initial_capacity = usize::try_from(result.meta.size)
                    .expect("artifact size is bounded by maximum_bytes");
                let mut bytes = Vec::with_capacity(initial_capacity);
                let mut stream = result.into_stream();
                while let Some(chunk) = stream.next().await {
                    let chunk = chunk.map_err(|error| {
                        LearningDeckArtifactError::ObjectStore(error.to_string())
                    })?;
                    if bytes.len().saturating_add(chunk.len()) > maximum_bytes {
                        return Err(LearningDeckArtifactError::TooLarge {
                            key: key.to_owned(),
                            maximum_bytes,
                        });
                    }
                    bytes.extend_from_slice(&chunk);
                }
                Ok(Some(bytes))
            }
        }
    }

    async fn delete(&self, key: &str) -> Result<(), LearningDeckArtifactError> {
        validate_object_key(key)?;
        match self {
            Self::Local { root } => delete_local(root, key).await,
            Self::S3 { store } => {
                let path = ObjectPath::parse(key).map_err(|error| {
                    LearningDeckArtifactError::UnsafeObjectKey(error.to_string())
                })?;
                match store.delete(&path).await {
                    Ok(()) | Err(ObjectStoreError::NotFound { .. }) => Ok(()),
                    Err(error) => Err(LearningDeckArtifactError::ObjectStore(error.to_string())),
                }
            }
        }
    }
}

async fn read_local_bounded(
    root: &Path,
    key: &str,
    maximum_bytes: usize,
) -> Result<Option<Vec<u8>>, LearningDeckArtifactError> {
    let slice = newsly_providers::read_local_object(root, key, maximum_bytes)
        .await
        .map_err(|error| match error {
            newsly_providers::LocalStorageError::UnsafePath(path) => {
                LearningDeckArtifactError::UnsafeLocalPath(path)
            }
            newsly_providers::LocalStorageError::File { path, source } => {
                LearningDeckArtifactError::File { path, source }
            }
        })?;
    slice
        .map(|slice| {
            if slice.truncated {
                return Err(LearningDeckArtifactError::TooLarge {
                    key: key.to_owned(),
                    maximum_bytes,
                });
            }
            Ok(slice.bytes)
        })
        .transpose()
}

async fn delete_local(root: &Path, key: &str) -> Result<(), LearningDeckArtifactError> {
    newsly_providers::delete_local_object(root, key)
        .await
        .map_err(|error| match error {
            newsly_providers::LocalStorageError::UnsafePath(path) => {
                LearningDeckArtifactError::UnsafeLocalPath(path)
            }
            newsly_providers::LocalStorageError::File { path, source } => {
                LearningDeckArtifactError::File { path, source }
            }
        })
}

fn validate_object_key(key: &str) -> Result<(), LearningDeckArtifactError> {
    if !newsly_providers::is_safe_object_key(key) {
        return Err(LearningDeckArtifactError::UnsafeObjectKey(key.to_owned()));
    }
    Ok(())
}

#[derive(Debug, Error)]
pub(super) enum LearningDeckArtifactError {
    #[error("unsafe local artifact path {0:?}")]
    UnsafeLocalPath(PathBuf),
    #[error("unsafe Learning Deck object key {0:?}")]
    UnsafeObjectKey(String),
    #[error("Learning Deck object {key:?} exceeds the {maximum_bytes}-byte response limit")]
    TooLarge { key: String, maximum_bytes: usize },
    #[error("Learning Deck object-store operation failed: {0}")]
    ObjectStore(String),
    #[error("Learning Deck local artifact operation failed for {path:?}")]
    File {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn local_reads_are_bounded_and_missing_objects_are_absent() {
        let temporary = tempfile::tempdir().unwrap();
        let root = temporary.path().to_path_buf();
        tokio::fs::create_dir_all(root.join("learning/deck"))
            .await
            .unwrap();
        tokio::fs::write(root.join("learning/deck/index.html"), b"deck")
            .await
            .unwrap();
        let store = LearningDeckArtifactStore::Local { root };

        assert_eq!(
            store
                .read_bounded("learning/deck/index.html", 4)
                .await
                .unwrap(),
            Some(b"deck".to_vec())
        );
        assert!(matches!(
            store.read_bounded("learning/deck/index.html", 3).await,
            Err(LearningDeckArtifactError::TooLarge { .. })
        ));
        assert_eq!(
            store
                .read_bounded("learning/deck/missing.html", 4)
                .await
                .unwrap(),
            None
        );
    }

    #[tokio::test]
    async fn local_reads_reject_parent_components() {
        let temporary = tempfile::tempdir().unwrap();
        let store = LearningDeckArtifactStore::Local {
            root: temporary.path().to_path_buf(),
        };
        assert!(matches!(
            store.read_bounded("../outside", 10).await,
            Err(LearningDeckArtifactError::UnsafeObjectKey(_))
        ));
    }
}
