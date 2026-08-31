use newsly_db::{LearningDeckSourceMaterial, LearningDeckTaskSnapshot};
use serde_json::{Map, Value};
use thiserror::Error;

use super::artifacts::{LearningDeckArtifactError, LearningDeckArtifactStore};

#[derive(Debug, Clone, PartialEq)]
pub(super) enum LearningDeckSourceLoad {
    Ready {
        full: Map<String, Value>,
        persistable: Map<String, Value>,
    },
    Missing {
        waiting_message: String,
    },
}

#[derive(Debug, Clone)]
pub(super) struct LearningDeckSourceLoader {
    artifacts: LearningDeckArtifactStore,
}

impl LearningDeckSourceLoader {
    pub(super) const fn new(artifacts: LearningDeckArtifactStore) -> Self {
        Self { artifacts }
    }

    /// Resolves a copied source descriptor without retaining a database connection.
    ///
    /// Object storage is external I/O, so callers must commit the preparation transaction before
    /// entering this method. The persistable snapshot deliberately omits `body_text`.
    pub(super) async fn load(
        &self,
        task: &LearningDeckTaskSnapshot,
    ) -> Result<LearningDeckSourceLoad, LearningDeckSourceLoadError> {
        match &task.source {
            LearningDeckSourceMaterial::Github { snapshot } => Ok(ready(snapshot.clone())),
            LearningDeckSourceMaterial::Content {
                snapshot,
                content_status,
                body,
                ..
            } => {
                let stored = match body.storage_key.as_deref() {
                    Some(key) => self.artifacts.get_text(key).await?,
                    None => None,
                };
                let body_text = stored
                    .as_deref()
                    .and_then(clean_text)
                    .or_else(|| body.fallback_text.as_deref().and_then(clean_text));
                let Some(body_text) = body_text else {
                    let waiting_message = if content_status == "completed" {
                        "Source text is not available yet"
                    } else {
                        "Source content is still processing"
                    };
                    return Ok(LearningDeckSourceLoad::Missing {
                        waiting_message: waiting_message.to_owned(),
                    });
                };
                let mut full = snapshot.clone();
                full.insert("body_text".to_owned(), Value::from(body_text));
                Ok(LearningDeckSourceLoad::Ready {
                    persistable: snapshot.clone(),
                    full,
                })
            }
        }
    }
}

fn ready(snapshot: Map<String, Value>) -> LearningDeckSourceLoad {
    LearningDeckSourceLoad::Ready {
        persistable: snapshot.clone(),
        full: snapshot,
    }
}

fn clean_text(value: &str) -> Option<String> {
    (!value.trim().is_empty()).then(|| value.to_owned())
}

#[derive(Debug, Error)]
pub(super) enum LearningDeckSourceLoadError {
    #[error(transparent)]
    Artifact(#[from] LearningDeckArtifactError),
}
