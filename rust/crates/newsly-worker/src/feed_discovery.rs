use std::collections::HashSet;
use std::error::Error;
use std::sync::Arc;

use newsly_db::{
    FeedDiscoveryTaskSnapshot, OnboardingAttemptStatus, PrepareFeedDiscoveryTaskOutcome,
    complete_feed_discovery_task, ensure_weekly_discovery_session, prepare_feed_discovery_task,
    settle_feed_discovery_attempt,
};
use newsly_e2b::FeedValidator;
use newsly_providers::OnboardingGateway;
use newsly_queue::{OwnedWorkPlan, QueueKernel, TaskResult, TaskType};
use serde_json::Value;
use sqlx::{PgPool, Postgres, Transaction};

use crate::onboarding_discovery::{enqueue_weekly_session_sync, normalize_seeds};
use crate::{
    HandlerExecution, HandlerFinalizerFuture, HandlerFuture, LeaseHealth, TaskFinalizer,
    TaskFinalizerResult, TaskHandler,
};

#[derive(Debug, Clone)]
pub struct FeedDiscoveryWorkerServices {
    pool: PgPool,
    queue: QueueKernel,
    provider: OnboardingGateway,
    feed_validator: FeedValidator,
    max_retries: i32,
    favorite_limit: i64,
    minimum_favorites: usize,
}

impl FeedDiscoveryWorkerServices {
    #[allow(clippy::too_many_arguments)]
    pub const fn new(
        pool: PgPool,
        queue: QueueKernel,
        provider: OnboardingGateway,
        feed_validator: FeedValidator,
        max_retries: i32,
        favorite_limit: i64,
        minimum_favorites: usize,
    ) -> Self {
        Self {
            pool,
            queue,
            provider,
            feed_validator,
            max_retries,
            favorite_limit,
            minimum_favorites,
        }
    }
}

#[derive(Debug, Clone)]
pub struct DiscoverFeedsHandler {
    services: Arc<FeedDiscoveryWorkerServices>,
}

impl DiscoverFeedsHandler {
    pub fn new(services: Arc<FeedDiscoveryWorkerServices>) -> Self {
        Self { services }
    }
}

impl TaskHandler for DiscoverFeedsHandler {
    fn task_type(&self) -> TaskType {
        TaskType::DiscoverFeeds
    }

    fn execute(&self, plan: Arc<OwnedWorkPlan>, lease: LeaseHealth) -> HandlerFuture<'_> {
        let services = Arc::clone(&self.services);
        Box::pin(async move { execute_feed_discovery(&services, &plan, &lease).await })
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct FeedDiscoveryRequest {
    user_id: i64,
    trigger: String,
}

async fn execute_feed_discovery(
    services: &FeedDiscoveryWorkerServices,
    task: &OwnedWorkPlan,
    lease: &LeaseHealth,
) -> HandlerExecution {
    let request = match parse_request(task) {
        Ok(request) => request,
        Err(error) => {
            return HandlerExecution::from_result(TaskResult::fail(Some(error), false));
        }
    };
    let snapshot = match prepare_feed_discovery_task(
        &services.pool,
        task.task_id,
        task.retry_count,
        request.user_id,
        &request.trigger,
        services.favorite_limit.clamp(5, 50),
    )
    .await
    {
        Ok(PrepareFeedDiscoveryTaskOutcome::Ready(snapshot)) => snapshot,
        Ok(PrepareFeedDiscoveryTaskOutcome::ReuseCompleted { run_id }) => {
            tracing::info!(
                task_id = task.task_id,
                user_id = request.user_id,
                run_id,
                "reusing this week's completed feed discovery"
            );
            return HandlerExecution::with_finalizer(
                TaskResult::ok(),
                FeedDiscoverySuccessFinalizer {
                    queue: services.queue.clone(),
                    task_id: task.task_id,
                    retry_count: task.retry_count,
                    publication: FeedDiscoveryPublication::Reuse {
                        user_id: request.user_id,
                    },
                },
            );
        }
        Ok(PrepareFeedDiscoveryTaskOutcome::MissingOrInactive) => {
            return HandlerExecution::from_result(TaskResult::fail(
                Some("discover_feeds owner is missing or inactive".to_owned()),
                false,
            ));
        }
        Err(error) => {
            return HandlerExecution::from_result(TaskResult::fail(Some(error.to_string()), true));
        }
    };

    if snapshot.favorites.len() < services.minimum_favorites {
        return success(services, task, snapshot, Vec::new());
    }
    if lease.ownership_lost() {
        return failure(
            services,
            task,
            snapshot,
            "feed discovery lease was lost before provider work",
            true,
        );
    }
    let (profile_summary, inferred_topics) = discovery_context(&snapshot);
    let seeds = match services
        .provider
        .fast_discover(&profile_summary, &inferred_topics)
        .await
    {
        Ok(seeds) => seeds,
        Err(error) => {
            return failure(services, task, snapshot, error.to_string(), true);
        }
    };
    if lease.ownership_lost() {
        return failure(
            services,
            task,
            snapshot,
            "feed discovery lease was lost after provider work",
            true,
        );
    }
    let suggestions = match normalize_seeds(
        &services.feed_validator,
        seeds,
        &profile_summary,
        &inferred_topics,
    )
    .await
    {
        Ok(suggestions) => suggestions,
        Err(error) => {
            return failure(services, task, snapshot, error.to_string(), true);
        }
    };
    success(services, task, snapshot, suggestions)
}

fn success(
    services: &FeedDiscoveryWorkerServices,
    task: &OwnedWorkPlan,
    snapshot: FeedDiscoveryTaskSnapshot,
    suggestions: Vec<newsly_db::NewOnboardingSuggestion>,
) -> HandlerExecution {
    HandlerExecution::with_finalizer(
        TaskResult::ok(),
        FeedDiscoverySuccessFinalizer {
            queue: services.queue.clone(),
            task_id: task.task_id,
            retry_count: task.retry_count,
            publication: FeedDiscoveryPublication::Complete {
                snapshot,
                suggestions,
            },
        },
    )
}

fn failure(
    services: &FeedDiscoveryWorkerServices,
    task: &OwnedWorkPlan,
    snapshot: FeedDiscoveryTaskSnapshot,
    error: impl Into<String>,
    retryable: bool,
) -> HandlerExecution {
    let error = error.into();
    let exhausted = !retryable || task.retry_count >= services.max_retries;
    HandlerExecution::with_finalizer(
        TaskResult::fail(Some(error.clone()), retryable),
        FeedDiscoveryFailureFinalizer {
            task_id: task.task_id,
            retry_count: task.retry_count,
            run_id: snapshot.run_id,
            user_id: snapshot.user_id,
            status: if exhausted {
                OnboardingAttemptStatus::Failed
            } else {
                OnboardingAttemptStatus::Pending
            },
            error,
        },
    )
}

fn parse_request(task: &OwnedWorkPlan) -> Result<FeedDiscoveryRequest, String> {
    let user_id = task
        .owner_user_id
        .filter(|value| *value > 0)
        .ok_or_else(|| "discover_feeds requires a positive owner user_id".to_owned())?;
    if task.payload.get("user_id").and_then(Value::as_i64) != Some(user_id) {
        return Err("discover_feeds owner and payload user_id must match".to_owned());
    }
    let trigger = task
        .payload
        .get("trigger")
        .map(|value| {
            value
                .as_str()
                .map(|value| clean_text(value, 64))
                .filter(|value| !value.is_empty())
                .ok_or_else(|| "discover_feeds trigger must be a non-empty string".to_owned())
        })
        .transpose()?
        .unwrap_or_else(|| "cron".to_owned());
    Ok(FeedDiscoveryRequest { user_id, trigger })
}

fn discovery_context(snapshot: &FeedDiscoveryTaskSnapshot) -> (String, Vec<String>) {
    let mut topics = Vec::new();
    let mut seen = HashSet::new();
    for favorite in &snapshot.favorites {
        if let Some(source) = favorite.source.as_deref() {
            push_topic(source, &mut topics, &mut seen);
        }
    }
    for favorite in &snapshot.favorites {
        push_topic(&favorite.title, &mut topics, &mut seen);
        if topics.len() >= 8 {
            break;
        }
    }
    let labels = snapshot
        .favorites
        .iter()
        .take(8)
        .map(|favorite| {
            favorite.source.as_ref().map_or_else(
                || clean_text(&favorite.title, 80),
                |source| {
                    format!(
                        "{}: {}",
                        clean_text(source, 40),
                        clean_text(&favorite.title, 80)
                    )
                },
            )
        })
        .collect::<Vec<_>>()
        .join("; ");
    let summary = clean_text(
        &format!("Saved Knowledge interests represented by {labels}"),
        600,
    );
    (summary, topics)
}

fn push_topic(value: &str, topics: &mut Vec<String>, seen: &mut HashSet<String>) {
    let topic = clean_text(value, 100);
    let key = topic.to_ascii_lowercase();
    if !topic.is_empty() && seen.insert(key) {
        topics.push(topic);
    }
}

fn clean_text(value: &str, max_chars: usize) -> String {
    value
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .chars()
        .take(max_chars)
        .collect()
}

#[derive(Debug)]
enum FeedDiscoveryPublication {
    Complete {
        snapshot: FeedDiscoveryTaskSnapshot,
        suggestions: Vec<newsly_db::NewOnboardingSuggestion>,
    },
    Reuse {
        user_id: i64,
    },
}

#[derive(Debug)]
struct FeedDiscoverySuccessFinalizer {
    queue: QueueKernel,
    task_id: i64,
    retry_count: i32,
    publication: FeedDiscoveryPublication,
}

impl FeedDiscoverySuccessFinalizer {
    async fn apply_inner(
        &self,
        transaction: &mut Transaction<'static, Postgres>,
    ) -> Result<TaskFinalizerResult, Box<dyn Error + Send + Sync>> {
        let user_id = match &self.publication {
            FeedDiscoveryPublication::Complete {
                snapshot,
                suggestions,
            } => {
                if !complete_feed_discovery_task(
                    transaction,
                    self.task_id,
                    self.retry_count,
                    snapshot,
                    suggestions,
                )
                .await?
                {
                    return Ok(TaskFinalizerResult::Keep);
                }
                snapshot.user_id
            }
            FeedDiscoveryPublication::Reuse { user_id } => *user_id,
        };
        if let Some(session) = ensure_weekly_discovery_session(transaction, user_id).await?
            && session.changed
        {
            enqueue_weekly_session_sync(transaction, &self.queue, user_id, session.session_id)
                .await?;
        }
        Ok(TaskFinalizerResult::Keep)
    }
}

impl TaskFinalizer for FeedDiscoverySuccessFinalizer {
    fn apply<'a>(
        &'a self,
        transaction: &'a mut Transaction<'static, Postgres>,
    ) -> HandlerFinalizerFuture<'a> {
        Box::pin(async move { self.apply_inner(transaction).await })
    }
}

#[derive(Debug)]
struct FeedDiscoveryFailureFinalizer {
    task_id: i64,
    retry_count: i32,
    run_id: i64,
    user_id: i64,
    status: OnboardingAttemptStatus,
    error: String,
}

impl TaskFinalizer for FeedDiscoveryFailureFinalizer {
    fn apply<'a>(
        &'a self,
        transaction: &'a mut Transaction<'static, Postgres>,
    ) -> HandlerFinalizerFuture<'a> {
        Box::pin(async move {
            settle_feed_discovery_attempt(
                transaction,
                self.task_id,
                self.retry_count,
                self.run_id,
                self.user_id,
                self.status,
                &self.error,
            )
            .await?;
            Ok(TaskFinalizerResult::Keep)
        })
    }
}

#[cfg(test)]
mod tests {
    use newsly_domain::RuntimeOwner;
    use newsly_queue::{OwnedWorkPlan, TaskQueue, TaskType};
    use serde_json::json;

    use super::{discovery_context, parse_request};

    fn plan(payload: serde_json::Value) -> OwnedWorkPlan {
        OwnedWorkPlan {
            task_id: 9,
            owner_user_id: Some(7),
            task_type: TaskType::DiscoverFeeds,
            content_id: None,
            payload: payload.as_object().cloned().unwrap(),
            retry_count: 0,
            queue_name: TaskQueue::Content,
            executor_runtime: RuntimeOwner::Rust,
            executor_version: 1,
            executor_namespace: "discover_feeds".to_owned(),
        }
    }

    #[test]
    fn request_owner_must_match_payload() {
        let error = parse_request(&plan(json!({"user_id": 8}))).unwrap_err();
        assert!(error.contains("must match"));
    }

    #[test]
    fn saved_content_context_is_bounded_and_deduplicated() {
        let snapshot = newsly_db::FeedDiscoveryTaskSnapshot {
            run_id: 1,
            user_id: 7,
            trigger: "manual".to_owned(),
            favorites: vec![
                newsly_db::FeedDiscoveryFavorite {
                    id: 1,
                    title: "Rust async runtimes".to_owned(),
                    source: Some("Example".to_owned()),
                    url: "https://example.com/one".to_owned(),
                    content_type: "article".to_owned(),
                    summary: None,
                },
                newsly_db::FeedDiscoveryFavorite {
                    id: 2,
                    title: "Postgres internals".to_owned(),
                    source: Some("Example".to_owned()),
                    url: "https://example.com/two".to_owned(),
                    content_type: "article".to_owned(),
                    summary: None,
                },
            ],
        };
        let (summary, topics) = discovery_context(&snapshot);
        assert!(summary.contains("Rust async runtimes"));
        assert_eq!(topics.iter().filter(|topic| *topic == "Example").count(), 1);
    }
}
