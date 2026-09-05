use chrono::NaiveDateTime;
use serde_json::{Map, Value, json};
use sqlx::{FromRow, Postgres, Transaction};
use thiserror::Error;

use super::input::{build_summarization_payload, input_fingerprint, runtime_metadata_view};
use super::model::{
    AppliedSummarization, PendingChatRequest, SummarizationApplyOutcome,
    SummarizationFinalizationPlan, SummarizationMutation, SummarizationSnapshot, SummaryUsage,
};

const RAW_BODY_FIELDS: [&str; 6] = [
    "content",
    "transcript",
    "content_to_summarize",
    "file_path",
    "transcript_path",
    "full_text",
];

/// Loads the immutable summarization input in the worker's bounded prepare transaction.
pub(super) async fn load_summarization_snapshot(
    transaction: &mut Transaction<'_, Postgres>,
    content_id: i64,
) -> Result<Option<SummarizationSnapshot>, SummarizationRepositoryError> {
    Ok(sqlx::query_as::<_, SummarizationSnapshot>(
        r"
        SELECT
            content.id::bigint AS id,
            content.content_type,
            content.url,
            content.title,
            content.source,
            content.platform,
            content.status,
            COALESCE(content.content_metadata, '{}'::json) AS content_metadata,
            content.publication_date,
            body.storage_provider AS body_storage_provider,
            body.storage_key AS body_storage_key,
            body.sha256 AS body_sha256
        FROM contents AS content
        LEFT JOIN content_bodies AS body
          ON body.content_id = content.id
         AND body.variant = 'source'
        WHERE content.id::bigint = $1
        ",
    )
    .bind(content_id)
    .fetch_optional(&mut **transaction)
    .await?)
}

/// Applies content state only inside the exact queue lease transaction.
pub(super) async fn apply_summarization_state(
    transaction: &mut Transaction<'static, Postgres>,
    plan: &SummarizationFinalizationPlan,
) -> Result<SummarizationApplyOutcome, SummarizationRepositoryError> {
    let Some(mut content) = load_locked_content(transaction, plan.attempt.content.id).await? else {
        return Ok(SummarizationApplyOutcome::ContentMissing);
    };
    if !source_is_current(&content, plan) {
        return Ok(SummarizationApplyOutcome::SourceChanged);
    }
    if matches!(content.status.as_str(), "failed" | "skipped")
        && !matches!(plan.mutation, SummarizationMutation::Failed { .. })
    {
        return Ok(SummarizationApplyOutcome::Applied(applied_context(
            &content,
            Vec::new(),
        )));
    }

    let pending_chat_requests = match &plan.mutation {
        SummarizationMutation::Complete { summary, usage } => {
            let pending = extract_pending_chat_requests(&content.content_metadata);
            apply_completed_summary(&mut content, plan, summary);
            persist_usage(transaction, plan, usage).await?;
            pending
        }
        SummarizationMutation::Unchanged => {
            apply_unchanged_summary(&mut content, plan);
            Vec::new()
        }
        SummarizationMutation::Failed {
            reason,
            retry_scheduled,
            skipped,
        } => {
            apply_failure(&mut content, plan, reason, *retry_scheduled, *skipped);
            Vec::new()
        }
    };
    persist_locked_content(transaction, &content).await?;
    Ok(SummarizationApplyOutcome::Applied(applied_context(
        &content,
        pending_chat_requests,
    )))
}

#[derive(Debug, FromRow)]
struct LockedContent {
    id: i64,
    content_type: String,
    title: Option<String>,
    status: String,
    classification: Option<String>,
    content_metadata: Value,
    error_message: Option<String>,
    processed_at: Option<NaiveDateTime>,
    current_body_sha256: Option<String>,
}

async fn load_locked_content(
    transaction: &mut Transaction<'static, Postgres>,
    content_id: i64,
) -> Result<Option<LockedContent>, sqlx::Error> {
    sqlx::query_as::<_, LockedContent>(
        r"
        SELECT
            content.id::bigint AS id,
            content.content_type,
            content.title,
            content.status,
            content.classification,
            COALESCE(content.content_metadata, '{}'::json) AS content_metadata,
            content.error_message,
            content.processed_at,
            body.sha256 AS current_body_sha256
        FROM contents AS content
        LEFT JOIN content_bodies AS body
          ON body.content_id = content.id
         AND body.variant = 'source'
        WHERE content.id::bigint = $1
        FOR UPDATE OF content
        ",
    )
    .bind(content_id)
    .fetch_optional(&mut **transaction)
    .await
}

fn source_is_current(content: &LockedContent, plan: &SummarizationFinalizationPlan) -> bool {
    if let Some(pointer) = plan.attempt.content.body_pointer() {
        return content.current_body_sha256.as_deref() == Some(pointer.sha256.as_str());
    }
    let payload =
        build_summarization_payload(&content.content_type, &content.content_metadata, None);
    input_fingerprint(&content.content_type, &payload) == plan.attempt.input_fingerprint
}

fn apply_completed_summary(
    content: &mut LockedContent,
    plan: &SummarizationFinalizationPlan,
    summary: &Value,
) {
    let mut metadata = metadata_map(&content.content_metadata);
    set_domain_field(&mut metadata, "summary", summary.clone());
    set_domain_field(
        &mut metadata,
        "summary_kind",
        Value::String("longform_artifact".to_owned()),
    );
    set_domain_field(&mut metadata, "summary_version", Value::from(1));
    if let Some(feed_preview) = summary.get("feed_preview") {
        set_domain_field(&mut metadata, "feed_preview", feed_preview.clone());
    }
    if let Some(selection_trace) = summary.get("selection_trace") {
        set_domain_field(&mut metadata, "selection_trace", selection_trace.clone());
    }
    set_domain_field(
        &mut metadata,
        "summarization_date",
        Value::String(plan.finalized_at.to_rfc3339()),
    );
    set_domain_field(
        &mut metadata,
        "summarization_input_fingerprint",
        Value::String(plan.attempt.input_fingerprint.clone()),
    );
    remove_processing_fields(
        &mut metadata,
        &["share_and_chat_user_ids", "share_and_chat_requests"],
    );
    if plan.attempt.content.body_pointer().is_some() {
        for field in RAW_BODY_FIELDS {
            remove_domain_field(&mut metadata, field);
        }
    }
    if content
        .title
        .as_deref()
        .is_none_or(|title| title.trim().is_empty())
        && let Some(title) = summary
            .get("title")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|title| !title.is_empty())
    {
        content.title = Some(title.chars().take(500).collect());
    }
    complete_readable_summary(content, metadata, plan.finalized_at.naive_utc());
}

fn apply_unchanged_summary(content: &mut LockedContent, plan: &SummarizationFinalizationPlan) {
    let mut metadata = metadata_map(&content.content_metadata);
    set_domain_field(
        &mut metadata,
        "summarization_input_fingerprint",
        Value::String(plan.attempt.input_fingerprint.clone()),
    );
    complete_readable_summary(content, metadata, plan.finalized_at.naive_utc());
}

fn complete_readable_summary(
    content: &mut LockedContent,
    mut metadata: Map<String, Value>,
    processed_at: NaiveDateTime,
) {
    "completed".clone_into(&mut content.status);
    if matches!(content.content_type.as_str(), "article" | "podcast")
        && runtime_metadata_view(&Value::Object(metadata.clone()))
            .get("image_generated_at")
            .is_none_or(Value::is_null)
    {
        set_domain_field(&mut metadata, "artwork_status", Value::from("pending"));
    }
    content.content_metadata = Value::Object(metadata);
    content.error_message = None;
    content.processed_at = Some(processed_at);
}

fn apply_failure(
    content: &mut LockedContent,
    plan: &SummarizationFinalizationPlan,
    reason: &str,
    retry_scheduled: bool,
    skipped: bool,
) {
    let mut metadata = metadata_map(&content.content_metadata);
    if !retry_scheduled {
        remove_domain_field(&mut metadata, "summary");
    }
    let mut errors = runtime_metadata_view(&Value::Object(metadata.clone()))
        .get("processing_errors")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    errors.push(json!({
        "stage": "summarization",
        "reason": reason,
        "retryable": retry_scheduled,
        "timestamp": plan.finalized_at.to_rfc3339(),
    }));
    set_processing_field(&mut metadata, "processing_errors", Value::Array(errors));
    let status = if retry_scheduled {
        "processing"
    } else if skipped {
        "skipped"
    } else {
        "failed"
    };
    status.clone_into(&mut content.status);
    content.error_message = Some(reason.chars().take(500).collect());
    content.processed_at = (!retry_scheduled).then_some(plan.finalized_at.naive_utc());
    content.content_metadata = Value::Object(metadata);
}

async fn persist_locked_content(
    transaction: &mut Transaction<'static, Postgres>,
    content: &LockedContent,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        r"
        UPDATE contents
        SET
            title = $2,
            status = $3,
            content_metadata = $4,
            error_message = $5,
            processed_at = $6,
            updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $1
        ",
    )
    .bind(content.id)
    .bind(&content.title)
    .bind(&content.status)
    .bind(&content.content_metadata)
    .bind(&content.error_message)
    .bind(content.processed_at)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

async fn persist_usage(
    transaction: &mut Transaction<'static, Postgres>,
    plan: &SummarizationFinalizationPlan,
    usage: &SummaryUsage,
) -> Result<(), sqlx::Error> {
    let submitted_by = runtime_metadata_view(&plan.attempt.content.content_metadata)
        .get("submitted_by_user_id")
        .and_then(positive_i64);
    let total_tokens = usage
        .usage
        .input_tokens
        .saturating_add(usage.usage.output_tokens);
    sqlx::query(
        r"
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
            input_tokens,
            cache_read_tokens,
            cache_write_tokens,
            output_tokens,
            total_tokens,
            request_count,
            currency,
            metadata,
            created_at
        )
        VALUES (
            $1, $2, 'summarization', 'summarization.llm_summarization', 'queue', $3,
            $4, $5,
            (SELECT id FROM users WHERE id::bigint = $6 AND is_active IS TRUE),
            $7, $8, $9, $10, $11, $12, 'USD', $13,
            timezone('UTC', clock_timestamp())
        )
        ",
    )
    .bind(&usage.provider)
    .bind(&usage.model)
    .bind(&usage.provider_response_id)
    .bind(plan.attempt.task_id)
    .bind(plan.attempt.content.id)
    .bind(submitted_by)
    .bind(saturating_i32(usage.usage.input_tokens))
    .bind(saturating_i32(usage.usage.cached_input_tokens))
    .bind(saturating_i32(usage.usage.cache_write_tokens))
    .bind(saturating_i32(usage.usage.output_tokens))
    .bind(saturating_i32(total_tokens))
    .bind(saturating_i32(usage.usage.request_count))
    .bind(json!({
        "content_type": plan.attempt.content.content_type,
        "summarization_type": "longform_artifact",
        "reasoning_tokens": usage.usage.reasoning_tokens,
    }))
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

fn applied_context(
    content: &LockedContent,
    pending_chat_requests: Vec<PendingChatRequest>,
) -> AppliedSummarization {
    AppliedSummarization {
        content_id: content.id,
        content_type: content.content_type.clone(),
        status: content.status.clone(),
        classification: content.classification.clone(),
        metadata: content.content_metadata.clone(),
        pending_chat_requests,
    }
}

fn extract_pending_chat_requests(metadata: &Value) -> Vec<PendingChatRequest> {
    let view = runtime_metadata_view(metadata);
    let mut requests = Vec::new();
    if let Some(values) = view
        .get("share_and_chat_requests")
        .and_then(Value::as_array)
    {
        for value in values {
            let Some(object) = value.as_object() else {
                continue;
            };
            let Some(user_id) = object.get("user_id").and_then(positive_i64) else {
                continue;
            };
            let initial_message = object
                .get("initial_message")
                .and_then(Value::as_str)
                .map(str::trim)
                .filter(|message| !message.is_empty())
                .map(str::to_owned);
            if !requests
                .iter()
                .any(|request: &PendingChatRequest| request.user_id == user_id)
            {
                requests.push(PendingChatRequest {
                    user_id,
                    initial_message,
                });
            }
        }
    }
    let legacy = view
        .get("share_and_chat_user_ids")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    for value in legacy {
        if let Some(user_id) = positive_i64(&value)
            && !requests.iter().any(|request| request.user_id == user_id)
        {
            requests.push(PendingChatRequest {
                user_id,
                initial_message: None,
            });
        }
    }
    requests
}

fn metadata_map(value: &Value) -> Map<String, Value> {
    value.as_object().cloned().unwrap_or_default()
}

fn set_domain_field(metadata: &mut Map<String, Value>, key: &str, value: Value) {
    metadata.insert(key.to_owned(), value.clone());
    let mut domain = metadata
        .get("domain")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    domain.insert(key.to_owned(), value);
    metadata.insert("domain".to_owned(), Value::Object(domain));
}

fn remove_domain_field(metadata: &mut Map<String, Value>, key: &str) {
    metadata.remove(key);
    if let Some(domain) = metadata.get_mut("domain").and_then(Value::as_object_mut) {
        domain.remove(key);
    }
}

fn set_processing_field(metadata: &mut Map<String, Value>, key: &str, value: Value) {
    metadata.insert(key.to_owned(), value.clone());
    let mut processing = metadata
        .get("processing")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    processing.insert(key.to_owned(), value);
    metadata.insert("processing".to_owned(), Value::Object(processing));
}

fn remove_processing_fields(metadata: &mut Map<String, Value>, keys: &[&str]) {
    for key in keys {
        metadata.remove(*key);
    }
    if let Some(processing) = metadata
        .get_mut("processing")
        .and_then(Value::as_object_mut)
    {
        for key in keys {
            processing.remove(*key);
        }
    }
}

fn positive_i64(value: &Value) -> Option<i64> {
    value
        .as_i64()
        .or_else(|| value.as_str()?.trim().parse::<i64>().ok())
        .filter(|value| *value > 0)
}

fn saturating_i32(value: u64) -> i32 {
    i32::try_from(value).unwrap_or(i32::MAX)
}

#[derive(Debug, Error)]
pub(super) enum SummarizationRepositoryError {
    #[error(transparent)]
    Sqlx(#[from] sqlx::Error),
}

#[cfg(test)]
mod readiness_tests {
    use super::*;
    use crate::summarization::model::PreparedSummarizationAttempt;
    use newsly_queue::QueueKernel;
    use sqlx::PgPool;

    #[sqlx::test]
    async fn reusable_summary_is_readable_and_enqueues_briefing_before_artwork(pool: PgPool) {
        newsly_db::run_migrations(&pool).await.unwrap();
        let user: i64 = sqlx::query_scalar("INSERT INTO users (apple_id, email, is_admin, is_active) VALUES ('summary-ready', 'summary@example.test', FALSE, TRUE) RETURNING id::bigint").fetch_one(&pool).await.unwrap();
        for kind in ["article", "podcast"] {
            let id: i64 = sqlx::query_scalar("INSERT INTO contents (content_type, url, is_aggregate, status, content_metadata) VALUES ($1, $2, FALSE, 'processing', $3) RETURNING id::bigint")
                .bind(kind).bind(format!("https://example.com/{kind}"))
                .bind(json!({"summary": {"title":"Useful summary"}, "content":"source text"})).fetch_one(&pool).await.unwrap();
            sqlx::query("INSERT INTO content_status (user_id, content_id, status, created_at, updated_at) VALUES ($1::bigint::integer, $2::bigint::integer, 'inbox', now(), now())").bind(user).bind(id).execute(&pool).await.unwrap();
            let mut tx = pool.begin().await.unwrap();
            let snapshot = load_summarization_snapshot(&mut tx, id)
                .await
                .unwrap()
                .unwrap();
            let payload = build_summarization_payload(kind, &snapshot.content_metadata, None);
            let plan = SummarizationFinalizationPlan {
                attempt: PreparedSummarizationAttempt {
                    task_id: 1,
                    content: snapshot,
                    input_fingerprint: input_fingerprint(kind, &payload),
                },
                mutation: SummarizationMutation::Unchanged,
                finalized_at: chrono::Utc::now(),
            };
            let SummarizationApplyOutcome::Applied(applied) =
                apply_summarization_state(&mut tx, &plan).await.unwrap()
            else {
                panic!("summary should apply")
            };
            assert_eq!(applied.status, "completed");
            crate::summarization::fanout::enqueue_summary_followups(
                &mut tx,
                &QueueKernel::new(pool.clone()),
                &applied,
                0,
                1,
            )
            .await
            .unwrap();
            tx.commit().await.unwrap();
        }
        let kinds: Vec<String> =
            sqlx::query_scalar("SELECT task_type FROM processing_tasks ORDER BY task_type")
                .fetch_all(&pool)
                .await
                .unwrap();
        assert_eq!(
            kinds,
            ["briefing_refresh", "generate_image", "generate_image"]
        );
        let pending: i64 = sqlx::query_scalar("SELECT count(*) FROM briefing_pending_sources")
            .fetch_one(&pool)
            .await
            .unwrap();
        assert_eq!(pending, 2);
    }
}
