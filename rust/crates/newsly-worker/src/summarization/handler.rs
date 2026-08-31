use std::sync::Arc;

use chrono::Utc;
use newsly_providers::{SummarizationGateway, SummarizationGatewayError, SummarizationSource};
use newsly_queue::{OwnedWorkPlan, QueueKernel, TaskResult, TaskType};
use serde_json::Value;
use sqlx::PgPool;

use crate::{HandlerExecution, HandlerFuture, LeaseHealth, TaskHandler};

use super::finalizer::SummarizationFinalizer;
use super::input::{
    build_summarization_payload, input_fingerprint, runtime_metadata_view, summary_matches,
};
use super::model::{
    PreparedSummarizationAttempt, SummarizationFinalizationPlan, SummarizationMutation,
    SummaryUsage,
};
use super::repository::load_summarization_snapshot;
use super::storage::SummarizationBodyStore;

#[derive(Debug, Clone)]
pub struct SummarizationWorkerServices {
    pool: PgPool,
    queue: QueueKernel,
    gateway: SummarizationGateway,
    body_store: SummarizationBodyStore,
    max_retries: i32,
    briefing_debounce_seconds: i64,
    briefing_batch_minimum: i64,
}

impl SummarizationWorkerServices {
    pub const fn new(
        pool: PgPool,
        queue: QueueKernel,
        gateway: SummarizationGateway,
        body_store: SummarizationBodyStore,
        max_retries: i32,
        briefing_debounce_seconds: i64,
        briefing_batch_minimum: i64,
    ) -> Self {
        Self {
            pool,
            queue,
            gateway,
            body_store,
            max_retries,
            briefing_debounce_seconds,
            briefing_batch_minimum,
        }
    }
}

#[derive(Debug, Clone)]
pub struct SummarizeHandler {
    services: Arc<SummarizationWorkerServices>,
}

impl SummarizeHandler {
    pub fn new(services: Arc<SummarizationWorkerServices>) -> Self {
        Self { services }
    }
}

impl TaskHandler for SummarizeHandler {
    fn task_type(&self) -> TaskType {
        TaskType::Summarize
    }

    fn execute(&self, plan: Arc<OwnedWorkPlan>, lease: LeaseHealth) -> HandlerFuture<'_> {
        let services = Arc::clone(&self.services);
        Box::pin(async move { execute_summarization(&services, &plan, lease).await })
    }
}

async fn execute_summarization(
    services: &SummarizationWorkerServices,
    plan: &OwnedWorkPlan,
    mut lease: LeaseHealth,
) -> HandlerExecution {
    let Some(content_id) = plan
        .content_id
        .or_else(|| plan.payload.get("content_id").and_then(Value::as_i64))
        .filter(|content_id| *content_id > 0)
    else {
        return plain_failure("summarize requires a positive content_id", false);
    };

    let snapshot = {
        let mut transaction = match services.pool.begin().await {
            Ok(transaction) => transaction,
            Err(error) => return plain_failure(error.to_string(), true),
        };
        let snapshot = match load_summarization_snapshot(&mut transaction, content_id).await {
            Ok(snapshot) => snapshot,
            Err(error) => return plain_failure(error.to_string(), true),
        };
        if let Err(error) = transaction.commit().await {
            return plain_failure(error.to_string(), true);
        }
        match snapshot {
            Some(snapshot) => snapshot,
            None => return plain_failure(format!("content {content_id} does not exist"), false),
        }
    };
    if snapshot.is_terminal() {
        return HandlerExecution::from_result(TaskResult::ok());
    }
    if !matches!(
        snapshot.content_type.as_str(),
        "article" | "news" | "podcast"
    ) {
        let payload =
            build_summarization_payload(&snapshot.content_type, &snapshot.content_metadata, None);
        let fingerprint = input_fingerprint(&snapshot.content_type, &payload);
        let error = format!(
            "Unknown content type for summarization: {}",
            snapshot.content_type
        );
        return failed_execution(services, plan, snapshot, fingerprint, error, false, false);
    }
    let metadata_view = runtime_metadata_view(&snapshot.content_metadata);
    if snapshot.status == "completed" && metadata_view.get("summary").is_some_and(Value::is_object)
    {
        return HandlerExecution::from_result(TaskResult::ok());
    }

    let source_text = if let Some(pointer) = snapshot.body_pointer() {
        match services.body_store.read_source(&pointer).await {
            Ok(body) => Some(body),
            Err(error) => {
                return failed_execution(
                    services,
                    plan,
                    snapshot,
                    String::new(),
                    error.to_string(),
                    true,
                    false,
                );
            }
        }
    } else {
        None
    };
    let payload = build_summarization_payload(
        &snapshot.content_type,
        &snapshot.content_metadata,
        source_text.as_deref(),
    );
    let fingerprint = input_fingerprint(&snapshot.content_type, &payload);
    if payload.trim().is_empty() {
        return failed_execution(
            services,
            plan,
            snapshot,
            fingerprint,
            format!("No text to summarize for content {content_id}"),
            false,
            true,
        );
    }
    if summary_matches(&snapshot.content_metadata, &fingerprint) {
        let attempt = PreparedSummarizationAttempt {
            task_id: plan.task_id,
            content: snapshot,
            input_fingerprint: fingerprint,
        };
        return with_finalizer(
            services,
            TaskResult::ok(),
            SummarizationFinalizationPlan {
                attempt,
                mutation: SummarizationMutation::Unchanged,
                finalized_at: Utc::now(),
            },
        );
    }

    let source = SummarizationSource {
        content_id,
        content_type: snapshot.content_type.clone(),
        title: snapshot.title.clone(),
        url: snapshot.url.clone(),
        source_name: snapshot.source.clone(),
        platform: snapshot.platform.clone(),
        publication_date: snapshot.publication_date_rfc3339(),
        metadata: snapshot.content_metadata.clone(),
        text: payload,
    };
    let provider_call = services.gateway.summarize(&source);
    tokio::pin!(provider_call);
    let generated = tokio::select! {
        result = &mut provider_call => result,
        () = lease.wait_for_ownership_loss() => {
            return plain_failure("lease ownership was lost during summarization", true);
        }
    };
    match generated {
        Ok(generated) => {
            if lease.ownership_lost() {
                return plain_failure("lease ownership was lost during summarization", true);
            }
            let (provider, fallback_model) = split_model_spec(services.gateway.model_spec());
            let attempt = PreparedSummarizationAttempt {
                task_id: plan.task_id,
                content: snapshot,
                input_fingerprint: fingerprint,
            };
            with_finalizer(
                services,
                TaskResult::ok(),
                SummarizationFinalizationPlan {
                    attempt,
                    mutation: SummarizationMutation::Complete {
                        summary: generated.summary_json,
                        usage: SummaryUsage {
                            provider: provider.to_owned(),
                            model: nonempty(&generated.model)
                                .unwrap_or_else(|| fallback_model.to_owned()),
                            provider_response_id: generated.provider_response_id,
                            usage: generated.usage,
                        },
                    },
                    finalized_at: Utc::now(),
                },
            )
        }
        Err(error) => {
            let retryable = provider_error_is_retryable(&error);
            failed_execution(
                services,
                plan,
                snapshot,
                fingerprint,
                format!("Summarization error: {error}"),
                retryable,
                false,
            )
        }
    }
}

fn failed_execution(
    services: &SummarizationWorkerServices,
    plan: &OwnedWorkPlan,
    snapshot: super::model::SummarizationSnapshot,
    input_fingerprint: String,
    reason: String,
    retryable: bool,
    skipped: bool,
) -> HandlerExecution {
    let retry_scheduled = retryable && plan.retry_count < services.max_retries.max(0);
    let task_result = if skipped {
        TaskResult::ok()
    } else {
        TaskResult::fail(Some(reason.clone()), retryable)
    };
    with_finalizer(
        services,
        task_result,
        SummarizationFinalizationPlan {
            attempt: PreparedSummarizationAttempt {
                task_id: plan.task_id,
                content: snapshot,
                input_fingerprint,
            },
            mutation: SummarizationMutation::Failed {
                reason,
                retry_scheduled,
                skipped,
            },
            finalized_at: Utc::now(),
        },
    )
}

fn with_finalizer(
    services: &SummarizationWorkerServices,
    task_result: TaskResult,
    plan: SummarizationFinalizationPlan,
) -> HandlerExecution {
    HandlerExecution::with_finalizer(
        task_result,
        SummarizationFinalizer::new(
            services.queue.clone(),
            plan,
            services.briefing_debounce_seconds,
            services.briefing_batch_minimum,
        ),
    )
}

fn provider_error_is_retryable(error: &SummarizationGatewayError) -> bool {
    let message = error.to_string().to_ascii_lowercase();
    [
        "timeout",
        "timed out",
        "rate limit",
        "too many requests",
        "429",
        "temporarily unavailable",
        "service unavailable",
        "bad gateway",
        "gateway timeout",
        "connection reset",
        "connection refused",
        "connection aborted",
        "resource exhausted",
        "precondition",
        "overloaded",
        "provider execution failed",
    ]
    .iter()
    .any(|token| message.contains(token))
}

fn split_model_spec(value: &str) -> (&str, &str) {
    value.split_once(':').unwrap_or(("unknown", value))
}

fn nonempty(value: &str) -> Option<String> {
    let value = value.trim();
    (!value.is_empty()).then(|| value.to_owned())
}

fn plain_failure(message: impl Into<String>, retryable: bool) -> HandlerExecution {
    HandlerExecution::from_result(TaskResult::fail(Some(message.into()), retryable))
}
