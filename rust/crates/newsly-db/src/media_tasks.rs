use chrono::{DateTime, NaiveDateTime, SecondsFormat, Utc};
use serde_json::{Map, Value, json};
use sqlx::{FromRow, Postgres, Transaction};
use thiserror::Error;

const DOMAIN_KEY: &str = "domain";
const PROCESSING_KEY: &str = "processing";
const RAW_BODY_KEYS: [&str; 6] = [
    "content",
    "transcript",
    "content_to_summarize",
    "file_path",
    "transcript_path",
    "full_text",
];

#[derive(Debug, Clone, FromRow)]
pub struct MediaContentSnapshot {
    pub id: i64,
    pub content_type: String,
    pub url: String,
    pub source_url: Option<String>,
    pub title: Option<String>,
    pub source: Option<String>,
    pub platform: Option<String>,
    pub status: String,
    pub content_metadata: Value,
    pub error_message: Option<String>,
    pub retry_count: i32,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MediaTranscriptPointer {
    pub storage_provider: String,
    pub storage_bucket: Option<String>,
    pub storage_key: String,
    pub content_format: String,
    pub sha256: String,
    pub byte_size: i32,
    pub char_count: i32,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MediaTranscriptionChunkUsage {
    pub task_id: i64,
    pub retry_count: i32,
    pub content_id: i64,
    pub user_id: Option<i64>,
    pub chunk_index: usize,
    pub model: String,
    pub media_kind: String,
    pub prompt_chars: i32,
    pub audio_size_bytes: i64,
    pub audio_duration_ms: Option<u64>,
    pub audio_duration_estimate_ms: Option<u64>,
    pub duration_source: String,
    pub standard_pricing: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MediaNextTask {
    Summarize,
    TranscribeTweetVideo,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum MediaMutation {
    PodcastCompleted {
        body: MediaTranscriptPointer,
        transcript: String,
        transcription_at: DateTime<Utc>,
        transcription_service: String,
        detected_language: Option<String>,
        resolved_audio_url: Option<String>,
        resolved_feed_url: Option<String>,
        resolved_episode_title: Option<String>,
    },
    PodcastFailed {
        error_message: String,
        increment_retry_count: bool,
    },
    TweetDownloaded {
        audio_path: String,
        duration_ms: Option<i64>,
        downloaded_at: DateTime<Utc>,
    },
    TweetSkipped {
        disabled: bool,
    },
    TweetFallback {
        stage: String,
        error_message: String,
        failed_at: DateTime<Utc>,
    },
    TweetTranscribed {
        transcript: String,
        transcription_at: DateTime<Utc>,
        transcription_service: String,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MediaApplyOutcome {
    Applied { next_task: Option<MediaNextTask> },
    ContentMissing,
    ContentTerminal,
}

/// Loads the immutable media-task input in one bounded database round trip. The returned value
/// owns all JSON and strings, so callers can commit before starting network, subprocess, or model
/// work.
pub async fn prepare_media_content(
    transaction: &mut Transaction<'_, Postgres>,
    content_id: i64,
) -> Result<Option<MediaContentSnapshot>, MediaTaskRepositoryError> {
    let row = sqlx::query_as::<_, MediaContentSnapshot>(
        r#"
        SELECT
            id::bigint AS id,
            content_type,
            url,
            source_url,
            title,
            source,
            platform,
            status,
            COALESCE(content_metadata, '{}'::json) AS content_metadata,
            error_message,
            COALESCE(retry_count, 0) AS retry_count
        FROM contents
        WHERE id::bigint = $1
        "#,
    )
    .bind(content_id)
    .fetch_optional(&mut **transaction)
    .await?;
    Ok(row)
}

/// Applies one media result inside the queue kernel's exact-lease transaction. The content row is
/// locked only for these bounded writes. Downstream work is returned to the worker finalizer so it
/// can be enqueued atomically in the same transaction.
pub async fn apply_media_mutation(
    transaction: &mut Transaction<'_, Postgres>,
    content_id: i64,
    mutation: &MediaMutation,
) -> Result<MediaApplyOutcome, MediaTaskRepositoryError> {
    let Some(mut content) = load_locked_content(transaction, content_id).await? else {
        return Ok(MediaApplyOutcome::ContentMissing);
    };
    if is_terminal_content_status(&content.status) {
        return Ok(MediaApplyOutcome::ContentTerminal);
    }

    let mut metadata = metadata_map(&content.content_metadata);
    let mut next_task = match mutation {
        MediaMutation::PodcastCompleted {
            body,
            transcript,
            transcription_at,
            transcription_service,
            detected_language,
            resolved_audio_url,
            resolved_feed_url,
            resolved_episode_title,
        } => {
            upsert_source_body(transaction, content.id, body).await?;
            set_domain_field(
                &mut metadata,
                "transcription_date",
                Value::String(utc_string(*transcription_at)),
            );
            set_domain_field(
                &mut metadata,
                "transcription_service",
                Value::String(transcription_service.clone()),
            );
            set_domain_field(&mut metadata, "has_transcript", Value::Bool(true));
            if let Some(language) = detected_language.as_deref().and_then(nonempty) {
                set_domain_field(
                    &mut metadata,
                    "detected_language",
                    Value::String(language.to_owned()),
                );
            }
            if let Some(audio_url) = resolved_audio_url.as_deref().and_then(nonempty) {
                set_domain_field(
                    &mut metadata,
                    "audio_url",
                    Value::String(audio_url.to_owned()),
                );
            }
            if let Some(feed_url) = resolved_feed_url.as_deref().and_then(nonempty) {
                set_domain_field(
                    &mut metadata,
                    "feed_url",
                    Value::String(feed_url.to_owned()),
                );
            }
            if let Some(episode_title) = resolved_episode_title.as_deref().and_then(nonempty) {
                set_domain_field(
                    &mut metadata,
                    "episode_title",
                    Value::String(episode_title.to_owned()),
                );
                if content.title.as_deref().and_then(nonempty).is_none() {
                    content.title = Some(truncate_chars(episode_title, 500));
                }
            }
            let excerpt = runtime_value(&metadata, "excerpt")
                .and_then(Value::as_str)
                .and_then(nonempty)
                .map(|value| truncate_chars(value, 1_000))
                .or_else(|| compact_excerpt(transcript));
            if let Some(excerpt) = &excerpt {
                set_domain_field(&mut metadata, "excerpt", Value::String(excerpt.clone()));
            }
            remove_raw_body_fields(&mut metadata);
            content.search_text = Some(build_search_text(&content, &metadata, excerpt.as_deref()));
            "processing".clone_into(&mut content.status);
            content.error_message = None;
            content.processed_at = Some(Utc::now().naive_utc());
            Some(MediaNextTask::Summarize)
        }
        MediaMutation::PodcastFailed {
            error_message,
            increment_retry_count,
        } => {
            "failed".clone_into(&mut content.status);
            content.error_message = Some(truncate_chars(error_message, 500));
            if *increment_retry_count {
                content.retry_count = content.retry_count.saturating_add(1);
            }
            content.processed_at = Some(Utc::now().naive_utc());
            None
        }
        MediaMutation::TweetDownloaded {
            audio_path,
            duration_ms,
            downloaded_at,
        } => {
            set_domain_field(&mut metadata, "has_video", Value::Bool(true));
            if let Some(duration_ms) = *duration_ms
                && duration_ms >= 0
            {
                set_domain_field(&mut metadata, "video_duration_ms", Value::from(duration_ms));
            }
            remove_domain_field(&mut metadata, "tweet_video_skip_reason");
            remove_domain_field(&mut metadata, "tweet_video_error");
            set_domain_field(
                &mut metadata,
                "video_audio_path",
                Value::String(audio_path.clone()),
            );
            set_domain_field(
                &mut metadata,
                "tweet_video_downloaded_at",
                Value::String(utc_string(*downloaded_at)),
            );
            "processing".clone_into(&mut content.status);
            content.error_message = None;
            Some(MediaNextTask::TranscribeTweetVideo)
        }
        MediaMutation::TweetSkipped { disabled } => {
            set_domain_field(&mut metadata, "has_video", Value::Bool(false));
            remove_domain_field(&mut metadata, "video_audio_path");
            if *disabled {
                set_domain_field(
                    &mut metadata,
                    "tweet_video_skip_reason",
                    Value::String("disabled".to_owned()),
                );
            }
            "processing".clone_into(&mut content.status);
            content.error_message = None;
            Some(MediaNextTask::Summarize)
        }
        MediaMutation::TweetFallback {
            stage,
            error_message,
            failed_at,
        } => {
            set_domain_field(&mut metadata, "has_video", Value::Bool(false));
            remove_domain_field(&mut metadata, "video_audio_path");
            set_domain_field(
                &mut metadata,
                "tweet_video_error",
                json!({
                    "stage": stage,
                    "message": truncate_chars(error_message, 500),
                    "timestamp": utc_string(*failed_at),
                }),
            );
            "processing".clone_into(&mut content.status);
            content.error_message = None;
            Some(MediaNextTask::Summarize)
        }
        MediaMutation::TweetTranscribed {
            transcript,
            transcription_at,
            transcription_service,
        } => {
            remove_domain_field(&mut metadata, "video_audio_path");
            set_domain_field(
                &mut metadata,
                "video_transcript",
                Value::String(transcript.clone()),
            );
            set_domain_field(
                &mut metadata,
                "video_transcription_date",
                Value::String(utc_string(*transcription_at)),
            );
            set_domain_field(
                &mut metadata,
                "video_transcription_service",
                Value::String(transcription_service.clone()),
            );
            remove_domain_field(&mut metadata, "tweet_video_error");
            "processing".clone_into(&mut content.status);
            content.error_message = None;
            Some(MediaNextTask::Summarize)
        }
    };

    if matches!(next_task, Some(MediaNextTask::Summarize))
        && runtime_value(&metadata, "deck_source_only").and_then(Value::as_bool) == Some(true)
    {
        next_task = None;
        "completed".clone_into(&mut content.status);
        content.processed_at = Some(Utc::now().naive_utc());
    }
    update_content(transaction, &content, Value::Object(metadata)).await?;
    Ok(MediaApplyOutcome::Applied { next_task })
}

/// Records one successful OpenAI media-transcription chunk independently of product finalization.
pub async fn record_media_transcription_chunk_usage(
    pool: &sqlx::PgPool,
    usage: &MediaTranscriptionChunkUsage,
) -> Result<bool, MediaTaskRepositoryError> {
    let request_id = format!(
        "media-{}-attempt-{}-chunk-{}",
        usage.task_id, usage.retry_count, usage.chunk_index
    );
    let mut transaction = pool.begin().await?;
    let priced = crate::openai_transcription_cost(
        &mut transaction,
        &usage.model,
        usage.audio_duration_ms,
        usage.duration_source == "ffprobe",
        usage.standard_pricing,
    )
    .await?;
    sqlx::query("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))")
        .bind(&request_id)
        .execute(&mut *transaction)
        .await?;
    let inserted = sqlx::query_scalar::<_, i64>(
        r#"
        INSERT INTO vendor_usage_records (
            provider,
            model,
            feature,
            operation,
            source,
            request_id,
            task_id,
            content_id,
            user_id,
            request_count,
            resource_count,
            currency,
            pricing_version,
            cost_usd,
            cost_basis,
            metadata,
            created_at
        ) SELECT
            'openai',
            $1,
            'transcription',
            'transcription.openai',
            'rust_worker',
            $2,
            $3::bigint::integer,
            $4::bigint::integer,
            (
                SELECT users.id
                FROM users
                WHERE users.id::bigint = $5
                  AND users.is_active IS TRUE
            ),
            1,
            $6,
            'USD',
            $7,
            $8,
            $9,
            $10,
            timezone('UTC', clock_timestamp())
        WHERE NOT EXISTS (
            SELECT 1 FROM vendor_usage_records WHERE request_id = $2
        )
        RETURNING id::bigint
        "#,
    )
    .bind(&usage.model)
    .bind(&request_id)
    .bind(usage.task_id)
    .bind(usage.content_id)
    .bind(usage.user_id)
    .bind(
        usage
            .audio_duration_ms
            .map(|value| i32::try_from(value.div_ceil(1_000)).unwrap_or(i32::MAX)),
    )
    .bind(priced.as_ref().map(|value| value.pricing_version.as_str()))
    .bind(priced.as_ref().map(|value| value.cost_usd))
    .bind(priced.as_ref().map(|_| "public_list_estimate"))
    .bind(json!({
        "media_kind": usage.media_kind,
        "attempt": usage.retry_count,
        "chunk_index": usage.chunk_index,
        "prompt_chars": usage.prompt_chars,
        "audio_size_bytes": usage.audio_size_bytes,
        "audio_duration_ms": usage.audio_duration_ms,
        "audio_duration_estimate_ms": usage.audio_duration_estimate_ms,
        "duration_source": usage.duration_source,
        "resource_count_unit": "audio_second",
        "pricing": priced.as_ref().map(|cost| &cost.metadata),
        "cost_reason": priced.is_none().then_some("missing_rate_or_unmeasured_audio_or_nonstandard_endpoint"),
    }))
    .fetch_optional(&mut *transaction)
    .await?;
    transaction.commit().await?;
    Ok(inserted.is_some())
}

#[derive(Debug, FromRow)]
struct LockedMediaContent {
    id: i64,
    title: Option<String>,
    source: Option<String>,
    status: String,
    error_message: Option<String>,
    retry_count: i32,
    content_metadata: Value,
    processed_at: Option<NaiveDateTime>,
    search_text: Option<String>,
}

async fn load_locked_content(
    transaction: &mut Transaction<'_, Postgres>,
    content_id: i64,
) -> Result<Option<LockedMediaContent>, sqlx::Error> {
    sqlx::query_as::<_, LockedMediaContent>(
        r#"
        SELECT
            id::bigint AS id,
            title,
            source,
            status,
            error_message,
            COALESCE(retry_count, 0) AS retry_count,
            COALESCE(content_metadata, '{}'::json) AS content_metadata,
            processed_at,
            search_text
        FROM contents
        WHERE id::bigint = $1
        FOR UPDATE
        "#,
    )
    .bind(content_id)
    .fetch_optional(&mut **transaction)
    .await
}

async fn update_content(
    transaction: &mut Transaction<'_, Postgres>,
    content: &LockedMediaContent,
    metadata: Value,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        r#"
        UPDATE contents
        SET
            title = $2,
            status = $3,
            error_message = $4,
            retry_count = $5,
            content_metadata = $6,
            processed_at = $7,
            search_text = $8,
            updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $1
        "#,
    )
    .bind(content.id)
    .bind(&content.title)
    .bind(&content.status)
    .bind(&content.error_message)
    .bind(content.retry_count)
    .bind(metadata)
    .bind(content.processed_at)
    .bind(&content.search_text)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

async fn upsert_source_body(
    transaction: &mut Transaction<'_, Postgres>,
    content_id: i64,
    body: &MediaTranscriptPointer,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        r#"
        INSERT INTO content_bodies (
            content_id,
            variant,
            storage_provider,
            storage_bucket,
            storage_key,
            content_format,
            sha256,
            byte_size,
            char_count,
            created_at,
            updated_at
        ) VALUES (
            $1::bigint::integer,
            'source',
            $2,
            $3,
            $4,
            $5,
            $6,
            $7,
            $8,
            timezone('UTC', clock_timestamp()),
            timezone('UTC', clock_timestamp())
        )
        ON CONFLICT (content_id, variant) DO UPDATE
        SET
            storage_provider = EXCLUDED.storage_provider,
            storage_bucket = EXCLUDED.storage_bucket,
            storage_key = EXCLUDED.storage_key,
            content_format = EXCLUDED.content_format,
            sha256 = EXCLUDED.sha256,
            byte_size = EXCLUDED.byte_size,
            char_count = EXCLUDED.char_count,
            updated_at = timezone('UTC', clock_timestamp())
        "#,
    )
    .bind(content_id)
    .bind(&body.storage_provider)
    .bind(&body.storage_bucket)
    .bind(&body.storage_key)
    .bind(&body.content_format)
    .bind(&body.sha256)
    .bind(body.byte_size)
    .bind(body.char_count)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

fn metadata_map(value: &Value) -> Map<String, Value> {
    value.as_object().cloned().unwrap_or_default()
}

fn runtime_value<'a>(metadata: &'a Map<String, Value>, key: &str) -> Option<&'a Value> {
    metadata
        .get(PROCESSING_KEY)
        .and_then(Value::as_object)
        .and_then(|processing| processing.get(key))
        .or_else(|| {
            metadata
                .get(DOMAIN_KEY)
                .and_then(Value::as_object)
                .and_then(|domain| domain.get(key))
        })
        .or_else(|| metadata.get(key))
}

fn set_domain_field(metadata: &mut Map<String, Value>, key: &str, value: Value) {
    metadata.insert(key.to_owned(), value.clone());
    if let Some(processing) = metadata
        .get_mut(PROCESSING_KEY)
        .and_then(Value::as_object_mut)
    {
        processing.remove(key);
    }
    let domain = metadata
        .entry(DOMAIN_KEY.to_owned())
        .or_insert_with(|| Value::Object(Map::new()));
    if !domain.is_object() {
        *domain = Value::Object(Map::new());
    }
    domain
        .as_object_mut()
        .expect("domain metadata was normalized to an object")
        .insert(key.to_owned(), value);
}

fn remove_domain_field(metadata: &mut Map<String, Value>, key: &str) {
    metadata.remove(key);
    if let Some(domain) = metadata.get_mut(DOMAIN_KEY).and_then(Value::as_object_mut) {
        domain.remove(key);
    }
    if let Some(processing) = metadata
        .get_mut(PROCESSING_KEY)
        .and_then(Value::as_object_mut)
    {
        processing.remove(key);
    }
}

fn remove_raw_body_fields(metadata: &mut Map<String, Value>) {
    for key in RAW_BODY_KEYS {
        metadata.remove(key);
        for namespace in [DOMAIN_KEY, PROCESSING_KEY] {
            if let Some(values) = metadata.get_mut(namespace).and_then(Value::as_object_mut) {
                values.remove(key);
            }
        }
    }
}

fn build_search_text(
    content: &LockedMediaContent,
    metadata: &Map<String, Value>,
    excerpt: Option<&str>,
) -> String {
    let metadata_source = runtime_value(metadata, "source")
        .and_then(Value::as_str)
        .and_then(nonempty);
    let stored_source = content.source.as_deref().and_then(nonempty);
    let mut parts = Vec::new();
    if metadata_source != stored_source
        && let Some(source) = metadata_source
    {
        parts.push(source.to_owned());
    }
    if let Some(excerpt) = excerpt.and_then(nonempty) {
        parts.push(excerpt.to_owned());
    }
    if let Some(summary) = runtime_value(metadata, "summary").and_then(Value::as_object) {
        for key in ["overview", "summary", "hook", "takeaway"] {
            if let Some(text) = summary.get(key).and_then(Value::as_str).and_then(nonempty) {
                parts.push(text.to_owned());
            }
        }
        for key in ["topics", "questions", "counter_arguments", "key_points"] {
            let Some(items) = summary.get(key).and_then(Value::as_array) else {
                continue;
            };
            for item in items {
                let text = match item {
                    Value::Object(item) => ["text", "point", "topic"]
                        .iter()
                        .find_map(|key| item.get(*key).and_then(Value::as_str).and_then(nonempty)),
                    Value::String(item) => nonempty(item),
                    _ => None,
                };
                if let Some(text) = text {
                    parts.push(text.to_owned());
                }
            }
        }
    }
    if let Some(discussion_url) = runtime_value(metadata, "discussion_url")
        .and_then(Value::as_str)
        .and_then(nonempty)
    {
        parts.push(discussion_url.to_owned());
    }
    parts.join("\n").trim().to_owned()
}

fn compact_excerpt(value: &str) -> Option<String> {
    let compact = value.split_whitespace().collect::<Vec<_>>().join(" ");
    nonempty(&compact).map(|text| truncate_chars(text, 1_000))
}

fn utc_string(value: DateTime<Utc>) -> String {
    value.to_rfc3339_opts(SecondsFormat::Micros, true)
}

fn nonempty(value: &str) -> Option<&str> {
    let trimmed = value.trim();
    (!trimmed.is_empty()).then_some(trimmed)
}

fn truncate_chars(value: &str, max_chars: usize) -> String {
    value.chars().take(max_chars).collect()
}

fn is_terminal_content_status(status: &str) -> bool {
    matches!(status, "completed" | "skipped")
}

#[derive(Debug, Error)]
pub enum MediaTaskRepositoryError {
    #[error("PostgreSQL media task operation failed")]
    Sqlx(#[from] sqlx::Error),
}

#[cfg(test)]
mod tests {
    use serde_json::{Value, json};
    use sqlx::PgPool;
    use uuid::Uuid;

    use super::{
        MediaTranscriptionChunkUsage, build_search_text, record_media_transcription_chunk_usage,
        remove_domain_field, runtime_value, set_domain_field,
    };

    #[sqlx::test]
    async fn transcription_chunk_usage_is_idempotent_before_product_finalization(pool: PgPool) {
        let user_id = sqlx::query_scalar::<_, i32>(
            "INSERT INTO users (apple_id, email, is_admin, is_active) VALUES ($1, $2, FALSE, TRUE) RETURNING id",
        )
        .bind(format!("media-usage-{}", Uuid::new_v4()))
        .bind(format!("media-usage-{}@example.com", Uuid::new_v4()))
        .fetch_one(&pool)
        .await
        .unwrap();
        let content_id = sqlx::query_scalar::<_, i32>(
            "INSERT INTO contents (content_type, url, status, is_aggregate, content_metadata) VALUES ('podcast', $1, 'processing', FALSE, '{}'::json) RETURNING id",
        )
        .bind(format!("https://example.com/{}", Uuid::new_v4()))
        .fetch_one(&pool)
        .await
        .unwrap();
        let usage = MediaTranscriptionChunkUsage {
            task_id: 700,
            retry_count: 2,
            content_id: i64::from(content_id),
            user_id: Some(i64::from(user_id)),
            chunk_index: 1,
            model: "gpt-transcribe".to_owned(),
            media_kind: "podcast".to_owned(),
            prompt_chars: 40,
            audio_size_bytes: 4_096,
            audio_duration_ms: Some(60_000),
            audio_duration_estimate_ms: None,
            duration_source: "ffprobe".to_owned(),
            standard_pricing: true,
        };

        assert!(
            record_media_transcription_chunk_usage(&pool, &usage)
                .await
                .unwrap()
        );
        assert!(
            !record_media_transcription_chunk_usage(&pool, &usage)
                .await
                .unwrap()
        );
        let row = sqlx::query_as::<_, (i64, i32, i32, String, String, f64, f64)>(
            r#"
            SELECT
                count(*)::bigint,
                max(request_count)::integer,
                max(resource_count)::integer,
                max(metadata->>'resource_count_unit'),
                max(metadata#>>'{pricing,components,0,unit}'),
                max((metadata#>>'{pricing,components,0,quantity}')::double precision),
                max(cost_usd)::double precision
            FROM vendor_usage_records
            WHERE request_id = 'media-700-attempt-2-chunk-1'
            "#,
        )
        .fetch_one(&pool)
        .await
        .unwrap();
        assert_eq!(row.0, 1);
        assert_eq!(row.1, 1);
        assert_eq!(row.2, 60);
        assert_eq!(row.3, "audio_second");
        assert_eq!(row.4, "audio_minute");
        assert!((row.5 - 1.0).abs() < f64::EPSILON);
        assert!((row.6 - 0.0045).abs() < 1e-12);
    }

    #[test]
    fn runtime_metadata_prefers_processing_then_domain_then_flat() {
        let metadata = json!({
            "value": "flat",
            "domain": {"value": "domain"},
            "processing": {"value": "processing"},
        });
        assert_eq!(
            runtime_value(metadata.as_object().unwrap(), "value"),
            Some(&Value::String("processing".to_owned()))
        );
    }

    #[test]
    fn domain_writes_remain_legacy_compatible() {
        let mut metadata = json!({"processing": {"has_video": false}})
            .as_object()
            .cloned()
            .unwrap();
        set_domain_field(&mut metadata, "has_video", Value::Bool(true));
        assert_eq!(metadata.get("has_video"), Some(&Value::Bool(true)));
        assert_eq!(
            metadata
                .get("domain")
                .and_then(Value::as_object)
                .and_then(|domain| domain.get("has_video")),
            Some(&Value::Bool(true))
        );
        remove_domain_field(&mut metadata, "has_video");
        assert!(metadata.get("has_video").is_none());
        assert!(
            metadata
                .get("processing")
                .and_then(Value::as_object)
                .is_some_and(|processing| !processing.contains_key("has_video"))
        );
    }

    #[test]
    fn search_text_preserves_existing_summary_fields() {
        let content = super::LockedMediaContent {
            id: 1,
            title: Some("Title".to_owned()),
            source: Some("Publisher".to_owned()),
            status: "processing".to_owned(),
            error_message: None,
            retry_count: 0,
            content_metadata: json!({}),
            processed_at: None,
            search_text: None,
        };
        let metadata = json!({
            "source": "Publisher",
            "summary": {
                "overview": "Overview",
                "topics": ["topic one", {"point": "topic two"}],
            },
            "discussion_url": "https://example.test/thread",
        });
        assert_eq!(
            build_search_text(
                &content,
                metadata.as_object().expect("object"),
                Some("Excerpt")
            ),
            "Excerpt\nOverview\ntopic one\ntopic two\nhttps://example.test/thread"
        );
    }
}
