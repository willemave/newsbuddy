use std::collections::{BTreeSet, HashMap};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use chrono::{DateTime, NaiveDateTime, Utc};
use newsly_domain::{LeaseToken, RuntimeOwner};
use serde_json::{Map, Value, json};
use sqlx::{FromRow, PgPool, Postgres, QueryBuilder, Transaction};
use thiserror::Error;
use tracing::{debug, warn};
use uuid::Uuid;

use crate::{
    ClaimRuntimeScope, ClaimedTask, EnqueueRequest, FinalizationOutcome, OwnedWorkPlan,
    PayloadError, QueueModelError, ResolvedFinalization, TaskExecutorStamp, TaskQueue, TaskResult,
    TaskStatus, TaskTransition, TaskType, UnknownQueueValue, compatibility_canonical_json,
};

const QUEUE_NOTIFY_CHANNEL: &str = "processing_tasks";

#[derive(Debug, Clone)]
pub struct ClaimRequest {
    pub worker_id: String,
    pub lease_duration: Duration,
    pub task_type: Option<TaskType>,
    pub queue_name: Option<TaskQueue>,
    pub runtime_scope: ClaimRuntimeScope,
}

impl ClaimRequest {
    pub fn for_queue(
        worker_id: impl Into<String>,
        queue_name: TaskQueue,
        runtime_scope: ClaimRuntimeScope,
    ) -> Self {
        Self {
            worker_id: worker_id.into(),
            lease_duration: Duration::from_secs(300),
            task_type: None,
            queue_name: Some(queue_name),
            runtime_scope,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EnqueueBatchResult {
    pub task_ids: Vec<i64>,
    pub inserted_task_ids: Vec<i64>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum PrepareWorkOutcome {
    Execute(OwnedWorkPlan),
    SkipInactiveOwner(OwnedWorkPlan),
}

mod finalization;
pub use finalization::FencedFinalization;

#[derive(Debug, Clone)]
pub struct QueueKernel {
    pool: PgPool,
    retry_bucket_cursors: Arc<Mutex<HashMap<ClaimCursorKey, usize>>>,
}

impl QueueKernel {
    pub fn new(pool: PgPool) -> Self {
        Self {
            pool,
            retry_bucket_cursors: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    pub const fn pool(&self) -> &PgPool {
        &self.pool
    }

    /// Enqueues a batch atomically, stamping each row with the active task-runtime decision.
    /// Active-user locking, dedupe resolution, polling grants, and the single worker wake-up all
    /// happen in the caller transaction. A transition promotion cannot race task stamping because
    /// ownership rows are held under `FOR SHARE` until commit.
    ///
    /// # Errors
    ///
    /// Fails closed when task ownership, payload, user state, or dedupe ownership cannot be proven.
    pub async fn enqueue_many(
        &self,
        requests: Vec<EnqueueRequest>,
    ) -> Result<EnqueueBatchResult, QueueError> {
        let mut transaction = self.pool.begin().await?;
        let result = self
            .enqueue_many_in_transaction(&mut transaction, requests)
            .await?;
        transaction.commit().await?;
        Ok(result)
    }

    /// Enqueues a batch inside an existing transaction. This is the product-state handoff used by
    /// lease-fenced handler finalization so state changes, provider usage, downstream work, and the
    /// owning task transition can commit together.
    ///
    /// The caller owns commit or rollback. Notifications are transactional `pg_notify` calls and
    /// therefore become visible only if that caller commits.
    ///
    /// # Errors
    ///
    /// Fails closed under the same ownership, payload, user, and dedupe rules as
    /// [`Self::enqueue_many`].
    pub async fn enqueue_many_in_transaction(
        &self,
        transaction: &mut Transaction<'_, Postgres>,
        requests: Vec<EnqueueRequest>,
    ) -> Result<EnqueueBatchResult, QueueError> {
        if requests.is_empty() {
            return Ok(EnqueueBatchResult {
                task_ids: Vec::new(),
                inserted_task_ids: Vec::new(),
            });
        }

        let prepared_without_stamp = requests
            .into_iter()
            .map(PreparedEnqueueWithoutStamp::try_from)
            .collect::<Result<Vec<_>, _>>()?;
        let task_types = prepared_without_stamp
            .iter()
            .map(|request| request.task_type)
            .collect::<BTreeSet<_>>();

        let ownership = load_task_ownership(transaction, &task_types).await?;
        let prepared = prepared_without_stamp
            .into_iter()
            .map(|request| {
                let stamp = ownership.get(&request.task_type).ok_or_else(|| {
                    QueueError::OwnershipUnavailable {
                        task_type: request.task_type,
                    }
                })?;
                Ok(request.with_stamp(stamp))
            })
            .collect::<Result<Vec<_>, QueueError>>()?;

        lock_active_users(transaction, &prepared).await?;
        let mut task_ids = Vec::with_capacity(prepared.len());
        let mut inserted_task_ids = Vec::new();
        for request in &prepared {
            let (task_id, inserted, durable_owner_user_id) =
                insert_or_resolve_task(transaction, request).await?;
            if request.owner_user_id.is_some() && request.owner_user_id != durable_owner_user_id {
                return Err(QueueError::DeduplicatedTaskOwnedByAnotherUser {
                    task_id,
                    requested_owner_user_id: request.owner_user_id,
                    durable_owner_user_id,
                });
            }
            if let Some(access_user_id) = request.access_user_id {
                grant_access(transaction, task_id, access_user_id).await?;
            }
            task_ids.push(task_id);
            if inserted {
                inserted_task_ids.push(task_id);
            }
        }

        if !inserted_task_ids.is_empty() {
            let payload = json!({
                "task_ids": inserted_task_ids.iter().take(100).collect::<Vec<_>>(),
                "count": inserted_task_ids.len(),
            });
            sqlx::query("SELECT pg_notify($1, $2)")
                .bind(QUEUE_NOTIFY_CHANNEL)
                .bind(payload.to_string())
                .execute(&mut **transaction)
                .await?;
        }
        Ok(EnqueueBatchResult {
            task_ids,
            inserted_task_ids,
        })
    }

    /// Enqueues one request using the same atomic ownership, dedupe, access, and notification path
    /// as a batch.
    ///
    /// # Errors
    ///
    /// Returns an enqueue validation, ownership, user, dedupe, or database error.
    pub async fn enqueue(&self, request: EnqueueRequest) -> Result<i64, QueueError> {
        let result = self.enqueue_many(vec![request]).await?;
        result
            .task_ids
            .into_iter()
            .next()
            .ok_or(QueueError::MissingEnqueueResult)
    }

    /// Grants an active user access to poll an existing task.
    ///
    /// # Errors
    ///
    /// Returns an error for a missing/inactive user, missing task, or database failure.
    pub async fn grant_access(&self, task_id: i64, user_id: i64) -> Result<(), QueueError> {
        database_id(task_id, "task_id")?;
        database_id(user_id, "user_id")?;
        let mut transaction = self.pool.begin().await?;
        self.grant_access_in_transaction(&mut transaction, task_id, user_id)
            .await?;
        transaction.commit().await?;
        Ok(())
    }

    /// Grants polling access inside an existing product-state transaction.
    ///
    /// This is the atomic reuse path for producers that discover an already-active task. The
    /// caller owns commit or rollback, so task visibility cannot escape without the corresponding
    /// product mutation.
    ///
    /// # Errors
    ///
    /// Returns an error for invalid identifiers, a missing/inactive user, a missing task, or a
    /// database failure.
    pub async fn grant_access_in_transaction(
        &self,
        transaction: &mut Transaction<'_, Postgres>,
        task_id: i64,
        user_id: i64,
    ) -> Result<(), QueueError> {
        database_id(task_id, "task_id")?;
        database_id(user_id, "user_id")?;
        lock_active_user_ids(transaction, &[user_id]).await?;
        let exists = sqlx::query_scalar::<_, bool>(
            "SELECT EXISTS(SELECT 1 FROM processing_tasks WHERE id::bigint = $1)",
        )
        .bind(task_id)
        .fetch_one(&mut **transaction)
        .await?;
        if !exists {
            return Err(QueueError::TaskNotFound(task_id));
        }
        grant_access(transaction, task_id, user_id).await?;
        Ok(())
    }

    /// Claims one ready task using `FOR UPDATE SKIP LOCKED` and a new opaque lease token.
    /// Retry generations are rotated best-effort so a hot generation-zero stream cannot starve
    /// retries. Executor runtime and optional namespace scope are durable claim filters.
    ///
    /// # Errors
    ///
    /// Returns an error for invalid worker configuration, durable rows, or database access.
    pub async fn claim(&self, request: &ClaimRequest) -> Result<Option<ClaimedTask>, QueueError> {
        validate_claim_request(request)?;
        let retry_counts = list_claimable_retry_counts(&self.pool, request).await?;
        if retry_counts.is_empty() {
            return Ok(None);
        }
        let ordered_retry_counts = self.rotate_retry_counts(request, retry_counts)?;
        for retry_count in ordered_retry_counts {
            if let Some(claim) = claim_retry_bucket(&self.pool, request, retry_count).await? {
                debug!(
                    task_id = claim.id,
                    task_type = %claim.task_type,
                    queue_name = %claim.queue_name,
                    worker_id = %claim.locked_by,
                    retry_count = claim.retry_count,
                    executor_runtime = %claim.executor_runtime,
                    executor_version = claim.executor_version,
                    executor_namespace = %claim.executor_namespace,
                    "task dequeued"
                );
                return Ok(Some(claim));
            }
        }
        Ok(None)
    }

    /// Renews only the exact unexpired lease generation originally claimed.
    ///
    /// # Errors
    ///
    /// Database errors are distinct from a compare-and-set rejection (`Ok(false)`).
    pub async fn renew_lease(
        &self,
        claim: &ClaimedTask,
        lease_duration: Duration,
    ) -> Result<bool, QueueError> {
        let lease_seconds = normalized_lease_seconds(lease_duration)?;
        let result = sqlx::query(
            r"
            UPDATE processing_tasks
            SET
                locked_at = timezone('UTC', now()),
                lease_expires_at = timezone('UTC', now()) + $1 * interval '1 second'
            WHERE
                id::bigint = $2
                AND status = 'processing'
                AND locked_by = $3
                AND lease_token = $4
                AND lease_expires_at > timezone('UTC', clock_timestamp())
                AND retry_count = $5
                AND executor_runtime = $6
                AND executor_version = $7
                AND executor_namespace = $8
            ",
        )
        .bind(lease_seconds)
        .bind(claim.id)
        .bind(&claim.locked_by)
        .bind(claim.lease_token.get())
        .bind(claim.retry_count)
        .bind(claim.executor_runtime.as_str())
        .bind(claim.executor_version)
        .bind(&claim.executor_namespace)
        .execute(&self.pool)
        .await?;
        Ok(result.rows_affected() == 1)
    }

    /// Persists success, terminal failure, retry, or deferral in a fresh transaction guarded by
    /// the complete lease and immutable executor stamp.
    ///
    /// # Errors
    ///
    /// Returns validation/database errors separately from stale ownership (`Ok(None)`).
    pub async fn finalize(
        &self,
        claim: &ClaimedTask,
        result: &TaskResult,
        max_retries: i32,
    ) -> Result<Option<TaskTransition>, QueueError> {
        let Some(finalization) = self
            .begin_fenced_finalization(claim, result, max_retries)
            .await?
        else {
            return Ok(None);
        };
        let transition = finalization.finish().await?;
        if transition.is_none() {
            warn!(
                task_id = claim.id,
                worker_id = %claim.locked_by,
                lease_token = %claim.lease_token,
                "task finalization rejected because lease ownership was lost"
            );
        }
        Ok(transition)
    }

    /// Locks the exact live claim and opens the only transaction allowed to publish its product
    /// state. The returned capability must be finished to commit.
    ///
    /// # Errors
    ///
    /// Returns validation or database errors separately from a stale lease (`Ok(None)`).
    pub async fn begin_fenced_finalization(
        &self,
        claim: &ClaimedTask,
        result: &TaskResult,
        max_retries: i32,
    ) -> Result<Option<FencedFinalization>, QueueError> {
        let resolved = ResolvedFinalization::from_result(claim, result, max_retries)?;
        let mut transaction = self.pool.begin().await?;
        let owned_task_id = sqlx::query_scalar::<_, i64>(
            r"
            SELECT id::bigint
            FROM processing_tasks
            WHERE
                id::bigint = $1
                AND status = 'processing'
                AND locked_by = $2
                AND lease_token = $3
                AND lease_expires_at > timezone('UTC', now())
                AND retry_count = $4
                AND executor_runtime = $5
                AND executor_version = $6
                AND executor_namespace = $7
            FOR UPDATE
            ",
        )
        .bind(claim.id)
        .bind(&claim.locked_by)
        .bind(claim.lease_token.get())
        .bind(claim.retry_count)
        .bind(claim.executor_runtime.as_str())
        .bind(claim.executor_version)
        .bind(&claim.executor_namespace)
        .fetch_optional(&mut *transaction)
        .await?;
        if owned_task_id.is_none() {
            transaction.rollback().await?;
            warn!(
                task_id = claim.id,
                worker_id = %claim.locked_by,
                lease_token = %claim.lease_token,
                "task product finalization rejected because lease ownership was lost"
            );
            return Ok(None);
        }
        Ok(Some(FencedFinalization {
            transaction,
            claim: claim.clone(),
            resolved,
        }))
    }

    /// Converts a still-pending task to a terminal failed/cancelled representation for an
    /// unscoped maintenance operation. Runtime task handlers should use
    /// [`Self::cancel_pending_fenced`] or [`Self::cancel_claim`]. The public task status contract
    /// has no `cancelled` state, so cancellation is represented by `failed` plus a stable reason.
    ///
    /// # Errors
    ///
    /// Returns an error for an invalid identifier/reason or a database failure.
    pub async fn cancel_pending(&self, task_id: i64, reason: &str) -> Result<bool, QueueError> {
        database_id(task_id, "task_id")?;
        validate_reason(reason)?;
        let result = sqlx::query(
            r"
            UPDATE processing_tasks
            SET
                status = 'failed',
                completed_at = timezone('UTC', now()),
                error_message = $1,
                locked_at = NULL,
                locked_by = NULL,
                lease_token = NULL,
                lease_expires_at = NULL
            WHERE id::bigint = $2 AND status = 'pending'
            ",
        )
        .bind(reason)
        .bind(task_id)
        .execute(&self.pool)
        .await?;
        Ok(result.rows_affected() == 1)
    }

    /// Cancels one pending task only when its immutable executor stamp is still exactly the one
    /// observed by the caller.
    ///
    /// # Errors
    ///
    /// Returns an error for an invalid identifier/reason or a database failure.
    pub async fn cancel_pending_fenced(
        &self,
        task_id: i64,
        executor: &TaskExecutorStamp,
        reason: &str,
    ) -> Result<bool, QueueError> {
        database_id(task_id, "task_id")?;
        validate_reason(reason)?;
        let result = sqlx::query(
            r"
            UPDATE processing_tasks
            SET
                status = 'failed',
                completed_at = timezone('UTC', now()),
                error_message = $1,
                locked_at = NULL,
                locked_by = NULL,
                lease_token = NULL,
                lease_expires_at = NULL
            WHERE
                id::bigint = $2
                AND status = 'pending'
                AND executor_runtime = $3
                AND executor_version = $4
                AND executor_namespace = $5
            ",
        )
        .bind(reason)
        .bind(task_id)
        .bind(executor.runtime.as_str())
        .bind(executor.ownership_version.get())
        .bind(executor.namespace.as_str())
        .execute(&self.pool)
        .await?;
        Ok(result.rows_affected() == 1)
    }

    /// Cancels a processing task only for the exact unexpired claim owner.
    ///
    /// # Errors
    ///
    /// Returns an error for an invalid reason/durable value or a database failure.
    pub async fn cancel_claim(
        &self,
        claim: &ClaimedTask,
        reason: &str,
    ) -> Result<Option<TaskTransition>, QueueError> {
        validate_reason(reason)?;
        let result = TaskResult::fail(Some(reason.to_owned()), false);
        let Some(finalization) = self.begin_fenced_finalization(claim, &result, 0).await? else {
            return Ok(None);
        };
        finalization.finish().await
    }

    /// Deletes pending account-owned work and reports whether any other account task is still
    /// processing. This preserves the account-deletion kernel's transactional contract.
    ///
    /// # Errors
    ///
    /// Returns an error for invalid identifiers or a database failure.
    pub async fn cancel_pending_for_user(
        &self,
        user_id: i64,
        current_task_id: i64,
    ) -> Result<bool, QueueError> {
        database_id(user_id, "user_id")?;
        database_id(current_task_id, "current_task_id")?;
        let mut transaction = self.pool.begin().await?;
        sqlx::query(
            r"
            DELETE FROM processing_tasks
            WHERE id::bigint <> $1 AND owner_user_id::bigint = $2 AND status = 'pending'
            ",
        )
        .bind(current_task_id)
        .bind(user_id)
        .execute(&mut *transaction)
        .await?;
        let active_exists = sqlx::query_scalar::<_, bool>(
            r"
            SELECT EXISTS(
                SELECT 1
                FROM processing_tasks
                WHERE id::bigint <> $1
                    AND owner_user_id::bigint = $2
                    AND status = 'processing'
            )
            ",
        )
        .bind(current_task_id)
        .bind(user_id)
        .fetch_one(&mut *transaction)
        .await?;
        transaction.commit().await?;
        Ok(!active_exists)
    }

    /// Performs the short prepare transaction for an immutable external-work plan. No database
    /// transaction or pooled connection survives this method.
    ///
    /// # Errors
    ///
    /// Invalid/mismatched owner identities and malformed payloads fail without dispatching work.
    pub async fn prepare_work(
        &self,
        claim: &ClaimedTask,
    ) -> Result<PrepareWorkOutcome, QueueError> {
        let normalized = claim
            .task_type
            .normalize_payload(Some(claim.payload.clone()))?;
        let mut plan = OwnedWorkPlan::from(claim);
        plan.payload = normalized;

        if !claim.task_type.spec().requires_owner {
            return Ok(PrepareWorkOutcome::Execute(plan));
        }
        let owner_user_id = claim
            .owner_user_id
            .ok_or(QueueError::OwnedTaskIdentityMismatch(claim.id))?;
        let payload_user_id = plan
            .payload
            .get("user_id")
            .and_then(Value::as_i64)
            .filter(|value| *value > 0);
        if payload_user_id != Some(owner_user_id) {
            return Err(QueueError::OwnedTaskIdentityMismatch(claim.id));
        }

        let mut transaction = self.pool.begin().await?;
        let active = sqlx::query_scalar::<_, bool>(
            r"
            SELECT is_active
            FROM users
            WHERE id::bigint = $1
            FOR SHARE
            ",
        )
        .bind(owner_user_id)
        .fetch_optional(&mut *transaction)
        .await?
        .unwrap_or(false);
        transaction.commit().await?;
        if active {
            Ok(PrepareWorkOutcome::Execute(plan))
        } else {
            Ok(PrepareWorkOutcome::SkipInactiveOwner(plan))
        }
    }

    fn rotate_retry_counts(
        &self,
        request: &ClaimRequest,
        retry_counts: Vec<i32>,
    ) -> Result<Vec<i32>, QueueError> {
        if retry_counts.len() <= 1 {
            return Ok(retry_counts);
        }
        let key = ClaimCursorKey::from_request(request);
        let mut cursors = self
            .retry_bucket_cursors
            .lock()
            .map_err(|_| QueueError::RetryCursorPoisoned)?;
        let cursor = cursors.entry(key).or_insert(0);
        let start = *cursor % retry_counts.len();
        *cursor = (start + 1) % retry_counts.len();
        let mut ordered = retry_counts[start..].to_vec();
        ordered.extend_from_slice(&retry_counts[..start]);
        Ok(ordered)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
struct ClaimCursorKey {
    runtime: RuntimeOwner,
    queue_name: Option<TaskQueue>,
    task_type: Option<TaskType>,
    namespaces: Option<Vec<String>>,
}

impl ClaimCursorKey {
    fn from_request(request: &ClaimRequest) -> Self {
        Self {
            runtime: request.runtime_scope.runtime(),
            queue_name: request.queue_name,
            task_type: request.task_type,
            namespaces: request
                .runtime_scope
                .namespace_values()
                .map(|values| values.into_iter().map(str::to_owned).collect::<Vec<_>>()),
        }
    }
}

#[derive(Debug)]
struct PreparedEnqueueWithoutStamp {
    task_type: TaskType,
    content_id: Option<i64>,
    payload: Map<String, Value>,
    queue_name: TaskQueue,
    dedupe_key: Option<String>,
    owner_user_id: Option<i64>,
    access_user_id: Option<i64>,
    available_at: DateTime<Utc>,
}

impl TryFrom<EnqueueRequest> for PreparedEnqueueWithoutStamp {
    type Error = QueueError;

    fn try_from(request: EnqueueRequest) -> Result<Self, Self::Error> {
        if let Some(content_id) = request.content_id {
            positive_id(content_id, "content_id")?;
        }
        let payload = request.task_type.normalize_payload(request.payload)?;
        let spec = request.task_type.spec();
        let queue_name = request.queue_name.unwrap_or(spec.queue);
        let owner_user_id = request
            .owner_user_id
            .map(|value| positive_id(value, "owner_user_id"))
            .transpose()?;
        let mut access_user_id = request
            .access_user_id
            .map(|value| positive_id(value, "access_user_id"))
            .transpose()?;
        let payload_user_id = payload
            .get("user_id")
            .map(|value| {
                value
                    .as_i64()
                    .ok_or(QueueError::InvalidPositiveId("payload.user_id"))
                    .and_then(|value| positive_id(value, "payload.user_id"))
            })
            .transpose()?;
        if spec.requires_owner && owner_user_id.is_none() {
            return Err(QueueError::OwnerRequired(request.task_type));
        }
        if spec.requires_owner && payload_user_id != owner_user_id {
            return Err(QueueError::OwnerPayloadMismatch(request.task_type));
        }
        if owner_user_id.is_some() && access_user_id.is_none() {
            access_user_id = owner_user_id;
        }

        let should_dedupe = request.dedupe.unwrap_or(spec.dedupe_by_content);
        let dedupe_key = request.dedupe_key.or_else(|| {
            build_task_dedupe_key(
                request.task_type,
                request.content_id,
                queue_name,
                &payload,
                should_dedupe,
            )
        });
        if dedupe_key.as_ref().is_some_and(|key| key.len() > 512) {
            return Err(QueueError::DedupeKeyTooLong);
        }

        Ok(Self {
            task_type: request.task_type,
            content_id: request.content_id,
            payload,
            queue_name,
            dedupe_key,
            owner_user_id,
            access_user_id,
            available_at: request.available_at.unwrap_or_else(Utc::now),
        })
    }
}

#[derive(Debug)]
struct PreparedEnqueue {
    task_type: TaskType,
    content_id: Option<i64>,
    payload: Map<String, Value>,
    queue_name: TaskQueue,
    dedupe_key: Option<String>,
    owner_user_id: Option<i64>,
    access_user_id: Option<i64>,
    available_at: DateTime<Utc>,
    executor_runtime: RuntimeOwner,
    executor_version: i64,
    executor_namespace: String,
}

impl PreparedEnqueueWithoutStamp {
    fn with_stamp(self, stamp: &ExecutorOwnershipRow) -> PreparedEnqueue {
        PreparedEnqueue {
            task_type: self.task_type,
            content_id: self.content_id,
            payload: self.payload,
            queue_name: self.queue_name,
            dedupe_key: self.dedupe_key,
            owner_user_id: self.owner_user_id,
            access_user_id: self.access_user_id,
            available_at: self.available_at,
            executor_runtime: stamp.active_owner,
            executor_version: stamp.active_version,
            executor_namespace: self.task_type.as_str().to_owned(),
        }
    }
}

fn build_task_dedupe_key(
    task_type: TaskType,
    content_id: Option<i64>,
    queue_name: TaskQueue,
    payload: &Map<String, Value>,
    should_dedupe: bool,
) -> Option<String> {
    if !should_dedupe {
        return None;
    }
    let mut parts = vec![queue_name.to_string(), task_type.to_string()];
    if let Some(content_id) = content_id {
        parts.push(format!("content:{content_id}"));
    } else if !payload.is_empty() {
        parts.push(format!("payload:{}", canonical_json_object(payload)));
    }
    Some(parts.join("|"))
}

fn canonical_json_object(value: &Map<String, Value>) -> String {
    compatibility_canonical_json(&Value::Object(value.clone()))
}

#[derive(Debug, FromRow)]
struct RawExecutorOwnershipRow {
    resource_key: String,
    active_owner: String,
    active_version: i64,
    transition_state: String,
}

#[derive(Debug)]
struct ExecutorOwnershipRow {
    active_owner: RuntimeOwner,
    active_version: i64,
}

async fn load_task_ownership(
    transaction: &mut Transaction<'_, Postgres>,
    task_types: &BTreeSet<TaskType>,
) -> Result<HashMap<TaskType, ExecutorOwnershipRow>, QueueError> {
    let keys = task_types
        .iter()
        .map(|task_type| task_type.as_str().to_owned())
        .collect::<Vec<_>>();
    let rows = sqlx::query_as::<_, RawExecutorOwnershipRow>(
        r"
        SELECT resource_key, active_owner, active_version, transition_state
        FROM runtime_ownership
        WHERE resource_kind = 'task_type' AND resource_key::text = ANY($1::text[])
        ORDER BY resource_key
        FOR SHARE
        ",
    )
    .bind(&keys)
    .fetch_all(&mut **transaction)
    .await?;
    let mut ownership = HashMap::with_capacity(rows.len());
    for row in rows {
        let task_type = row.resource_key.parse::<TaskType>()?;
        let active_owner = row.active_owner.parse::<RuntimeOwner>()?;
        if row.active_version <= 0
            || !matches!(row.transition_state.as_str(), "active" | "preparing")
        {
            return Err(QueueError::InvalidOwnershipState(task_type));
        }
        ownership.insert(
            task_type,
            ExecutorOwnershipRow {
                active_owner,
                active_version: row.active_version,
            },
        );
    }
    if let Some(missing) = task_types
        .iter()
        .find(|task_type| !ownership.contains_key(task_type))
    {
        return Err(QueueError::OwnershipUnavailable {
            task_type: *missing,
        });
    }
    Ok(ownership)
}

async fn lock_active_users(
    transaction: &mut Transaction<'_, Postgres>,
    requests: &[PreparedEnqueue],
) -> Result<(), QueueError> {
    let user_ids = requests
        .iter()
        .flat_map(|request| [request.owner_user_id, request.access_user_id])
        .flatten()
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect::<Vec<_>>();
    lock_active_user_ids(transaction, &user_ids).await
}

async fn lock_active_user_ids(
    transaction: &mut Transaction<'_, Postgres>,
    user_ids: &[i64],
) -> Result<(), QueueError> {
    if user_ids.is_empty() {
        return Ok(());
    }
    let active_ids = sqlx::query_scalar::<_, i64>(
        r"
        SELECT id::bigint
        FROM users
        WHERE id::bigint = ANY($1::bigint[]) AND is_active IS TRUE
        ORDER BY id
        FOR SHARE
        ",
    )
    .bind(user_ids)
    .fetch_all(&mut **transaction)
    .await?
    .into_iter()
    .collect::<BTreeSet<_>>();
    if user_ids.iter().any(|user_id| !active_ids.contains(user_id)) {
        return Err(QueueError::UserMissingOrInactive);
    }
    Ok(())
}

#[derive(Debug, FromRow)]
struct InsertedTaskRow {
    id: i64,
    owner_user_id: Option<i64>,
}

async fn insert_or_resolve_task(
    transaction: &mut Transaction<'_, Postgres>,
    request: &PreparedEnqueue,
) -> Result<(i64, bool, Option<i64>), QueueError> {
    let payload = Value::Object(request.payload.clone());
    let content_id = request
        .content_id
        .map(|value| postgres_integer(value, "content_id"))
        .transpose()?;
    let owner_user_id = request
        .owner_user_id
        .map(|value| postgres_integer(value, "owner_user_id"))
        .transpose()?;
    let inserted = if request.dedupe_key.is_some() {
        sqlx::query_as::<_, InsertedTaskRow>(
            r"
            INSERT INTO processing_tasks (
                task_type,
                content_id,
                payload,
                status,
                queue_name,
                available_at,
                dedupe_key,
                owner_user_id,
                executor_runtime,
                executor_version,
                executor_namespace
            )
            VALUES ($1, $2, $3, 'pending', $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT (dedupe_key)
                WHERE dedupe_key IS NOT NULL AND status IN ('pending', 'processing')
                DO NOTHING
            RETURNING id::bigint AS id, owner_user_id::bigint AS owner_user_id
            ",
        )
        .bind(request.task_type.as_str())
        .bind(content_id)
        .bind(payload)
        .bind(request.queue_name.as_str())
        .bind(request.available_at.naive_utc())
        .bind(&request.dedupe_key)
        .bind(owner_user_id)
        .bind(request.executor_runtime.as_str())
        .bind(request.executor_version)
        .bind(&request.executor_namespace)
        .fetch_optional(&mut **transaction)
        .await?
    } else {
        Some(
            sqlx::query_as::<_, InsertedTaskRow>(
                r"
                INSERT INTO processing_tasks (
                    task_type,
                    content_id,
                    payload,
                    status,
                    queue_name,
                    available_at,
                    dedupe_key,
                    owner_user_id,
                    executor_runtime,
                    executor_version,
                    executor_namespace
                )
                VALUES ($1, $2, $3, 'pending', $4, $5, NULL, $6, $7, $8, $9)
                RETURNING id::bigint AS id, owner_user_id::bigint AS owner_user_id
                ",
            )
            .bind(request.task_type.as_str())
            .bind(content_id)
            .bind(payload)
            .bind(request.queue_name.as_str())
            .bind(request.available_at.naive_utc())
            .bind(owner_user_id)
            .bind(request.executor_runtime.as_str())
            .bind(request.executor_version)
            .bind(&request.executor_namespace)
            .fetch_one(&mut **transaction)
            .await?,
        )
    };
    if let Some(inserted) = inserted {
        return Ok((inserted.id, true, inserted.owner_user_id));
    }
    let dedupe_key = request
        .dedupe_key
        .as_deref()
        .ok_or(QueueError::DedupeRace)?;
    let existing = sqlx::query_as::<_, InsertedTaskRow>(
        r"
        SELECT id::bigint AS id, owner_user_id::bigint AS owner_user_id
        FROM processing_tasks
        WHERE dedupe_key = $1 AND status IN ('pending', 'processing')
        ",
    )
    .bind(dedupe_key)
    .fetch_optional(&mut **transaction)
    .await?
    .ok_or(QueueError::DedupeRace)?;
    Ok((existing.id, false, existing.owner_user_id))
}

async fn grant_access(
    transaction: &mut Transaction<'_, Postgres>,
    task_id: i64,
    user_id: i64,
) -> Result<(), sqlx::Error> {
    let task_id = i32::try_from(task_id).expect("database task ids fit PostgreSQL INTEGER");
    let user_id = i32::try_from(user_id).expect("validated user ids fit PostgreSQL INTEGER");
    sqlx::query(
        r"
        INSERT INTO processing_task_user_access (task_id, user_id, created_at)
        VALUES ($1, $2, timezone('UTC', now()))
        ON CONFLICT (task_id, user_id) DO NOTHING
        ",
    )
    .bind(task_id)
    .bind(user_id)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

async fn list_claimable_retry_counts(
    pool: &PgPool,
    request: &ClaimRequest,
) -> Result<Vec<i32>, QueueError> {
    let mut query = QueryBuilder::<Postgres>::new(
        r"
        SELECT DISTINCT retry_count
        FROM processing_tasks
        WHERE (
            (status = 'pending' AND available_at <= timezone('UTC', now()))
            OR
            (status = 'processing' AND lease_expires_at IS NOT NULL
                AND lease_expires_at <= timezone('UTC', now()))
        )
        AND executor_runtime =
        ",
    );
    query.push_bind(request.runtime_scope.runtime().as_str());
    push_optional_claim_filters(&mut query, request);
    query.push(" ORDER BY retry_count");
    Ok(query.build_query_scalar::<i32>().fetch_all(pool).await?)
}

async fn claim_retry_bucket(
    pool: &PgPool,
    request: &ClaimRequest,
    retry_count: i32,
) -> Result<Option<ClaimedTask>, QueueError> {
    let lease_token = Uuid::new_v4();
    let lease_seconds = normalized_lease_seconds(request.lease_duration)?;
    let mut query = QueryBuilder::<Postgres>::new(
        r"
        WITH candidate AS (
            SELECT id
            FROM processing_tasks
            WHERE (
                (status = 'pending' AND available_at <= timezone('UTC', now()))
                OR
                (status = 'processing' AND lease_expires_at IS NOT NULL
                    AND lease_expires_at <= timezone('UTC', now()))
            )
            AND retry_count =
        ",
    );
    query.push_bind(retry_count);
    query.push(" AND executor_runtime = ");
    query.push_bind(request.runtime_scope.runtime().as_str());
    push_optional_claim_filters(&mut query, request);
    query.push(
        r"
            ORDER BY available_at ASC, created_at ASC, id ASC
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        UPDATE processing_tasks AS task
        SET
            status = 'processing',
            started_at = timezone('UTC', now()),
            locked_at = timezone('UTC', now()),
            locked_by =
        ",
    );
    query.push_bind(&request.worker_id);
    query.push(", lease_token = ");
    query.push_bind(lease_token);
    query.push(", lease_expires_at = timezone('UTC', now()) + ");
    query.push_bind(lease_seconds);
    query.push(
        r" * interval '1 second',
            retry_count = CASE
                WHEN task.status = 'processing'
                THEN task.retry_count + 1
                ELSE task.retry_count
            END
        FROM candidate
        WHERE task.id = candidate.id
        RETURNING
            task.id::bigint AS id,
            task.owner_user_id::bigint AS owner_user_id,
            task.task_type,
            task.content_id::bigint AS content_id,
            task.payload,
            task.retry_count,
            task.status,
            task.queue_name,
            task.executor_runtime,
            task.executor_version,
            task.executor_namespace,
            task.created_at,
            task.available_at,
            task.started_at,
            task.locked_at,
            task.locked_by,
            task.lease_token,
            task.lease_expires_at
        ",
    );
    let mut transaction = pool.begin().await?;
    let row = query
        .build_query_as::<ClaimRow>()
        .fetch_optional(&mut *transaction)
        .await?;
    let claim: Option<ClaimedTask> = row.map(TryInto::try_into).transpose()?;
    if let Some(claim) = &claim {
        if claim.locked_by != request.worker_id || claim.lease_token.get() != lease_token {
            return Err(QueueError::ClaimOwnershipMismatch(claim.id));
        }
        if !request.runtime_scope.allows(&claim.executor_stamp()?) {
            return Err(QueueError::ClaimScopeMismatch(claim.id));
        }
    }
    transaction.commit().await?;
    Ok(claim)
}

fn push_optional_claim_filters(query: &mut QueryBuilder<Postgres>, request: &ClaimRequest) {
    if let Some(task_type) = request.task_type {
        query.push(" AND task_type = ");
        query.push_bind(task_type.as_str());
    }
    if let Some(queue_name) = request.queue_name {
        query.push(" AND queue_name = ");
        query.push_bind(queue_name.as_str());
    }
    if let Some(namespaces) = request.runtime_scope.namespace_values() {
        query.push(" AND executor_namespace::text = ANY(");
        query.push_bind(
            namespaces
                .into_iter()
                .map(str::to_owned)
                .collect::<Vec<_>>(),
        );
        query.push("::text[])");
    }
}

#[derive(Debug, FromRow)]
struct ClaimRow {
    id: i64,
    owner_user_id: Option<i64>,
    task_type: String,
    content_id: Option<i64>,
    payload: Option<Value>,
    retry_count: i32,
    status: String,
    queue_name: String,
    executor_runtime: String,
    executor_version: i64,
    executor_namespace: String,
    created_at: Option<NaiveDateTime>,
    available_at: NaiveDateTime,
    started_at: Option<NaiveDateTime>,
    locked_at: Option<NaiveDateTime>,
    locked_by: Option<String>,
    lease_token: Option<Uuid>,
    lease_expires_at: Option<NaiveDateTime>,
}

impl TryFrom<ClaimRow> for ClaimedTask {
    type Error = QueueError;

    fn try_from(row: ClaimRow) -> Result<Self, Self::Error> {
        if row.retry_count < 0 {
            return Err(QueueError::InvalidClaim(row.id, "negative retry_count"));
        }
        let status = row.status.parse::<TaskStatus>()?;
        if status != TaskStatus::Processing {
            return Err(QueueError::InvalidClaim(row.id, "status is not processing"));
        }
        let payload = match row.payload {
            None => Map::new(),
            Some(Value::Object(payload)) => payload,
            Some(_) => return Err(QueueError::InvalidClaim(row.id, "payload is not an object")),
        };
        let locked_by = required_claim_value(row.id, row.locked_by, "locked_by")?;
        if locked_by.trim().is_empty() {
            return Err(QueueError::InvalidClaim(row.id, "locked_by is empty"));
        }
        if row.executor_version <= 0 || row.executor_namespace.trim().is_empty() {
            return Err(QueueError::InvalidClaim(row.id, "invalid executor stamp"));
        }
        Ok(Self {
            id: row.id,
            owner_user_id: row.owner_user_id,
            task_type: row.task_type.parse()?,
            content_id: row.content_id,
            payload,
            retry_count: row.retry_count,
            status,
            queue_name: row.queue_name.parse()?,
            executor_runtime: row.executor_runtime.parse()?,
            executor_version: row.executor_version,
            executor_namespace: row.executor_namespace,
            created_at: row.created_at.map(|value| value.and_utc()),
            available_at: row.available_at.and_utc(),
            started_at: required_claim_value(row.id, row.started_at, "started_at")?.and_utc(),
            locked_at: required_claim_value(row.id, row.locked_at, "locked_at")?.and_utc(),
            locked_by,
            lease_token: LeaseToken::from(required_claim_value(
                row.id,
                row.lease_token,
                "lease_token",
            )?),
            lease_expires_at: required_claim_value(
                row.id,
                row.lease_expires_at,
                "lease_expires_at",
            )?
            .and_utc(),
        })
    }
}

fn required_claim_value<T>(
    task_id: i64,
    value: Option<T>,
    field: &'static str,
) -> Result<T, QueueError> {
    value.ok_or(QueueError::InvalidClaim(task_id, field))
}

#[derive(Debug, FromRow)]
struct TransitionRow {
    task_type: String,
    queue_name: String,
    content_id: Option<i64>,
    error_message: Option<String>,
    status: String,
    retry_count: i32,
    available_at: NaiveDateTime,
}

#[allow(clippy::too_many_lines)]
async fn finalize_resolved(
    transaction: &mut Transaction<'_, Postgres>,
    claim: &ClaimedTask,
    resolved: &ResolvedFinalization,
) -> Result<Option<TaskTransition>, QueueError> {
    // Positive jitter spreads retries without shortening any provider-requested delay.
    let retry_delay = resolved.retry_delay_seconds.map(|delay| {
        if resolved.outcome == FinalizationOutcome::Retry {
            let spread = u128::try_from((delay / 5).clamp(0, 60)).unwrap_or(0);
            delay.saturating_add(
                i64::try_from(claim.lease_token.get().as_u128() % (spread + 1)).unwrap_or(0),
            )
        } else {
            delay
        }
    });
    let mut query = QueryBuilder::<Postgres>::new(
        r"
        UPDATE processing_tasks
        SET
            locked_at = NULL,
            locked_by = NULL,
            lease_token = NULL,
            lease_expires_at = NULL,
        ",
    );
    match resolved.outcome {
        FinalizationOutcome::Succeeded => {
            query.push(
                r"
                status = 'completed',
                completed_at = timezone('UTC', now()),
                error_message = NULL
                ",
            );
        }
        FinalizationOutcome::Failed => {
            query.push(
                r"
                status = 'failed',
                completed_at = timezone('UTC', now()),
                error_message =
                ",
            );
            query.push_bind(&resolved.error_message);
        }
        FinalizationOutcome::Retry | FinalizationOutcome::Deferred => {
            let delay = retry_delay.ok_or(QueueError::MissingRetryDelay)?;
            query.push(
                r"
                status = 'pending',
                started_at = NULL,
                completed_at = NULL,
                available_at = timezone('UTC', now()) +
                ",
            );
            query.push_bind(delay);
            query.push(" * interval '1 second', error_message = ");
            if resolved.outcome == FinalizationOutcome::Deferred {
                query.push("NULL");
            } else {
                query.push_bind(&resolved.error_message);
            }
            if resolved.outcome == FinalizationOutcome::Retry {
                query.push(", retry_count = retry_count + 1");
            }
        }
    }
    query.push(
        r"
        WHERE
            id::bigint =
        ",
    );
    query.push_bind(claim.id);
    query.push(" AND status = 'processing' AND locked_by = ");
    query.push_bind(&claim.locked_by);
    query.push(" AND lease_token = ");
    query.push_bind(claim.lease_token.get());
    // Unlike `now()`, `clock_timestamp()` advances during this already-open fenced transaction.
    // The second CAS therefore rolls back product writes if bounded persistence outlives the
    // lease after the initial `FOR UPDATE` check.
    query.push(" AND lease_expires_at > timezone('UTC', clock_timestamp()) AND retry_count = ");
    query.push_bind(claim.retry_count);
    query.push(" AND executor_runtime = ");
    query.push_bind(claim.executor_runtime.as_str());
    query.push(" AND executor_version = ");
    query.push_bind(claim.executor_version);
    query.push(" AND executor_namespace = ");
    query.push_bind(&claim.executor_namespace);
    query.push(
        r"
        RETURNING
            task_type,
            queue_name,
            content_id::bigint AS content_id,
            error_message,
            status,
            retry_count,
            available_at
        ",
    );
    let row = query
        .build_query_as::<TransitionRow>()
        .fetch_optional(&mut **transaction)
        .await?;
    row.map(|row| {
        Ok(TaskTransition {
            task_type: row.task_type.parse()?,
            queue_name: row.queue_name.parse()?,
            content_id: row.content_id,
            error_message: row.error_message,
            status: row.status.parse()?,
            retry_count: row.retry_count,
            retry_delay_seconds: retry_delay,
            deferred: resolved.outcome == FinalizationOutcome::Deferred,
            available_at: row.available_at.and_utc(),
        })
    })
    .transpose()
}

fn validate_claim_request(request: &ClaimRequest) -> Result<(), QueueError> {
    if request.worker_id.trim().is_empty() || request.worker_id.len() > 100 {
        return Err(QueueError::InvalidWorkerId);
    }
    normalized_lease_seconds(request.lease_duration)?;
    Ok(())
}

fn normalized_lease_seconds(duration: Duration) -> Result<f64, QueueError> {
    let seconds = duration.as_secs_f64().max(1.0);
    if !seconds.is_finite() || seconds > i32::MAX.into() {
        return Err(QueueError::InvalidLeaseDuration);
    }
    Ok(seconds)
}

fn positive_id(value: i64, field: &'static str) -> Result<i64, QueueError> {
    if value <= 0 {
        Err(QueueError::InvalidPositiveId(field))
    } else {
        Ok(value)
    }
}

fn database_id(value: i64, field: &'static str) -> Result<i64, QueueError> {
    positive_id(value, field)?;
    postgres_integer(value, field).map(i64::from)
}

fn postgres_integer(value: i64, field: &'static str) -> Result<i32, QueueError> {
    i32::try_from(value).map_err(|_| QueueError::IdentifierOutOfRange(field))
}

fn validate_reason(reason: &str) -> Result<(), QueueError> {
    if reason.trim().is_empty() {
        Err(QueueError::EmptyCancellationReason)
    } else {
        Ok(())
    }
}

#[derive(Debug, Error)]
pub enum QueueError {
    #[error("PostgreSQL queue operation failed")]
    Sqlx(#[from] sqlx::Error),
    #[error(transparent)]
    Payload(#[from] PayloadError),
    #[error(transparent)]
    Model(#[from] QueueModelError),
    #[error(transparent)]
    UnknownValue(#[from] UnknownQueueValue),
    #[error(transparent)]
    OwnershipValue(#[from] newsly_domain::InvalidOwnershipValue),
    #[error("task runtime ownership is not registered for {task_type}")]
    OwnershipUnavailable { task_type: TaskType },
    #[error("task runtime ownership is invalid for {0}")]
    InvalidOwnershipState(TaskType),
    #[error("{0} must be a positive integer")]
    InvalidPositiveId(&'static str),
    #[error("{0} exceeds the PostgreSQL INTEGER range")]
    IdentifierOutOfRange(&'static str),
    #[error("task {0} requires owner_user_id")]
    OwnerRequired(TaskType),
    #[error("task {0} owner_user_id must match payload user_id")]
    OwnerPayloadMismatch(TaskType),
    #[error("task user is missing or inactive")]
    UserMissingOrInactive,
    #[error("dedupe key exceeds the processing_tasks limit")]
    DedupeKeyTooLong,
    #[error("task dedupe conflict did not resolve an active task")]
    DedupeRace,
    #[error(
        "deduplicated task {task_id} belongs to {durable_owner_user_id:?}, not {requested_owner_user_id:?}"
    )]
    DeduplicatedTaskOwnedByAnotherUser {
        task_id: i64,
        requested_owner_user_id: Option<i64>,
        durable_owner_user_id: Option<i64>,
    },
    #[error("queue batch did not resolve every task id")]
    MissingEnqueueResult,
    #[error("task {0} does not exist")]
    TaskNotFound(i64),
    #[error("worker id must contain 1 to 100 nonblank bytes")]
    InvalidWorkerId,
    #[error("lease duration is out of range")]
    InvalidLeaseDuration,
    #[error("retry-bucket cursor lock is poisoned")]
    RetryCursorPoisoned,
    #[error("claimed task {0} ownership does not match the claim request")]
    ClaimOwnershipMismatch(i64),
    #[error("claimed task {0} lies outside the worker runtime scope")]
    ClaimScopeMismatch(i64),
    #[error("invalid claimed task {0}: {1}")]
    InvalidClaim(i64, &'static str),
    #[error("retry/deferred finalization is missing its resolved delay")]
    MissingRetryDelay,
    #[error("cancellation reason must be nonblank")]
    EmptyCancellationReason,
    #[error("owned task {0} identity is missing or inconsistent")]
    OwnedTaskIdentityMismatch(i64),
}

#[cfg(test)]
mod tests {
    use serde_json::{Map, Value, json};

    use super::build_task_dedupe_key;
    use crate::{TaskQueue, TaskType};

    #[test]
    fn dedupe_payload_is_canonical_and_ignored_when_content_is_present() {
        let payload: Map<String, Value> = serde_json::from_value(json!({"z": 1, "a": 2})).unwrap();
        assert_eq!(
            build_task_dedupe_key(
                TaskType::OnboardingDiscover,
                None,
                TaskQueue::Onboarding,
                &payload,
                true,
            ),
            Some("onboarding|onboarding_discover|payload:{\"a\":2,\"z\":1}".to_owned())
        );
        assert_eq!(
            build_task_dedupe_key(
                TaskType::ProcessContent,
                Some(42),
                TaskQueue::Content,
                &payload,
                true,
            ),
            Some("content|process_content|content:42".to_owned())
        );

        let nested_payload: Map<String, Value> = serde_json::from_value(json!({
            "z": {"nested_z": 1, "nested_a": 2},
            "a": [{"array_z": 3, "array_a": 4}]
        }))
        .unwrap();
        assert_eq!(
            build_task_dedupe_key(
                TaskType::OnboardingDiscover,
                None,
                TaskQueue::Onboarding,
                &nested_payload,
                true,
            ),
            Some(
                "onboarding|onboarding_discover|payload:{\"a\":[{\"array_a\":4,\"array_z\":3}],\"z\":{\"nested_a\":2,\"nested_z\":1}}"
                    .to_owned()
            )
        );

        let unicode_payload: Map<String, Value> =
            serde_json::from_value(json!({"query": "café 🚀"})).unwrap();
        assert_eq!(
            build_task_dedupe_key(
                TaskType::OnboardingDiscover,
                None,
                TaskQueue::Onboarding,
                &unicode_payload,
                true,
            ),
            Some(
                "onboarding|onboarding_discover|payload:{\"query\":\"caf\\u00e9 \\ud83d\\ude80\"}"
                    .to_owned()
            )
        );
    }
}
