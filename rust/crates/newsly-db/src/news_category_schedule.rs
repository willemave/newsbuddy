use std::str::FromStr;

use chrono::{DateTime, Duration, LocalResult, NaiveDate, NaiveDateTime, NaiveTime, TimeZone, Utc};
use chrono_tz::Tz;
use serde_json::Value;
use sqlx::{PgPool, Postgres, Transaction};
use thiserror::Error;
use uuid::Uuid;

const NOMINAL_HOUR: u32 = 3;
const WINDOW_END_HOUR: u32 = 5;
const MAX_CALENDAR_SEARCH_DAYS: i64 = 370;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NewsCategorySchedule {
    pub user_id: i64,
    pub timezone: Option<String>,
    pub timezone_revision: i64,
    pub next_due_at: Option<DateTime<Utc>>,
    pub next_local_date: Option<NaiveDate>,
    pub next_window_start_at: Option<DateTime<Utc>>,
    pub next_window_end_at: Option<DateTime<Utc>>,
    pub last_published_at: Option<DateTime<Utc>>,
    pub last_published_local_date: Option<NaiveDate>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NewsCategoryNight {
    pub local_date: NaiveDate,
    pub due_at: DateTime<Utc>,
    pub window_start_at: DateTime<Utc>,
    pub window_end_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NewsCategoryMaintenanceMode {
    Shadow,
    Publish,
}

impl NewsCategoryMaintenanceMode {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Shadow => "shadow",
            Self::Publish => "publish",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DueNewsCategoryRun {
    pub run_id: i64,
    pub user_id: i64,
    pub local_date: NaiveDate,
    pub timezone: String,
    pub timezone_revision: i64,
    pub scheduled_at: DateTime<Utc>,
    pub window_start_at: DateTime<Utc>,
    pub window_end_at: DateTime<Utc>,
    pub mode: NewsCategoryMaintenanceMode,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NewsCategoryRunClaimFence {
    pub locked_by: String,
    pub lease_token: Uuid,
    pub retry_count: i32,
    pub executor_runtime: String,
    pub executor_version: i64,
    pub executor_namespace: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NewsCategoryRunContext {
    pub task_id: i64,
    pub run: DueNewsCategoryRun,
    pub claim_fence: NewsCategoryRunClaimFence,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BeginNewsCategoryRunOutcome {
    Ready(NewsCategoryRunContext),
    Ineligible,
    SkippedWindow,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NewsCategoryRunStatus {
    NoOp,
    Shadowed,
    Published,
    Failed,
    SkippedWindow,
}

impl NewsCategoryRunStatus {
    const fn as_str(self) -> &'static str {
        match self {
            Self::NoOp => "no_op",
            Self::Shadowed => "shadowed",
            Self::Published => "published",
            Self::Failed => "failed",
            Self::SkippedWindow => "skipped_window",
        }
    }
}

#[derive(Debug, Error)]
pub enum NewsCategoryScheduleError {
    #[error("unknown IANA timezone {0:?}")]
    InvalidTimezone(String),
    #[error("user does not exist")]
    UserNotFound,
    #[error("timezone update requires current revision {current}")]
    StaleRevision { current: i64 },
    #[error("could not resolve a valid local night within the supported search horizon")]
    NoValidLocalNight,
    #[error("news-category maintenance mode is invalid")]
    InvalidMode,
    #[error("news-category schedule database operation failed")]
    Sqlx(#[from] sqlx::Error),
}

/// Validate an IANA timezone and return its stable persisted spelling.
pub fn canonicalize_timezone(value: &str) -> Result<String, NewsCategoryScheduleError> {
    let trimmed = value.trim();
    let timezone = Tz::from_str(trimmed)
        .map_err(|_| NewsCategoryScheduleError::InvalidTimezone(value.to_owned()))?;
    // Normalize the common UTC links so equivalent reports do not churn the revision.
    if matches!(
        trimmed,
        "Etc/UTC" | "Etc/GMT" | "GMT" | "UCT" | "Universal" | "Zulu"
    ) {
        return Ok("UTC".to_owned());
    }
    Ok(timezone.name().to_owned())
}

/// Resolve the next 03:00 local maintenance window using calendar arithmetic and IANA rules.
pub fn next_news_category_night(
    user_id: i64,
    timezone: &str,
    after: DateTime<Utc>,
    last_published_at: Option<DateTime<Utc>>,
) -> Result<NewsCategoryNight, NewsCategoryScheduleError> {
    let canonical = canonicalize_timezone(timezone)?;
    let timezone = Tz::from_str(&canonical)
        .map_err(|_| NewsCategoryScheduleError::InvalidTimezone(timezone.to_owned()))?;
    let first_date = after.with_timezone(&timezone).date_naive();
    let jitter = Duration::minutes(deterministic_jitter_minutes(user_id));
    let nominal_time = NaiveTime::from_hms_opt(NOMINAL_HOUR, 0, 0)
        .ok_or(NewsCategoryScheduleError::NoValidLocalNight)?;
    let window_end_time = NaiveTime::from_hms_opt(WINDOW_END_HOUR, 0, 0)
        .ok_or(NewsCategoryScheduleError::NoValidLocalNight)?;

    for offset in 0..MAX_CALENDAR_SEARCH_DAYS {
        let Some(local_date) = first_date.checked_add_signed(Duration::days(offset)) else {
            break;
        };
        let Some(window_start_at) =
            first_valid_at_or_after(timezone, local_date, nominal_time, window_end_time)
        else {
            continue;
        };
        let Some(window_end_at) =
            last_valid_at_or_before(timezone, local_date, window_end_time, nominal_time)
        else {
            continue;
        };
        let due_at = window_start_at + jitter;
        let too_soon_after_publish =
            last_published_at.is_some_and(|published| due_at < published + Duration::hours(12));
        if window_start_at >= window_end_at
            || due_at >= window_end_at
            || due_at <= after
            || too_soon_after_publish
        {
            continue;
        }
        return Ok(NewsCategoryNight {
            local_date,
            due_at,
            window_start_at,
            window_end_at,
        });
    }
    Err(NewsCategoryScheduleError::NoValidLocalNight)
}

/// Persist a client timezone report in the caller's account-update transaction.
pub async fn set_user_news_category_timezone(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    timezone: &str,
    expected_revision: Option<i64>,
) -> Result<NewsCategorySchedule, NewsCategoryScheduleError> {
    let timezone = canonicalize_timezone(timezone)?;
    let now = database_now(transaction).await?;
    let exists =
        sqlx::query_scalar::<_, i64>("SELECT id::bigint FROM users WHERE id::bigint=$1 FOR UPDATE")
            .bind(user_id)
            .fetch_optional(&mut **transaction)
            .await?
            .is_some();
    if !exists {
        return Err(NewsCategoryScheduleError::UserNotFound);
    }
    let existing = load_schedule_for_update(transaction, user_id).await?;
    if let Some(ref schedule) = existing {
        if schedule.timezone.as_deref() == Some(timezone.as_str()) {
            return Ok(schedule.clone());
        }
        if expected_revision != Some(schedule.timezone_revision) {
            return Err(NewsCategoryScheduleError::StaleRevision {
                current: schedule.timezone_revision,
            });
        }
    } else if expected_revision.is_some_and(|revision| revision != 0) {
        return Err(NewsCategoryScheduleError::StaleRevision { current: 0 });
    }
    let revision = existing
        .as_ref()
        .map_or(1, |schedule| schedule.timezone_revision + 1);
    let last_published_at = existing
        .as_ref()
        .and_then(|schedule| schedule.last_published_at);
    let last_published_local_date = existing
        .as_ref()
        .and_then(|schedule| schedule.last_published_local_date);
    let night = next_news_category_night(user_id, &timezone, now, last_published_at)?;
    sqlx::query(
        r#"
        INSERT INTO user_news_category_schedule (
            user_id, timezone, timezone_revision, next_due_at, next_local_date,
            next_window_start_at, next_window_end_at, last_published_at,
            last_published_local_date, updated_at
        ) VALUES (
            $1::bigint::integer, $2, $3, $4, $5, $6, $7, $8, $9, $10
        )
        ON CONFLICT (user_id) DO UPDATE SET
            timezone=EXCLUDED.timezone,
            timezone_revision=EXCLUDED.timezone_revision,
            next_due_at=EXCLUDED.next_due_at,
            next_local_date=EXCLUDED.next_local_date,
            next_window_start_at=EXCLUDED.next_window_start_at,
            next_window_end_at=EXCLUDED.next_window_end_at,
            updated_at=EXCLUDED.updated_at
        "#,
    )
    .bind(user_id)
    .bind(&timezone)
    .bind(revision)
    .bind(night.due_at.naive_utc())
    .bind(night.local_date)
    .bind(night.window_start_at.naive_utc())
    .bind(night.window_end_at.naive_utc())
    .bind(last_published_at.map(|value| value.naive_utc()))
    .bind(last_published_local_date)
    .bind(now.naive_utc())
    .execute(&mut **transaction)
    .await?;
    Ok(NewsCategorySchedule {
        user_id,
        timezone: Some(timezone),
        timezone_revision: revision,
        next_due_at: Some(night.due_at),
        next_local_date: Some(night.local_date),
        next_window_start_at: Some(night.window_start_at),
        next_window_end_at: Some(night.window_end_at),
        last_published_at,
        last_published_local_date,
    })
}

/// Lock a bounded due set, create the durable local-date identities, and advance each schedule.
/// The caller must enqueue returned runs before committing the same transaction.
pub async fn prepare_due_news_category_runs(
    transaction: &mut Transaction<'_, Postgres>,
    now: DateTime<Utc>,
    limit: i64,
    mode: NewsCategoryMaintenanceMode,
) -> Result<Vec<DueNewsCategoryRun>, NewsCategoryScheduleError> {
    if !(1..=1_000).contains(&limit) {
        return Err(NewsCategoryScheduleError::Sqlx(sqlx::Error::Protocol(
            "news-category due limit outside 1..=1000".to_owned(),
        )));
    }
    let settled = settle_stale_news_category_runs(transaction, now, limit).await?;
    if settled > 0 {
        tracing::info!(settled, "settled stale news-category maintenance runs");
    }
    let user_ids = sqlx::query_scalar::<_, i64>(
        r#"
        SELECT schedule.user_id::bigint
        FROM user_news_category_schedule AS schedule
        JOIN users AS app_user ON app_user.id = schedule.user_id
        WHERE schedule.timezone IS NOT NULL
          AND schedule.next_due_at <= $1
          AND app_user.is_active IS TRUE
          AND NOT EXISTS (
              SELECT 1 FROM onboarding_first_edition_runs AS onboarding
              WHERE onboarding.user_id=schedule.user_id
                AND onboarding.status='active'
                AND onboarding.news_seed_settled IS NOT TRUE
          )
        ORDER BY schedule.next_due_at, schedule.user_id
        FOR SHARE OF app_user SKIP LOCKED
        LIMIT $2
        "#,
    )
    .bind(now.naive_utc())
    .bind(limit)
    .fetch_all(&mut **transaction)
    .await?;
    let mut due = Vec::with_capacity(user_ids.len());
    for user_id in user_ids {
        let Some(row) = sqlx::query_as::<_, DueScheduleRow>(
            r#"
            SELECT user_id::bigint, timezone, timezone_revision, next_due_at,
                   next_local_date, next_window_start_at, next_window_end_at,
                   last_published_at
            FROM user_news_category_schedule
            WHERE user_id::bigint=$1 AND next_due_at <= $2
            FOR UPDATE
            "#,
        )
        .bind(user_id)
        .bind(now.naive_utc())
        .fetch_optional(&mut **transaction)
        .await?
        else {
            continue;
        };
        let timezone = row
            .timezone
            .ok_or(NewsCategoryScheduleError::NoValidLocalNight)?;
        let local_date = row
            .next_local_date
            .ok_or(NewsCategoryScheduleError::NoValidLocalNight)?;
        let scheduled_at = utc(row
            .next_due_at
            .ok_or(NewsCategoryScheduleError::NoValidLocalNight)?);
        let window_start_at = utc(row
            .next_window_start_at
            .ok_or(NewsCategoryScheduleError::NoValidLocalNight)?);
        let window_end_at = utc(row
            .next_window_end_at
            .ok_or(NewsCategoryScheduleError::NoValidLocalNight)?);
        let run_id = sqlx::query_scalar::<_, i64>(
            r#"
            INSERT INTO news_category_maintenance_runs (
                user_id, local_date, timezone, timezone_revision, scheduled_at,
                window_start_at, window_end_at, mode, status
            ) VALUES ($1::bigint::integer,$2,$3,$4,$5,$6,$7,$8,'scheduled')
            ON CONFLICT (user_id, local_date) DO NOTHING
            RETURNING id
            "#,
        )
        .bind(row.user_id)
        .bind(local_date)
        .bind(&timezone)
        .bind(row.timezone_revision)
        .bind(scheduled_at.naive_utc())
        .bind(window_start_at.naive_utc())
        .bind(window_end_at.naive_utc())
        .bind(mode.as_str())
        .fetch_optional(&mut **transaction)
        .await?;
        let next = next_news_category_night(
            row.user_id,
            &timezone,
            window_end_at,
            row.last_published_at.map(utc),
        )?;
        sqlx::query(
            r#"
            UPDATE user_news_category_schedule SET
                next_due_at=$2, next_local_date=$3, next_window_start_at=$4,
                next_window_end_at=$5, updated_at=$6
            WHERE user_id::bigint=$1 AND timezone_revision=$7
            "#,
        )
        .bind(row.user_id)
        .bind(next.due_at.naive_utc())
        .bind(next.local_date)
        .bind(next.window_start_at.naive_utc())
        .bind(next.window_end_at.naive_utc())
        .bind(now.naive_utc())
        .bind(row.timezone_revision)
        .execute(&mut **transaction)
        .await?;
        if let Some(run_id) = run_id {
            due.push(DueNewsCategoryRun {
                run_id,
                user_id: row.user_id,
                local_date,
                timezone,
                timezone_revision: row.timezone_revision,
                scheduled_at,
                window_start_at,
                window_end_at,
                mode,
            });
        }
    }
    Ok(due)
}

/// Settle runs whose publication window elapsed or whose durable queue task is terminal.
/// Users are locked before run rows to preserve the account/scheduler/finalizer lock order.
pub async fn settle_stale_news_category_runs(
    transaction: &mut Transaction<'_, Postgres>,
    now: DateTime<Utc>,
    limit: i64,
) -> Result<usize, sqlx::Error> {
    let user_ids = sqlx::query_scalar::<_, i64>(
        r#"
        SELECT DISTINCT run.user_id::bigint
        FROM news_category_maintenance_runs AS run
        LEFT JOIN processing_tasks AS task ON task.id=run.task_id
        WHERE run.status IN ('scheduled','running')
          AND (
              run.window_end_at <= $1
              OR task.status IN ('completed','failed')
          )
        ORDER BY run.user_id::bigint
        LIMIT $2
        "#,
    )
    .bind(now.naive_utc())
    .bind(limit.clamp(1, 1_000))
    .fetch_all(&mut **transaction)
    .await?;
    let mut settled = 0_usize;
    for user_id in user_ids {
        sqlx::query_scalar::<_, i64>("SELECT id::bigint FROM users WHERE id::bigint=$1 FOR SHARE")
            .bind(user_id)
            .fetch_optional(&mut **transaction)
            .await?;
        let result = sqlx::query(
            r#"
            UPDATE news_category_maintenance_runs AS run SET
                status=CASE WHEN run.window_end_at <= $2 THEN 'skipped_window' ELSE 'failed' END,
                output=run.output || jsonb_build_object(
                    'reason', CASE
                        WHEN run.window_end_at <= $2 THEN 'publication_window_expired'
                        ELSE 'queue_task_terminal_without_run_completion'
                    END
                ),
                completed_at=$2,
                updated_at=$2
            WHERE run.user_id::bigint=$1
              AND run.status IN ('scheduled','running')
              AND (
                  run.window_end_at <= $2
                  OR EXISTS (
                      SELECT 1 FROM processing_tasks AS task
                      WHERE task.id=run.task_id AND task.status IN ('completed','failed')
                  )
              )
            "#,
        )
        .bind(user_id)
        .bind(now.naive_utc())
        .execute(&mut **transaction)
        .await?;
        settled = settled.saturating_add(result.rows_affected() as usize);
    }
    Ok(settled)
}

pub async fn attach_news_category_run_task(
    transaction: &mut Transaction<'_, Postgres>,
    run_id: i64,
    task_id: i64,
) -> Result<bool, sqlx::Error> {
    let result = sqlx::query(
        "UPDATE news_category_maintenance_runs SET task_id=$2, updated_at=timezone('UTC',now()) WHERE id=$1 AND task_id IS NULL",
    )
    .bind(run_id)
    .bind(task_id)
    .execute(&mut **transaction)
    .await?;
    Ok(result.rows_affected() == 1)
}

pub async fn begin_news_category_run(
    pool: &PgPool,
    task_id: i64,
    user_id: i64,
    run_id: i64,
    timezone_revision: i64,
    claim_fence: &NewsCategoryRunClaimFence,
    now: DateTime<Utc>,
) -> Result<BeginNewsCategoryRunOutcome, NewsCategoryScheduleError> {
    let mut transaction = pool.begin().await?;
    let row = sqlx::query_as::<_, BeginRunRow>(
        r#"
        SELECT run.id, run.user_id::bigint, run.local_date, run.timezone,
               run.timezone_revision, run.scheduled_at, run.window_start_at,
               run.window_end_at, run.mode,
               (app_user.is_active IS TRUE AND schedule.timezone_revision=$4
                 AND run.timezone_revision=$4) AS eligible
        FROM news_category_maintenance_runs AS run
        JOIN user_news_category_schedule AS schedule ON schedule.user_id=run.user_id
        JOIN users AS app_user ON app_user.id=run.user_id
        JOIN processing_tasks AS task ON task.id=run.task_id
        WHERE run.id=$3 AND run.user_id::bigint=$2 AND run.task_id::bigint=$1
          AND run.status IN ('scheduled','running')
          AND task.task_type='recluster_news_lenses' AND task.status='processing'
          AND task.owner_user_id::bigint=$2
          AND task.locked_by=$6 AND task.lease_token=$7
          AND task.retry_count=$8 AND task.executor_runtime=$9
          AND task.executor_version=$10 AND task.executor_namespace=$11
          AND task.lease_expires_at > $5
        FOR UPDATE OF run
        "#,
    )
    .bind(task_id)
    .bind(user_id)
    .bind(run_id)
    .bind(timezone_revision)
    .bind(now.naive_utc())
    .bind(&claim_fence.locked_by)
    .bind(claim_fence.lease_token)
    .bind(claim_fence.retry_count)
    .bind(&claim_fence.executor_runtime)
    .bind(claim_fence.executor_version)
    .bind(&claim_fence.executor_namespace)
    .fetch_optional(&mut *transaction)
    .await?;
    let Some(row) = row else {
        transaction.rollback().await?;
        return Ok(BeginNewsCategoryRunOutcome::Ineligible);
    };
    if !row.eligible {
        sqlx::query("UPDATE news_category_maintenance_runs SET status='failed', output=jsonb_build_object('reason','inactive_or_timezone_revision_changed'), completed_at=$2, updated_at=$2 WHERE id=$1")
            .bind(run_id)
            .bind(now.naive_utc())
            .execute(&mut *transaction)
            .await?;
        transaction.commit().await?;
        return Ok(BeginNewsCategoryRunOutcome::Ineligible);
    }
    if now < utc(row.window_start_at) || now >= utc(row.window_end_at) {
        sqlx::query("UPDATE news_category_maintenance_runs SET status='skipped_window', completed_at=$2, updated_at=$2 WHERE id=$1")
            .bind(run_id)
            .bind(now.naive_utc())
            .execute(&mut *transaction)
            .await?;
        transaction.commit().await?;
        return Ok(BeginNewsCategoryRunOutcome::SkippedWindow);
    }
    sqlx::query("UPDATE news_category_maintenance_runs SET status='running', started_at=COALESCE(started_at,$2), updated_at=$2 WHERE id=$1")
        .bind(run_id)
        .bind(now.naive_utc())
        .execute(&mut *transaction)
        .await?;
    transaction.commit().await?;
    Ok(BeginNewsCategoryRunOutcome::Ready(NewsCategoryRunContext {
        task_id,
        run: DueNewsCategoryRun {
            run_id: row.id,
            user_id: row.user_id,
            local_date: row.local_date,
            timezone: row.timezone,
            timezone_revision: row.timezone_revision,
            scheduled_at: utc(row.scheduled_at),
            window_start_at: utc(row.window_start_at),
            window_end_at: utc(row.window_end_at),
            mode: parse_mode(&row.mode)?,
        },
        claim_fence: claim_fence.clone(),
    }))
}

/// Complete a run only while its local-night and timezone fences still hold.
pub async fn finish_news_category_run(
    transaction: &mut Transaction<'_, Postgres>,
    run_id: i64,
    timezone_revision: i64,
    status: NewsCategoryRunStatus,
    output: &Value,
    now: DateTime<Utc>,
) -> Result<bool, NewsCategoryScheduleError> {
    let user_id = sqlx::query_scalar::<_, i64>(
        "SELECT user_id::bigint FROM news_category_maintenance_runs WHERE id=$1",
    )
    .bind(run_id)
    .fetch_optional(&mut **transaction)
    .await?;
    let Some(user_id) = user_id else {
        return Ok(false);
    };
    sqlx::query_scalar::<_, i64>("SELECT id::bigint FROM users WHERE id::bigint=$1 FOR SHARE")
        .bind(user_id)
        .fetch_optional(&mut **transaction)
        .await?;
    let row = sqlx::query_as::<_, FinishRunRow>(
        r#"
        SELECT run.user_id::bigint, run.local_date, run.mode, run.window_start_at,
               run.window_end_at, schedule.timezone_revision, run.timezone_revision AS run_timezone_revision,
               schedule.last_published_at, app_user.is_active
        FROM news_category_maintenance_runs AS run
        JOIN user_news_category_schedule AS schedule ON schedule.user_id=run.user_id
        JOIN users AS app_user ON app_user.id=run.user_id
        WHERE run.id=$1 AND run.status='running'
        FOR UPDATE OF run, schedule
        "#,
    )
    .bind(run_id)
    .fetch_optional(&mut **transaction)
    .await?;
    let Some(row) = row else { return Ok(false) };
    let window_ok = now >= utc(row.window_start_at) && now < utc(row.window_end_at);
    if !window_ok {
        sqlx::query("UPDATE news_category_maintenance_runs SET status='skipped_window', output=$2, completed_at=$3, updated_at=$3 WHERE id=$1")
            .bind(run_id)
            .bind(output)
            .bind(now.naive_utc())
            .execute(&mut **transaction)
            .await?;
        return Ok(false);
    }
    let revision_ok = row.is_active
        && row.timezone_revision == timezone_revision
        && row.run_timezone_revision == timezone_revision;
    let publication_ok = status != NewsCategoryRunStatus::Published
        || (row.mode == "publish"
            && row
                .last_published_at
                .is_none_or(|published| now >= utc(published) + Duration::hours(12)));
    if !revision_ok || !publication_ok {
        let reason = if revision_ok {
            "publication_separation_not_met"
        } else {
            "inactive_or_timezone_revision_changed"
        };
        sqlx::query("UPDATE news_category_maintenance_runs SET status='failed', output=$2 || jsonb_build_object('reason',$3::text), completed_at=$4, updated_at=$4 WHERE id=$1")
            .bind(run_id)
            .bind(output)
            .bind(reason)
            .bind(now.naive_utc())
            .execute(&mut **transaction)
            .await?;
        return Ok(false);
    }
    sqlx::query("UPDATE news_category_maintenance_runs SET status=$2, output=$3, completed_at=$4, updated_at=$4 WHERE id=$1")
        .bind(run_id)
        .bind(status.as_str())
        .bind(output)
        .bind(now.naive_utc())
        .execute(&mut **transaction)
        .await?;
    if status == NewsCategoryRunStatus::Published {
        sqlx::query("UPDATE user_news_category_schedule SET last_published_at=$2, last_published_local_date=$3, updated_at=$2 WHERE user_id::bigint=$1 AND timezone_revision=$4")
            .bind(row.user_id)
            .bind(now.naive_utc())
            .bind(row.local_date)
            .bind(timezone_revision)
            .execute(&mut **transaction)
            .await?;
    }
    Ok(true)
}

/// Atomically reserve one of two per-run naming attempts and bounded global UTC-day tokens.
pub async fn reserve_news_category_naming_attempt(
    pool: &PgPool,
    context: &NewsCategoryRunContext,
    requested_tokens: i64,
    global_daily_token_limit: i64,
) -> Result<bool, sqlx::Error> {
    if requested_tokens <= 0 || global_daily_token_limit < requested_tokens {
        return Ok(false);
    }
    let mut transaction = pool.begin().await?;
    sqlx::query_scalar::<_, i64>("SELECT id::bigint FROM users WHERE id::bigint=$1 FOR SHARE")
        .bind(context.run.user_id)
        .fetch_optional(&mut *transaction)
        .await?;
    sqlx::query("SELECT pg_advisory_xact_lock(hashtext('news-category-naming-budget'))")
        .execute(&mut *transaction)
        .await?;
    let reserved = sqlx::query_scalar::<_, i64>(
        r#"
        INSERT INTO news_category_naming_budget (utc_date, reserved_tokens)
        VALUES ((timezone('UTC',now()))::date, $1)
        ON CONFLICT (utc_date) DO UPDATE SET
            reserved_tokens=news_category_naming_budget.reserved_tokens + EXCLUDED.reserved_tokens,
            updated_at=timezone('UTC',now())
        WHERE news_category_naming_budget.reserved_tokens + EXCLUDED.reserved_tokens <= $2
        RETURNING reserved_tokens
        "#,
    )
    .bind(requested_tokens)
    .bind(global_daily_token_limit)
    .fetch_optional(&mut *transaction)
    .await?
    .unwrap_or(-1);
    if reserved < 0 {
        transaction.rollback().await?;
        return Ok(false);
    }
    let updated = sqlx::query(
        r#"
        UPDATE news_category_maintenance_runs AS run SET
            naming_attempts=naming_attempts+1,
            naming_reserved_tokens=naming_reserved_tokens+$3,
            updated_at=timezone('UTC',now())
        FROM processing_tasks AS task, user_news_category_schedule AS schedule, users AS app_user
        WHERE run.id=$1 AND run.task_id=$2 AND run.status='running'
          AND run.naming_attempts < 2 AND task.id=$2
          AND task.status='processing' AND task.task_type='recluster_news_lenses'
          AND task.locked_by=$4 AND task.lease_token=$5
          AND task.retry_count=$6 AND task.executor_runtime=$7
          AND task.executor_version=$8 AND task.executor_namespace=$9
          AND task.lease_expires_at > timezone('UTC',clock_timestamp())
          AND schedule.user_id=run.user_id AND app_user.id=run.user_id
          AND app_user.is_active IS TRUE
          AND schedule.timezone_revision=$10 AND run.timezone_revision=$10
          AND timezone('UTC',clock_timestamp()) >= run.window_start_at
          AND timezone('UTC',clock_timestamp()) < run.window_end_at
        "#,
    )
    .bind(context.run.run_id)
    .bind(context.task_id)
    .bind(requested_tokens)
    .bind(&context.claim_fence.locked_by)
    .bind(context.claim_fence.lease_token)
    .bind(context.claim_fence.retry_count)
    .bind(&context.claim_fence.executor_runtime)
    .bind(context.claim_fence.executor_version)
    .bind(&context.claim_fence.executor_namespace)
    .bind(context.run.timezone_revision)
    .execute(&mut *transaction)
    .await?;
    if updated.rows_affected() != 1 {
        transaction.rollback().await?;
        return Ok(false);
    }
    transaction.commit().await?;
    Ok(true)
}

fn deterministic_jitter_minutes(user_id: i64) -> i64 {
    let bits = u64::from_ne_bytes(user_id.to_ne_bytes());
    let mixed = bits.wrapping_mul(0x9E37_79B9_7F4A_7C15) ^ (bits >> 29);
    i64::try_from(mixed % 31).expect("0..=30 fits i64")
}

fn first_valid_at_or_after(
    timezone: Tz,
    date: NaiveDate,
    start: NaiveTime,
    end: NaiveTime,
) -> Option<DateTime<Utc>> {
    let mut local = date.and_time(start);
    let limit = date.and_time(end);
    while local < limit {
        if let Some(value) = resolve_earlier(timezone.from_local_datetime(&local)) {
            return Some(value.with_timezone(&Utc));
        }
        local += Duration::minutes(1);
    }
    None
}

fn last_valid_at_or_before(
    timezone: Tz,
    date: NaiveDate,
    start: NaiveTime,
    floor: NaiveTime,
) -> Option<DateTime<Utc>> {
    let mut local = date.and_time(start);
    let limit = date.and_time(floor);
    while local > limit {
        if let Some(value) = resolve_earlier(timezone.from_local_datetime(&local)) {
            return Some(value.with_timezone(&Utc));
        }
        local -= Duration::minutes(1);
    }
    None
}

fn resolve_earlier(result: LocalResult<DateTime<Tz>>) -> Option<DateTime<Tz>> {
    match result {
        LocalResult::Single(value) => Some(value),
        LocalResult::Ambiguous(first, second) => Some(first.min(second)),
        LocalResult::None => None,
    }
}

async fn database_now(
    transaction: &mut Transaction<'_, Postgres>,
) -> Result<DateTime<Utc>, sqlx::Error> {
    let value = sqlx::query_scalar::<_, NaiveDateTime>("SELECT timezone('UTC', clock_timestamp())")
        .fetch_one(&mut **transaction)
        .await?;
    Ok(utc(value))
}

async fn load_schedule_for_update(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
) -> Result<Option<NewsCategorySchedule>, sqlx::Error> {
    let row = sqlx::query_as::<_, ScheduleRow>(
        "SELECT user_id::bigint,timezone,timezone_revision,next_due_at,next_local_date,next_window_start_at,next_window_end_at,last_published_at,last_published_local_date FROM user_news_category_schedule WHERE user_id::bigint=$1 FOR UPDATE",
    )
    .bind(user_id)
    .fetch_optional(&mut **transaction)
    .await?;
    Ok(row.map(Into::into))
}

fn parse_mode(value: &str) -> Result<NewsCategoryMaintenanceMode, NewsCategoryScheduleError> {
    match value {
        "shadow" => Ok(NewsCategoryMaintenanceMode::Shadow),
        "publish" => Ok(NewsCategoryMaintenanceMode::Publish),
        _ => Err(NewsCategoryScheduleError::InvalidMode),
    }
}

fn utc(value: NaiveDateTime) -> DateTime<Utc> {
    DateTime::from_naive_utc_and_offset(value, Utc)
}

#[derive(Debug, sqlx::FromRow)]
struct ScheduleRow {
    user_id: i64,
    timezone: Option<String>,
    timezone_revision: i64,
    next_due_at: Option<NaiveDateTime>,
    next_local_date: Option<NaiveDate>,
    next_window_start_at: Option<NaiveDateTime>,
    next_window_end_at: Option<NaiveDateTime>,
    last_published_at: Option<NaiveDateTime>,
    last_published_local_date: Option<NaiveDate>,
}

impl From<ScheduleRow> for NewsCategorySchedule {
    fn from(row: ScheduleRow) -> Self {
        Self {
            user_id: row.user_id,
            timezone: row.timezone,
            timezone_revision: row.timezone_revision,
            next_due_at: row.next_due_at.map(utc),
            next_local_date: row.next_local_date,
            next_window_start_at: row.next_window_start_at.map(utc),
            next_window_end_at: row.next_window_end_at.map(utc),
            last_published_at: row.last_published_at.map(utc),
            last_published_local_date: row.last_published_local_date,
        }
    }
}

#[derive(Debug, sqlx::FromRow)]
struct DueScheduleRow {
    user_id: i64,
    timezone: Option<String>,
    timezone_revision: i64,
    next_due_at: Option<NaiveDateTime>,
    next_local_date: Option<NaiveDate>,
    next_window_start_at: Option<NaiveDateTime>,
    next_window_end_at: Option<NaiveDateTime>,
    last_published_at: Option<NaiveDateTime>,
}

#[derive(Debug, sqlx::FromRow)]
struct BeginRunRow {
    id: i64,
    user_id: i64,
    local_date: NaiveDate,
    timezone: String,
    timezone_revision: i64,
    scheduled_at: NaiveDateTime,
    window_start_at: NaiveDateTime,
    window_end_at: NaiveDateTime,
    mode: String,
    eligible: bool,
}

#[derive(Debug, sqlx::FromRow)]
struct FinishRunRow {
    user_id: i64,
    local_date: NaiveDate,
    mode: String,
    window_start_at: NaiveDateTime,
    window_end_at: NaiveDateTime,
    timezone_revision: i64,
    run_timezone_revision: i64,
    last_published_at: Option<NaiveDateTime>,
    is_active: bool,
}

#[cfg(test)]
#[path = "news_category_schedule_tests.rs"]
mod tests;
