use std::collections::HashSet;

use chrono::{Datelike, Days, NaiveDateTime, Utc};
use newsly_agent_runtime::{
    AssistantPart, MessagePart, MessageRole, NewslyMessage, NewslyTranscript, ProviderUsage,
    TranscriptFinishReason,
};
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use sqlx::types::Json;
use sqlx::{FromRow, PgPool, Postgres, Transaction};
use thiserror::Error;

use crate::canonicalize_feed_url;
use crate::chat_transcripts::latest_assistant_text;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OnboardingTaskLane {
    pub id: i64,
    pub name: String,
    pub goal: String,
    pub target: String,
    pub queries: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OnboardingTaskSnapshot {
    pub run_id: i64,
    pub user_id: i64,
    pub topic_summary: String,
    pub inferred_topics: Vec<String>,
    pub lanes: Vec<OnboardingTaskLane>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PrepareOnboardingTaskOutcome {
    Ready(OnboardingTaskSnapshot),
    AlreadyCompleted,
    MissingOrInactive,
    Superseded,
}

#[derive(Debug, Clone, PartialEq)]
pub struct NewOnboardingSuggestion {
    pub suggestion_type: String,
    pub title: Option<String>,
    pub site_url: Option<String>,
    pub feed_url: Option<String>,
    pub subreddit: Option<String>,
    pub rationale: Option<String>,
    pub score: Option<f64>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OnboardingAttemptStatus {
    Pending,
    Failed,
}

impl OnboardingAttemptStatus {
    const fn as_str(self) -> &'static str {
        match self {
            Self::Pending => "pending",
            Self::Failed => "failed",
        }
    }

    const fn lane_status(self) -> &'static str {
        match self {
            Self::Pending => "queued",
            Self::Failed => "failed",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct WeeklyDiscoverySessionOutcome {
    pub session_id: i64,
    pub changed: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FeedDiscoveryFavorite {
    pub id: i64,
    pub title: String,
    pub source: Option<String>,
    pub url: String,
    pub content_type: String,
    pub summary: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FeedDiscoveryTaskSnapshot {
    pub run_id: i64,
    pub user_id: i64,
    pub trigger: String,
    pub favorites: Vec<FeedDiscoveryFavorite>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PrepareFeedDiscoveryTaskOutcome {
    Ready(FeedDiscoveryTaskSnapshot),
    ReuseCompleted { run_id: i64 },
    MissingOrInactive,
}

#[derive(Debug, FromRow)]
struct RunRow {
    id: i64,
    user_id: i64,
    status: String,
    topic_summary: Option<String>,
    inferred_topics: Json<Value>,
    discovery_task_id: Option<i64>,
}

#[derive(Debug, FromRow)]
struct LaneRow {
    id: i64,
    lane_name: String,
    goal: Option<String>,
    target: Option<String>,
    queries: Json<Value>,
}

#[derive(Debug, FromRow)]
struct FavoriteRow {
    id: i64,
    title: Option<String>,
    source: Option<String>,
    url: String,
    content_type: String,
    summary: Option<String>,
}

/// Claims one persisted onboarding run for the current queue task and returns a connection-free
/// snapshot. A newer task id permanently supersedes an older duplicate task for the same run.
pub async fn prepare_onboarding_discovery_task(
    pool: &PgPool,
    task_id: i64,
    retry_count: i32,
    user_id: i64,
    run_id: i64,
) -> Result<PrepareOnboardingTaskOutcome, OnboardingTaskRepositoryError> {
    let mut transaction = pool.begin().await?;
    let user_active = sqlx::query_scalar::<_, bool>(
        r#"
        SELECT is_active
        FROM users
        WHERE id::bigint = $1
        FOR UPDATE
        "#,
    )
    .bind(user_id)
    .fetch_optional(&mut *transaction)
    .await?;
    if user_active != Some(true) {
        transaction.rollback().await?;
        return Ok(PrepareOnboardingTaskOutcome::MissingOrInactive);
    }
    let run = sqlx::query_as::<_, RunRow>(
        r#"
        SELECT
            id::bigint AS id,
            user_id::bigint AS user_id,
            status,
            topic_summary,
            inferred_topics,
            discovery_task_id::bigint AS discovery_task_id
        FROM onboarding_discovery_runs
        WHERE id::bigint = $1 AND user_id::bigint = $2
        FOR UPDATE
        "#,
    )
    .bind(run_id)
    .bind(user_id)
    .fetch_optional(&mut *transaction)
    .await?;
    let Some(run) = run else {
        transaction.rollback().await?;
        return Ok(PrepareOnboardingTaskOutcome::MissingOrInactive);
    };
    if run.status == "completed" {
        transaction.rollback().await?;
        return Ok(PrepareOnboardingTaskOutcome::AlreadyCompleted);
    }
    if run
        .discovery_task_id
        .is_some_and(|claimed_task_id| claimed_task_id > task_id)
    {
        transaction.rollback().await?;
        return Ok(PrepareOnboardingTaskOutcome::Superseded);
    }
    let lanes = sqlx::query_as::<_, LaneRow>(
        r#"
        SELECT
            id::bigint AS id,
            lane_name,
            goal,
            target,
            queries
        FROM onboarding_discovery_lanes
        WHERE run_id::bigint = $1
        ORDER BY id
        FOR UPDATE
        "#,
    )
    .bind(run_id)
    .fetch_all(&mut *transaction)
    .await?;
    sqlx::query(
        r#"
        UPDATE onboarding_discovery_runs
        SET status = 'processing',
            error_message = NULL,
            completed_at = NULL,
            discovery_task_id = $2::bigint::integer,
            discovery_retry_count = $3
        WHERE id::bigint = $1
        "#,
    )
    .bind(run_id)
    .bind(task_id)
    .bind(retry_count)
    .execute(&mut *transaction)
    .await?;
    sqlx::query(
        r#"
        UPDATE onboarding_discovery_lanes
        SET status = 'processing',
            completed_queries = 0,
            updated_at = timezone('UTC', now())
        WHERE run_id::bigint = $1
        "#,
    )
    .bind(run_id)
    .execute(&mut *transaction)
    .await?;
    transaction.commit().await?;
    Ok(PrepareOnboardingTaskOutcome::Ready(
        OnboardingTaskSnapshot {
            run_id: run.id,
            user_id: run.user_id,
            topic_summary: run
                .topic_summary
                .unwrap_or_else(|| "News interests".to_owned()),
            inferred_topics: string_array(&run.inferred_topics.0),
            lanes: lanes
                .into_iter()
                .map(|lane| OnboardingTaskLane {
                    id: lane.id,
                    name: lane.lane_name,
                    goal: lane.goal.unwrap_or_default(),
                    target: lane.target.unwrap_or_else(|| "feeds".to_owned()),
                    queries: string_array(&lane.queries.0),
                })
                .collect(),
        },
    ))
}

/// Publishes one audio-discovery result only while its product claim still names this exact queue
/// attempt. The caller invokes this inside the queue kernel's exact-lease transaction.
pub async fn complete_onboarding_discovery_task(
    transaction: &mut Transaction<'_, Postgres>,
    task_id: i64,
    retry_count: i32,
    snapshot: &OnboardingTaskSnapshot,
    suggestions: &[NewOnboardingSuggestion],
) -> Result<bool, OnboardingTaskRepositoryError> {
    if !lock_current_claim(
        transaction,
        snapshot.run_id,
        snapshot.user_id,
        task_id,
        retry_count,
    )
    .await?
    {
        return Ok(false);
    }
    sqlx::query("DELETE FROM onboarding_discovery_suggestions WHERE run_id::bigint = $1")
        .bind(snapshot.run_id)
        .execute(&mut **transaction)
        .await?;
    for suggestion in suggestions {
        sqlx::query(
            r#"
            INSERT INTO onboarding_discovery_suggestions (
                run_id,
                user_id,
                suggestion_type,
                site_url,
                feed_url,
                subreddit,
                title,
                rationale,
                score,
                status,
                created_at,
                updated_at
            )
            VALUES (
                $1::bigint::integer,
                $2::bigint::integer,
                $3,
                $4,
                $5,
                $6,
                $7,
                $8,
                $9,
                'new',
                timezone('UTC', now()),
                timezone('UTC', now())
            )
            "#,
        )
        .bind(snapshot.run_id)
        .bind(snapshot.user_id)
        .bind(&suggestion.suggestion_type)
        .bind(&suggestion.site_url)
        .bind(&suggestion.feed_url)
        .bind(&suggestion.subreddit)
        .bind(&suggestion.title)
        .bind(&suggestion.rationale)
        .bind(suggestion.score)
        .execute(&mut **transaction)
        .await?;
    }
    sqlx::query(
        r#"
        UPDATE onboarding_discovery_lanes
        SET status = 'completed',
            completed_queries = query_count,
            updated_at = timezone('UTC', now())
        WHERE run_id::bigint = $1
        "#,
    )
    .bind(snapshot.run_id)
    .execute(&mut **transaction)
    .await?;
    sqlx::query(
        r#"
        UPDATE onboarding_discovery_runs
        SET status = 'completed',
            completed_at = timezone('UTC', now()),
            error_message = NULL,
            discovery_task_id = NULL,
            discovery_retry_count = NULL
        WHERE id::bigint = $1
        "#,
    )
    .bind(snapshot.run_id)
    .execute(&mut **transaction)
    .await?;
    Ok(true)
}

pub async fn settle_onboarding_discovery_attempt(
    transaction: &mut Transaction<'_, Postgres>,
    task_id: i64,
    retry_count: i32,
    run_id: i64,
    user_id: i64,
    status: OnboardingAttemptStatus,
    error_message: &str,
) -> Result<bool, OnboardingTaskRepositoryError> {
    if !lock_current_claim(transaction, run_id, user_id, task_id, retry_count).await? {
        return Ok(false);
    }
    sqlx::query(
        r#"
        UPDATE onboarding_discovery_lanes
        SET status = $2,
            updated_at = timezone('UTC', now())
        WHERE run_id::bigint = $1
        "#,
    )
    .bind(run_id)
    .bind(status.lane_status())
    .execute(&mut **transaction)
    .await?;
    sqlx::query(
        r#"
        UPDATE onboarding_discovery_runs
        SET status = $2,
            completed_at = CASE WHEN $2 = 'failed' THEN timezone('UTC', now()) ELSE NULL END,
            error_message = $3,
            discovery_task_id = NULL,
            discovery_retry_count = NULL
        WHERE id::bigint = $1
        "#,
    )
    .bind(run_id)
    .bind(status.as_str())
    .bind(error_message.chars().take(2_000).collect::<String>())
    .execute(&mut **transaction)
    .await?;
    Ok(true)
}

/// Starts or resumes one weekly feed-discovery run and returns immutable saved-content evidence.
/// A clean completed run in the current UTC week is reused without another provider call.
pub async fn prepare_feed_discovery_task(
    pool: &PgPool,
    task_id: i64,
    retry_count: i32,
    user_id: i64,
    trigger: &str,
    favorite_limit: i64,
) -> Result<PrepareFeedDiscoveryTaskOutcome, OnboardingTaskRepositoryError> {
    let mut transaction = pool.begin().await?;
    if !lock_active_user(&mut transaction, user_id).await? {
        transaction.rollback().await?;
        return Ok(PrepareFeedDiscoveryTaskOutcome::MissingOrInactive);
    }
    let week_start = current_utc_week_start();
    if let Some(run_id) = sqlx::query_scalar::<_, i64>(
        r#"
        SELECT id::bigint
        FROM feed_discovery_runs
        WHERE user_id::bigint = $1
          AND status = 'completed'
          AND error_message IS NULL
          AND created_at >= $2
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        FOR UPDATE
        "#,
    )
    .bind(user_id)
    .bind(week_start)
    .fetch_optional(&mut *transaction)
    .await?
    {
        transaction.rollback().await?;
        return Ok(PrepareFeedDiscoveryTaskOutcome::ReuseCompleted { run_id });
    }
    let favorites = sqlx::query_as::<_, FavoriteRow>(
        r#"
        SELECT
            content.id::bigint AS id,
            content.title,
            content.source,
            content.url,
            content.content_type,
            content.short_summary AS summary
        FROM content_knowledge_saves AS saved
        JOIN contents AS content ON content.id = saved.content_id
        WHERE saved.user_id::bigint = $1
          AND content.url IS NOT NULL
        ORDER BY saved.saved_at DESC, content.id DESC
        LIMIT $2
        "#,
    )
    .bind(user_id)
    .bind(favorite_limit.clamp(5, 50))
    .fetch_all(&mut *transaction)
    .await?;
    let seed_content_ids = favorites.iter().map(|row| row.id).collect::<Vec<_>>();
    let run_id = if let Some(run_id) = sqlx::query_scalar::<_, i64>(
        r#"
        SELECT id::bigint
        FROM feed_discovery_runs
        WHERE user_id::bigint = $1
          AND discovery_task_id::bigint = $2
          AND status IN ('pending', 'processing')
        ORDER BY id DESC
        LIMIT 1
        FOR UPDATE
        "#,
    )
    .bind(user_id)
    .bind(task_id)
    .fetch_optional(&mut *transaction)
    .await?
    {
        sqlx::query(
            r#"
            UPDATE feed_discovery_runs
            SET status = 'processing',
                error_message = NULL,
                completed_at = NULL,
                seed_content_ids = $2,
                discovery_retry_count = $3
            WHERE id::bigint = $1
            "#,
        )
        .bind(run_id)
        .bind(serde_json::to_value(&seed_content_ids)?)
        .bind(retry_count)
        .execute(&mut *transaction)
        .await?;
        run_id
    } else {
        sqlx::query_scalar::<_, i64>(
            r#"
            INSERT INTO feed_discovery_runs (
                user_id,
                status,
                direction_summary,
                seed_content_ids,
                created_at,
                discovery_task_id,
                discovery_retry_count
            )
            VALUES (
                $1::bigint::integer,
                'processing',
                $2,
                $3,
                timezone('UTC', now()),
                $4::bigint::integer,
                $5
            )
            RETURNING id::bigint
            "#,
        )
        .bind(user_id)
        .bind(format!("rust:{trigger}"))
        .bind(serde_json::to_value(&seed_content_ids)?)
        .bind(task_id)
        .bind(retry_count)
        .fetch_one(&mut *transaction)
        .await?
    };
    transaction.commit().await?;
    Ok(PrepareFeedDiscoveryTaskOutcome::Ready(
        FeedDiscoveryTaskSnapshot {
            run_id,
            user_id,
            trigger: trigger.to_owned(),
            favorites: favorites
                .into_iter()
                .map(|row| FeedDiscoveryFavorite {
                    id: row.id,
                    title: row.title.unwrap_or_else(|| "Untitled".to_owned()),
                    source: row.source,
                    url: row.url,
                    content_type: row.content_type,
                    summary: row.summary,
                })
                .collect(),
        },
    ))
}

pub async fn complete_feed_discovery_task(
    transaction: &mut Transaction<'_, Postgres>,
    task_id: i64,
    retry_count: i32,
    snapshot: &FeedDiscoveryTaskSnapshot,
    suggestions: &[NewOnboardingSuggestion],
) -> Result<bool, OnboardingTaskRepositoryError> {
    if !lock_feed_discovery_claim(
        transaction,
        snapshot.run_id,
        snapshot.user_id,
        task_id,
        retry_count,
    )
    .await?
    {
        return Ok(false);
    }
    let active_urls = sqlx::query_scalar::<_, String>(
        r#"
        SELECT COALESCE(feed_url, config::jsonb ->> 'feed_url')
        FROM user_scraper_configs
        WHERE user_id::bigint = $1
          AND is_active = TRUE
          AND COALESCE(feed_url, config::jsonb ->> 'feed_url') IS NOT NULL
        "#,
    )
    .bind(snapshot.user_id)
    .fetch_all(&mut **transaction)
    .await?
    .into_iter()
    .map(|url| canonicalize_feed_url(&url))
    .collect::<HashSet<_>>();
    for suggestion in suggestions
        .iter()
        .filter(|item| item.suggestion_type != "reddit")
    {
        let Some(feed_url) = suggestion.feed_url.as_deref() else {
            continue;
        };
        let feed_url = canonicalize_feed_url(feed_url);
        let status = if active_urls.contains(&feed_url) {
            "subscribed"
        } else {
            "new"
        };
        let existing = sqlx::query_as::<_, (i64, String)>(
            r#"
            SELECT id::bigint, status
            FROM feed_discovery_suggestions
            WHERE user_id::bigint = $1 AND feed_url = $2
            FOR UPDATE
            "#,
        )
        .bind(snapshot.user_id)
        .bind(&feed_url)
        .fetch_optional(&mut **transaction)
        .await?;
        if let Some((id, _)) = existing {
            if status == "subscribed" {
                sqlx::query(
                    "UPDATE feed_discovery_suggestions SET run_id = $2::bigint::integer, status = 'subscribed', updated_at = timezone('UTC', now()) WHERE id::bigint = $1",
                )
                .bind(id)
                .bind(snapshot.run_id)
                .execute(&mut **transaction)
                .await?;
            }
            continue;
        }
        sqlx::query(
            r#"
            INSERT INTO feed_discovery_suggestions (
                run_id,
                user_id,
                suggestion_type,
                site_url,
                feed_url,
                title,
                rationale,
                score,
                status,
                config,
                metadata,
                created_at,
                updated_at
            )
            VALUES (
                $1::bigint::integer,
                $2::bigint::integer,
                $3,
                $4,
                $5,
                $6,
                $7,
                $8,
                $9,
                json_build_object('feed_url', $5),
                '{}'::json,
                timezone('UTC', now()),
                timezone('UTC', now())
            )
            ON CONFLICT (user_id, feed_url) DO NOTHING
            "#,
        )
        .bind(snapshot.run_id)
        .bind(snapshot.user_id)
        .bind(&suggestion.suggestion_type)
        .bind(&suggestion.site_url)
        .bind(&feed_url)
        .bind(&suggestion.title)
        .bind(&suggestion.rationale)
        .bind(suggestion.score)
        .bind(status)
        .execute(&mut **transaction)
        .await?;
    }
    let empty_reason = match snapshot.favorites.len() {
        0 => Some("no_favorites"),
        1 | 2 => Some("insufficient_favorites"),
        _ => None,
    };
    sqlx::query(
        r#"
        UPDATE feed_discovery_runs
        SET status = 'completed',
            direction_summary = $2,
            completed_at = timezone('UTC', now()),
            error_message = $3,
            discovery_task_id = NULL,
            discovery_retry_count = NULL
        WHERE id::bigint = $1
        "#,
    )
    .bind(snapshot.run_id)
    .bind(format!(
        "Saved Knowledge source discovery ({})",
        snapshot.trigger
    ))
    .bind(empty_reason)
    .execute(&mut **transaction)
    .await?;
    Ok(true)
}

pub async fn settle_feed_discovery_attempt(
    transaction: &mut Transaction<'_, Postgres>,
    task_id: i64,
    retry_count: i32,
    run_id: i64,
    user_id: i64,
    status: OnboardingAttemptStatus,
    error_message: &str,
) -> Result<bool, OnboardingTaskRepositoryError> {
    if !lock_feed_discovery_claim(transaction, run_id, user_id, task_id, retry_count).await? {
        return Ok(false);
    }
    sqlx::query(
        r#"
        UPDATE feed_discovery_runs
        SET status = $2,
            completed_at = CASE WHEN $2 = 'failed' THEN timezone('UTC', now()) ELSE NULL END,
            error_message = $3,
            discovery_task_id = CASE WHEN $2 = 'failed' THEN NULL ELSE discovery_task_id END,
            discovery_retry_count = CASE WHEN $2 = 'failed' THEN NULL ELSE discovery_retry_count END
        WHERE id::bigint = $1
        "#,
    )
    .bind(run_id)
    .bind(status.as_str())
    .bind(error_message.chars().take(2_000).collect::<String>())
    .execute(&mut **transaction)
    .await?;
    Ok(true)
}

/// Creates or reprojects the current UTC-week discovery chat without rewriting an engaged thread.
pub async fn ensure_weekly_discovery_session(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
) -> Result<Option<WeeklyDiscoverySessionOutcome>, OnboardingTaskRepositoryError> {
    let onboarding_complete = sqlx::query_scalar::<_, bool>(
        r#"
        SELECT has_completed_onboarding
        FROM users
        WHERE id::bigint = $1 AND is_active = TRUE
        FOR UPDATE
        "#,
    )
    .bind(user_id)
    .fetch_optional(&mut **transaction)
    .await?;
    if onboarding_complete != Some(true) {
        return Ok(None);
    }
    let seed = load_weekly_seed(transaction, user_id).await?;
    let session = sqlx::query_as::<_, WeeklySessionRow>(
        r#"
        SELECT id::bigint AS id, context_snapshot
        FROM chat_sessions
        WHERE user_id::bigint = $1
          AND session_type = 'weekly_discovery'
          AND topic = $2
          AND is_archived = FALSE
        ORDER BY id
        LIMIT 1
        FOR UPDATE
        "#,
    )
    .bind(user_id)
    .bind(&seed.week_key)
    .fetch_optional(&mut **transaction)
    .await?;
    let context_snapshot = weekly_context(&seed);
    let assistant_text = weekly_message(&seed);
    let render_metadata = weekly_render_metadata(&seed);
    let message_list = assistant_transcript(&assistant_text)?;
    let now = Utc::now().naive_utc();
    if let Some(session) = session {
        let messages = sqlx::query_as::<_, WeeklyMessageRow>(
            r#"
            SELECT
                id::bigint AS id,
                message_list,
                render_metadata,
                processing_context,
                status
            FROM chat_messages
            WHERE session_id::bigint = $1
            ORDER BY id
            LIMIT 2
            FOR UPDATE
            "#,
        )
        .bind(session.id)
        .fetch_all(&mut **transaction)
        .await?;
        let Some(message) = messages.first().filter(|_| messages.len() == 1) else {
            return Ok(Some(WeeklyDiscoverySessionOutcome {
                session_id: session.id,
                changed: false,
            }));
        };
        if message.status != "completed" || message.processing_context.is_some() {
            return Ok(Some(WeeklyDiscoverySessionOutcome {
                session_id: session.id,
                changed: false,
            }));
        }
        let existing_text = latest_assistant_text(&message.message_list).ok();
        if session.context_snapshot.as_deref() == Some(&context_snapshot)
            && existing_text.as_deref() == Some(&assistant_text)
            && message.render_metadata == render_metadata
        {
            return Ok(Some(WeeklyDiscoverySessionOutcome {
                session_id: session.id,
                changed: false,
            }));
        }
        sqlx::query("DELETE FROM chat_messages WHERE id::bigint = $1")
            .bind(message.id)
            .execute(&mut **transaction)
            .await?;
        insert_weekly_message(
            transaction,
            session.id,
            &message_list,
            render_metadata.as_ref(),
            now,
        )
        .await?;
        sqlx::query(
            r#"
            UPDATE chat_sessions
            SET context_snapshot = $2,
                updated_at = $3,
                last_message_at = $3
            WHERE id::bigint = $1
            "#,
        )
        .bind(session.id)
        .bind(context_snapshot)
        .bind(now)
        .execute(&mut **transaction)
        .await?;
        return Ok(Some(WeeklyDiscoverySessionOutcome {
            session_id: session.id,
            changed: true,
        }));
    }
    let session_id = sqlx::query_scalar::<_, i64>(
        r#"
        INSERT INTO chat_sessions (
            user_id,
            content_id,
            title,
            session_type,
            topic,
            context_snapshot,
            llm_provider,
            llm_model,
            created_at,
            updated_at,
            last_message_at
        )
        VALUES (
            $1::bigint::integer,
            NULL,
            $2,
            'weekly_discovery',
            $3,
            $4,
            'openai',
            'openai:gpt-5.6-terra',
            $5,
            $5,
            $5
        )
        RETURNING id::bigint
        "#,
    )
    .bind(user_id)
    .bind(format!("Weekly Discovery • {}", seed.week_label))
    .bind(&seed.week_key)
    .bind(context_snapshot)
    .bind(now)
    .fetch_one(&mut **transaction)
    .await?;
    insert_weekly_message(
        transaction,
        session_id,
        &message_list,
        render_metadata.as_ref(),
        now,
    )
    .await?;
    Ok(Some(WeeklyDiscoverySessionOutcome {
        session_id,
        changed: true,
    }))
}

async fn lock_current_claim(
    transaction: &mut Transaction<'_, Postgres>,
    run_id: i64,
    user_id: i64,
    task_id: i64,
    retry_count: i32,
) -> Result<bool, sqlx::Error> {
    Ok(sqlx::query_scalar::<_, i64>(
        r#"
        SELECT discovery.id::bigint
        FROM onboarding_discovery_runs AS discovery
        JOIN users AS owner ON owner.id = discovery.user_id
        WHERE discovery.id::bigint = $1
          AND discovery.user_id::bigint = $2
          AND discovery.discovery_task_id::bigint = $3
          AND discovery.discovery_retry_count = $4
          AND discovery.status = 'processing'
          AND owner.is_active = TRUE
        FOR UPDATE OF discovery, owner
        "#,
    )
    .bind(run_id)
    .bind(user_id)
    .bind(task_id)
    .bind(retry_count)
    .fetch_optional(&mut **transaction)
    .await?
    .is_some())
}

async fn lock_feed_discovery_claim(
    transaction: &mut Transaction<'_, Postgres>,
    run_id: i64,
    user_id: i64,
    task_id: i64,
    retry_count: i32,
) -> Result<bool, sqlx::Error> {
    Ok(sqlx::query_scalar::<_, i64>(
        r#"
        SELECT discovery.id::bigint
        FROM feed_discovery_runs AS discovery
        JOIN users AS owner ON owner.id = discovery.user_id
        WHERE discovery.id::bigint = $1
          AND discovery.user_id::bigint = $2
          AND discovery.discovery_task_id::bigint = $3
          AND discovery.discovery_retry_count = $4
          AND discovery.status = 'processing'
          AND owner.is_active = TRUE
        FOR UPDATE OF discovery, owner
        "#,
    )
    .bind(run_id)
    .bind(user_id)
    .bind(task_id)
    .bind(retry_count)
    .fetch_optional(&mut **transaction)
    .await?
    .is_some())
}

async fn lock_active_user(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
) -> Result<bool, sqlx::Error> {
    Ok(sqlx::query_scalar::<_, i64>(
        "SELECT id::bigint FROM users WHERE id::bigint = $1 AND is_active = TRUE FOR UPDATE",
    )
    .bind(user_id)
    .fetch_optional(&mut **transaction)
    .await?
    .is_some())
}

#[derive(Debug)]
struct WeeklySeed {
    local_date: String,
    week_key: String,
    week_label: String,
    topic_summary: Option<String>,
    inferred_topics: Vec<String>,
    recent_reads: Vec<RecentRead>,
    feed_options: Vec<Value>,
}

#[derive(Debug, FromRow)]
struct RecentRead {
    id: i64,
    title: String,
    url: String,
}

#[derive(Debug, FromRow)]
struct FeedOptionRow {
    suggestion_type: String,
    site_url: Option<String>,
    feed_url: String,
    title: Option<String>,
    description: Option<String>,
    rationale: Option<String>,
}

#[derive(Debug, FromRow)]
struct LatestOnboardingRow {
    topic_summary: Option<String>,
    inferred_topics: Json<Value>,
}

#[derive(Debug, FromRow)]
struct WeeklySessionRow {
    id: i64,
    context_snapshot: Option<String>,
}

#[derive(Debug, FromRow)]
struct WeeklyMessageRow {
    id: i64,
    message_list: String,
    render_metadata: Option<Value>,
    processing_context: Option<Value>,
    status: String,
}

async fn load_weekly_seed(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
) -> Result<WeeklySeed, sqlx::Error> {
    let today = Utc::now().date_naive();
    let week_start = current_utc_week_start().date();
    let onboarding = sqlx::query_as::<_, LatestOnboardingRow>(
        r#"
        SELECT topic_summary, inferred_topics
        FROM onboarding_discovery_runs
        WHERE user_id::bigint = $1
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        "#,
    )
    .bind(user_id)
    .fetch_optional(&mut **transaction)
    .await?;
    let recent_reads = sqlx::query_as::<_, RecentRead>(
        r#"
        SELECT
            content.id::bigint AS id,
            COALESCE(
                NULLIF(btrim(content.title), ''),
                NULLIF(btrim(content.content_metadata::jsonb ->> 'title'), ''),
                'Untitled'
            ) AS title,
            content.url
        FROM contents AS content
        JOIN content_read_status AS read ON read.content_id = content.id
        WHERE read.user_id::bigint = $1 AND content.url IS NOT NULL
        ORDER BY read.read_at DESC, content.id DESC
        LIMIT 6
        "#,
    )
    .bind(user_id)
    .fetch_all(&mut **transaction)
    .await?;
    let option_rows = sqlx::query_as::<_, FeedOptionRow>(
        r#"
        WITH latest_run AS (
            SELECT id
            FROM feed_discovery_runs
            WHERE user_id::bigint = $1 AND status = 'completed'
            ORDER BY created_at DESC, id DESC
            LIMIT 1
        )
        SELECT
            suggestion.suggestion_type,
            suggestion.site_url,
            suggestion.feed_url,
            suggestion.title,
            suggestion.description,
            suggestion.rationale
        FROM feed_discovery_suggestions AS suggestion
        JOIN latest_run ON latest_run.id = suggestion.run_id
        WHERE suggestion.user_id::bigint = $1 AND suggestion.status = 'new'
        ORDER BY suggestion.score DESC NULLS LAST, suggestion.id
        LIMIT 20
        "#,
    )
    .bind(user_id)
    .fetch_all(&mut **transaction)
    .await?;
    let active_urls = sqlx::query_scalar::<_, String>(
        r#"
        SELECT COALESCE(feed_url, config::jsonb ->> 'feed_url')
        FROM user_scraper_configs
        WHERE user_id::bigint = $1
          AND is_active = TRUE
          AND COALESCE(feed_url, config::jsonb ->> 'feed_url') IS NOT NULL
        "#,
    )
    .bind(user_id)
    .fetch_all(&mut **transaction)
    .await?
    .into_iter()
    .map(|url| canonicalize_feed_url(&url))
    .collect::<HashSet<_>>();
    let mut seen = HashSet::new();
    let feed_options = option_rows
        .into_iter()
        .filter_map(|row| build_feed_option(row, &active_urls, &mut seen))
        .take(5)
        .collect();
    Ok(WeeklySeed {
        local_date: today.to_string(),
        week_key: format!("weekly:{week_start}"),
        week_label: week_start.to_string(),
        topic_summary: onboarding
            .as_ref()
            .and_then(|row| row.topic_summary.clone()),
        inferred_topics: onboarding
            .as_ref()
            .map(|row| string_array(&row.inferred_topics.0))
            .unwrap_or_default(),
        recent_reads,
        feed_options,
    })
}

fn current_utc_week_start() -> NaiveDateTime {
    let today = Utc::now().date_naive();
    today
        .checked_sub_days(Days::new(u64::from(today.weekday().num_days_from_sunday())))
        .unwrap_or(today)
        .and_time(chrono::NaiveTime::MIN)
}

fn build_feed_option(
    row: FeedOptionRow,
    active_urls: &HashSet<String>,
    seen: &mut HashSet<String>,
) -> Option<Value> {
    let feed_url = canonicalize_feed_url(&row.feed_url);
    if feed_url.is_empty() || active_urls.contains(&feed_url) || !seen.insert(feed_url.clone()) {
        return None;
    }
    let feed_type = match row.suggestion_type.as_str() {
        "rss" | "substack" => "substack",
        "atom" => "atom",
        "podcast_rss" => "podcast_rss",
        "youtube" => "youtube",
        _ => return None,
    };
    let site_url = row
        .site_url
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| feed_url.clone());
    let title = clean(row.title, 300).unwrap_or_else(|| site_url.clone());
    Some(json!({
        "id": short_digest(&feed_url),
        "title": title,
        "site_url": site_url,
        "feed_url": feed_url,
        "feed_type": feed_type,
        "feed_format": if row.suggestion_type == "atom" { "atom" } else { "rss" },
        "description": clean(row.description, 600),
        "rationale": clean(row.rationale, 600),
        "evidence_url": site_url,
        "is_subscribed": false,
    }))
}

fn weekly_context(seed: &WeeklySeed) -> String {
    let mut lines = vec![
        format!("Weekly discovery date: {}", seed.local_date),
        format!("Weekly discovery week: {}", seed.week_label),
    ];
    if let Some(summary) = seed.topic_summary.as_ref() {
        lines.push(format!("Onboarding summary: {summary}"));
    }
    if !seed.inferred_topics.is_empty() {
        lines.push(format!(
            "Inferred topics: {}",
            seed.inferred_topics
                .iter()
                .take(8)
                .cloned()
                .collect::<Vec<_>>()
                .join(", ")
        ));
    }
    if !seed.recent_reads.is_empty() {
        lines.push("Recent reads:".to_owned());
        lines.extend(
            seed.recent_reads
                .iter()
                .map(|row| format!("- [{}] {} — {}", row.id, row.title, row.url)),
        );
    }
    if !seed.feed_options.is_empty() {
        lines.push("Fresh discovery suggestions in canonical numbered order (ordinal follow-ups refer to this exact order):".to_owned());
        for (index, option) in seed.feed_options.iter().enumerate() {
            lines.push(format!(
                "{}. {}",
                index + 1,
                option
                    .get("title")
                    .and_then(Value::as_str)
                    .unwrap_or("Untitled")
            ));
            for key in ["feed_type", "feed_url", "site_url", "rationale"] {
                if let Some(value) = option.get(key).and_then(Value::as_str) {
                    lines.push(format!("   {key}={value}"));
                }
            }
        }
    }
    lines.join("\n")
}

fn weekly_message(seed: &WeeklySeed) -> String {
    let intro = format!(
        "Here are a few things worth exploring for the week of {}.",
        seed.week_label
    );
    if !seed.feed_options.is_empty() {
        let mut lines = vec![intro, String::new(), "Fresh suggestions:".to_owned()];
        for (index, option) in seed.feed_options.iter().enumerate() {
            lines.push(format!(
                "{}. {}",
                index + 1,
                option
                    .get("title")
                    .and_then(Value::as_str)
                    .unwrap_or("Untitled")
            ));
            if let Some(rationale) = option.get("rationale").and_then(Value::as_str) {
                lines.push(format!("   Why it stands out: {rationale}"));
            }
        }
        lines.extend([
            String::new(),
            "Reply with things like “add the first two to my feed”, “subscribe me to the podcast”, or “find more like this”.".to_owned(),
        ]);
        return lines.join("\n");
    }
    if !seed.recent_reads.is_empty() {
        return format!(
            "{intro}\n\nI don't have fresh discovery suggestions yet, but your recent reading has clustered around: {}. Ask me to find related articles, podcasts, or feeds.",
            seed.recent_reads
                .iter()
                .take(3)
                .map(|row| row.title.as_str())
                .collect::<Vec<_>>()
                .join(", ")
        );
    }
    if !seed.inferred_topics.is_empty() {
        return format!(
            "{intro}\n\nI'll use your onboarding interests as the starting point: {}. Ask me to find something new.",
            seed.inferred_topics
                .iter()
                .take(5)
                .cloned()
                .collect::<Vec<_>>()
                .join(", ")
        );
    }
    format!(
        "{intro}\n\nI don't have enough personalized signal yet. Ask me for a topic and I'll start building your weekly discovery thread from there."
    )
}

fn weekly_render_metadata(seed: &WeeklySeed) -> Option<Value> {
    (!seed.feed_options.is_empty()).then(|| {
        json!({
            "feed_options": seed.feed_options,
            "council_candidates": [],
            "active_council_child_session_id": null,
        })
    })
}

fn assistant_transcript(text: &str) -> Result<String, serde_json::Error> {
    serde_json::to_string(&NewslyTranscript {
        messages: vec![NewslyMessage {
            id: None,
            role: MessageRole::Assistant,
            parts: vec![MessagePart::Assistant(AssistantPart::Text {
                text: text.to_owned(),
            })],
            created_at: Utc::now(),
            run_id: None,
            provider: None,
            model: None,
            finish_reason: Some(TranscriptFinishReason::Stop),
            usage: ProviderUsage::default(),
            metadata: Map::new(),
        }],
        ..NewslyTranscript::default()
    })
}

async fn insert_weekly_message(
    transaction: &mut Transaction<'_, Postgres>,
    session_id: i64,
    message_list: &str,
    render_metadata: Option<&Value>,
    created_at: NaiveDateTime,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        r#"
        INSERT INTO chat_messages (
            session_id,
            message_list,
            render_metadata,
            created_at,
            status
        )
        VALUES ($1::bigint::integer, $2, $3, $4, 'completed')
        "#,
    )
    .bind(session_id)
    .bind(message_list)
    .bind(render_metadata)
    .bind(created_at)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

fn string_array(value: &Value) -> Vec<String> {
    value
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .collect()
}

fn clean(value: Option<String>, max_chars: usize) -> Option<String> {
    let value = value?.split_whitespace().collect::<Vec<_>>().join(" ");
    (!value.is_empty()).then(|| value.chars().take(max_chars).collect())
}

fn short_digest(value: &str) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let digest = Sha256::digest(value.as_bytes());
    let mut output = String::with_capacity(16);
    for byte in &digest[..8] {
        output.push(char::from(HEX[usize::from(byte >> 4)]));
        output.push(char::from(HEX[usize::from(byte & 0x0f)]));
    }
    output
}

#[derive(Debug, Error)]
pub enum OnboardingTaskRepositoryError {
    #[error("onboarding task database operation failed")]
    Sqlx(#[from] sqlx::Error),
    #[error("onboarding transcript serialization failed")]
    Json(#[from] serde_json::Error),
    #[error("the onboarding user is missing or inactive")]
    UserMissingOrInactive,
}

#[cfg(test)]
mod tests {
    use super::{WeeklySeed, short_digest, weekly_message};

    #[test]
    fn weekly_seed_without_signal_is_still_actionable() {
        let message = weekly_message(&WeeklySeed {
            local_date: "2026-08-30".to_owned(),
            week_key: "weekly:2026-08-30".to_owned(),
            week_label: "2026-08-30".to_owned(),
            topic_summary: None,
            inferred_topics: Vec::new(),
            recent_reads: Vec::new(),
            feed_options: Vec::new(),
        });
        assert!(message.contains("Ask me for a topic"));
    }

    #[test]
    fn feed_option_ids_are_short_and_stable() {
        assert_eq!(
            short_digest("https://example.com/feed"),
            short_digest("https://example.com/feed")
        );
        assert_eq!(short_digest("https://example.com/feed").len(), 16);
    }
}
