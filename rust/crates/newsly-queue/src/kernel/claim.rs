//! Atomic claim updates and bounded retry of per-owner execution conflicts.

use sqlx::{PgPool, Postgres, QueryBuilder};
use uuid::Uuid;

use crate::ClaimedTask;

use super::{
    ClaimRequest, ClaimRow, QueueError, normalized_lease_seconds, push_optional_claim_filters,
};

pub(super) async fn claim_retry_bucket(
    pool: &PgPool,
    request: &ClaimRequest,
    retry_count: i32,
) -> Result<Option<ClaimedTask>, QueueError> {
    // Two snapshots can both miss the same owner's uncommitted processing claim. The unique
    // index is authoritative; retry only that conflict in a fresh transaction/snapshot.
    for _ in 0..3 {
        match claim_retry_bucket_once(pool, request, retry_count).await {
            Err(QueueError::Sqlx(sqlx::Error::Database(error)))
                if error.code().as_deref() == Some("23505")
                    && matches!(
                        error.constraint(),
                        Some(
                            "uq_processing_tasks_briefing_owner_processing"
                                | "uq_processing_tasks_learning_deck_owner_processing"
                        )
                    ) => {}
            result => return result,
        }
    }
    Ok(None)
}

async fn claim_retry_bucket_once(
    pool: &PgPool,
    request: &ClaimRequest,
    retry_count: i32,
) -> Result<Option<ClaimedTask>, QueueError> {
    let lease_token = Uuid::new_v4();
    let lease_seconds = normalized_lease_seconds(request.lease_duration)?;
    let mut query = QueryBuilder::<Postgres>::new(CANDIDATE_PREFIX_SQL);
    query.push_bind(retry_count);
    query.push(" AND executor_runtime = ");
    query.push_bind(request.runtime_scope.runtime().as_str());
    push_optional_claim_filters(&mut query, request);
    query.push(
        r"
            ORDER BY priority DESC,
                     available_at ASC, created_at ASC, id ASC
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
    let row = match query
        .build_query_as::<ClaimRow>()
        .fetch_optional(&mut *transaction)
        .await
    {
        Ok(row) => row,
        Err(error) => {
            transaction.rollback().await?;
            return Err(error.into());
        }
    };
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

const CANDIDATE_PREFIX_SQL: &str = r"
        WITH candidate AS (
            SELECT id FROM processing_tasks AS candidate_task
            WHERE (
                (status = 'pending' AND available_at <= timezone('UTC', now()))
                OR
                (status = 'processing' AND lease_expires_at IS NOT NULL
                    AND lease_expires_at <= timezone('UTC', now()))
            )
            AND (candidate_task.task_type <> 'briefing_refresh' OR NOT EXISTS (
                SELECT 1 FROM processing_tasks AS other
                WHERE other.owner_user_id = candidate_task.owner_user_id
                  AND other.task_type = 'briefing_refresh' AND other.status = 'processing'
                  AND other.id <> candidate_task.id
            ))
            AND (candidate_task.task_type <> 'run_llm_task'
                OR candidate_task.payload ->> 'task_kind' IS DISTINCT FROM 'learning_deck'
                OR NOT EXISTS (
                    SELECT 1 FROM processing_tasks AS other
                    WHERE other.owner_user_id = candidate_task.owner_user_id
                      AND other.task_type = 'run_llm_task'
                      AND other.payload ->> 'task_kind' = 'learning_deck'
                      AND (
                          other.status = 'processing'
                          OR (other.status = 'pending' AND EXISTS (
                              SELECT 1 FROM learning_deck_generation_state AS generation
                              WHERE generation.llm_task_id::text = other.payload ->> 'llm_task_id'
                          ))
                      )
                      AND other.id <> candidate_task.id
                ))
            AND retry_count =
        ";
