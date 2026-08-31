use chrono::NaiveDateTime;
use serde_json::{Map, Value};
use sqlx::{Acquire, FromRow, Postgres, Transaction};
use thiserror::Error;

use super::model::{ImageContentSnapshot, ImageFinalizationPlan, ImageTargetOutcome};
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

/// Applies image metadata and usage inside the queue kernel's exact-lease transaction. The caller
/// publishes already-staged local files only when this returns `Ready`, before the transaction is
/// committed. No provider work or image transformation runs while `PostgreSQL` is held.
pub(super) async fn apply_generated_image(
    transaction: &mut Transaction<'static, Postgres>,
    plan: &ImageFinalizationPlan,
) -> Result<ImageTargetOutcome, ImageRepositoryError> {
    let Some(mut content) = load_locked_content(transaction, plan.attempt.content.id).await? else {
        return Ok(ImageTargetOutcome::ContentMissing);
    };
    persist_usage_best_effort(transaction, plan, &content.content_metadata).await?;
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
        Value::String(format!(
            "/static/images/content/{}.png",
            plan.attempt.content.id
        )),
    );
    set_domain_field(
        &mut metadata,
        "thumbnail_url",
        Value::String(format!(
            "/static/images/thumbnails/{}.png",
            plan.attempt.content.id
        )),
    );
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

async fn persist_usage_best_effort(
    transaction: &mut Transaction<'static, Postgres>,
    plan: &ImageFinalizationPlan,
    latest_metadata: &Value,
) -> Result<(), sqlx::Error> {
    let mut savepoint = transaction.begin().await?;
    if let Err(error) = insert_usage(&mut savepoint, plan, latest_metadata).await {
        savepoint.rollback().await?;
        tracing::warn!(
            task_id = plan.attempt.task_id,
            content_id = plan.attempt.content.id,
            provider = %plan.usage.provider,
            model = %plan.usage.model,
            error = %error,
            "image generation usage persistence degraded without blocking publication"
        );
        return Ok(());
    }
    savepoint.commit().await
}

async fn insert_usage(
    transaction: &mut Transaction<'_, Postgres>,
    plan: &ImageFinalizationPlan,
    latest_metadata: &Value,
) -> Result<(), sqlx::Error> {
    let usage = &plan.usage;
    let runtime = runtime_metadata_view(latest_metadata);
    let submitted_by = runtime.get("submitted_by_user_id").and_then(positive_i64);
    let total_tokens = usage.total_tokens.or_else(|| {
        usage
            .input_tokens
            .zip(usage.output_tokens)
            .map(|(input, output)| input.saturating_add(output))
    });
    let cost_usd = usage.response_cost_usd.or_else(|| estimated_cost(usage));
    let mut metadata = usage.metadata.as_object().cloned().unwrap_or_default();
    metadata.insert(
        "content_type".to_owned(),
        Value::String(plan.attempt.content.content_type.clone()),
    );
    metadata.insert(
        "input_fingerprint".to_owned(),
        Value::String(plan.attempt.input_fingerprint.clone()),
    );
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
            output_tokens,
            total_tokens,
            request_count,
            cost_usd,
            currency,
            pricing_version,
            metadata,
            created_at
        )
        VALUES (
            $1, $2, 'image_generation', 'image_generation.infographic', 'queue', $3,
            $4, $5,
            (SELECT id FROM users WHERE id::bigint = $6 AND is_active IS TRUE),
            $7, $8, $9, $10, $11, $12, 'USD', '2026-08-02', $13,
            timezone('UTC', clock_timestamp())
        )
        ",
    )
    .bind(&usage.provider)
    .bind(&usage.model)
    .bind(&usage.request_id)
    .bind(plan.attempt.task_id)
    .bind(plan.attempt.content.id)
    .bind(submitted_by)
    .bind(usage.input_tokens.map(saturating_i32))
    .bind(usage.cache_read_tokens.map(saturating_i32))
    .bind(usage.output_tokens.map(saturating_i32))
    .bind(total_tokens.map(saturating_i32))
    .bind(saturating_i32(usage.request_count))
    .bind(cost_usd)
    .bind(Value::Object(metadata))
    .execute(&mut **transaction)
    .await?;
    Ok(())
}

fn estimated_cost(usage: &newsly_providers::ImageGenerationUsage) -> Option<f64> {
    if usage.provider == "runware" {
        return match usage.model.as_str() {
            "bytedance:seedream@5.0-lite" => Some(0.035),
            "runware:101@1" => Some(0.0038),
            _ => None,
        };
    }
    if usage.provider == "google"
        && usage.model == "gemini-3.1-flash-image-preview"
        && let (Some(input), Some(output)) = (usage.input_tokens, usage.output_tokens)
    {
        let input = f64::from(saturating_i32(input).max(0));
        let output = f64::from(saturating_i32(output).max(0));
        let cost = input / 1_000_000.0 * 0.50 + output / 1_000_000.0 * 60.00;
        return Some((cost * 100_000_000.0).round() / 100_000_000.0);
    }
    None
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
    use newsly_providers::ImageGenerationUsage;
    use serde_json::json;

    use super::*;

    #[test]
    fn estimates_seedream_and_google_image_costs() {
        let runware = ImageGenerationUsage {
            provider: "runware".to_owned(),
            model: "bytedance:seedream@5.0-lite".to_owned(),
            request_id: None,
            input_tokens: None,
            cache_read_tokens: None,
            output_tokens: None,
            total_tokens: None,
            request_count: 1,
            response_cost_usd: None,
            metadata: json!({}),
        };
        assert_eq!(estimated_cost(&runware), Some(0.035));

        let google = ImageGenerationUsage {
            provider: "google".to_owned(),
            model: "gemini-3.1-flash-image-preview".to_owned(),
            request_id: None,
            input_tokens: Some(1_000_000),
            cache_read_tokens: None,
            output_tokens: Some(1_000_000),
            total_tokens: Some(2_000_000),
            request_count: 1,
            response_cost_usd: None,
            metadata: json!({}),
        };
        assert_eq!(estimated_cost(&google), Some(60.5));
    }

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
