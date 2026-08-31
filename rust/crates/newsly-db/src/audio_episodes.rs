use std::fmt::Write as _;

use chrono::{DateTime, NaiveDateTime, Utc};
use serde_json::Value;
use sha2::{Digest, Sha256};
use sqlx::{AssertSqlSafe, FromRow, PgPool, Postgres, Transaction};
use thiserror::Error;

use crate::briefing::BriefingReadMarkProjection;
use crate::{mark_briefing_sources_read, mark_contents_read, mark_visible_news_items_read};

const PROMPT_VERSION: i32 = 5;

#[derive(Debug, Clone, PartialEq)]
pub struct AudioEpisodeRecord {
    pub id: i64,
    pub user_id: i64,
    pub kind: String,
    pub status: String,
    pub title: String,
    pub source_content_id: Option<i64>,
    pub source_item_ids: Value,
    pub source_snapshot: Value,
    pub script: Option<Value>,
    pub script_text: Option<String>,
    pub model: Option<String>,
    pub audio_storage_path: Option<String>,
    pub audio_content_type: String,
    pub duration_seconds: Option<i32>,
    pub error_message: Option<String>,
    pub share_enabled: bool,
    pub started_at: Option<DateTime<Utc>>,
    pub completed_at: Option<DateTime<Utc>>,
    pub created_at: DateTime<Utc>,
    pub updated_at: Option<DateTime<Utc>>,
}

#[derive(Debug, FromRow)]
struct AudioEpisodeRow {
    id: i64,
    user_id: i64,
    kind: String,
    status: String,
    title: String,
    source_content_id: Option<i64>,
    source_item_ids: Value,
    source_snapshot: Value,
    script: Option<Value>,
    script_text: Option<String>,
    model: Option<String>,
    audio_storage_path: Option<String>,
    audio_content_type: String,
    duration_seconds: Option<i32>,
    error_message: Option<String>,
    share_enabled: bool,
    started_at: Option<NaiveDateTime>,
    completed_at: Option<NaiveDateTime>,
    created_at: NaiveDateTime,
    updated_at: Option<NaiveDateTime>,
}

impl From<AudioEpisodeRow> for AudioEpisodeRecord {
    fn from(row: AudioEpisodeRow) -> Self {
        Self {
            id: row.id,
            user_id: row.user_id,
            kind: row.kind,
            status: row.status,
            title: row.title,
            source_content_id: row.source_content_id,
            source_item_ids: row.source_item_ids,
            source_snapshot: row.source_snapshot,
            script: row.script,
            script_text: row.script_text,
            model: row.model,
            audio_storage_path: row.audio_storage_path,
            audio_content_type: row.audio_content_type,
            duration_seconds: row.duration_seconds,
            error_message: row.error_message,
            share_enabled: row.share_enabled,
            started_at: row.started_at.map(|value| value.and_utc()),
            completed_at: row.completed_at.map(|value| value.and_utc()),
            created_at: row.created_at.and_utc(),
            updated_at: row.updated_at.map(|value| value.and_utc()),
        }
    }
}

#[derive(Debug, Clone)]
pub struct NewAudioEpisode<'a> {
    pub user_id: i64,
    pub kind: &'a str,
    pub title: &'a str,
    pub source_content_id: Option<i64>,
    pub source_item_ids: &'a [i64],
    pub source_snapshot: &'a Value,
}

pub async fn upsert_audio_episode(
    transaction: &mut Transaction<'_, Postgres>,
    input: &NewAudioEpisode<'_>,
) -> Result<AudioEpisodeRecord, AudioEpisodeRepositoryError> {
    let input_hash = stable_snapshot_hash(input.source_snapshot)?;
    let row = sqlx::query_as::<_, AudioEpisodeRow>(
        r#"
        INSERT INTO audio_episodes (
            user_id, kind, status, title, source_content_id, input_hash,
            source_item_ids, source_snapshot, prompt_version,
            audio_content_type, share_enabled, created_at, updated_at
        ) VALUES (
            $1::bigint::integer, $2, 'pending', $3, $4::bigint::integer, $5,
            $6::jsonb, $7::jsonb, $8, 'audio/mpeg', FALSE,
            timezone('UTC', now()), timezone('UTC', now())
        )
        ON CONFLICT (user_id, kind, input_hash) DO UPDATE SET
            title = EXCLUDED.title,
            source_content_id = EXCLUDED.source_content_id,
            source_item_ids = EXCLUDED.source_item_ids,
            source_snapshot = EXCLUDED.source_snapshot,
            prompt_version = EXCLUDED.prompt_version,
            status = CASE WHEN audio_episodes.status = 'failed' THEN 'pending'
                          ELSE audio_episodes.status END,
            error_message = CASE WHEN audio_episodes.status = 'failed' THEN NULL
                                 ELSE audio_episodes.error_message END,
            script = CASE WHEN audio_episodes.status = 'failed' THEN NULL
                          ELSE audio_episodes.script END,
            script_text = CASE WHEN audio_episodes.status = 'failed' THEN NULL
                               ELSE audio_episodes.script_text END,
            audio_storage_path = CASE WHEN audio_episodes.status = 'failed' THEN NULL
                                      ELSE audio_episodes.audio_storage_path END,
            duration_seconds = CASE WHEN audio_episodes.status = 'failed' THEN NULL
                                    ELSE audio_episodes.duration_seconds END,
            started_at = CASE WHEN audio_episodes.status = 'failed' THEN NULL
                              ELSE audio_episodes.started_at END,
            completed_at = CASE WHEN audio_episodes.status = 'failed' THEN NULL
                                ELSE audio_episodes.completed_at END,
            updated_at = timezone('UTC', now())
        RETURNING
            id::bigint AS id, user_id::bigint AS user_id, kind, status, title,
            source_content_id::bigint AS source_content_id,
            source_item_ids::jsonb AS source_item_ids,
            source_snapshot::jsonb AS source_snapshot, script::jsonb AS script, script_text, model,
            audio_storage_path, audio_content_type, duration_seconds,
            error_message, share_enabled, started_at, completed_at,
            created_at, updated_at
        "#,
    )
    .bind(input.user_id)
    .bind(input.kind)
    .bind(input.title)
    .bind(input.source_content_id)
    .bind(input_hash)
    .bind(serde_json::to_value(input.source_item_ids)?)
    .bind(input.source_snapshot)
    .bind(PROMPT_VERSION)
    .fetch_one(&mut **transaction)
    .await?;
    Ok(row.into())
}

pub async fn find_user_audio_episode(
    pool: &PgPool,
    user_id: i64,
    audio_episode_id: i64,
) -> Result<Option<AudioEpisodeRecord>, AudioEpisodeRepositoryError> {
    Ok(
        sqlx::query_as::<_, AudioEpisodeRow>(AssertSqlSafe(select_audio_episode_sql(
            "AND id::bigint = $2::bigint",
        )))
        .bind(user_id)
        .bind(audio_episode_id)
        .fetch_optional(pool)
        .await?
        .map(Into::into),
    )
}

pub async fn find_user_audio_episode_for_update(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    audio_episode_id: i64,
) -> Result<Option<AudioEpisodeRecord>, AudioEpisodeRepositoryError> {
    let query = format!(
        "{} FOR UPDATE",
        select_audio_episode_sql("AND id::bigint = $2::bigint")
    );
    Ok(sqlx::query_as::<_, AudioEpisodeRow>(AssertSqlSafe(query))
        .bind(user_id)
        .bind(audio_episode_id)
        .fetch_optional(&mut **transaction)
        .await?
        .map(Into::into))
}

pub async fn list_user_custom_narrations(
    pool: &PgPool,
    user_id: i64,
    limit: usize,
) -> Result<Vec<AudioEpisodeRecord>, AudioEpisodeRepositoryError> {
    let suffix = "AND kind = 'custom_narration' ORDER BY created_at DESC, id DESC LIMIT $2";
    Ok(
        sqlx::query_as::<_, AudioEpisodeRow>(AssertSqlSafe(select_audio_episode_sql(suffix)))
            .bind(user_id)
            .bind(i64::try_from(limit.clamp(1, 50)).unwrap_or(50))
            .fetch_all(pool)
            .await?
            .into_iter()
            .map(Into::into)
            .collect(),
    )
}

pub async fn find_shared_audio_episode(
    pool: &PgPool,
    audio_episode_id: i64,
    nonce: &str,
    token_hash: &str,
) -> Result<Option<AudioEpisodeRecord>, AudioEpisodeRepositoryError> {
    Ok(sqlx::query_as::<_, AudioEpisodeRow>(
        r#"
        SELECT
            id::bigint AS id, user_id::bigint AS user_id, kind, status, title,
            source_content_id::bigint AS source_content_id,
            source_item_ids::jsonb AS source_item_ids,
            source_snapshot::jsonb AS source_snapshot, script::jsonb AS script, script_text, model,
            audio_storage_path, audio_content_type, duration_seconds,
            error_message, share_enabled, started_at, completed_at,
            created_at, updated_at
        FROM audio_episodes
        WHERE id::bigint = $1::bigint
          AND share_enabled IS TRUE
          AND share_token_nonce = $2
          AND share_token_hash = $3
          AND kind = 'custom_narration'
          AND status = 'completed'
          AND btrim(coalesce(audio_storage_path, '')) <> ''
        "#,
    )
    .bind(audio_episode_id)
    .bind(nonce)
    .bind(token_hash)
    .fetch_optional(pool)
    .await?
    .map(Into::into))
}

pub async fn reset_audio_episode_for_generation(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    audio_episode_id: i64,
) -> Result<Option<AudioEpisodeRecord>, AudioEpisodeRepositoryError> {
    let row = sqlx::query_as::<_, AudioEpisodeRow>(
        r#"
        UPDATE audio_episodes
        SET status = 'pending', error_message = NULL, started_at = NULL,
            completed_at = NULL, updated_at = timezone('UTC', now())
        WHERE id::bigint = $2::bigint AND user_id::bigint = $1::bigint
          AND (
              status IN ('pending', 'failed')
              OR status <> 'completed'
                 AND (started_at IS NULL OR started_at < timezone('UTC', now()) - interval '15 minutes')
          )
        RETURNING
            id::bigint AS id, user_id::bigint AS user_id, kind, status, title,
            source_content_id::bigint AS source_content_id,
            source_item_ids::jsonb AS source_item_ids,
            source_snapshot::jsonb AS source_snapshot, script::jsonb AS script, script_text, model,
            audio_storage_path, audio_content_type, duration_seconds,
            error_message, share_enabled, started_at, completed_at,
            created_at, updated_at
        "#,
    )
    .bind(user_id)
    .bind(audio_episode_id)
    .fetch_optional(&mut **transaction)
    .await?;
    Ok(row.map(Into::into))
}

#[derive(Debug, Clone, PartialEq)]
pub enum PrepareAudioEpisodeGenerationOutcome {
    Prepared(AudioEpisodeRecord),
    AlreadyCompleted,
    AlreadyProcessing,
    NotFound,
}

/// Locks and snapshots one generation target in a short transaction.
///
/// The returned record is fully owned and can cross the provider boundary without retaining an
/// ORM object, transaction, or pooled connection. `started_at` is the generation fence used by
/// the fresh finalization transaction.
pub async fn prepare_audio_episode_generation(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    audio_episode_id: i64,
) -> Result<PrepareAudioEpisodeGenerationOutcome, AudioEpisodeRepositoryError> {
    let Some(episode) =
        find_user_audio_episode_for_update(transaction, user_id, audio_episode_id).await?
    else {
        return Ok(PrepareAudioEpisodeGenerationOutcome::NotFound);
    };
    let user_is_active = sqlx::query_scalar::<_, bool>(
        "SELECT EXISTS(SELECT 1 FROM users WHERE id::bigint = $1::bigint AND is_active IS TRUE)",
    )
    .bind(user_id)
    .fetch_one(&mut **transaction)
    .await?;
    if !user_is_active {
        return Ok(PrepareAudioEpisodeGenerationOutcome::NotFound);
    }
    if episode.status == "completed"
        && episode
            .audio_storage_path
            .as_deref()
            .is_some_and(|path| !path.trim().is_empty())
    {
        return Ok(PrepareAudioEpisodeGenerationOutcome::AlreadyCompleted);
    }
    if episode.status == "processing"
        && episode.started_at.is_some_and(|started_at| {
            Utc::now().signed_duration_since(started_at) < chrono::Duration::minutes(15)
        })
    {
        return Ok(PrepareAudioEpisodeGenerationOutcome::AlreadyProcessing);
    }

    let row = sqlx::query_as::<_, AudioEpisodeRow>(
        r#"
        UPDATE audio_episodes
        SET status = 'processing', error_message = NULL,
            started_at = timezone('UTC', clock_timestamp()), completed_at = NULL,
            updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $2::bigint AND user_id::bigint = $1::bigint
        RETURNING
            id::bigint AS id, user_id::bigint AS user_id, kind, status, title,
            source_content_id::bigint AS source_content_id,
            source_item_ids::jsonb AS source_item_ids,
            source_snapshot::jsonb AS source_snapshot, script::jsonb AS script, script_text, model,
            audio_storage_path, audio_content_type, duration_seconds,
            error_message, share_enabled, started_at, completed_at,
            created_at, updated_at
        "#,
    )
    .bind(user_id)
    .bind(audio_episode_id)
    .fetch_one(&mut **transaction)
    .await?;
    Ok(PrepareAudioEpisodeGenerationOutcome::Prepared(row.into()))
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AudioEpisodeScriptUsage {
    pub provider: String,
    pub model: String,
    pub request_count: i32,
    pub input_tokens: i32,
    pub output_tokens: i32,
    pub cache_read_tokens: i32,
    pub cache_write_tokens: i32,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AudioEpisodeTtsUsage {
    pub model: String,
    pub request_count: i32,
    pub text_chars: i32,
}

#[derive(Debug)]
pub struct CompleteAudioEpisodeGeneration<'a> {
    pub task_id: i64,
    pub user_id: i64,
    pub audio_episode_id: i64,
    pub source_content_id: Option<i64>,
    pub prepared_started_at: DateTime<Utc>,
    pub title: &'a str,
    pub script: &'a Value,
    pub script_text: &'a str,
    pub model: &'a str,
    pub audio_storage_path: &'a str,
    pub duration_seconds: i32,
    pub script_usage: Option<&'a AudioEpisodeScriptUsage>,
    pub tts_usage: &'a AudioEpisodeTtsUsage,
}

/// Publishes a generated MP3 and its metering only when the exact prepared episode attempt still
/// owns the product row. Queue ownership is independently fenced by the worker kernel.
pub async fn complete_audio_episode_generation(
    transaction: &mut Transaction<'_, Postgres>,
    completed: &CompleteAudioEpisodeGeneration<'_>,
) -> Result<bool, AudioEpisodeRepositoryError> {
    let updated = sqlx::query(
        r#"
        UPDATE audio_episodes
        SET status = 'completed', title = $4, script = $5::jsonb, script_text = $6,
            model = $7, audio_storage_path = $8, audio_content_type = 'audio/mpeg',
            duration_seconds = $9, error_message = NULL,
            completed_at = timezone('UTC', clock_timestamp()),
            updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $2::bigint AND user_id::bigint = $1::bigint
          AND status = 'processing' AND started_at = $3
          AND EXISTS (
              SELECT 1 FROM users
              WHERE users.id::bigint = $1::bigint AND users.is_active IS TRUE
          )
        "#,
    )
    .bind(completed.user_id)
    .bind(completed.audio_episode_id)
    .bind(completed.prepared_started_at.naive_utc())
    .bind(completed.title)
    .bind(completed.script)
    .bind(completed.script_text)
    .bind(completed.model)
    .bind(completed.audio_storage_path)
    .bind(completed.duration_seconds)
    .execute(&mut **transaction)
    .await?
    .rows_affected();
    if updated == 0 {
        return Ok(false);
    }
    if let Some(usage) = completed.script_usage {
        insert_audio_episode_script_usage(
            transaction,
            completed.task_id,
            completed.user_id,
            completed.audio_episode_id,
            completed.source_content_id,
            usage,
        )
        .await?;
    }
    insert_audio_episode_tts_usage(transaction, completed, completed.tts_usage).await?;
    Ok(true)
}

pub async fn fail_audio_episode_generation(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    audio_episode_id: i64,
    prepared_started_at: DateTime<Utc>,
    error_message: &str,
    retry_scheduled: bool,
) -> Result<bool, AudioEpisodeRepositoryError> {
    let updated = sqlx::query(
        r#"
        UPDATE audio_episodes
        SET status = CASE WHEN $5 THEN 'pending' ELSE 'failed' END,
            error_message = $4, audio_storage_path = NULL,
            started_at = CASE WHEN $5 THEN NULL ELSE started_at END,
            completed_at = CASE WHEN $5 THEN NULL ELSE timezone('UTC', clock_timestamp()) END,
            updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $2::bigint AND user_id::bigint = $1::bigint
          AND status = 'processing' AND started_at = $3
        "#,
    )
    .bind(user_id)
    .bind(audio_episode_id)
    .bind(prepared_started_at.naive_utc())
    .bind(error_message)
    .bind(retry_scheduled)
    .execute(&mut **transaction)
    .await?
    .rows_affected();
    Ok(updated > 0)
}

#[derive(Debug)]
pub struct CheckpointAudioEpisodeScript<'a> {
    pub task_id: i64,
    pub user_id: i64,
    pub audio_episode_id: i64,
    pub source_content_id: Option<i64>,
    pub prepared_started_at: DateTime<Utc>,
    pub title: &'a str,
    pub script: &'a Value,
    pub script_text: &'a str,
    pub model: &'a str,
    pub duration_seconds: i32,
    pub usage: &'a AudioEpisodeScriptUsage,
}

/// Persists an already-billed generated script before a retryable TTS or file failure. This lets
/// the next queue attempt reuse the exact validated script instead of paying for another model
/// call. It does not change the episode lifecycle state.
pub async fn checkpoint_audio_episode_script(
    transaction: &mut Transaction<'_, Postgres>,
    checkpoint: &CheckpointAudioEpisodeScript<'_>,
) -> Result<bool, AudioEpisodeRepositoryError> {
    let updated = sqlx::query(
        r#"
        UPDATE audio_episodes
        SET title = $4, script = $5::jsonb, script_text = $6, model = $7,
            duration_seconds = $8, updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $2::bigint AND user_id::bigint = $1::bigint
          AND status = 'processing' AND started_at = $3
        "#,
    )
    .bind(checkpoint.user_id)
    .bind(checkpoint.audio_episode_id)
    .bind(checkpoint.prepared_started_at.naive_utc())
    .bind(checkpoint.title)
    .bind(checkpoint.script)
    .bind(checkpoint.script_text)
    .bind(checkpoint.model)
    .bind(checkpoint.duration_seconds)
    .execute(&mut **transaction)
    .await?
    .rows_affected();
    if updated == 0 {
        return Ok(false);
    }
    insert_audio_episode_script_usage(
        transaction,
        checkpoint.task_id,
        checkpoint.user_id,
        checkpoint.audio_episode_id,
        checkpoint.source_content_id,
        checkpoint.usage,
    )
    .await?;
    Ok(true)
}

async fn insert_audio_episode_script_usage(
    transaction: &mut Transaction<'_, Postgres>,
    task_id: i64,
    user_id: i64,
    audio_episode_id: i64,
    source_content_id: Option<i64>,
    usage: &AudioEpisodeScriptUsage,
) -> Result<(), sqlx::Error> {
    let total_tokens = usage.input_tokens.saturating_add(usage.output_tokens);
    sqlx::query(
        r#"
        INSERT INTO vendor_usage_records (
            provider, model, feature, operation, source, task_id, content_id, user_id,
            input_tokens, cache_read_tokens, cache_write_tokens, output_tokens, total_tokens,
            request_count, currency, metadata, created_at
        ) VALUES (
            $1, $2, 'audio_episode_script', 'audio_episodes.generate_script', 'rust_worker',
            $3::bigint::integer, $4::bigint::integer, $5::bigint::integer,
            $6, $7, $8, $9, $10, $11, 'USD', $12::jsonb,
            timezone('UTC', clock_timestamp())
        )
        "#,
    )
    .bind(&usage.provider)
    .bind(&usage.model)
    .bind(task_id)
    .bind(source_content_id)
    .bind(user_id)
    .bind(usage.input_tokens)
    .bind(usage.cache_read_tokens)
    .bind(usage.cache_write_tokens)
    .bind(usage.output_tokens)
    .bind(total_tokens)
    .bind(usage.request_count)
    .bind(serde_json::json!({
        "audio_episode_id": audio_episode_id,
        "prompt_version": PROMPT_VERSION,
    }))
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

async fn insert_audio_episode_tts_usage(
    transaction: &mut Transaction<'_, Postgres>,
    completed: &CompleteAudioEpisodeGeneration<'_>,
    usage: &AudioEpisodeTtsUsage,
) -> Result<(), sqlx::Error> {
    let cost_usd = f64::from(usage.text_chars) * (50.0 / 1_000_000.0);
    sqlx::query(
        r#"
        INSERT INTO vendor_usage_records (
            provider, model, feature, operation, source, task_id, content_id, user_id,
            request_count, resource_count, cost_usd, currency, pricing_version, metadata,
            created_at
        ) VALUES (
            'elevenlabs', $1, 'audio_episode_tts', 'narration.synthesize_dialogue_mp3',
            'rust_worker', $2::bigint::integer, $3::bigint::integer, $4::bigint::integer,
            $5, $6, $7, 'USD', '2026-08-02', $8::jsonb,
            timezone('UTC', clock_timestamp())
        )
        "#,
    )
    .bind(&usage.model)
    .bind(completed.task_id)
    .bind(completed.source_content_id)
    .bind(completed.user_id)
    .bind(usage.request_count)
    .bind(usage.text_chars)
    .bind(cost_usd)
    .bind(serde_json::json!({
        "audio_episode_id": completed.audio_episode_id,
        "text_chars": usage.text_chars,
    }))
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AudioEpisodeShareOutcome {
    Enabled,
    NotFound,
    WrongKind,
    NotReady,
}

pub async fn enable_audio_episode_share(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    audio_episode_id: i64,
    nonce: &str,
    token_hash: &str,
) -> Result<AudioEpisodeShareOutcome, AudioEpisodeRepositoryError> {
    let Some(episode) =
        find_user_audio_episode_for_update(transaction, user_id, audio_episode_id).await?
    else {
        return Ok(AudioEpisodeShareOutcome::NotFound);
    };
    if episode.kind != "custom_narration" {
        return Ok(AudioEpisodeShareOutcome::WrongKind);
    }
    if episode.status != "completed"
        || episode
            .audio_storage_path
            .as_deref()
            .is_none_or(|path| path.trim().is_empty())
    {
        return Ok(AudioEpisodeShareOutcome::NotReady);
    }
    sqlx::query(
        r#"
        UPDATE audio_episodes
        SET share_enabled = TRUE, share_token_nonce = $3, share_token_hash = $4,
            updated_at = timezone('UTC', now())
        WHERE user_id::bigint = $1::bigint AND id::bigint = $2::bigint
        "#,
    )
    .bind(user_id)
    .bind(audio_episode_id)
    .bind(nonce)
    .bind(token_hash)
    .execute(&mut **transaction)
    .await?;
    Ok(AudioEpisodeShareOutcome::Enabled)
}

pub async fn disable_audio_episode_share(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    audio_episode_id: i64,
) -> Result<bool, AudioEpisodeRepositoryError> {
    Ok(sqlx::query(
        r#"
        UPDATE audio_episodes
        SET share_enabled = FALSE, share_token_nonce = NULL, share_token_hash = NULL,
            updated_at = timezone('UTC', now())
        WHERE user_id::bigint = $1::bigint AND id::bigint = $2::bigint
        "#,
    )
    .bind(user_id)
    .bind(audio_episode_id)
    .execute(&mut **transaction)
    .await?
    .rows_affected()
        > 0)
}

pub async fn mark_audio_episode_sources_read(
    transaction: &mut Transaction<'_, Postgres>,
    episode: &AudioEpisodeRecord,
    trigger: AudioEpisodeReadTrigger,
) -> Result<Option<BriefingReadMarkProjection>, AudioEpisodeRepositoryError> {
    match (episode.kind.as_str(), trigger) {
        ("custom_narration", AudioEpisodeReadTrigger::Play) => {
            let policy = episode.source_snapshot.get("read_on_play");
            let content_ids = json_i64_array(policy.and_then(|value| value.get("content_ids")));
            let news_ids = json_i64_array(policy.and_then(|value| value.get("news_item_ids")));
            if !content_ids.is_empty() {
                mark_contents_read(transaction, episode.user_id, &content_ids).await?;
            }
            if !news_ids.is_empty() {
                mark_visible_news_items_read(transaction, episode.user_id, &news_ids).await?;
            }
            Ok(None)
        }
        ("briefing_narration", AudioEpisodeReadTrigger::Finish) => {
            let source_keys = json_string_array(episode.source_snapshot.get("source_keys"));
            if source_keys.is_empty() {
                return Ok(None);
            }
            let result =
                mark_briefing_sources_read(transaction, episode.user_id, &source_keys).await?;
            Ok(Some(result))
        }
        _ => Ok(None),
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AudioEpisodeReadTrigger {
    Play,
    Finish,
}

fn select_audio_episode_sql(suffix: &str) -> String {
    format!(
        r#"
        SELECT
            id::bigint AS id, user_id::bigint AS user_id, kind, status, title,
            source_content_id::bigint AS source_content_id,
            source_item_ids::jsonb AS source_item_ids,
            source_snapshot::jsonb AS source_snapshot, script::jsonb AS script, script_text, model,
            audio_storage_path, audio_content_type, duration_seconds,
            error_message, share_enabled, started_at, completed_at,
            created_at, updated_at
        FROM audio_episodes
        WHERE user_id::bigint = $1::bigint {suffix}
        "#,
    )
}

fn stable_snapshot_hash(snapshot: &Value) -> Result<String, serde_json::Error> {
    let payload = serde_json::json!({
        "prompt_version": PROMPT_VERSION,
        "source_snapshot": snapshot,
    });
    let bytes = serde_json::to_vec(&payload)?;
    let digest = Sha256::digest(bytes);
    let mut encoded = String::with_capacity(digest.len() * 2);
    for byte in digest {
        write!(&mut encoded, "{byte:02x}").expect("writing to a String cannot fail");
    }
    Ok(encoded)
}

fn json_i64_array(value: Option<&Value>) -> Vec<i64> {
    let Some(Value::Array(values)) = value else {
        return Vec::new();
    };
    let mut ids = values.iter().filter_map(Value::as_i64).collect::<Vec<_>>();
    ids.sort_unstable();
    ids.dedup();
    ids
}

fn json_string_array(value: Option<&Value>) -> Vec<String> {
    let Some(Value::Array(values)) = value else {
        return Vec::new();
    };
    let mut strings = values
        .iter()
        .filter_map(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(ToOwned::to_owned)
        .collect::<Vec<_>>();
    strings.sort();
    strings.dedup();
    strings
}

#[derive(Debug, Error)]
pub enum AudioEpisodeRepositoryError {
    #[error("audio episode database operation failed")]
    Sqlx(#[from] sqlx::Error),
    #[error("audio episode source snapshot serialization failed")]
    Json(#[from] serde_json::Error),
    #[error("audio episode content read update failed")]
    ContentRead(#[from] crate::ContentActionRepositoryError),
    #[error("audio episode news read update failed")]
    NewsRead(#[from] crate::NewsActionRepositoryError),
    #[error("audio episode Briefing read update failed")]
    Briefing(#[from] crate::BriefingRepositoryError),
}
