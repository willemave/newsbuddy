use std::path::PathBuf;

use newsly_providers::{ObjectStorageBackend, ObjectStorageOpError};
use thiserror::Error;

#[derive(Debug, Clone)]
pub(super) struct LearningDeckArtifactStore {
    backend: ObjectStorageBackend,
}

impl LearningDeckArtifactStore {
    pub(super) fn from_content_store(
        store: &crate::content_body_storage::ContentBodyStore,
    ) -> Self {
        Self {
            backend: store.backend(),
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
            self.backend.delete(key).await?;
        }
        Ok(())
    }

    /// Reads an immutable artifact without allowing the object to exceed the route-specific cap.
    pub(super) async fn read_bounded(
        &self,
        key: &str,
        maximum_bytes: usize,
    ) -> Result<Option<Vec<u8>>, LearningDeckArtifactError> {
        Ok(self.backend.read_bounded(key, maximum_bytes).await?)
    }
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

impl From<ObjectStorageOpError> for LearningDeckArtifactError {
    fn from(error: ObjectStorageOpError) -> Self {
        match error {
            ObjectStorageOpError::UnsafeKey(key) => Self::UnsafeObjectKey(key),
            ObjectStorageOpError::UnsafeLocalPath(path)
            | ObjectStorageOpError::UnsafeExistingObject(path) => Self::UnsafeLocalPath(path),
            ObjectStorageOpError::TooLarge { key, maximum_bytes } => {
                Self::TooLarge { key, maximum_bytes }
            }
            ObjectStorageOpError::File { path, source } => Self::File { path, source },
            ObjectStorageOpError::ObjectStore(message) => Self::ObjectStore(message),
        }
    }
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
        let store = LearningDeckArtifactStore {
            backend: ObjectStorageBackend::local(root).unwrap(),
        };

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
        let store = LearningDeckArtifactStore {
            backend: ObjectStorageBackend::local(temporary.path().to_path_buf()).unwrap(),
        };
        assert!(matches!(
            store.read_bounded("../outside", 10).await,
            Err(LearningDeckArtifactError::UnsafeObjectKey(_))
        ));
    }
}
