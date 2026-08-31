//! Task executor and scheduler composition.
//!
//! Every executor follows prepare transaction, immutable owned work plan, external execution,
//! and fresh fenced finalize transaction. Provider-specific handlers deliberately live outside
//! this kernel and are registered only after their task ownership is cut over to Rust.

#![forbid(unsafe_code)]
// Worker functions intentionally keep lifecycle sequencing visible: prepare, cancellation-aware
// external execution, and exact-lease finalization are easier to audit when the state machine is
// not fragmented solely to satisfy a line threshold. Public constructors expose concrete error
// enums, immutable snapshots are owned so they cannot retain database borrows, and short-lived
// outcome enums remain inline. Similar names reflect paired domain concepts (links/lines,
// source/current source), while the only integer-to-float casts are bounded batch cardinalities.
#![allow(
    clippy::cast_precision_loss,
    clippy::large_enum_variant,
    clippy::missing_errors_doc,
    clippy::needless_pass_by_value,
    clippy::similar_names,
    clippy::too_many_lines
)]

pub mod agent_data;
pub mod agent_vm;
pub mod audio_episode;
pub mod briefing_refresh;
pub mod chat_turn;
pub mod config;
pub mod content;
pub mod discussion;
pub mod feed_backfill;
pub mod feed_discovery;
pub mod image_generation;
pub mod learning_deck;
pub mod media;
pub mod news_item;
pub mod onboarding_discovery;
pub mod queue_process_config;
pub mod run_llm_task;
pub mod scrape;
pub mod share_actions;
pub mod summarization;
pub mod x_sync;

use std::collections::HashMap;
use std::fmt::{self, Debug, Formatter};
use std::future::Future;
use std::panic::AssertUnwindSafe;
use std::pin::Pin;
use std::sync::Arc;
use std::time::Duration;

use futures_util::FutureExt;
use newsly_domain::RuntimeOwner;
use newsly_queue::{
    ClaimRequest, ClaimedTask, OwnedWorkPlan, PrepareWorkOutcome, QueueError, QueueKernel,
    QueueNotificationWaiter, QueueWakeOutcome, TaskResult, TaskStatus, TaskTransition, TaskType,
};
use sqlx::{Postgres, Transaction};
use thiserror::Error;
use tokio::sync::watch;
use tokio::task::JoinHandle;
use tokio::time::{Instant, sleep};
use tracing::{error, info, warn};

/// A handler receives only an immutable, connection-free work plan plus lease-health state.
/// Preparing input and persisting output remain kernel responsibilities.
pub trait TaskHandler: Debug + Send + Sync + 'static {
    fn task_type(&self) -> TaskType;

    fn execute(&self, plan: Arc<OwnedWorkPlan>, lease: LeaseHealth) -> HandlerFuture<'_>;
}

pub type HandlerFuture<'a> = Pin<Box<dyn Future<Output = HandlerExecution> + Send + 'a>>;
pub type HandlerFinalizerFuture<'a> = Pin<
    Box<
        dyn Future<Output = Result<TaskFinalizerResult, Box<dyn std::error::Error + Send + Sync>>>
            + Send
            + 'a,
    >,
>;
pub type HandlerAfterCommitFuture<'a> = Pin<Box<dyn Future<Output = ()> + Send + 'a>>;

/// Result of the bounded product-state phase that runs inside the exact queue lease fence.
///
/// Most finalizers preserve the handler result. A finalizer may override it when applying the
/// product mutation reveals a domain failure that could not be known during external execution.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TaskFinalizerResult {
    Keep,
    Override(TaskResult),
}

/// Product-state writes applied only after the queue kernel has locked the exact live lease.
/// Implementations must remain bounded database work; external calls belong in `TaskHandler`.
pub trait TaskFinalizer: Debug + Send + Sync + 'static {
    fn apply<'a>(
        &'a self,
        transaction: &'a mut Transaction<'static, Postgres>,
    ) -> HandlerFinalizerFuture<'a>;

    /// Runs best-effort local cleanup only after the fenced transaction has committed. Product
    /// state must never depend on this hook; failures leave recoverable orphaned files rather than
    /// invalidating the already-committed task transition.
    fn after_commit(&self) -> HandlerAfterCommitFuture<'_> {
        Box::pin(async {})
    }
}

/// External-work result plus optional state to publish atomically with the queue transition.
#[derive(Debug)]
pub struct HandlerExecution {
    pub task_result: TaskResult,
    finalizer: Option<Box<dyn TaskFinalizer>>,
}

impl HandlerExecution {
    pub const fn from_result(task_result: TaskResult) -> Self {
        Self {
            task_result,
            finalizer: None,
        }
    }

    pub fn with_finalizer(task_result: TaskResult, finalizer: impl TaskFinalizer) -> Self {
        Self {
            task_result,
            finalizer: Some(Box::new(finalizer)),
        }
    }
}

#[derive(Debug, Clone)]
pub struct LeaseHealth {
    ownership_lost_rx: watch::Receiver<bool>,
}

impl LeaseHealth {
    pub fn ownership_lost(&self) -> bool {
        *self.ownership_lost_rx.borrow()
    }

    /// Waits until the exact lease can no longer be renewed. Handlers may use this to stop costly
    /// external work early; finalization is independently fenced even if a handler ignores it.
    pub async fn wait_for_ownership_loss(&mut self) {
        while !*self.ownership_lost_rx.borrow_and_update() {
            if self.ownership_lost_rx.changed().await.is_err() {
                return;
            }
        }
    }
}

#[derive(Default)]
pub struct HandlerRegistry {
    handlers: HashMap<TaskType, Arc<dyn TaskHandler>>,
}

impl Debug for HandlerRegistry {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("HandlerRegistry")
            .field("task_types", &self.handlers.keys().collect::<Vec<_>>())
            .finish()
    }
}

impl HandlerRegistry {
    pub fn new() -> Self {
        Self::default()
    }

    /// Registers one task executor. Duplicate registrations are rejected rather than silently
    /// changing production dispatch order.
    ///
    /// # Errors
    ///
    /// Returns [`WorkerError::DuplicateHandler`] when the task type is already registered.
    pub fn register(&mut self, handler: impl TaskHandler) -> Result<(), WorkerError> {
        let task_type = handler.task_type();
        if self.handlers.contains_key(&task_type) {
            return Err(WorkerError::DuplicateHandler(task_type));
        }
        self.handlers.insert(task_type, Arc::new(handler));
        Ok(())
    }

    pub fn handler(&self, task_type: TaskType) -> Option<Arc<dyn TaskHandler>> {
        self.handlers.get(&task_type).cloned()
    }

    pub fn contains(&self, task_type: TaskType) -> bool {
        self.handlers.contains_key(&task_type)
    }
}

#[derive(Debug, Clone)]
pub struct WorkerConfig {
    pub claim: ClaimRequest,
    pub max_retries: i32,
    pub startup_poll_count: u32,
    pub startup_poll_interval: Duration,
    pub normal_poll_interval: Duration,
    pub empty_backoff_after: u32,
    pub empty_backoff_interval: Duration,
    pub database_error_interval: Duration,
}

impl WorkerConfig {
    pub fn new(claim: ClaimRequest) -> Self {
        Self {
            claim,
            max_retries: 3,
            startup_poll_count: 10,
            startup_poll_interval: Duration::from_millis(100),
            normal_poll_interval: Duration::from_secs(1),
            empty_backoff_after: 5,
            empty_backoff_interval: Duration::from_secs(5),
            database_error_interval: Duration::from_secs(5),
        }
    }

    /// Validates worker timing and retry invariants before entering an unbounded loop.
    ///
    /// # Errors
    ///
    /// Returns [`WorkerError::InvalidConfig`] for invalid retry or timing values.
    pub fn validate(&self) -> Result<(), WorkerError> {
        if self.max_retries < 0 {
            return Err(WorkerError::InvalidConfig(
                "max_retries must be nonnegative",
            ));
        }
        if self.startup_poll_interval.is_zero()
            || self.normal_poll_interval.is_zero()
            || self.empty_backoff_interval.is_zero()
            || self.database_error_interval.is_zero()
        {
            return Err(WorkerError::InvalidConfig(
                "worker poll and recovery intervals must be nonzero",
            ));
        }
        if self.empty_backoff_after == 0 {
            return Err(WorkerError::InvalidConfig(
                "empty_backoff_after must be greater than zero",
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum WorkerAttempt {
    Empty,
    Completed(TaskTransition),
    Retried(TaskTransition),
    Deferred(TaskTransition),
    Failed(TaskTransition),
    SkippedInactiveOwner(TaskTransition),
    FinalizationRejected {
        task_id: i64,
        handler_succeeded: bool,
    },
    OwnershipLost {
        task_id: i64,
        handler_succeeded: bool,
    },
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct WorkerSummary {
    pub claimed: u64,
    pub completed: u64,
    pub retried: u64,
    pub deferred: u64,
    pub failed: u64,
    pub skipped_inactive_owner: u64,
    pub ownership_lost: u64,
    pub finalization_rejected: u64,
}

impl WorkerSummary {
    fn record(&mut self, attempt: &WorkerAttempt) {
        if !matches!(attempt, WorkerAttempt::Empty) {
            self.claimed += 1;
        }
        match attempt {
            WorkerAttempt::Empty => {}
            WorkerAttempt::Completed(_) => self.completed += 1,
            WorkerAttempt::Retried(_) => self.retried += 1,
            WorkerAttempt::Deferred(_) => self.deferred += 1,
            WorkerAttempt::Failed(_) => self.failed += 1,
            WorkerAttempt::SkippedInactiveOwner(_) => self.skipped_inactive_owner += 1,
            WorkerAttempt::OwnershipLost { .. } => self.ownership_lost += 1,
            WorkerAttempt::FinalizationRejected { .. } => self.finalization_rejected += 1,
        }
    }
}

#[derive(Debug)]
pub struct WorkerKernel {
    queue: QueueKernel,
    handlers: HandlerRegistry,
    config: WorkerConfig,
    notifications: Option<QueueNotificationWaiter>,
}

impl WorkerKernel {
    /// Builds a worker only when an explicitly scoped task type has a Rust handler. Unregistered
    /// rows in a wider queue scope are terminalized as non-retryable instead of falling through to
    /// another runtime; durable executor stamps already choose which runtime may claim them.
    ///
    /// # Errors
    ///
    /// Returns an error for invalid configuration, a non-Rust claim scope, an unbounded scope, or
    /// any namespace without a registered handler.
    pub fn new(
        queue: QueueKernel,
        handlers: HandlerRegistry,
        config: WorkerConfig,
        notifications: Option<QueueNotificationWaiter>,
    ) -> Result<Self, WorkerError> {
        config.validate()?;
        if config.claim.runtime_scope.runtime() != RuntimeOwner::Rust {
            return Err(WorkerError::NonRustRuntimeScope);
        }
        if let Some(task_type) = config.claim.task_type
            && !handlers.contains(task_type)
        {
            return Err(WorkerError::MissingScopedHandler(task_type));
        }
        match config.claim.runtime_scope.namespace_values() {
            Some(namespaces) => {
                for namespace in namespaces {
                    let task_type = namespace
                        .parse::<TaskType>()
                        .map_err(|_| WorkerError::InvalidExecutorNamespace(namespace.to_owned()))?;
                    if !handlers.contains(task_type) {
                        return Err(WorkerError::MissingScopedHandler(task_type));
                    }
                }
            }
            None if config.claim.task_type.is_none() => {
                return Err(WorkerError::UnboundedClaimScope);
            }
            None => {}
        }
        Ok(Self {
            queue,
            handlers,
            config,
            notifications,
        })
    }

    pub const fn queue(&self) -> &QueueKernel {
        &self.queue
    }

    /// Runs one prepare/external/finalize attempt.
    ///
    /// No transaction crosses `handler.execute`: claim and owner preflight have committed before
    /// external work begins, and result persistence uses a fresh lease-fenced statement.
    ///
    /// # Errors
    ///
    /// Returns a queue/database error when the attempt cannot safely claim, prepare, or finalize.
    pub async fn run_once(&self) -> Result<WorkerAttempt, WorkerError> {
        let Some(claim) = self.queue.claim(&self.config.claim).await? else {
            return Ok(WorkerAttempt::Empty);
        };

        let prepare = match self.queue.prepare_work(&claim).await {
            Ok(prepare) => prepare,
            Err(error) if is_nonretryable_prepare_error(&error) => {
                let execution =
                    HandlerExecution::from_result(TaskResult::fail(Some(error.to_string()), false));
                return self.finalize_attempt(&claim, execution).await;
            }
            Err(error) => return Err(error.into()),
        };

        if let PrepareWorkOutcome::SkipInactiveOwner(_) = prepare {
            let execution = HandlerExecution::from_result(TaskResult::ok());
            let transition = self
                .queue
                .finalize(&claim, &execution.task_result, self.config.max_retries)
                .await?;
            return Ok(match transition {
                Some(transition) => WorkerAttempt::SkippedInactiveOwner(transition),
                None => WorkerAttempt::FinalizationRejected {
                    task_id: claim.id,
                    handler_succeeded: true,
                },
            });
        }

        let PrepareWorkOutcome::Execute(plan) = prepare else {
            unreachable!("inactive owners return before dispatch")
        };
        let execution = self.execute_under_heartbeat(&claim, Arc::new(plan)).await;
        match execution {
            ExecutedTask::Finished(execution) => self.finalize_attempt(&claim, execution).await,
            ExecutedTask::OwnershipLost(execution) => Ok(WorkerAttempt::OwnershipLost {
                task_id: claim.id,
                handler_succeeded: execution.task_result.success,
            }),
        }
    }

    /// Runs until shutdown, retaining LISTEN/NOTIFY only as a latency optimization over polling.
    /// Database failures do not terminate the process; the loop backs off and retries.
    ///
    /// # Errors
    ///
    /// Reserved for nonrecoverable worker-runtime failures; individual queue attempts are logged
    /// and retried inside the loop.
    pub async fn run(
        &mut self,
        mut shutdown_rx: watch::Receiver<bool>,
    ) -> Result<WorkerSummary, WorkerError> {
        let mut summary = WorkerSummary::default();
        let mut empty_polls = 0_u32;
        let mut startup_polls = 0_u32;

        while !*shutdown_rx.borrow() {
            match self.run_once().await {
                Ok(attempt) => {
                    summary.record(&attempt);
                    if matches!(attempt, WorkerAttempt::Empty) {
                        empty_polls = empty_polls.saturating_add(1);
                        startup_polls = startup_polls.saturating_add(1);
                        let delay = if startup_polls <= self.config.startup_poll_count {
                            self.config.startup_poll_interval
                        } else if empty_polls >= self.config.empty_backoff_after {
                            self.config.empty_backoff_interval
                        } else {
                            self.config.normal_poll_interval
                        };
                        if !self.wait_until_wake(delay, &mut shutdown_rx).await {
                            break;
                        }
                    } else {
                        empty_polls = 0;
                    }
                }
                Err(error) => {
                    error!(error = ?error, worker_id = %self.config.claim.worker_id, "worker attempt failed");
                    if !wait_or_shutdown(self.config.database_error_interval, &mut shutdown_rx)
                        .await
                    {
                        break;
                    }
                }
            }
        }
        Ok(summary)
    }

    async fn execute_under_heartbeat(
        &self,
        claim: &ClaimedTask,
        plan: Arc<OwnedWorkPlan>,
    ) -> ExecutedTask {
        let (heartbeat_stop_tx, heartbeat_stop_rx) = watch::channel(false);
        let (ownership_lost_tx, ownership_lost_rx) = watch::channel(false);
        let heartbeat = spawn_lease_heartbeat(
            self.queue.clone(),
            claim.clone(),
            self.config.claim.lease_duration,
            heartbeat_stop_rx,
            ownership_lost_tx,
        );
        let lease = LeaseHealth { ownership_lost_rx };

        let execution = match self.handlers.handler(claim.task_type) {
            Some(handler) => {
                if let Ok(execution) = AssertUnwindSafe(handler.execute(plan, lease.clone()))
                    .catch_unwind()
                    .await
                {
                    execution.with_default_error(claim.task_type)
                } else {
                    error!(task_id = claim.id, task_type = %claim.task_type, "task handler panicked");
                    HandlerExecution::from_result(TaskResult::fail(
                        Some(format!("{} handler panicked", claim.task_type)),
                        true,
                    ))
                }
            }
            None => HandlerExecution::from_result(TaskResult::fail(
                Some(format!(
                    "No Rust handler registered for {}",
                    claim.task_type
                )),
                false,
            )),
        };

        heartbeat_stop_tx.send_replace(true);
        let _ = heartbeat.await;
        if lease.ownership_lost() {
            warn!(
                task_id = claim.id,
                task_type = %claim.task_type,
                "task result not finalized because lease ownership was lost"
            );
            ExecutedTask::OwnershipLost(execution)
        } else {
            ExecutedTask::Finished(execution)
        }
    }

    async fn finalize_attempt(
        &self,
        claim: &ClaimedTask,
        execution: HandlerExecution,
    ) -> Result<WorkerAttempt, WorkerError> {
        let handler_succeeded = execution.task_result.success;
        let Some(mut finalization) = self
            .queue
            .begin_fenced_finalization(claim, &execution.task_result, self.config.max_retries)
            .await?
        else {
            return Ok(WorkerAttempt::FinalizationRejected {
                task_id: claim.id,
                handler_succeeded,
            });
        };
        let finalizer = execution.finalizer;
        if let Some(finalizer) = finalizer.as_ref() {
            let finalizer_result = finalizer
                .apply(finalization.transaction_mut())
                .await
                .map_err(|source| WorkerError::HandlerFinalization {
                    task_id: claim.id,
                    source,
                })?;
            if let TaskFinalizerResult::Override(result) = finalizer_result {
                finalization
                    .replace_result(&result, self.config.max_retries)
                    .map_err(QueueError::from)?;
            }
        }
        let transition = finalization.finish().await?;
        let Some(transition) = transition else {
            return Ok(WorkerAttempt::FinalizationRejected {
                task_id: claim.id,
                handler_succeeded,
            });
        };
        if let Some(finalizer) = finalizer {
            finalizer.after_commit().await;
        }
        Ok(classify_transition(transition))
    }

    async fn wait_until_wake(
        &mut self,
        delay: Duration,
        shutdown_rx: &mut watch::Receiver<bool>,
    ) -> bool {
        if let Some(notifications) = &mut self.notifications {
            tokio::select! {
                outcome = notifications.wait(delay) => {
                    !matches!(outcome, QueueWakeOutcome::ShuttingDown)
                }
                changed = shutdown_rx.changed() => {
                    changed.is_ok() && !*shutdown_rx.borrow()
                }
            }
        } else {
            wait_or_shutdown(delay, shutdown_rx).await
        }
    }
}

#[derive(Debug)]
enum ExecutedTask {
    Finished(HandlerExecution),
    OwnershipLost(HandlerExecution),
}

fn classify_transition(transition: TaskTransition) -> WorkerAttempt {
    match transition.status {
        TaskStatus::Completed => {
            info!(task_type = %transition.task_type, "task completed");
            WorkerAttempt::Completed(transition)
        }
        TaskStatus::Pending if transition.deferred => WorkerAttempt::Deferred(transition),
        TaskStatus::Pending => WorkerAttempt::Retried(transition),
        TaskStatus::Failed => WorkerAttempt::Failed(transition),
        TaskStatus::Processing => {
            unreachable!("finalization cannot return a processing transition")
        }
    }
}

fn spawn_lease_heartbeat(
    queue: QueueKernel,
    claim: ClaimedTask,
    lease_duration: Duration,
    mut stop_rx: watch::Receiver<bool>,
    ownership_lost_tx: watch::Sender<bool>,
) -> JoinHandle<()> {
    tokio::spawn(async move {
        let interval = heartbeat_interval(lease_duration);
        let mut last_renewed_at = Instant::now();
        let mut wait_duration = interval;
        loop {
            tokio::select! {
                () = sleep(wait_duration) => {}
                changed = stop_rx.changed() => {
                    if changed.is_err() || *stop_rx.borrow() {
                        return;
                    }
                    continue;
                }
            }
            match queue.renew_lease(&claim, lease_duration).await {
                Ok(true) => {
                    last_renewed_at = Instant::now();
                    wait_duration = interval;
                }
                Ok(false) => {
                    ownership_lost_tx.send_replace(true);
                    warn!(
                        task_id = claim.id,
                        "task lease heartbeat stopped after ownership rejection"
                    );
                    return;
                }
                Err(error) => {
                    let elapsed = last_renewed_at.elapsed();
                    if elapsed >= lease_duration {
                        ownership_lost_tx.send_replace(true);
                        error!(task_id = claim.id, error = %error, "task lease heartbeat exhausted renewal window");
                        return;
                    }
                    warn!(task_id = claim.id, error = %error, "task lease heartbeat renewal failed; retrying");
                    wait_duration =
                        heartbeat_retry_interval(lease_duration.saturating_sub(elapsed));
                }
            }
        }
    })
}

fn heartbeat_interval(lease_duration: Duration) -> Duration {
    (lease_duration / 3).min(Duration::from_secs(30))
}

fn heartbeat_retry_interval(remaining: Duration) -> Duration {
    remaining.min(Duration::from_secs(5))
}

async fn wait_or_shutdown(delay: Duration, shutdown_rx: &mut watch::Receiver<bool>) -> bool {
    tokio::select! {
        () = sleep(delay) => true,
        changed = shutdown_rx.changed() => changed.is_ok() && !*shutdown_rx.borrow(),
    }
}

fn is_nonretryable_prepare_error(error: &QueueError) -> bool {
    matches!(
        error,
        QueueError::Payload(_)
            | QueueError::OwnedTaskIdentityMismatch(_)
            | QueueError::UnknownValue(_)
            | QueueError::InvalidClaim(_, _)
    )
}

trait TaskHandlerResultExt {
    fn with_default_error(self, task_type: TaskType) -> Self;
}

impl TaskHandlerResultExt for HandlerExecution {
    fn with_default_error(mut self, task_type: TaskType) -> Self {
        if !self.task_result.success
            && !self.task_result.deferred
            && self.task_result.error_message.is_none()
        {
            self.task_result.error_message = Some(format!("{task_type} returned false"));
        }
        self
    }
}

#[derive(Debug, Error)]
pub enum WorkerError {
    #[error(transparent)]
    Queue(#[from] QueueError),
    #[error("a handler is already registered for {0}")]
    DuplicateHandler(TaskType),
    #[error("no handler is registered for explicitly scoped task {0}")]
    MissingScopedHandler(TaskType),
    #[error("Rust workers may only claim rows stamped for the Rust runtime")]
    NonRustRuntimeScope,
    #[error("worker claim scope must name task namespaces or one explicit task type")]
    UnboundedClaimScope,
    #[error("executor namespace {0:?} is not a registered task type")]
    InvalidExecutorNamespace(String),
    #[error("invalid worker configuration: {0}")]
    InvalidConfig(&'static str),
    #[error("task {task_id} product finalization failed")]
    HandlerFinalization {
        task_id: i64,
        #[source]
        source: Box<dyn std::error::Error + Send + Sync>,
    },
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use super::{heartbeat_interval, heartbeat_retry_interval};

    #[test]
    fn heartbeat_cadence_matches_python_bounds() {
        assert_eq!(
            heartbeat_interval(Duration::from_secs(300)),
            Duration::from_secs(30)
        );
        assert_eq!(
            heartbeat_interval(Duration::from_secs(15)),
            Duration::from_secs(5)
        );
        assert_eq!(
            heartbeat_retry_interval(Duration::from_secs(2)),
            Duration::from_secs(2)
        );
    }
}
