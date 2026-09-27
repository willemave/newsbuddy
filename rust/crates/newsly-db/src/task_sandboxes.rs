use chrono::{DateTime, NaiveDateTime, Utc};
use serde_json::{Value, json};
use sqlx::PgPool;
use thiserror::Error;
use uuid::Uuid;

const E2B_CPU_COST_PER_VCPU_SECOND_USD: f64 = 0.000_014;
const E2B_MEMORY_COST_PER_GIB_SECOND_USD: f64 = 0.000_004_5;
const E2B_PRICING_VERSION: &str = "e2b-public-2026-09-27";
const E2B_COST_BASIS: &str = "public_list_estimate";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RecordedTaskSandbox {
    pub session_id: Option<Uuid>,
    pub sandbox_id: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TaskSandboxCleanupCandidate {
    pub session_id: Uuid,
    pub task_id: i64,
    pub user_id: i64,
    pub sandbox_id: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct NewTaskSandboxSession<'a> {
    pub id: Uuid,
    pub task_id: i64,
    pub user_id: i64,
    pub feature: &'a str,
    pub template_id: &'a str,
    pub template_revision: &'a str,
    pub timeout_seconds: i32,
    pub requested_at: DateTime<Utc>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TaskSandboxProviderInfo {
    pub sandbox_id: String,
    pub started_at: Option<DateTime<Utc>>,
    pub end_at: Option<DateTime<Utc>>,
    pub cpu_count: Option<i32>,
    pub memory_mb: Option<i32>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TaskSandboxEnd {
    Confirmed { observed_at: DateTime<Utc> },
    Missing { observed_at: DateTime<Utc> },
    TimeoutBound { observed_at: DateTime<Utc> },
}

impl TaskSandboxEnd {
    fn observed_at(self) -> DateTime<Utc> {
        match self {
            Self::Confirmed { observed_at }
            | Self::Missing { observed_at }
            | Self::TimeoutBound { observed_at } => observed_at,
        }
    }

    const fn basis(self) -> &'static str {
        match self {
            Self::Confirmed { .. } => "kill_confirmed",
            Self::Missing { .. } => "sandbox_missing",
            Self::TimeoutBound { .. } => "timeout_bound",
        }
    }
}

pub async fn find_recorded_task_sandbox(
    pool: &PgPool,
    task_id: i64,
    user_id: i64,
) -> Result<Option<RecordedTaskSandbox>, TaskSandboxRepositoryError> {
    validate(task_id, user_id)?;
    let row = sqlx::query_as::<_, (Option<String>, Option<Uuid>)>(
        r#"
        SELECT tasks.sandbox_id, sessions.id
        FROM llm_tasks AS tasks
        LEFT JOIN task_sandbox_sessions AS sessions
          ON sessions.llm_task_id = tasks.id
         AND sessions.sandbox_id = tasks.sandbox_id
         AND sessions.ended_at IS NULL
        WHERE tasks.id::bigint = $1
          AND tasks.user_id::bigint = $2
          AND tasks.status NOT IN ('completed', 'failed', 'cancelled')
        ORDER BY sessions.requested_at DESC NULLS LAST
        LIMIT 1
        "#,
    )
    .bind(task_id)
    .bind(user_id)
    .fetch_optional(pool)
    .await?
    .ok_or(TaskSandboxRepositoryError::AttemptUnavailable)?;
    Ok(row.0.map(|sandbox_id| RecordedTaskSandbox {
        session_id: row.1,
        sandbox_id,
    }))
}

/// Persists an accounting intent before the E2B create request can incur cost.
pub async fn begin_task_sandbox_session(
    pool: &PgPool,
    session: &NewTaskSandboxSession<'_>,
) -> Result<(), TaskSandboxRepositoryError> {
    validate_session(session)?;
    let idempotency_key = format!("e2b:{}", session.id);
    let metadata = json!({
        "billing_status": "creating",
        "session_id": session.id,
        "template_id": session.template_id,
        "template_revision": session.template_revision,
        "timeout_seconds": session.timeout_seconds,
        "requested_at": session.requested_at,
        "duration_basis": "provider_started_at_to_kill_ack",
        "pricing_source": "https://e2b.dev/pricing",
    });
    let mut transaction = pool.begin().await?;
    let live_task = sqlx::query_scalar::<_, i64>(
        r#"
        SELECT id::bigint FROM llm_tasks
        WHERE id::bigint = $1 AND user_id::bigint = $2
          AND status NOT IN ('completed', 'failed', 'cancelled')
        FOR SHARE
        "#,
    )
    .bind(session.task_id)
    .bind(session.user_id)
    .fetch_optional(&mut *transaction)
    .await?;
    if live_task.is_none() {
        return Err(TaskSandboxRepositoryError::AttemptUnavailable);
    }
    let session_inserted = sqlx::query(
        r#"
        INSERT INTO task_sandbox_sessions (
            id, llm_task_id, user_id, feature, template_id, template_revision,
            timeout_seconds, requested_at
        )
        VALUES ($1, $2::bigint::integer, $3::bigint::integer, $4, $5, $6, $7,
                $8::timestamptz AT TIME ZONE 'UTC')
        "#,
    )
    .bind(session.id)
    .bind(session.task_id)
    .bind(session.user_id)
    .bind(session.feature)
    .bind(session.template_id)
    .bind(session.template_revision)
    .bind(session.timeout_seconds)
    .bind(session.requested_at)
    .execute(&mut *transaction)
    .await?;
    exact_update(session_inserted.rows_affected())?;
    let usage_inserted = sqlx::query(
        r#"
        INSERT INTO vendor_usage_records (
            provider, model, feature, operation, source, task_id, user_id,
            request_count, cost_usd, currency, pricing_version, cost_basis,
            idempotency_key, metadata, created_at
        )
        VALUES ('e2b', $1, $2, 'sandbox.runtime', 'queue', $3::bigint::integer,
                $4::bigint::integer, 1, NULL, 'USD', NULL, NULL, $5, $6,
                $7::timestamptz AT TIME ZONE 'UTC')
        ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING
        "#,
    )
    .bind(session.template_revision)
    .bind(session.feature)
    .bind(session.task_id)
    .bind(session.user_id)
    .bind(idempotency_key)
    .bind(metadata)
    .bind(session.requested_at)
    .execute(&mut *transaction)
    .await?;
    exact_update(usage_inserted.rows_affected())?;
    transaction.commit().await?;
    Ok(())
}

/// Attaches the remote identity even if the task became terminal while create was in flight.
pub async fn attach_task_sandbox(
    pool: &PgPool,
    session_id: Uuid,
    task_id: i64,
    user_id: i64,
    info: &TaskSandboxProviderInfo,
) -> Result<bool, TaskSandboxRepositoryError> {
    validate_provider_info(session_id, task_id, user_id, info)?;
    let mut transaction = pool.begin().await?;
    let attached = sqlx::query(
        r#"
        UPDATE task_sandbox_sessions
        SET sandbox_id = $2, provider_started_at = $3, provider_end_at = $4,
            cpu_count = $5, memory_mb = $6,
            updated_at = timezone('UTC', clock_timestamp())
        WHERE id = $1 AND llm_task_id::bigint = $7 AND user_id::bigint = $8
          AND ended_at IS NULL AND sandbox_id IS NULL
        "#,
    )
    .bind(session_id)
    .bind(&info.sandbox_id)
    .bind(info.started_at)
    .bind(info.end_at)
    .bind(info.cpu_count)
    .bind(info.memory_mb)
    .bind(task_id)
    .bind(user_id)
    .execute(&mut *transaction)
    .await?;
    exact_update(attached.rows_affected())?;
    let task_attached = sqlx::query(
        r#"
        UPDATE llm_tasks
        SET sandbox_provider = 'e2b', sandbox_id = $3,
            sandbox_cleanup_required = FALSE,
            updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $1 AND user_id::bigint = $2
          AND status NOT IN ('completed', 'failed', 'cancelled')
        "#,
    )
    .bind(task_id)
    .bind(user_id)
    .bind(&info.sandbox_id)
    .execute(&mut *transaction)
    .await?
    .rows_affected()
        == 1;
    let usage_attached = sqlx::query(
        r#"
        UPDATE vendor_usage_records
        SET request_id = $2,
            metadata = metadata::jsonb || $3::jsonb
        WHERE idempotency_key = $1
        "#,
    )
    .bind(format!("e2b:{session_id}"))
    .bind(&info.sandbox_id)
    .bind(json!({
        "billing_status": "running",
        "sandbox_id": info.sandbox_id,
        "provider_started_at": info.started_at,
        "provider_end_at": info.end_at,
        "cpu_count": info.cpu_count,
        "memory_mb": info.memory_mb,
    }))
    .execute(&mut *transaction)
    .await?;
    exact_update(usage_attached.rows_affected())?;
    transaction.commit().await?;
    Ok(task_attached)
}

pub async fn mark_task_sandbox_cleanup_required(
    pool: &PgPool,
    session_id: Uuid,
    task_id: i64,
    user_id: i64,
    sandbox_id: &str,
) -> Result<(), TaskSandboxRepositoryError> {
    validate_sandbox_identity(task_id, user_id, sandbox_id)?;
    let updated = sqlx::query(
        r#"
        UPDATE task_sandbox_sessions
        SET cleanup_required = TRUE, updated_at = timezone('UTC', clock_timestamp())
        WHERE id = $1 AND llm_task_id::bigint = $2 AND user_id::bigint = $3
          AND sandbox_id = $4 AND ended_at IS NULL
        "#,
    )
    .bind(session_id)
    .bind(task_id)
    .bind(user_id)
    .bind(sandbox_id)
    .execute(pool)
    .await?;
    exact_update(updated.rows_affected())
}

/// Finalizes the provisional usage row and session state atomically.
pub async fn finalize_task_sandbox_session(
    pool: &PgPool,
    session_id: Uuid,
    task_id: i64,
    user_id: i64,
    sandbox_id: Option<&str>,
    end: TaskSandboxEnd,
) -> Result<bool, TaskSandboxRepositoryError> {
    validate(task_id, user_id)?;
    if let Some(sandbox_id) = sandbox_id {
        validate_sandbox_identity(task_id, user_id, sandbox_id)?;
    }
    let mut transaction = pool.begin().await?;
    let session = sqlx::query_as::<_, TaskSandboxSessionRow>(
        r#"
        SELECT feature, template_id, template_revision, timeout_seconds,
               requested_at, provider_started_at, cpu_count, memory_mb, ended_at
        FROM task_sandbox_sessions
        WHERE id = $1 AND llm_task_id::bigint = $2 AND user_id::bigint = $3
        FOR UPDATE
        "#,
    )
    .bind(session_id)
    .bind(task_id)
    .bind(user_id)
    .fetch_optional(&mut *transaction)
    .await?
    .ok_or(TaskSandboxRepositoryError::AttemptUnavailable)?;
    if session.ended_at.is_some() {
        transaction.rollback().await?;
        return Ok(false);
    }

    let observed_at = end.observed_at();
    let priced = price_confirmed_runtime(&session, end, observed_at);
    let metadata_patch = usage_end_metadata(&session, end, observed_at, priced.as_ref());
    let (resource_count, cost_usd, pricing_version, cost_basis) =
        priced.map_or((None, None, None, None), |priced| {
            (
                Some(priced.billed_seconds),
                Some(priced.cost_usd),
                Some(E2B_PRICING_VERSION),
                Some(E2B_COST_BASIS),
            )
        });
    let usage_finalized = sqlx::query(
        r#"
        UPDATE vendor_usage_records
        SET resource_count = $2, cost_usd = $3, pricing_version = $4, cost_basis = $5,
            metadata = metadata::jsonb || $6::jsonb
        WHERE idempotency_key = $1
        "#,
    )
    .bind(format!("e2b:{session_id}"))
    .bind(resource_count)
    .bind(cost_usd)
    .bind(pricing_version)
    .bind(cost_basis)
    .bind(metadata_patch)
    .execute(&mut *transaction)
    .await?;
    exact_update(usage_finalized.rows_affected())?;
    sqlx::query(
        r#"
        UPDATE task_sandbox_sessions
        SET ended_at = $2::timestamptz AT TIME ZONE 'UTC', end_basis = $3,
            cleanup_required = FALSE, updated_at = timezone('UTC', clock_timestamp())
        WHERE id = $1
        "#,
    )
    .bind(session_id)
    .bind(observed_at)
    .bind(end.basis())
    .execute(&mut *transaction)
    .await?;
    if let Some(sandbox_id) = sandbox_id {
        sqlx::query(
            r#"
            UPDATE llm_tasks
            SET sandbox_provider = NULL, sandbox_id = NULL,
                sandbox_cleanup_required = FALSE,
                updated_at = timezone('UTC', clock_timestamp())
            WHERE id::bigint = $1 AND user_id::bigint = $2 AND sandbox_id = $3
            "#,
        )
        .bind(task_id)
        .bind(user_id)
        .bind(sandbox_id)
        .execute(&mut *transaction)
        .await?;
    }
    transaction.commit().await?;
    Ok(true)
}

pub async fn list_task_sandbox_cleanup_candidates(
    pool: &PgPool,
    limit: i64,
) -> Result<Vec<TaskSandboxCleanupCandidate>, TaskSandboxRepositoryError> {
    if !(1..=100).contains(&limit) {
        return Err(TaskSandboxRepositoryError::InvalidInput);
    }
    let rows = sqlx::query_as::<_, (Uuid, i64, i64, Option<String>)>(
        r#"
        SELECT sessions.id, sessions.llm_task_id::bigint, sessions.user_id::bigint,
               sessions.sandbox_id
        FROM task_sandbox_sessions AS sessions
        LEFT JOIN llm_tasks AS tasks ON tasks.id = sessions.llm_task_id
        WHERE sessions.ended_at IS NULL
          AND (
              sessions.cleanup_required = TRUE
              OR tasks.id IS NULL
              OR tasks.status IN ('completed', 'failed', 'cancelled')
              OR timezone('UTC', clock_timestamp()) >= sessions.requested_at
                   + make_interval(secs => sessions.timeout_seconds + 60)
          )
        ORDER BY sessions.requested_at, sessions.id
        LIMIT $1
        "#,
    )
    .bind(limit)
    .fetch_all(pool)
    .await?;
    Ok(rows
        .into_iter()
        .map(
            |(session_id, task_id, user_id, sandbox_id)| TaskSandboxCleanupCandidate {
                session_id,
                task_id,
                user_id,
                sandbox_id,
            },
        )
        .collect())
}

#[derive(Debug, sqlx::FromRow)]
struct TaskSandboxSessionRow {
    feature: String,
    template_id: String,
    template_revision: String,
    timeout_seconds: i32,
    requested_at: NaiveDateTime,
    provider_started_at: Option<DateTime<Utc>>,
    cpu_count: Option<i32>,
    memory_mb: Option<i32>,
    ended_at: Option<NaiveDateTime>,
}

#[derive(Debug)]
struct PricedRuntime {
    duration_milliseconds: i64,
    duration_seconds: f64,
    billed_seconds: i32,
    cost_usd: f64,
}

fn price_confirmed_runtime(
    session: &TaskSandboxSessionRow,
    end: TaskSandboxEnd,
    observed_at: DateTime<Utc>,
) -> Option<PricedRuntime> {
    if !matches!(end, TaskSandboxEnd::Confirmed { .. }) {
        return None;
    }
    let started_at = session.provider_started_at?;
    let cpu_count = session.cpu_count?;
    let memory_mb = session.memory_mb?;
    let elapsed = observed_at
        .signed_duration_since(started_at)
        .max(chrono::TimeDelta::zero());
    let duration_milliseconds = elapsed.num_milliseconds();
    let duration_seconds = elapsed.to_std().map_or(0.0, |value| value.as_secs_f64());
    let memory_gib = f64::from(memory_mb) / 1_024.0;
    let rate = f64::from(cpu_count) * E2B_CPU_COST_PER_VCPU_SECOND_USD
        + memory_gib * E2B_MEMORY_COST_PER_GIB_SECOND_USD;
    let billed_seconds = i32::try_from((duration_milliseconds + 999) / 1_000).unwrap_or(i32::MAX);
    Some(PricedRuntime {
        duration_milliseconds,
        duration_seconds,
        billed_seconds,
        cost_usd: duration_seconds * rate,
    })
}

fn usage_end_metadata(
    session: &TaskSandboxSessionRow,
    end: TaskSandboxEnd,
    observed_at: DateTime<Utc>,
    priced: Option<&PricedRuntime>,
) -> Value {
    let requested_at = session.requested_at.and_utc();
    let upper_bound_seconds = observed_at
        .signed_duration_since(requested_at)
        .max(chrono::TimeDelta::zero())
        .to_std()
        .map_or(0.0, |value| value.as_secs_f64());
    let mut value = json!({
        "billing_status": if priced.is_some() { "priced" } else { "unpriced" },
        "end_basis": end.basis(),
        "observed_ended_at": observed_at,
        "requested_at": requested_at,
        "provider_started_at": session.provider_started_at,
        "cpu_count": session.cpu_count,
        "memory_mb": session.memory_mb,
        "timeout_seconds": session.timeout_seconds,
        "template_id": session.template_id,
        "template_revision": session.template_revision,
        "feature": session.feature,
        "upper_bound_seconds": upper_bound_seconds,
        "cpu_cost_per_vcpu_second_usd": E2B_CPU_COST_PER_VCPU_SECOND_USD,
        "memory_cost_per_gib_second_usd": E2B_MEMORY_COST_PER_GIB_SECOND_USD,
        "pricing_source": "https://e2b.dev/pricing",
    });
    if let (Some(object), Some(priced)) = (value.as_object_mut(), priced) {
        object.insert(
            "duration_milliseconds".to_owned(),
            json!(priced.duration_milliseconds),
        );
        object.insert(
            "duration_seconds".to_owned(),
            json!(priced.duration_seconds),
        );
        object.insert(
            "resource_count_unit".to_owned(),
            json!("rounded_runtime_seconds"),
        );
    }
    value
}

fn validate_session(session: &NewTaskSandboxSession<'_>) -> Result<(), TaskSandboxRepositoryError> {
    validate(session.task_id, session.user_id)?;
    validate_text(session.feature, 100)?;
    validate_text(session.template_id, 255)?;
    validate_text(session.template_revision, 255)?;
    if session.timeout_seconds <= 0 {
        return Err(TaskSandboxRepositoryError::InvalidInput);
    }
    Ok(())
}

fn validate_provider_info(
    session_id: Uuid,
    task_id: i64,
    user_id: i64,
    info: &TaskSandboxProviderInfo,
) -> Result<(), TaskSandboxRepositoryError> {
    if session_id.is_nil()
        || info.cpu_count.is_some_and(|value| value <= 0)
        || info.memory_mb.is_some_and(|value| value <= 0)
    {
        return Err(TaskSandboxRepositoryError::InvalidInput);
    }
    validate_sandbox_identity(task_id, user_id, &info.sandbox_id)
}

fn validate_sandbox_identity(
    task_id: i64,
    user_id: i64,
    sandbox_id: &str,
) -> Result<(), TaskSandboxRepositoryError> {
    validate(task_id, user_id)?;
    validate_text(sandbox_id, 255)
}

fn validate_text(value: &str, maximum: usize) -> Result<(), TaskSandboxRepositoryError> {
    if value.trim().is_empty()
        || value.trim() != value
        || value.len() > maximum
        || value.chars().any(char::is_control)
    {
        Err(TaskSandboxRepositoryError::InvalidInput)
    } else {
        Ok(())
    }
}

fn exact_update(rows_affected: u64) -> Result<(), TaskSandboxRepositoryError> {
    if rows_affected == 1 {
        Ok(())
    } else {
        Err(TaskSandboxRepositoryError::AttemptUnavailable)
    }
}

fn validate(task_id: i64, user_id: i64) -> Result<(), TaskSandboxRepositoryError> {
    if task_id <= 0 || user_id <= 0 {
        Err(TaskSandboxRepositoryError::InvalidInput)
    } else {
        Ok(())
    }
}

#[derive(Debug, Error)]
pub enum TaskSandboxRepositoryError {
    #[error("task sandbox identity is invalid")]
    InvalidInput,
    #[error("LLM task attempt is no longer available")]
    AttemptUnavailable,
    #[error("task sandbox database operation failed")]
    Sqlx(#[from] sqlx::Error),
}

#[cfg(test)]
mod tests {
    use chrono::{TimeZone, Utc};
    use sqlx::PgPool;
    use uuid::Uuid;

    use super::{
        NewTaskSandboxSession, TaskSandboxEnd, TaskSandboxProviderInfo, TaskSandboxRepositoryError,
        attach_task_sandbox, begin_task_sandbox_session, finalize_task_sandbox_session,
        mark_task_sandbox_cleanup_required, validate,
    };

    async fn fixture(pool: &PgPool, suffix: &str) -> (i64, i64) {
        let user_id: i64 = sqlx::query_scalar(
            "INSERT INTO users (apple_id, email, is_admin, is_active) VALUES ($1, $2, FALSE, TRUE) RETURNING id::bigint",
        )
        .bind(format!("sandbox-{suffix}"))
        .bind(format!("sandbox-{suffix}@example.test"))
        .fetch_one(pool)
        .await
        .unwrap();
        let task_id: i64 = sqlx::query_scalar(
            r#"
            INSERT INTO llm_tasks (
                user_id, task_kind, mode, workflow_key, workflow_version, workflow_state,
                status, approval_policy, allowed_actions, tool_policy, input_json, output_json,
                artifact_manifest, usage_json, status_history, created_at, updated_at
            )
            VALUES ($1::bigint::integer, 'chat', 'chat', $2, 1, 'running', 'running',
                    '{}'::jsonb, '[]'::jsonb, '[]'::jsonb, '{}'::jsonb, '{}'::jsonb,
                    '{}'::jsonb, '{}'::jsonb, '[]'::jsonb,
                    timezone('UTC', clock_timestamp()), timezone('UTC', clock_timestamp()))
            RETURNING id::bigint
            "#,
        )
        .bind(user_id)
        .bind(format!("sandbox-{suffix}"))
        .fetch_one(pool)
        .await
        .unwrap();
        (user_id, task_id)
    }

    #[sqlx::test]
    async fn lifecycle_records_provisional_and_confirmed_usage_once(pool: PgPool) {
        let (user_id, task_id) = fixture(&pool, "confirmed").await;
        let session_id = Uuid::new_v4();
        let requested_at = Utc.with_ymd_and_hms(2026, 9, 27, 12, 0, 0).unwrap();
        begin_task_sandbox_session(
            &pool,
            &NewTaskSandboxSession {
                id: session_id,
                task_id,
                user_id,
                feature: "chat",
                template_id: "newsly-agent",
                template_revision: "revision-1",
                timeout_seconds: 300,
                requested_at,
            },
        )
        .await
        .unwrap();
        let provisional: (Option<f64>, String) = sqlx::query_as(
            "SELECT cost_usd, metadata->>'billing_status' FROM vendor_usage_records WHERE idempotency_key = $1",
        )
        .bind(format!("e2b:{session_id}"))
        .fetch_one(&pool)
        .await
        .unwrap();
        assert_eq!(provisional, (None, "creating".to_owned()));

        let started_at = requested_at + chrono::Duration::milliseconds(400);
        let info = TaskSandboxProviderInfo {
            sandbox_id: "sandbox-confirmed".to_owned(),
            started_at: Some(started_at),
            end_at: Some(requested_at + chrono::Duration::seconds(300)),
            cpu_count: Some(2),
            memory_mb: Some(2_048),
        };
        assert!(
            attach_task_sandbox(&pool, session_id, task_id, user_id, &info)
                .await
                .unwrap()
        );
        mark_task_sandbox_cleanup_required(&pool, session_id, task_id, user_id, &info.sandbox_id)
            .await
            .unwrap();
        let observed_at = started_at + chrono::Duration::milliseconds(1_500);
        assert!(
            finalize_task_sandbox_session(
                &pool,
                session_id,
                task_id,
                user_id,
                Some(&info.sandbox_id),
                TaskSandboxEnd::Confirmed { observed_at },
            )
            .await
            .unwrap()
        );
        assert!(
            !finalize_task_sandbox_session(
                &pool,
                session_id,
                task_id,
                user_id,
                Some(&info.sandbox_id),
                TaskSandboxEnd::Missing { observed_at },
            )
            .await
            .unwrap()
        );
        let usage: (i64, Option<i32>, Option<f64>, Option<String>, Option<String>, String) =
            sqlx::query_as(
                "SELECT count(*) OVER (), resource_count, cost_usd, pricing_version, cost_basis, metadata->>'billing_status' FROM vendor_usage_records WHERE idempotency_key = $1",
            )
            .bind(format!("e2b:{session_id}"))
            .fetch_one(&pool)
            .await
            .unwrap();
        assert_eq!(usage.0, 1);
        assert_eq!(usage.1, Some(2));
        assert!((usage.2.unwrap() - 0.000_055_5).abs() < 1e-12);
        assert_eq!(usage.3.as_deref(), Some("e2b-public-2026-09-27"));
        assert_eq!(usage.4.as_deref(), Some("public_list_estimate"));
        assert_eq!(usage.5, "priced");
    }

    #[sqlx::test]
    async fn missing_sandbox_stays_unpriced_and_retry_is_idempotent(pool: PgPool) {
        let (user_id, task_id) = fixture(&pool, "missing").await;
        let session_id = Uuid::new_v4();
        let requested_at = Utc.with_ymd_and_hms(2026, 9, 27, 12, 0, 0).unwrap();
        begin_task_sandbox_session(
            &pool,
            &NewTaskSandboxSession {
                id: session_id,
                task_id,
                user_id,
                feature: "learning_deck",
                template_id: "newsly-agent",
                template_revision: "revision-1",
                timeout_seconds: 300,
                requested_at,
            },
        )
        .await
        .unwrap();
        let observed_at = requested_at + chrono::Duration::seconds(361);
        assert!(
            finalize_task_sandbox_session(
                &pool,
                session_id,
                task_id,
                user_id,
                None,
                TaskSandboxEnd::TimeoutBound { observed_at },
            )
            .await
            .unwrap()
        );
        assert!(
            !finalize_task_sandbox_session(
                &pool,
                session_id,
                task_id,
                user_id,
                None,
                TaskSandboxEnd::TimeoutBound { observed_at },
            )
            .await
            .unwrap()
        );
        let usage: (Option<i32>, Option<f64>, Option<String>, Option<String>, String) =
            sqlx::query_as(
                "SELECT resource_count, cost_usd, pricing_version, cost_basis, metadata->>'end_basis' FROM vendor_usage_records WHERE idempotency_key = $1",
            )
            .bind(format!("e2b:{session_id}"))
            .fetch_one(&pool)
            .await
            .unwrap();
        assert_eq!(usage, (None, None, None, None, "timeout_bound".to_owned()));
    }

    #[test]
    fn task_sandbox_attempt_requires_positive_identities() {
        assert!(validate(1, 1).is_ok());
        assert!(matches!(
            validate(0, 1),
            Err(TaskSandboxRepositoryError::InvalidInput)
        ));
        assert!(validate(1, -1).is_err());
    }
}
