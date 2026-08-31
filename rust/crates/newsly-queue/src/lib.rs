//! Durable queue specifications and lease-fenced worker orchestration.
//!
//! Queue implementation moves here only with task ownership cutovers. It must preserve the
//! existing `PostgreSQL` compare-and-set lease, retry-generation, deferral, and notification rules.

#![forbid(unsafe_code)]

mod compatibility_json;
mod kernel;
mod model;
mod notifications;
mod ownership;

pub use compatibility_json::python_canonical_json;
pub use kernel::{
    ClaimRequest, EnqueueBatchResult, FencedFinalization, PrepareWorkOutcome, QueueError,
    QueueKernel,
};
pub use model::{
    ClaimedTask, EnqueueRequest, FinalizationOutcome, OwnedWorkPlan, PayloadError, QueueModelError,
    ResolvedFinalization, TaskQueue, TaskResult, TaskSpec, TaskStatus, TaskTransition, TaskType,
    UnknownQueueValue,
};
pub use notifications::{QueueNotificationHub, QueueNotificationWaiter, QueueWakeOutcome};

pub use ownership::{
    ClaimRuntimeScope, ExecutorFenceError, TaskExecutorStamp, verify_executor_fence,
};
