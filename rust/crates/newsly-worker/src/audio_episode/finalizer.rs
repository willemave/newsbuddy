use std::error::Error;

use newsly_db::{
    CheckpointAudioEpisodeScript, CompleteAudioEpisodeGeneration, checkpoint_audio_episode_script,
    complete_audio_episode_generation, fail_audio_episode_generation,
};
use sqlx::{Postgres, Transaction};

use crate::{HandlerFinalizerFuture, TaskFinalizer, TaskFinalizerResult};

use super::model::{AudioEpisodeFinalizationPlan, AudioEpisodeMutation};

#[derive(Debug, Clone)]
pub(super) struct AudioEpisodeFinalizer {
    plan: AudioEpisodeFinalizationPlan,
}

impl AudioEpisodeFinalizer {
    pub(super) const fn new(plan: AudioEpisodeFinalizationPlan) -> Self {
        Self { plan }
    }

    async fn apply_inner(
        &self,
        transaction: &mut Transaction<'static, Postgres>,
    ) -> Result<(), Box<dyn Error + Send + Sync>> {
        let attempt = &self.plan.attempt;
        match &self.plan.mutation {
            AudioEpisodeMutation::Complete {
                script,
                audio_storage_path,
                tts_usage,
            } => {
                complete_audio_episode_generation(
                    transaction,
                    &CompleteAudioEpisodeGeneration {
                        task_id: attempt.task_id,
                        user_id: attempt.user_id,
                        audio_episode_id: attempt.audio_episode_id,
                        source_content_id: attempt.source_content_id,
                        prepared_started_at: attempt.prepared_started_at,
                        title: &script.script.title,
                        script: &script.script_json,
                        script_text: &script.script_text,
                        model: &script.model,
                        audio_storage_path,
                        duration_seconds: script.duration_seconds,
                        script_usage: script.usage.as_ref(),
                        tts_usage,
                    },
                )
                .await?;
            }
            AudioEpisodeMutation::Failed {
                error_message,
                retry_scheduled,
                generated_script,
            } => {
                if let Some(script) = generated_script
                    && let Some(usage) = script.usage.as_ref()
                {
                    checkpoint_audio_episode_script(
                        transaction,
                        &CheckpointAudioEpisodeScript {
                            task_id: attempt.task_id,
                            user_id: attempt.user_id,
                            audio_episode_id: attempt.audio_episode_id,
                            source_content_id: attempt.source_content_id,
                            prepared_started_at: attempt.prepared_started_at,
                            title: &script.script.title,
                            script: &script.script_json,
                            script_text: &script.script_text,
                            model: &script.model,
                            duration_seconds: script.duration_seconds,
                            usage,
                        },
                    )
                    .await?;
                }
                fail_audio_episode_generation(
                    transaction,
                    attempt.user_id,
                    attempt.audio_episode_id,
                    attempt.prepared_started_at,
                    error_message,
                    *retry_scheduled,
                )
                .await?;
            }
        }
        Ok(())
    }
}

impl TaskFinalizer for AudioEpisodeFinalizer {
    fn apply<'a>(
        &'a self,
        transaction: &'a mut Transaction<'static, Postgres>,
    ) -> HandlerFinalizerFuture<'a> {
        Box::pin(async move {
            self.apply_inner(transaction).await?;
            Ok(TaskFinalizerResult::Keep)
        })
    }
}
