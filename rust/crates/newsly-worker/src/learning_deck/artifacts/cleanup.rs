use super::{Duration, LearningDeckArtifactError, LearningDeckArtifactStore};

impl LearningDeckArtifactStore {
    pub(in crate::learning_deck) fn start_cleanup(&self) {
        let store = self.clone();
        tokio::spawn(async move {
            let mut interval = tokio::time::interval(Duration::from_secs(3600));
            loop {
                interval.tick().await;
                if store.pool.is_closed() {
                    break;
                }
                if let Err(error) = store.cleanup_unreferenced().await {
                    tracing::warn!(error = %error, "artifact cleanup will retry");
                }
            }
        });
    }

    async fn cleanup_unreferenced(&self) -> Result<(), LearningDeckArtifactError> {
        for key in newsly_db::artifact_cleanup_candidates(&self.pool, "deck").await? {
            if let Err(error) = self.delete_many(std::slice::from_ref(&key)).await {
                tracing::warn!(error = %error, "artifact deletion retained for retry");
                continue;
            }
            newsly_db::forget_cleaned_artifact(&self.pool, &key).await?;
        }
        Ok(())
    }
}
