use std::collections::BTreeSet;

use chrono::{Duration, Utc};
use newsly_db::content_metadata::is_deck_source_only;
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

    if !requests.is_empty() {
        queue
            .enqueue_many_in_transaction(transaction, requests)
            .await?;
    }
    enqueue_briefing_followups(
        transaction,
        queue,
        applied.content_id,
        briefing_debounce_seconds,
        briefing_batch_minimum,
    )
    .await
}

pub(crate) async fn enqueue_briefing_followups(
    transaction: &mut Transaction<'static, Postgres>,
    queue: &QueueKernel,
    content_id: i64,
    debounce_seconds: i64,
    batch_minimum: i64,
) -> Result<(), SummarizationFanoutError> {
    let briefing_requests =
        prepare_briefing_requests(transaction, content_id, debounce_seconds, batch_minimum).await?;
    crate::briefing_refresh::enqueue_and_pull_forward_briefing_refreshes(
        transaction,
        queue,
        briefing_requests,
    )
    .await?;
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
    if !matches!(applied.status.as_str(), "completed" | "awaiting_image")
        || !matches!(applied.content_type.as_str(), "article" | "podcast")
    {
        return Ok(false);
    }
    let metadata = runtime_metadata_view(&applied.metadata);
    if is_deck_source_only(&metadata)
        || metadata
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
                WHERE content_id = $1::bigint AND status = 'inbox'
            )
            OR EXISTS (
                SELECT 1 FROM content_knowledge_saves
                WHERE content_id = $1::bigint
            )
        ",
    )
    .bind(applied.content_id)
    .fetch_one(&mut **transaction)
    .await
}

async fn prepare_briefing_requests(
    transaction: &mut Transaction<'static, Postgres>,
    content_id: i64,
    debounce_seconds: i64,
    batch_minimum: i64,
) -> Result<Vec<EnqueueRequest>, sqlx::Error> {
    let content_type = sqlx::query_scalar::<_, String>(
        r"
        SELECT content_type
        FROM contents
        WHERE id = $1::bigint
          AND status = 'completed'
          AND content_type IN ('article', 'podcast')
          AND NULLIF(COALESCE(
              content_metadata::jsonb #> '{domain,image_generated_at}',
              content_metadata::jsonb -> 'image_generated_at'
          ), 'null'::jsonb) IS NOT NULL
          AND NULLIF(COALESCE(
              content_metadata::jsonb #> '{domain,image_url}',
              content_metadata::jsonb -> 'image_url',
              content_metadata::jsonb #> '{domain,thumbnail_url}',
              content_metadata::jsonb -> 'thumbnail_url'
          ), 'null'::jsonb) IS NOT NULL
        ",
    )
    .bind(content_id)
    .fetch_optional(&mut **transaction)
    .await?;
    let Some(content_type) = content_type else {
        return Ok(Vec::new());
    };
    let user_ids = sqlx::query_scalar::<_, i64>(
        r"
        SELECT DISTINCT status.user_id::bigint
        FROM content_status AS status
        JOIN users ON users.id = status.user_id AND users.is_active IS TRUE
        LEFT JOIN content_read_status AS reads
          ON reads.user_id = status.user_id
         AND reads.content_id = status.content_id
        WHERE status.content_id = $1::bigint
          AND status.status = 'inbox'
          AND reads.id IS NULL
        ORDER BY status.user_id::bigint
        ",
    )
    .bind(content_id)
    .fetch_all(&mut **transaction)
    .await?;
    if user_ids.is_empty() {
        return Ok(Vec::new());
    }
    let lens_key = if content_type == "podcast" {
        "podcasts"
    } else {
        "articles"
    };
    let unique_users = user_ids.into_iter().collect::<BTreeSet<_>>();
    let unique_user_vec = unique_users.into_iter().collect::<Vec<_>>();
    sqlx::query(
        r"
        INSERT INTO briefing_pending_sources (
            user_id, lens_key, source_kind, source_id, enqueued_at
        )
        SELECT user_id, $2, 'content', $3, timezone('UTC', clock_timestamp())
        FROM unnest($1::bigint[]) AS user_id
        ON CONFLICT (user_id, source_kind, source_id) DO UPDATE
        SET lens_key = COALESCE(briefing_pending_sources.lens_key, EXCLUDED.lens_key)
        ",
    )
    .bind(&unique_user_vec)
    .bind(lens_key)
    .bind(content_id)
    .execute(&mut **transaction)
    .await?;

    let counts = sqlx::query_as::<_, (i64, i64)>(
        r"
        SELECT user_id::bigint, count(*)::bigint
        FROM briefing_pending_sources
        WHERE user_id = ANY($1::bigint[]) AND lens_key = $2
        GROUP BY user_id
        ",
    )
    .bind(&unique_user_vec)
    .bind(lens_key)
    .fetch_all(&mut **transaction)
    .await?
    .into_iter()
    .collect::<std::collections::HashMap<_, _>>();

    let now = Utc::now();
    let mut requests = Vec::with_capacity(unique_user_vec.len());
    for user_id in unique_user_vec {
        let count = counts.get(&user_id).copied().unwrap_or(0);
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
        request.available_at = Some(now + Duration::seconds(delay));
        requests.push(request);
    }
    Ok(requests)
}

async fn active_user_exists(
    transaction: &mut Transaction<'static, Postgres>,
    user_id: i64,
) -> Result<bool, sqlx::Error> {
    sqlx::query_scalar::<_, bool>(
        "SELECT EXISTS(SELECT 1 FROM users WHERE id = $1::bigint AND is_active IS TRUE)",
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
pub(crate) enum SummarizationFanoutError {
    #[error(transparent)]
    Sqlx(#[from] sqlx::Error),
    #[error(transparent)]
    Queue(#[from] QueueError),
    #[error(transparent)]
    ContentSubmission(#[from] newsly_db::ContentSubmissionRepositoryError),
    #[error(transparent)]
    Json(#[from] serde_json::Error),
}
