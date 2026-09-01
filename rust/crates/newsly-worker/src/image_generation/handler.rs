use std::sync::Arc;

use chrono::Utc;
use newsly_providers::ImageGenerationGateway;
use newsly_queue::{OwnedWorkPlan, TaskResult, TaskType};
use serde_json::Value;
use sqlx::PgPool;

use crate::{HandlerExecution, HandlerFuture, LeaseHealth, TaskHandler};

use super::finalizer::ImageFinalizer;
use super::model::{ImageFinalizationPlan, PreparedImageAttempt};
use super::prompt::{
    build_infographic_prompt, has_generated_image, image_input_fingerprint, runtime_metadata_view,
};
use super::repository::load_image_snapshot;
use super::storage::ImageFileStore;

#[derive(Debug, Clone)]
pub struct ImageWorkerServices {
    pool: PgPool,
    gateway: ImageGenerationGateway,
    file_store: ImageFileStore,
}

impl ImageWorkerServices {
    pub const fn new(
        pool: PgPool,
        gateway: ImageGenerationGateway,
        file_store: ImageFileStore,
    ) -> Self {
        Self {
            pool,
            gateway,
            file_store,
        }
    }
}

#[derive(Debug, Clone)]
pub struct GenerateImageHandler {
    services: Arc<ImageWorkerServices>,
}

impl GenerateImageHandler {
    pub fn new(services: Arc<ImageWorkerServices>) -> Self {
        Self { services }
    }
}

impl TaskHandler for GenerateImageHandler {
    fn task_type(&self) -> TaskType {
        TaskType::GenerateImage
    }

    fn execute(&self, plan: Arc<OwnedWorkPlan>, lease: LeaseHealth) -> HandlerFuture<'_> {
        let services = Arc::clone(&self.services);
        Box::pin(async move { execute_image_generation(&services, &plan, lease).await })
    }
}

async fn execute_image_generation(
    services: &ImageWorkerServices,
    plan: &OwnedWorkPlan,
    mut lease: LeaseHealth,
) -> HandlerExecution {
    let prepared = match prepare_image_generation(services, plan).await {
        Ok(prepared) => prepared,
        Err(finished) => return finished,
    };
    let content_id = prepared.attempt.content.id;
    let task_id = prepared.attempt.task_id;

    let provider_call =
        services
            .gateway
            .generate_infographic(&prepared.prompt, content_id, task_id);
    tokio::pin!(provider_call);
    let generated = tokio::select! {
        result = &mut provider_call => result,
        () = lease.wait_for_ownership_loss() => {
            return plain_failure("lease ownership was lost during image generation", true);
        }
    };
    let generated = match generated {
        Ok(generated) => generated,
        Err(error) => {
            tracing::warn!(
                content_id,
                task_id,
                provider_retryable = error.retryable(),
                error = %error,
                "image generation provider failed"
            );
            // The durable queue retries every provider failure. The gateway's structured retryable
            // flag applies only to callers that perform retries inline.
            return plain_failure(error.to_string(), true);
        }
    };
    if lease.ownership_lost() {
        return plain_failure("lease ownership was lost during image generation", true);
    }
    let staged = match services
        .file_store
        .stage(content_id, task_id, &generated.bytes)
        .await
    {
        Ok(staged) => staged,
        Err(error) => return plain_failure(error.to_string(), true),
    };
    if lease.ownership_lost() {
        staged.cleanup().await;
        return plain_failure(
            "lease ownership was lost while staging generated image",
            true,
        );
    }

    HandlerExecution::with_finalizer(
        TaskResult::ok(),
        ImageFinalizer::new(ImageFinalizationPlan {
            attempt: prepared.attempt,
            staged,
            usage: generated.usage,
            generated_at: Utc::now(),
        }),
    )
}

struct PreparedImageGeneration {
    attempt: PreparedImageAttempt,
    prompt: String,
}

async fn prepare_image_generation(
    services: &ImageWorkerServices,
    plan: &OwnedWorkPlan,
) -> Result<PreparedImageGeneration, HandlerExecution> {
    let Some(content_id) = plan
        .content_id
        .or_else(|| plan.payload.get("content_id").and_then(Value::as_i64))
        .filter(|content_id| *content_id > 0)
    else {
        return Err(plain_failure(
            "generate_image requires a positive content_id",
            false,
        ));
    };
    let force = plan
        .payload
        .get("force")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let mut transaction = services
        .pool
        .begin()
        .await
        .map_err(|error| plain_failure(error.to_string(), true))?;
    let snapshot = load_image_snapshot(&mut transaction, content_id)
        .await
        .map_err(|error| plain_failure(error.to_string(), true))?;
    transaction
        .commit()
        .await
        .map_err(|error| plain_failure(error.to_string(), true))?;
    let Some(snapshot) = snapshot else {
        return Err(plain_failure(
            format!("content {content_id} does not exist"),
            false,
        ));
    };

    if snapshot.content_type == "news" || matches!(snapshot.status.as_str(), "failed" | "skipped") {
        return Err(HandlerExecution::from_result(TaskResult::ok()));
    }
    if matches!(snapshot.content_type.as_str(), "article" | "podcast")
        && !matches!(snapshot.status.as_str(), "awaiting_image" | "completed")
    {
        return Err(plain_failure(
            format!(
                "content {content_id} is not ready for image generation from status {}",
                snapshot.status
            ),
            true,
        ));
    }
    let runtime = runtime_metadata_view(&snapshot.content_metadata);
    if !runtime.get("summary").is_some_and(Value::is_object) {
        tracing::info!(
            content_id,
            "image generation skipped because no summary is available"
        );
        return Err(HandlerExecution::from_result(TaskResult::ok()));
    }
    if !force && has_generated_image(&snapshot.content_metadata) {
        tracing::info!(content_id, "reusing already-generated image");
        return Err(HandlerExecution::from_result(TaskResult::ok()));
    }
    let Some(prompt) = build_infographic_prompt(
        &snapshot.content_type,
        snapshot.title.as_deref(),
        &snapshot.content_metadata,
    ) else {
        return Err(plain_failure(
            format!(
                "content type {} cannot generate an infographic",
                snapshot.content_type
            ),
            false,
        ));
    };
    let input_fingerprint = image_input_fingerprint(&prompt);
    Ok(PreparedImageGeneration {
        attempt: PreparedImageAttempt {
            task_id: plan.task_id,
            content: snapshot,
            input_fingerprint,
            force,
        },
        prompt,
    })
}

fn plain_failure(message: impl Into<String>, retryable: bool) -> HandlerExecution {
    HandlerExecution::from_result(TaskResult::fail(Some(message.into()), retryable))
}
