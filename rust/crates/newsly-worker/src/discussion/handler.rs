use std::sync::Arc;

use chrono::Utc;
use newsly_providers::{
    ContentMiscGateway, ContentMiscGatewayError, DiscussionRefreshResult,
    GeneratedDiscussionSummary,
};
use newsly_queue::{OwnedWorkPlan, TaskResult, TaskType};
use serde_json::{Value, json};
use sqlx::PgPool;

use crate::{HandlerExecution, HandlerFuture, LeaseHealth, TaskHandler};

use super::finalizer::DiscussionFinalizer;
use super::input::{build_merge_prompt, build_summary_input, plan_summary};
use super::model::{
    DiscussionFinalizationPlan, DiscussionMutation, DiscussionPreparation, DiscussionSnapshot,
    DiscussionSummaryMode, DiscussionUsage, FetchedDiscussionArtifact, SummaryPublication,
};
use super::repository::prepare_discussion;
use super::storage::{DiscussionObjectStore, DiscussionObjectStoreError};

#[derive(Debug, Clone)]
pub struct DiscussionWorkerServices {
    pool: PgPool,
    gateway: ContentMiscGateway,
    object_store: DiscussionObjectStore,
}

impl DiscussionWorkerServices {
    pub const fn new(
        pool: PgPool,
        gateway: ContentMiscGateway,
        object_store: DiscussionObjectStore,
    ) -> Self {
        Self {
            pool,
            gateway,
            object_store,
        }
    }
}

#[derive(Debug, Clone)]
pub struct FetchNewsItemDiscussionHandler {
    services: Arc<DiscussionWorkerServices>,
}

impl FetchNewsItemDiscussionHandler {
    pub fn new(services: Arc<DiscussionWorkerServices>) -> Self {
        Self { services }
    }
}

impl TaskHandler for FetchNewsItemDiscussionHandler {
    fn task_type(&self) -> TaskType {
        TaskType::FetchNewsItemDiscussion
    }

    fn execute(&self, plan: Arc<OwnedWorkPlan>, lease: LeaseHealth) -> HandlerFuture<'_> {
        let services = Arc::clone(&self.services);
        Box::pin(async move { execute_discussion(&services, &plan, lease).await })
    }
}

async fn execute_discussion(
    services: &DiscussionWorkerServices,
    plan: &OwnedWorkPlan,
    mut lease: LeaseHealth,
) -> HandlerExecution {
    let Some(news_item_id) = plan
        .payload
        .get("news_item_id")
        .and_then(Value::as_i64)
        .filter(|value| *value > 0)
    else {
        return plain_failure(
            "fetch_news_item_discussion requires a positive news_item_id",
            false,
        );
    };
    let preparation = {
        let mut transaction = match services.pool.begin().await {
            Ok(transaction) => transaction,
            Err(error) => return plain_failure(error.to_string(), true),
        };
        let preparation = match prepare_discussion(&mut transaction, news_item_id).await {
            Ok(preparation) => preparation,
            Err(error) => return plain_failure(error.to_string(), true),
        };
        if let Err(error) = transaction.commit().await {
            return plain_failure(error.to_string(), true);
        }
        preparation
    };
    let snapshot = match preparation {
        DiscussionPreparation::Ready(snapshot) => snapshot,
        DiscussionPreparation::Fresh | DiscussionPreparation::Terminal => {
            return HandlerExecution::from_result(TaskResult::ok());
        }
        DiscussionPreparation::Deferred(seconds) => {
            return HandlerExecution::from_result(TaskResult::defer(seconds));
        }
        DiscussionPreparation::Unsupported => {
            tracing::info!(news_item_id, "skipping unsupported news-item discussion");
            return HandlerExecution::from_result(TaskResult::ok());
        }
        DiscussionPreparation::NotFound => {
            return plain_failure(format!("news item {news_item_id} does not exist"), false);
        }
    };
    if lease.ownership_lost() {
        return plain_failure("queue lease was lost before discussion fetch", true);
    }

    let fetched = {
        let fetch = services.gateway.refresh_discussion(
            Some(&snapshot.platform),
            Some(&snapshot.discussion_url),
            Some(&snapshot.external_id),
        );
        tokio::pin!(fetch);
        tokio::select! {
            result = &mut fetch => result,
            () = lease.wait_for_ownership_loss() => {
                return plain_failure("queue lease was lost during discussion fetch", true);
            }
        }
    };
    let fetched = match fetched {
        Ok(fetched) => fetched,
        Err(error) => {
            if let Some(status) = error.discussion_terminal_status() {
                return terminal_execution(plan, snapshot, status, error.to_string());
            }
            let retryable = error.discussion_retryable();
            return failed_execution(plan, snapshot, error.to_string(), retryable, None);
        }
    };
    if lease.ownership_lost() {
        return plain_failure("queue lease was lost after discussion fetch", true);
    }

    let raw_payload = raw_payload(&fetched);
    let comment_count = fetched.comments.len();
    let pointer = match services
        .object_store
        .stage(news_item_id, &raw_payload, comment_count)
        .await
    {
        Ok(pointer) => pointer,
        Err(error) => {
            let retryable = storage_error_is_retryable(&error);
            return failed_execution(plan, snapshot, error.to_string(), retryable, None);
        }
    };
    let fetched_at = Utc::now();
    let fetched_artifact = FetchedDiscussionArtifact {
        pointer,
        title: fetched
            .thread
            .title
            .clone()
            .or_else(|| snapshot.title.clone()),
        author: fetched
            .thread
            .author
            .clone()
            .or_else(|| snapshot.author.clone()),
        score: fetched
            .thread
            .score
            .and_then(nonnegative_i32)
            .or(snapshot.score),
        declared_comment_count: fetched
            .thread
            .comment_count
            .and_then(nonnegative_i32)
            .or(snapshot.comment_count),
        fetched_comment_count: i32::try_from(comment_count).unwrap_or(i32::MAX),
        fetched_at,
    };
    let summary_input = build_summary_input(
        &snapshot.platform,
        &snapshot.discussion_url,
        fetched_artifact.title.as_deref(),
        &raw_payload,
    );
    let summary_plan = plan_summary(
        &snapshot,
        &summary_input,
        snapshot.raw_comments_sha256.as_deref(),
        &fetched_artifact.pointer.sha256,
        fetched_at,
    );
    let publication = match summary_plan.mode {
        DiscussionSummaryMode::None if summary_input.comment_count == 0 => {
            if snapshot.summary.is_none() {
                SummaryPublication::NotReady
            } else {
                SummaryPublication::Preserve
            }
        }
        DiscussionSummaryMode::None => SummaryPublication::Preserve,
        DiscussionSummaryMode::TrackSummarized => SummaryPublication::TrackSummarized {
            input: summary_input,
        },
        DiscussionSummaryMode::TrackSeen => SummaryPublication::TrackSeen {
            input: summary_input,
        },
        DiscussionSummaryMode::Full | DiscussionSummaryMode::Merge => {
            let requested_mode = summary_plan.mode;
            let prompt = if requested_mode == DiscussionSummaryMode::Merge {
                build_merge_prompt(&snapshot, &summary_input, &summary_plan.changed_comments)
            } else {
                summary_input.prompt.clone()
            };
            let first = summarize_with_lease(
                &services.gateway,
                &prompt,
                requested_mode == DiscussionSummaryMode::Merge,
                &snapshot.discussion_url,
                &mut lease,
            )
            .await;
            let (generated, effective_mode) = match first {
                SummaryCall::Generated(generated) => (generated, requested_mode),
                SummaryCall::LeaseLost => {
                    return plain_failure("queue lease was lost during discussion summary", true);
                }
                SummaryCall::Failed(error) if requested_mode == DiscussionSummaryMode::Merge => {
                    tracing::warn!(
                        news_item_id,
                        error = %error,
                        "discussion merge failed; falling back to full summary"
                    );
                    match summarize_with_lease(
                        &services.gateway,
                        &summary_input.prompt,
                        false,
                        &snapshot.discussion_url,
                        &mut lease,
                    )
                    .await
                    {
                        SummaryCall::Generated(generated) => {
                            (generated, DiscussionSummaryMode::Full)
                        }
                        SummaryCall::LeaseLost => {
                            return plain_failure(
                                "queue lease was lost during full discussion-summary fallback",
                                true,
                            );
                        }
                        SummaryCall::Failed(error) => {
                            return failed_execution(
                                plan,
                                snapshot,
                                error.to_string(),
                                true,
                                Some(fetched_artifact),
                            );
                        }
                    }
                }
                SummaryCall::Failed(error) => {
                    return failed_execution(
                        plan,
                        snapshot,
                        error.to_string(),
                        true,
                        Some(fetched_artifact),
                    );
                }
            };
            let usage = DiscussionUsage {
                provider: generated.provider.clone(),
                model: generated.model.clone(),
                provider_response_id: generated.provider_response_id.clone(),
                usage: generated.usage.clone(),
                summary_mode: effective_mode,
                summary_input_sha256: summary_input.input_sha256.clone(),
                summary_comment_count: summary_input.comment_count,
                changed_comment_count: i32::try_from(summary_plan.changed_comments.len())
                    .unwrap_or(i32::MAX),
            };
            SummaryPublication::Generated {
                input: summary_input,
                summary: generated.summary_json,
                model: generated.model,
                mode: effective_mode,
                usage,
            }
        }
    };
    with_finalizer(
        TaskResult::ok(),
        DiscussionFinalizationPlan {
            task_id: plan.task_id,
            snapshot,
            mutation: DiscussionMutation::Completed {
                fetched: fetched_artifact,
                summary: publication,
            },
            finalized_at: Utc::now(),
        },
    )
}

enum SummaryCall {
    Generated(GeneratedDiscussionSummary),
    Failed(ContentMiscGatewayError),
    LeaseLost,
}

async fn summarize_with_lease(
    gateway: &ContentMiscGateway,
    prompt: &str,
    merge: bool,
    discussion_url: &str,
    lease: &mut LeaseHealth,
) -> SummaryCall {
    if lease.ownership_lost() {
        return SummaryCall::LeaseLost;
    }
    let call = gateway.summarize_discussion(prompt, merge, Some(discussion_url));
    tokio::pin!(call);
    tokio::select! {
        result = &mut call => match result {
            Ok(generated) => SummaryCall::Generated(generated),
            Err(error) => SummaryCall::Failed(error),
        },
        () = lease.wait_for_ownership_loss() => SummaryCall::LeaseLost,
    }
}

fn raw_payload(result: &DiscussionRefreshResult) -> Value {
    json!({
        "platform": result.platform,
        "external_id": result.external_id,
        "discussion_url": result.source_url,
        "thread": {
            "title": result.thread.title,
            "author": result.thread.author,
            "score": result.thread.score,
            "comment_count": result.thread.comment_count,
            "created_at": result.thread.created_at,
            "subreddit": result.thread.subreddit,
        },
        "comments": result.comments.iter().map(|comment| json!({
            "comment_id": comment.comment_id,
            "parent_id": comment.parent_id,
            "author": comment.author.as_deref().unwrap_or("unknown"),
            "text": comment.text,
            "compact_text": comment.compact_text,
            "depth": comment.depth,
            "created_at": comment.created_at,
            "source_url": comment.source_url,
        })).collect::<Vec<_>>(),
        "links": result.links.iter().map(|link| json!({
            "url": link.url,
            "source": "comment",
            "comment_id": link.comment_id,
            "title": link.title,
        })).collect::<Vec<_>>(),
        "stats": {
            "provider": result.provider,
            "declared_comment_count": result.thread.comment_count,
            "fetched_count": result.comments.len(),
            "total_seen": result.total_seen,
            "stored_comment_cap": result.comment_cap,
            "cap_reached": result.cap_reached,
        },
    })
}

fn terminal_execution(
    plan: &OwnedWorkPlan,
    snapshot: DiscussionSnapshot,
    status: &str,
    reason: String,
) -> HandlerExecution {
    with_finalizer(
        TaskResult::ok(),
        DiscussionFinalizationPlan {
            task_id: plan.task_id,
            snapshot,
            mutation: DiscussionMutation::Terminal {
                status: status.to_owned(),
                reason,
            },
            finalized_at: Utc::now(),
        },
    )
}

fn failed_execution(
    plan: &OwnedWorkPlan,
    snapshot: DiscussionSnapshot,
    reason: String,
    retryable: bool,
    fetched: Option<FetchedDiscussionArtifact>,
) -> HandlerExecution {
    with_finalizer(
        TaskResult::fail(Some(reason.clone()), retryable),
        DiscussionFinalizationPlan {
            task_id: plan.task_id,
            snapshot,
            mutation: DiscussionMutation::Failed { reason, fetched },
            finalized_at: Utc::now(),
        },
    )
}

fn with_finalizer(result: TaskResult, plan: DiscussionFinalizationPlan) -> HandlerExecution {
    HandlerExecution::with_finalizer(result, DiscussionFinalizer::new(plan))
}

fn plain_failure(message: impl Into<String>, retryable: bool) -> HandlerExecution {
    HandlerExecution::from_result(TaskResult::fail(Some(message.into()), retryable))
}

fn storage_error_is_retryable(error: &DiscussionObjectStoreError) -> bool {
    matches!(
        error,
        DiscussionObjectStoreError::CurrentDirectory(_)
            | DiscussionObjectStoreError::Write(_)
            | DiscussionObjectStoreError::Read(_)
    )
}

fn nonnegative_i32(value: i64) -> Option<i32> {
    i32::try_from(value).ok().filter(|value| *value >= 0)
}
