use std::sync::Arc;
use std::{future::Future, pin::Pin};

use chrono::Utc;
use newsly_providers::{
    ImageGenerationGateway, ImageGenerationUsage, ImageGenerationUsageObserver,
};
use newsly_queue::{OwnedWorkPlan, QueueKernel, TaskResult, TaskType};
use serde_json::Value;
use sqlx::PgPool;

use crate::{HandlerExecution, HandlerFuture, LeaseHealth, TaskHandler};

use super::finalizer::ImageFinalizer;
use super::model::{ImageFinalizationPlan, PreparedImageAttempt};
use super::prompt::{
    build_infographic_prompt, has_generated_image, image_input_fingerprint, runtime_metadata_view,
};
use super::repository::{load_image_snapshot, record_image_generation_usage};
use super::storage::ImageFileStore;

#[derive(Debug, Clone)]
pub struct ImageWorkerServices {
    pool: PgPool,
    gateway: ImageGenerationGateway,
    file_store: ImageFileStore,
    queue: QueueKernel,
    briefing_debounce_seconds: i64,
    briefing_batch_minimum: i64,
}

impl ImageWorkerServices {
    pub fn new(
        pool: PgPool,
        gateway: ImageGenerationGateway,
        file_store: ImageFileStore,
        queue: QueueKernel,
        briefing_debounce_seconds: i64,
        briefing_batch_minimum: i64,
    ) -> Self {
        file_store.start_cleanup(pool.clone());
        Self {
            pool,
            gateway,
            file_store,
            queue,
            briefing_debounce_seconds,
            briefing_batch_minimum,
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

    let usage_observer: Arc<dyn ImageGenerationUsageObserver> =
        Arc::new(DurableImageUsageObserver {
            pool: services.pool.clone(),
            attempt: prepared.attempt.clone(),
        });
    let provider_call = services.gateway.generate_infographic(
        &prepared.prompt,
        content_id,
        task_id,
        usage_observer,
    );
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
            return plain_failure(error.to_string(), error.retryable());
        }
    };
    if lease.ownership_lost() {
        return plain_failure("lease ownership was lost during image generation", true);
    }
    let staged = match services
        .file_store
        .stage(&services.pool, content_id, task_id, &generated.bytes)
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
        ImageFinalizer::new(
            ImageFinalizationPlan {
                attempt: prepared.attempt,
                staged,
                generated_at: Utc::now(),
            },
            services.queue.clone(),
            services.briefing_debounce_seconds,
            services.briefing_batch_minimum,
        ),
    )
}

#[derive(Debug, Clone)]
struct DurableImageUsageObserver {
    pool: PgPool,
    attempt: PreparedImageAttempt,
}

impl ImageGenerationUsageObserver for DurableImageUsageObserver {
    fn observe(
        &self,
        usage: ImageGenerationUsage,
    ) -> Pin<Box<dyn Future<Output = Result<(), String>> + Send + '_>> {
        let pool = self.pool.clone();
        let attempt = self.attempt.clone();
        Box::pin(async move {
            tokio::spawn(async move {
                let result = record_image_generation_usage(&pool, &attempt, &usage).await;
                if let Err(error) = &result {
                    tracing::error!(
                        task_id = attempt.task_id,
                        content_id = attempt.content.id,
                        provider = usage.provider,
                        model = usage.model,
                        error = %error,
                        "image generation usage persistence failed"
                    );
                }
                result.map(|_| ()).map_err(|error| error.to_string())
            })
            .await
            .map_err(|error| format!("image usage persistence task failed: {error}"))?
        })
    }
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

#[cfg(test)]
mod tests {
    use std::sync::Arc;
    use std::time::Duration;

    use newsly_providers::{ImageGenerationUsage, ImageGenerationUsageObserver};
    use newsly_queue::{EnqueueRequest, QueueKernel, TaskType};
    use serde_json::json;
    use sqlx::PgPool;

    use super::{DurableImageUsageObserver, PreparedImageAttempt};
    use crate::image_generation::repository::load_image_snapshot;

    #[sqlx::test]
    async fn cancelling_observer_wait_does_not_cancel_durable_usage_insert(pool: PgPool) {
        newsly_db::run_migrations(&pool).await.unwrap();
        let user_id: i64 = sqlx::query_scalar(
            "INSERT INTO users(apple_id,email,is_active,is_admin)
             VALUES('image-observer','image-observer@example.com',true,false)
             RETURNING id::bigint",
        )
        .fetch_one(&pool)
        .await
        .unwrap();
        let content_id: i64 = sqlx::query_scalar(
            "INSERT INTO contents(content_type,url,title,status,is_aggregate,content_metadata)
             VALUES('article','https://example.com/observer','Test','awaiting_image',false,$1)
             RETURNING id::bigint",
        )
        .bind(json!({
            "summary": {"title": "Test", "overview": "Observed"},
            "submitted_by_user_id": user_id
        }))
        .fetch_one(&pool)
        .await
        .unwrap();
        let queue = QueueKernel::new(pool.clone());
        let mut enqueue = EnqueueRequest::new(TaskType::GenerateImage);
        enqueue.content_id = Some(content_id);
        let task_id = queue.enqueue(enqueue).await.unwrap();
        let mut snapshot_tx = pool.begin().await.unwrap();
        let content = load_image_snapshot(&mut snapshot_tx, content_id)
            .await
            .unwrap()
            .unwrap();
        snapshot_tx.commit().await.unwrap();
        let observer = Arc::new(DurableImageUsageObserver {
            pool: pool.clone(),
            attempt: PreparedImageAttempt {
                task_id,
                content,
                input_fingerprint: "observer-fixture".to_owned(),
                force: false,
            },
        });
        let usage = ImageGenerationUsage {
            provider: "runware".to_owned(),
            model: "fixture".to_owned(),
            request_id: Some("detached-response".to_owned()),
            input_tokens: None,
            cache_read_tokens: None,
            output_tokens: None,
            total_tokens: None,
            request_count: 1,
            response_cost_usd: Some(0.01),
            metadata: json!({}),
        };

        let mut blocker = pool.begin().await.unwrap();
        sqlx::query("LOCK TABLE vendor_usage_records IN ACCESS EXCLUSIVE MODE")
            .execute(&mut *blocker)
            .await
            .unwrap();
        let waiting_observer = tokio::spawn(async move { observer.observe(usage).await });
        tokio::time::sleep(Duration::from_millis(25)).await;
        let blocked_count: i64 = sqlx::query_scalar(
            "SELECT count(*) FROM vendor_usage_records WHERE request_id='detached-response'",
        )
        .fetch_one(&mut *blocker)
        .await
        .unwrap();
        assert_eq!(blocked_count, 0);
        waiting_observer.abort();
        blocker.rollback().await.unwrap();

        let inserted = tokio::time::timeout(Duration::from_secs(2), async {
            loop {
                let count: i64 = sqlx::query_scalar(
                    "SELECT count(*) FROM vendor_usage_records WHERE request_id='detached-response'",
                )
                .fetch_one(&pool)
                .await
                .unwrap();
                if count == 1 {
                    break count;
                }
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .expect("detached usage insert should finish after caller cancellation");
        assert_eq!(inserted, 1);
    }
}
