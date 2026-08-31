use std::future::Future;
use std::sync::Arc;

use newsly_agent_runtime::ProviderUsage;
use newsly_db::{
    AudioEpisodeRecord, AudioEpisodeScriptUsage, AudioEpisodeTtsUsage,
    PrepareAudioEpisodeGenerationOutcome, prepare_audio_episode_generation,
};
use newsly_providers::{
    AudioEpisodeGateway, AudioEpisodeGatewayError, AudioEpisodeScript, AudioEpisodeSpeaker,
    AudioEpisodeTurn,
};
use newsly_queue::{OwnedWorkPlan, TaskResult, TaskType};
use serde_json::Value;
use sqlx::PgPool;

use crate::{HandlerExecution, HandlerFuture, LeaseHealth, TaskHandler};

use super::finalizer::AudioEpisodeFinalizer;
use super::model::{
    AudioEpisodeFinalizationPlan, AudioEpisodeMutation, PreparedAudioEpisodeAttempt, PreparedScript,
};
use super::storage::AudioEpisodeFileStore;

#[derive(Debug, Clone)]
pub struct AudioEpisodeWorkerServices {
    pool: PgPool,
    gateway: AudioEpisodeGateway,
    file_store: AudioEpisodeFileStore,
    max_retries: i32,
}

impl AudioEpisodeWorkerServices {
    pub const fn new(
        pool: PgPool,
        gateway: AudioEpisodeGateway,
        file_store: AudioEpisodeFileStore,
        max_retries: i32,
    ) -> Self {
        Self {
            pool,
            gateway,
            file_store,
            max_retries,
        }
    }
}

#[derive(Debug, Clone)]
pub struct GenerateAudioEpisodeHandler {
    services: Arc<AudioEpisodeWorkerServices>,
}

impl GenerateAudioEpisodeHandler {
    pub fn new(services: Arc<AudioEpisodeWorkerServices>) -> Self {
        Self { services }
    }
}

impl TaskHandler for GenerateAudioEpisodeHandler {
    fn task_type(&self) -> TaskType {
        TaskType::GenerateAudioEpisode
    }

    fn execute(&self, plan: Arc<OwnedWorkPlan>, lease: LeaseHealth) -> HandlerFuture<'_> {
        let services = Arc::clone(&self.services);
        Box::pin(async move { execute_generation(&services, &plan, lease).await })
    }
}

async fn execute_generation(
    services: &AudioEpisodeWorkerServices,
    plan: &OwnedWorkPlan,
    mut lease: LeaseHealth,
) -> HandlerExecution {
    let Some(user_id) = plan.payload.get("user_id").and_then(Value::as_i64) else {
        return HandlerExecution::from_result(TaskResult::fail(
            Some("Missing user_id in generate_audio_episode payload".to_owned()),
            false,
        ));
    };
    let Some(audio_episode_id) = plan.payload.get("audio_episode_id").and_then(Value::as_i64)
    else {
        return HandlerExecution::from_result(TaskResult::fail(
            Some("Missing audio_episode_id in generate_audio_episode payload".to_owned()),
            false,
        ));
    };

    let prepared = {
        let mut transaction = match services.pool.begin().await {
            Ok(transaction) => transaction,
            Err(error) => return plain_failure(error.to_string(), true),
        };
        let outcome =
            match prepare_audio_episode_generation(&mut transaction, user_id, audio_episode_id)
                .await
            {
                Ok(outcome) => outcome,
                Err(error) => return plain_failure(error.to_string(), true),
            };
        if let Err(error) = transaction.commit().await {
            return plain_failure(error.to_string(), true);
        }
        match outcome {
            PrepareAudioEpisodeGenerationOutcome::Prepared(episode) => {
                match prepared_attempt(plan, episode) {
                    Ok(attempt) => attempt,
                    Err(message) => return plain_failure(message, false),
                }
            }
            PrepareAudioEpisodeGenerationOutcome::AlreadyCompleted => {
                return HandlerExecution::from_result(TaskResult::ok());
            }
            PrepareAudioEpisodeGenerationOutcome::AlreadyProcessing => {
                return HandlerExecution::from_result(TaskResult::defer(15));
            }
            PrepareAudioEpisodeGenerationOutcome::NotFound => {
                return plain_failure("Audio episode not found", false);
            }
        }
    };

    let script = match prepare_script(services, &prepared, &mut lease).await {
        Ok(script) => script,
        Err(GenerationStageError::LeaseLost) => return lease_lost_failure(),
        Err(GenerationStageError::Provider(error)) => {
            return failed_execution(
                services,
                prepared,
                error.to_string(),
                error.retryable(),
                None,
            );
        }
        Err(GenerationStageError::Input(message)) => {
            return failed_execution(services, prepared, message, false, None);
        }
    };

    let dialogue = match provider_call(
        &mut lease,
        services.gateway.synthesize_dialogue(&script.script.turns),
    )
    .await
    {
        Ok(Ok(dialogue)) => dialogue,
        Ok(Err(error)) => {
            let retryable = error.retryable();
            return failed_execution(
                services,
                prepared,
                error.to_string(),
                retryable,
                Some(script),
            );
        }
        Err(LeaseLost) => return lease_lost_failure(),
    };
    if lease.ownership_lost() {
        return lease_lost_failure();
    }
    let audio_storage_path = match services
        .file_store
        .write(
            prepared.audio_episode_id,
            prepared.task_id,
            prepared.retry_count,
            &dialogue.audio_bytes,
        )
        .await
    {
        Ok(path) => path,
        Err(error) => {
            let retryable = error.retryable();
            return failed_execution(
                services,
                prepared,
                error.to_string(),
                retryable,
                Some(script),
            );
        }
    };
    if lease.ownership_lost() {
        return lease_lost_failure();
    }
    let tts_usage = AudioEpisodeTtsUsage {
        model: services.gateway.tts_model().to_owned(),
        request_count: dialogue.request_count,
        text_chars: dialogue.text_chars,
    };
    HandlerExecution::with_finalizer(
        TaskResult::ok(),
        AudioEpisodeFinalizer::new(AudioEpisodeFinalizationPlan {
            attempt: prepared,
            mutation: AudioEpisodeMutation::Complete {
                script,
                audio_storage_path,
                tts_usage,
            },
        }),
    )
}

fn prepared_attempt(
    plan: &OwnedWorkPlan,
    episode: AudioEpisodeRecord,
) -> Result<PreparedAudioEpisodeAttempt, String> {
    let prepared_started_at = episode
        .started_at
        .ok_or_else(|| "Prepared audio episode has no generation fence".to_owned())?;
    Ok(PreparedAudioEpisodeAttempt {
        task_id: plan.task_id,
        retry_count: plan.retry_count,
        user_id: episode.user_id,
        audio_episode_id: episode.id,
        prepared_started_at,
        kind: episode.kind,
        fallback_title: episode.title,
        source_content_id: episode.source_content_id,
        source_snapshot: episode.source_snapshot,
        existing_script: episode.script,
        existing_script_text: episode.script_text,
        existing_model: episode.model,
    })
}

async fn prepare_script(
    services: &AudioEpisodeWorkerServices,
    attempt: &PreparedAudioEpisodeAttempt,
    lease: &mut LeaseHealth,
) -> Result<PreparedScript, GenerationStageError> {
    if attempt.kind == "briefing_narration" {
        let text = attempt
            .source_snapshot
            .get("script_text")
            .and_then(Value::as_str)
            .or(attempt.existing_script_text.as_deref())
            .map(str::trim)
            .filter(|text| !text.is_empty())
            .ok_or_else(|| {
                GenerationStageError::Input(
                    "Preauthored audio episode narration is empty".to_owned(),
                )
            })?
            .to_owned();
        let script = AudioEpisodeScript {
            title: attempt.fallback_title.clone(),
            estimated_duration_seconds: estimate_duration_seconds(&text).max(1),
            turns: vec![AudioEpisodeTurn {
                speaker: AudioEpisodeSpeaker::Host,
                text: text.clone(),
            }],
        };
        return prepared_script(script, text, "deterministic".to_owned(), None)
            .map_err(GenerationStageError::Input);
    }
    if let Some(existing) = attempt.existing_script.clone()
        && let Ok(script) = serde_json::from_value::<AudioEpisodeScript>(existing)
        && valid_existing_script(&script)
    {
        let text = attempt
            .existing_script_text
            .clone()
            .unwrap_or_else(|| script.render_text());
        let model = attempt
            .existing_model
            .clone()
            .unwrap_or_else(|| services.gateway.script_model().to_owned());
        return prepared_script(script, text, model, None).map_err(GenerationStageError::Input);
    }
    let generated = match provider_call(
        lease,
        services
            .gateway
            .generate_script(&attempt.kind, &attempt.source_snapshot),
    )
    .await
    {
        Ok(Ok(generated)) => generated,
        Ok(Err(error)) => return Err(GenerationStageError::Provider(error)),
        Err(LeaseLost) => return Err(GenerationStageError::LeaseLost),
    };
    let usage = script_usage(&generated.model, &generated.usage);
    let text = generated.script.render_text();
    prepared_script(generated.script, text, generated.model, Some(usage))
        .map_err(GenerationStageError::Input)
}

fn prepared_script(
    mut script: AudioEpisodeScript,
    script_text: String,
    model: String,
    usage: Option<AudioEpisodeScriptUsage>,
) -> Result<PreparedScript, String> {
    if script.title.trim().is_empty() || script.turns.is_empty() || script_text.trim().is_empty() {
        return Err("Audio episode script is empty".to_owned());
    }
    script.title = script.title.trim().chars().take(255).collect();
    let duration_seconds = estimate_duration_seconds(&script_text).max(1);
    script.estimated_duration_seconds = duration_seconds;
    let script_json = serde_json::to_value(&script)
        .map_err(|error| format!("Could not serialize audio episode script: {error}"))?;
    Ok(PreparedScript {
        script,
        script_json,
        script_text,
        model,
        duration_seconds,
        usage,
    })
}

fn valid_existing_script(script: &AudioEpisodeScript) -> bool {
    !script.title.trim().is_empty()
        && !script.turns.is_empty()
        && script.turns.len() <= 100
        && script.turns.iter().all(|turn| !turn.text.trim().is_empty())
}

fn script_usage(model: &str, usage: &ProviderUsage) -> AudioEpisodeScriptUsage {
    let provider = model
        .split_once(':')
        .map_or_else(|| "unknown".to_owned(), |(provider, _)| provider.to_owned());
    AudioEpisodeScriptUsage {
        provider,
        model: model.to_owned(),
        request_count: bounded_i32(usage.request_count),
        input_tokens: bounded_i32(usage.input_tokens),
        output_tokens: bounded_i32(usage.output_tokens),
        cache_read_tokens: bounded_i32(usage.cached_input_tokens),
        cache_write_tokens: bounded_i32(usage.cache_write_tokens),
    }
}

fn bounded_i32(value: u64) -> i32 {
    i32::try_from(value).unwrap_or(i32::MAX)
}

fn estimate_duration_seconds(text: &str) -> i32 {
    let words = i64::try_from(text.split_whitespace().count()).unwrap_or(i64::MAX);
    i32::try_from((words.saturating_mul(60).saturating_add(144)) / 145).unwrap_or(i32::MAX)
}

fn failed_execution(
    services: &AudioEpisodeWorkerServices,
    attempt: PreparedAudioEpisodeAttempt,
    error_message: String,
    retryable: bool,
    generated_script: Option<PreparedScript>,
) -> HandlerExecution {
    let retry_scheduled = retryable && attempt.retry_count < services.max_retries.max(0);
    HandlerExecution::with_finalizer(
        TaskResult::fail(Some(error_message.clone()), retryable),
        AudioEpisodeFinalizer::new(AudioEpisodeFinalizationPlan {
            attempt,
            mutation: AudioEpisodeMutation::Failed {
                error_message,
                retry_scheduled,
                generated_script,
            },
        }),
    )
}

fn plain_failure(message: impl Into<String>, retryable: bool) -> HandlerExecution {
    HandlerExecution::from_result(TaskResult::fail(Some(message.into()), retryable))
}

fn lease_lost_failure() -> HandlerExecution {
    HandlerExecution::from_result(TaskResult::fail(
        Some("Audio episode generation lease was lost".to_owned()),
        true,
    ))
}

async fn provider_call<F, T, E>(
    lease: &mut LeaseHealth,
    future: F,
) -> Result<Result<T, E>, LeaseLost>
where
    F: Future<Output = Result<T, E>>,
{
    tokio::pin!(future);
    tokio::select! {
        biased;
        () = lease.wait_for_ownership_loss() => Err(LeaseLost),
        result = &mut future => Ok(result),
    }
}

#[derive(Debug)]
struct LeaseLost;

#[derive(Debug)]
enum GenerationStageError {
    LeaseLost,
    Provider(AudioEpisodeGatewayError),
    Input(String),
}
