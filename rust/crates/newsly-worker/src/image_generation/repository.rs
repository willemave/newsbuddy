use chrono::NaiveDateTime;
use newsly_providers::ImageGenerationUsage;
use serde_json::{Map, Value};
use sqlx::{FromRow, PgPool, Postgres, Transaction};
use thiserror::Error;

use super::model::{
    ImageContentSnapshot, ImageFinalizationPlan, ImageTargetOutcome, PreparedImageAttempt,
};
use super::prompt::{
    build_infographic_prompt, has_generated_image, image_input_fingerprint, runtime_metadata_view,
};

pub(super) async fn load_image_snapshot(
    transaction: &mut Transaction<'_, Postgres>,
    content_id: i64,
) -> Result<Option<ImageContentSnapshot>, ImageRepositoryError> {
    Ok(sqlx::query_as::<_, ImageContentSnapshot>(
        r"
        SELECT
            id::bigint AS id,
            content_type,
            title,
            status,
            COALESCE(content_metadata, '{}'::json) AS content_metadata
        FROM contents
        WHERE id::bigint = $1
        ",
    )
    .bind(content_id)
    .fetch_optional(&mut **transaction)
    .await?)
}

/// Applies image metadata inside the queue kernel's exact-lease transaction. The caller
/// publishes already-staged local files only when this returns `Ready`, before the transaction is
/// committed. No provider work or image transformation runs while `PostgreSQL` is held.
pub(super) async fn apply_generated_image(
    transaction: &mut Transaction<'static, Postgres>,
    plan: &ImageFinalizationPlan,
) -> Result<ImageTargetOutcome, ImageRepositoryError> {
    let Some(mut content) = load_locked_content(transaction, plan.attempt.content.id).await? else {
        return Ok(ImageTargetOutcome::ContentMissing);
    };
    if content.content_type == "news" {
        return Ok(ImageTargetOutcome::ContentBecameNews);
    }
    if !plan.attempt.force && has_generated_image(&content.content_metadata) {
        return Ok(ImageTargetOutcome::AlreadyGenerated);
    }
    let current_fingerprint = build_infographic_prompt(
        &content.content_type,
        content.title.as_deref(),
        &content.content_metadata,
    )
    .map(|prompt| image_input_fingerprint(&prompt));
    if current_fingerprint.as_deref() != Some(plan.attempt.input_fingerprint.as_str()) {
        return Ok(ImageTargetOutcome::InputChanged);
    }
    if matches!(content.content_type.as_str(), "article" | "podcast")
        && !matches!(content.status.as_str(), "awaiting_image" | "completed")
    {
        return Ok(ImageTargetOutcome::InvalidStatus);
    }

    let mut metadata = metadata_map(&content.content_metadata);
    set_domain_field(
        &mut metadata,
        "image_generated_at",
        Value::String(plan.generated_at.to_rfc3339()),
    );
    set_domain_field(
        &mut metadata,
        "image_url",
        Value::String(plan.staged.image_url()),
    );
    set_domain_field(
        &mut metadata,
        "thumbnail_url",
        Value::String(plan.staged.thumbnail_url()),
    );
    set_domain_field(&mut metadata, "artwork_status", Value::from("ready"));
    content.content_metadata = Value::Object(metadata);
    "completed".clone_into(&mut content.status);
    content.error_message = None;
    content.processed_at = Some(plan.generated_at.naive_utc());
    persist_locked_content(transaction, &content).await?;
    Ok(ImageTargetOutcome::Ready)
}

#[derive(Debug, FromRow)]
struct LockedImageContent {
    id: i64,
    content_type: String,
    title: Option<String>,
    status: String,
    content_metadata: Value,
    error_message: Option<String>,
    processed_at: Option<NaiveDateTime>,
}

async fn load_locked_content(
    transaction: &mut Transaction<'static, Postgres>,
    content_id: i64,
) -> Result<Option<LockedImageContent>, sqlx::Error> {
    sqlx::query_as::<_, LockedImageContent>(
        r"
        SELECT
            id::bigint AS id,
            content_type,
            title,
            status,
            COALESCE(content_metadata, '{}'::json) AS content_metadata,
            error_message,
            processed_at
        FROM contents
        WHERE id::bigint = $1
        FOR UPDATE
        ",
    )
    .bind(content_id)
    .fetch_optional(&mut **transaction)
    .await
}

async fn persist_locked_content(
    transaction: &mut Transaction<'static, Postgres>,
    content: &LockedImageContent,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        r"
        UPDATE contents
        SET
            status = $2,
            content_metadata = $3,
            error_message = $4,
            processed_at = $5,
            updated_at = timezone('UTC', clock_timestamp())
        WHERE id::bigint = $1
        ",
    )
    .bind(content.id)
    .bind(&content.status)
    .bind(&content.content_metadata)
    .bind(&content.error_message)
    .bind(content.processed_at)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

/// Records one parsed provider response independently of image download, transformation, lease,
/// and product publication. The provider request identity makes retries idempotent.
pub(super) async fn record_image_generation_usage(
    pool: &PgPool,
    attempt: &PreparedImageAttempt,
    usage: &ImageGenerationUsage,
) -> Result<bool, ImageRepositoryError> {
    let mut transaction = pool.begin().await?;
    let runtime = runtime_metadata_view(&attempt.content.content_metadata);
    let submitted_by = runtime.get("submitted_by_user_id").and_then(positive_i64);
    let total_tokens = usage.total_tokens.or_else(|| {
        usage
            .input_tokens
            .zip(usage.output_tokens)
            .map(|(input, output)| input.saturating_add(output))
    });
    let reported_cost = usage
        .response_cost_usd
        .filter(|cost| cost.is_finite() && *cost >= 0.0);
    let billable_image_count = usage
        .metadata
        .get("billable_image_count")
        .and_then(Value::as_u64)
        .and_then(|count| i32::try_from(count).ok());
    let estimate = if reported_cost.is_none()
        && usage.response_cost_usd.is_none()
        && usage.provider == "runware"
        && usage.request_count == 1
        && usage
            .metadata
            .get("standard_pricing_endpoint")
            .and_then(Value::as_bool)
            == Some(true)
        && usage
            .metadata
            .get("billable_image_count")
            .and_then(Value::as_u64)
            == Some(1)
    {
        newsly_db::calculate_vendor_resource_cost(
            &mut transaction,
            &usage.provider,
            &usage.model,
            &[newsly_db::VendorResourceMeter {
                unit: "image",
                quantity: 1.0,
            }],
            chrono::Utc::now(),
        )
        .await?
    } else {
        None
    };
    let cost_usd = reported_cost.or_else(|| estimate.as_ref().map(|cost| cost.cost_usd));
    let cost_basis = reported_cost
        .map(|_| "provider_reported")
        .or_else(|| estimate.as_ref().map(|_| "public_list_estimate"));
    let pricing_version = if reported_cost.is_some() {
        Some("provider-response")
    } else {
        estimate.as_ref().map(|cost| cost.pricing_version.as_str())
    };
    let mut metadata = usage.metadata.as_object().cloned().unwrap_or_default();
    if billable_image_count.is_some() {
        metadata.insert(
            "resource_count_unit".to_owned(),
            Value::String("image".to_owned()),
        );
    }
    metadata.insert(
        "pricing".to_owned(),
        estimate
            .as_ref()
            .map_or(Value::Null, |cost| cost.metadata.clone()),
    );
    if cost_usd.is_none() {
        metadata.insert(
            "cost_reason".to_owned(),
            Value::from("missing_provider_cost_or_applicable_image_rate"),
        );
    }
    metadata.insert(
        "content_type".to_owned(),
        Value::String(attempt.content.content_type.clone()),
    );
    metadata.insert(
        "input_fingerprint".to_owned(),
        Value::String(attempt.input_fingerprint.clone()),
    );
    let request_id = usage
        .request_id
        .as_deref()
        .filter(|request_id| request_id.len() <= 100);
    let response_identity = usage.request_id.as_deref().unwrap_or("missing-request-id");
    let idempotency_key = format!(
        "image:{}:{}:{}",
        attempt.task_id, usage.provider, response_identity
    );
    let inserted = sqlx::query_scalar::<_, i64>(
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
            output_tokens,
            total_tokens,
            request_count,
            resource_count,
            cost_usd,
            currency,
            pricing_version,
            cost_basis,
            metadata,
            idempotency_key,
            created_at
        )
        VALUES (
            $1, $2, 'image_generation', 'image_generation.infographic', 'queue', $3,
            $4, $5,
            (SELECT id FROM users WHERE id::bigint = $6 AND is_active IS TRUE),
            $7, $8, $9, $10, $11, $12, $13, 'USD', $14, $15, $16, $17,
            timezone('UTC', clock_timestamp())
        )
        ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING
        RETURNING id::bigint
        ",
    )
    .bind(&usage.provider)
    .bind(&usage.model)
    .bind(request_id)
    .bind(attempt.task_id)
    .bind(attempt.content.id)
    .bind(submitted_by)
    .bind(usage.input_tokens.map(saturating_i32))
    .bind(usage.cache_read_tokens.map(saturating_i32))
    .bind(usage.output_tokens.map(saturating_i32))
    .bind(total_tokens.map(saturating_i32))
    .bind(saturating_i32(usage.request_count))
    .bind(billable_image_count)
    .bind(cost_usd)
    .bind(pricing_version)
    .bind(cost_basis)
    .bind(Value::Object(metadata))
    .bind(idempotency_key)
    .fetch_optional(&mut *transaction)
    .await?;
    transaction.commit().await?;
    Ok(inserted.is_some())
}

fn metadata_map(value: &Value) -> Map<String, Value> {
    value.as_object().cloned().unwrap_or_default()
}

fn set_domain_field(metadata: &mut Map<String, Value>, key: &str, value: Value) {
    if let Some(domain) = metadata.get_mut("domain").and_then(Value::as_object_mut) {
        domain.insert(key.to_owned(), value.clone());
    }
    metadata.insert(key.to_owned(), value);
}

fn positive_i64(value: &Value) -> Option<i64> {
    value.as_i64().filter(|value| *value > 0)
}

fn saturating_i32(value: i64) -> i32 {
    i32::try_from(value).unwrap_or(if value.is_negative() {
        i32::MIN
    } else {
        i32::MAX
    })
}

#[derive(Debug, Error)]
pub(super) enum ImageRepositoryError {
    #[error("image-generation persistence failed")]
    Sqlx(#[from] sqlx::Error),
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn domain_metadata_receives_image_fields_without_overwriting_processing() {
        let mut metadata = json!({
            "domain": {"summary": {"title": "Title"}},
            "processing": {"share_and_chat_requests": [1]}
        })
        .as_object()
        .unwrap()
        .clone();
        set_domain_field(
            &mut metadata,
            "image_url",
            Value::String("/image".to_owned()),
        );
        assert_eq!(metadata["domain"]["image_url"], "/image");
        assert_eq!(metadata["image_url"], "/image");
        assert_eq!(
            metadata["processing"]["share_and_chat_requests"],
            json!([1])
        );
    }
}
