use std::collections::BTreeSet;

use chrono::{Duration, Utc};
use newsly_queue::{EnqueueRequest, QueueError, QueueKernel, TaskType};
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};
use sqlx::{Postgres, Transaction};
use thiserror::Error;

use super::input::runtime_metadata_view;
use super::model::{AppliedSummarization, PendingChatRequest};

pub(super) async fn enqueue_summary_followups(
    transaction: &mut Transaction<'static, Postgres>,
    queue: &QueueKernel,
    applied: &AppliedSummarization,
    briefing_debounce_seconds: i64,
    briefing_batch_minimum: i64,
) -> Result<(), SummarizationFanoutError> {
    let mut requests = Vec::new();
    enqueue_pending_chats(transaction, applied, &mut requests).await?;

    if image_is_eligible(transaction, applied).await? {
        let mut request = EnqueueRequest::new(TaskType::GenerateImage);
        request.content_id = Some(applied.content_id);
        request.dedupe = Some(true);
        requests.push(request);
    }

    let briefing_requests = prepare_briefing_requests(
        transaction,
        applied,
        briefing_debounce_seconds,
        briefing_batch_minimum,
    )
    .await?;
    let briefing_deadlines = briefing_requests
        .iter()
        .filter_map(|request| Some((request.dedupe_key.clone()?, request.available_at?)))
        .collect::<Vec<_>>();
    requests.extend(briefing_requests);

    if !requests.is_empty() {
        queue
            .enqueue_many_in_transaction(transaction, requests)
            .await?;
    }
    // An active deduped refresh may predate this source. Pull it forward to the newly computed
    // deadline instead of waiting for the older schedule.
    for (dedupe_key, available_at) in briefing_deadlines {
        sqlx::query(
            r"
            UPDATE processing_tasks
            SET available_at = LEAST(available_at, $2)
            WHERE dedupe_key = $1
              AND status = 'pending'
            ",
        )
        .bind(dedupe_key)
        .bind(available_at.naive_utc())
        .execute(&mut **transaction)
        .await?;
    }
    Ok(())
}

async fn enqueue_pending_chats(
    transaction: &mut Transaction<'static, Postgres>,
    applied: &AppliedSummarization,
    requests: &mut Vec<EnqueueRequest>,
) -> Result<(), sqlx::Error> {
    for pending in &applied.pending_chat_requests {
        if !active_user_exists(transaction, pending.user_id).await? {
            continue;
        }
        requests.push(dig_deeper_request(applied.content_id, pending));
    }
    Ok(())
}

fn dig_deeper_request(content_id: i64, pending: &PendingChatRequest) -> EnqueueRequest {
    let mut payload = Map::from_iter([("user_id".to_owned(), Value::from(pending.user_id))]);
    if let Some(initial_message) = &pending.initial_message {
        payload.insert(
            "initial_message".to_owned(),
            Value::String(initial_message.clone()),
        );
    }
    let digest = Sha256::digest(
        pending
            .initial_message
            .as_deref()
            .unwrap_or_default()
            .as_bytes(),
    );
    let mut request = EnqueueRequest::new(TaskType::DigDeeper);
    request.content_id = Some(content_id);
    request.payload = Some(payload);
    request.owner_user_id = Some(pending.user_id);
    request.dedupe = Some(true);
    request.dedupe_key = Some(format!(
        "dig_deeper|user:{}|content:{content_id}|message:{}",
        pending.user_id,
        &hex_encode(&digest)[..16]
    ));
    request
}

async fn image_is_eligible(
    transaction: &mut Transaction<'static, Postgres>,
    applied: &AppliedSummarization,
) -> Result<bool, sqlx::Error> {
    if applied.status != "awaiting_image"
        || !matches!(applied.content_type.as_str(), "article" | "podcast")
        || applied.classification.as_deref() == Some("skip")
    {
        return Ok(false);
    }
    let metadata = runtime_metadata_view(&applied.metadata);
    if metadata
        .get("image_generated_at")
        .is_some_and(|value| !value.is_null())
        || !metadata.get("summary").is_some_and(Value::is_object)
    {
        return Ok(false);
    }
    sqlx::query_scalar::<_, bool>(
        r"
        SELECT
            EXISTS (
                SELECT 1 FROM content_status
                WHERE content_id::bigint = $1 AND status = 'inbox'
            )
            OR EXISTS (
                SELECT 1 FROM content_knowledge_saves
                WHERE content_id::bigint = $1
            )
        ",
    )
    .bind(applied.content_id)
    .fetch_one(&mut **transaction)
    .await
}

async fn prepare_briefing_requests(
    transaction: &mut Transaction<'static, Postgres>,
    applied: &AppliedSummarization,
    debounce_seconds: i64,
    batch_minimum: i64,
) -> Result<Vec<EnqueueRequest>, sqlx::Error> {
    if applied.status != "completed"
        || !matches!(applied.content_type.as_str(), "article" | "podcast")
        || applied.classification.as_deref() == Some("skip")
    {
        return Ok(Vec::new());
    }
    let user_ids = sqlx::query_scalar::<_, i64>(
        r"
        SELECT DISTINCT status.user_id::bigint
        FROM content_status AS status
        JOIN users ON users.id = status.user_id AND users.is_active IS TRUE
        LEFT JOIN content_read_status AS reads
          ON reads.user_id = status.user_id
         AND reads.content_id = status.content_id
        WHERE status.content_id::bigint = $1
          AND status.status = 'inbox'
          AND reads.id IS NULL
        ORDER BY status.user_id::bigint
        ",
    )
    .bind(applied.content_id)
    .fetch_all(&mut **transaction)
    .await?;
    if user_ids.is_empty() {
        return Ok(Vec::new());
    }
    let lens_key = if applied.content_type == "podcast" {
        "podcasts"
    } else {
        "articles"
    };
    let mut unique_users = BTreeSet::new();
    for user_id in user_ids {
        unique_users.insert(user_id);
        sqlx::query(
            r"
            INSERT INTO briefing_pending_sources (
                user_id, lens_key, source_kind, source_id, enqueued_at
            )
            VALUES ($1, $2, 'content', $3, timezone('UTC', clock_timestamp()))
            ON CONFLICT (user_id, source_kind, source_id) DO UPDATE
            SET lens_key = COALESCE(briefing_pending_sources.lens_key, EXCLUDED.lens_key)
            ",
        )
        .bind(user_id)
        .bind(lens_key)
        .bind(applied.content_id)
        .execute(&mut **transaction)
        .await?;
    }
    let mut requests = Vec::with_capacity(unique_users.len());
    for user_id in unique_users {
        let count = sqlx::query_scalar::<_, i64>(
            r"
            SELECT count(*)::bigint
            FROM briefing_pending_sources
            WHERE user_id::bigint = $1 AND lens_key = $2
            ",
        )
        .bind(user_id)
        .bind(lens_key)
        .fetch_one(&mut **transaction)
        .await?;
        let delay = if count >= batch_minimum.max(1) {
            0
        } else {
            debounce_seconds.max(0)
        };
        let mut request = EnqueueRequest::new(TaskType::BriefingRefresh);
        request.payload = Some(Map::from_iter([
            ("user_id".to_owned(), Value::from(user_id)),
            ("mode".to_owned(), Value::from("append")),
        ]));
        request.owner_user_id = Some(user_id);
        request.dedupe = Some(true);
        request.dedupe_key = Some(format!("briefing_refresh:{user_id}:append"));
        request.available_at = Some(Utc::now() + Duration::seconds(delay));
        requests.push(request);
    }
    Ok(requests)
}

async fn active_user_exists(
    transaction: &mut Transaction<'static, Postgres>,
    user_id: i64,
) -> Result<bool, sqlx::Error> {
    sqlx::query_scalar::<_, bool>(
        "SELECT EXISTS(SELECT 1 FROM users WHERE id::bigint = $1 AND is_active IS TRUE)",
    )
    .bind(user_id)
    .fetch_one(&mut **transaction)
    .await
}

fn hex_encode(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut encoded = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        encoded.push(char::from(HEX[usize::from(byte >> 4)]));
        encoded.push(char::from(HEX[usize::from(byte & 0x0f)]));
    }
    encoded
}

#[derive(Debug, Error)]
pub(super) enum SummarizationFanoutError {
    #[error(transparent)]
    Sqlx(#[from] sqlx::Error),
    #[error(transparent)]
    Queue(#[from] QueueError),
    #[error(transparent)]
    ContentSubmission(#[from] newsly_db::ContentSubmissionRepositoryError),
    #[error(transparent)]
    Json(#[from] serde_json::Error),
}
