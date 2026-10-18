//! Exact-claim fenced private checkpoints and a retry-stable generation deadline.
use chrono::{DateTime, Utc};
use serde_json::json;
use sqlx::{FromRow, PgPool, Postgres, Transaction};
use uuid::Uuid;

#[derive(Debug, Clone)]
pub struct LearningDeckGenerationClaimFence {
    pub processing_task_id: i64,
    pub user_id: i64,
    pub llm_task_id: i64,
    pub deck_id: i64,
    pub locked_by: String,
    pub lease_token: Uuid,
    pub retry_count: i32,
    pub executor_runtime: String,
    pub executor_version: i32,
    pub executor_namespace: String,
}

#[derive(Debug, Clone, PartialEq, Eq, FromRow)]
pub struct LearningDeckGenerationState {
    pub input_fingerprint: String,
    pub deadline_at: DateTime<Utc>,
    pub remaining_seconds: i64,
    pub draft_object_key: Option<String>,
}

/// Starts the budget only after source preparation. Changed inputs discard the old private
/// checkpoint while preserving the original deadline across every automatic retry.
pub async fn begin_learning_deck_generation(
    pool: &PgPool,
    fence: &LearningDeckGenerationClaimFence,
    input_fingerprint: &str,
    budget_seconds: i64,
) -> Result<Option<LearningDeckGenerationState>, sqlx::Error> {
    let mut tx = pool.begin().await?;
    if !lock_owned_generation(&mut tx, fence).await? {
        return Ok(None);
    }
    sqlx::query(
        "INSERT INTO learning_deck_generation_state(llm_task_id,input_fingerprint,deadline_at)
         VALUES($1,$2,clock_timestamp() + make_interval(secs => $3::bigint::double precision))
         ON CONFLICT(llm_task_id) DO UPDATE
         SET input_fingerprint = EXCLUDED.input_fingerprint, draft_object_key = NULL
         WHERE learning_deck_generation_state.input_fingerprint <> EXCLUDED.input_fingerprint",
    )
    .bind(fence.llm_task_id)
    .bind(input_fingerprint)
    .bind(budget_seconds.clamp(1, 3540))
    .execute(&mut *tx)
    .await?;
    let state = sqlx::query_as::<_, LearningDeckGenerationState>(
        "SELECT input_fingerprint, deadline_at, draft_object_key,
         greatest(0, floor(extract(epoch FROM (deadline_at - clock_timestamp()))))::bigint
           AS remaining_seconds
         FROM learning_deck_generation_state WHERE llm_task_id = $1",
    )
    .bind(fence.llm_task_id)
    .fetch_one(&mut *tx)
    .await?;
    tx.commit().await?;
    Ok(Some(state))
}

/// Reads an existing budget under the original exact claim; never creates or extends one.
pub async fn read_learning_deck_generation(
    pool: &PgPool,
    fence: &LearningDeckGenerationClaimFence,
) -> Result<Option<LearningDeckGenerationState>, sqlx::Error> {
    let mut tx = pool.begin().await?;
    if !lock_owned_generation(&mut tx, fence).await? {
        return Ok(None);
    }
    let state = sqlx::query_as::<_, LearningDeckGenerationState>(
        "SELECT input_fingerprint, deadline_at, draft_object_key,
         greatest(0, floor(extract(epoch FROM (deadline_at - clock_timestamp()))))::bigint
           AS remaining_seconds
         FROM learning_deck_generation_state WHERE llm_task_id = $1",
    )
    .bind(fence.llm_task_id)
    .fetch_optional(&mut *tx)
    .await?;
    tx.commit().await?;
    Ok(state)
}

/// Publishes only a pointer to an already immutable, registered private snapshot.
pub async fn publish_learning_deck_draft(
    pool: &PgPool,
    fence: &LearningDeckGenerationClaimFence,
    input_fingerprint: &str,
    draft_object_key: &str,
) -> Result<bool, sqlx::Error> {
    let mut tx = pool.begin().await?;
    if !lock_owned_generation(&mut tx, fence).await? {
        return Ok(false);
    }
    let changed = sqlx::query(
        "UPDATE learning_deck_generation_state SET draft_object_key = $3
         WHERE llm_task_id = $1 AND input_fingerprint = $2 AND deadline_at > clock_timestamp()
         AND EXISTS(SELECT 1 FROM artifact_cleanup_candidates
         WHERE object_key = $3 AND llm_task_id = $1)",
    )
    .bind(fence.llm_task_id)
    .bind(input_fingerprint)
    .bind(draft_object_key)
    .execute(&mut *tx)
    .await?
    .rows_affected();
    tx.commit().await?;
    Ok(changed == 1)
}

/// Appends bounded stage progress while preserving the public running state.
pub async fn publish_learning_deck_progress(
    pool: &PgPool,
    fence: &LearningDeckGenerationClaimFence,
    note: &str,
) -> Result<bool, sqlx::Error> {
    let mut tx = pool.begin().await?;
    if !lock_owned_generation(&mut tx, fence).await? {
        return Ok(false);
    }
    let note: String = note.chars().take(256).collect();
    let entry = json!({
        "status": "running", "workflow_state": "running", "note": note,
        "created_at": Utc::now().naive_utc().format("%Y-%m-%dT%H:%M:%S%.6f").to_string(),
    });
    let changed = sqlx::query(
        "UPDATE llm_tasks SET status = 'running', workflow_state = 'running',
         updated_at = timezone('UTC', clock_timestamp()),
         status_history = COALESCE(status_history, '[]'::jsonb) || jsonb_build_array($2::jsonb)
         WHERE id::bigint = $1 AND EXISTS (SELECT 1 FROM learning_deck_generation_state
           WHERE llm_task_id = $1 AND deadline_at > clock_timestamp())",
    )
    .bind(fence.llm_task_id)
    .bind(entry)
    .execute(&mut *tx)
    .await?
    .rows_affected();
    tx.commit().await?;
    Ok(changed == 1)
}

/// Checks live ownership and cancellation without consuming or extending the generation budget.
pub async fn learning_deck_generation_is_owned(
    pool: &PgPool,
    fence: &LearningDeckGenerationClaimFence,
) -> Result<bool, sqlx::Error> {
    let mut tx = pool.begin().await?;
    let owned = lock_owned_generation(&mut tx, fence).await?;
    tx.commit().await?;
    Ok(owned)
}

async fn lock_owned_generation(
    tx: &mut Transaction<'_, Postgres>,
    fence: &LearningDeckGenerationClaimFence,
) -> Result<bool, sqlx::Error> {
    let queue: Option<i64> = sqlx::query_scalar(
        "SELECT id::bigint FROM processing_tasks WHERE id::bigint = $1
         AND owner_user_id::bigint = $2 AND task_type = 'run_llm_task' AND queue_name = 'llm'
         AND payload::jsonb @> jsonb_build_object('llm_task_id',$3::bigint,'user_id',$2::bigint)
         AND status = 'processing' AND locked_by = $4 AND lease_token = $5
         AND retry_count = $6 AND executor_runtime = $7 AND executor_version = $8
         AND executor_namespace = $9
         AND lease_expires_at > timezone('UTC', clock_timestamp()) FOR SHARE",
    )
    .bind(fence.processing_task_id)
    .bind(fence.user_id)
    .bind(fence.llm_task_id)
    .bind(&fence.locked_by)
    .bind(fence.lease_token)
    .bind(fence.retry_count)
    .bind(&fence.executor_runtime)
    .bind(fence.executor_version)
    .bind(&fence.executor_namespace)
    .fetch_optional(&mut **tx)
    .await?;
    if queue.is_none() {
        return Ok(false);
    }
    let task: Option<i64> = sqlx::query_scalar(
        "SELECT id::bigint FROM llm_tasks WHERE id::bigint = $1 AND user_id::bigint = $2
         AND subject_id::bigint = $3 AND task_kind = 'learning_deck'
         AND status IN ('preparing','running') FOR UPDATE",
    )
    .bind(fence.llm_task_id)
    .bind(fence.user_id)
    .bind(fence.deck_id)
    .fetch_optional(&mut **tx)
    .await?;
    if task.is_none() {
        return Ok(false);
    }
    // Deliberately do not lock the deck: deletion locks deck -> llm task and cancels that task
    // before committing. The task lock serializes publication with deletion without a cycle.
    sqlx::query_scalar(
        "SELECT EXISTS(SELECT 1 FROM learning_decks WHERE id::bigint = $1
         AND user_id::bigint = $2 AND latest_task_id::bigint = $3 AND deleted_at IS NULL)
         AND EXISTS(SELECT 1 FROM processing_tasks WHERE id::bigint = $4
         AND lease_expires_at > timezone('UTC', clock_timestamp()))
         AND EXISTS(SELECT 1 FROM users WHERE id::bigint = $2 AND is_active IS TRUE)",
    )
    .bind(fence.deck_id)
    .bind(fence.user_id)
    .bind(fence.llm_task_id)
    .bind(fence.processing_task_id)
    .fetch_one(&mut **tx)
    .await
}

#[cfg(test)]
mod tests;
