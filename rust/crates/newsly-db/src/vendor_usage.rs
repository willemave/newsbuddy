use serde_json::Value;
use sqlx::{Postgres, Transaction};
use thiserror::Error;

#[derive(Debug, Clone, PartialEq)]
pub struct NewTranscriptionUsage<'a> {
    pub request_id: &'a str,
    pub user_id: i64,
    pub model: &'a str,
    pub request_count: i32,
    pub audio_duration_ms: Option<u64>,
    pub audio_duration_estimate_ms: Option<u64>,
    pub duration_source: &'a str,
    pub standard_pricing: bool,
    pub metadata: Value,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NewNarrationTtsUsage<'a> {
    pub request_id: &'a str,
    pub user_id: i64,
    pub content_id: i64,
    pub model: &'a str,
    pub request_count: i32,
    pub text_chars: u64,
    pub standard_pricing: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NewXUserLookupUsage<'a> {
    pub request_id: &'a str,
    pub user_id: i64,
    pub provider_user_id: &'a str,
}

/// Records one completed backend-managed transcription request when the user remains active.
///
/// The caller deliberately invokes this in a fresh transaction after the external provider call.
/// Returning `false` means account deletion won the race and usage attribution was skipped.
///
/// # Errors
///
/// Returns [`VendorUsageRepositoryError::Sqlx`] when PostgreSQL rejects the insert.
pub async fn record_transcription_usage(
    transaction: &mut Transaction<'_, Postgres>,
    usage: &NewTranscriptionUsage<'_>,
) -> Result<bool, VendorUsageRepositoryError> {
    let priced = crate::openai_transcription_cost(
        transaction,
        usage.model,
        usage.audio_duration_ms,
        usage.duration_source == "ffprobe",
        usage.standard_pricing,
    )
    .await?;
    let mut metadata = usage.metadata.as_object().cloned().unwrap_or_default();
    metadata.insert(
        "audio_duration_ms".to_owned(),
        usage.audio_duration_ms.map_or(Value::Null, Value::from),
    );
    metadata.insert(
        "audio_duration_estimate_ms".to_owned(),
        usage
            .audio_duration_estimate_ms
            .map_or(Value::Null, Value::from),
    );
    metadata.insert(
        "duration_source".to_owned(),
        Value::from(usage.duration_source),
    );
    metadata.insert(
        "resource_count_unit".to_owned(),
        Value::from("audio_second"),
    );
    metadata.insert(
        "pricing".to_owned(),
        priced
            .as_ref()
            .map_or(Value::Null, |cost| cost.metadata.clone()),
    );
    if priced.is_none() {
        metadata.insert(
            "cost_reason".to_owned(),
            Value::from("missing_rate_or_unmeasured_audio_or_nonstandard_endpoint"),
        );
    }
    let inserted = sqlx::query_scalar::<_, i64>(
        r#"
        INSERT INTO vendor_usage_records (
            provider,
            model,
            feature,
            operation,
            source,
            request_id,
            user_id,
            request_count,
            resource_count,
            cost_usd,
            currency,
            pricing_version,
            cost_basis,
            metadata,
            created_at
        )
        SELECT
            'openai',
            $3,
            'transcription',
            'transcription.openai',
            'api',
            $1,
            users.id,
            $4,
            $5,
            $6,
            'USD',
            $7,
            $8,
            $9,
            timezone('UTC', clock_timestamp())
        FROM users
        WHERE users.id = $2
          AND users.is_active IS TRUE
        RETURNING id::bigint
        "#,
    )
    .bind(usage.request_id)
    .bind(usage.user_id)
    .bind(usage.model)
    .bind(usage.request_count)
    .bind(
        usage
            .audio_duration_ms
            .map(|value| i32::try_from(value.div_ceil(1_000)).unwrap_or(i32::MAX)),
    )
    .bind(priced.as_ref().map(|value| value.cost_usd))
    .bind(priced.as_ref().map(|value| value.pricing_version.as_str()))
    .bind(priced.as_ref().map(|_| "public_list_estimate"))
    .bind(Value::Object(metadata))
    .fetch_optional(&mut **transaction)
    .await?;
    Ok(inserted.is_some())
}

/// Records one successful synchronous ElevenLabs content narration request.
pub async fn record_narration_tts_usage(
    transaction: &mut Transaction<'_, Postgres>,
    usage: &NewNarrationTtsUsage<'_>,
) -> Result<bool, VendorUsageRepositoryError> {
    let priced = crate::elevenlabs_tts_cost(
        transaction,
        usage.model,
        usage.text_chars,
        usage.standard_pricing,
    )
    .await?;
    let metadata = serde_json::json!({
        "text_chars": usage.text_chars,
        "resource_count_unit": "character",
        "pricing": priced.as_ref().map(|cost| &cost.metadata),
        "cost_reason": priced.is_none().then_some("missing_rate_or_nonstandard_endpoint"),
    });
    let inserted = sqlx::query_scalar::<_, i64>(
        r#"
        INSERT INTO vendor_usage_records (
            provider, model, feature, operation, source, request_id, content_id, user_id,
            request_count, resource_count, cost_usd, currency, pricing_version, cost_basis,
            metadata, created_at
        )
        SELECT
            'elevenlabs', $4, 'content_narration', 'narration.synthesize_content_mp3',
            'api', $1, $3::bigint::integer, users.id, $5, $6, $7, 'USD', $8, $9, $10,
            timezone('UTC', clock_timestamp())
        FROM users
        WHERE users.id = $2
          AND users.is_active IS TRUE
        RETURNING id::bigint
        "#,
    )
    .bind(usage.request_id)
    .bind(usage.user_id)
    .bind(usage.content_id)
    .bind(usage.model)
    .bind(usage.request_count)
    .bind(i32::try_from(usage.text_chars).unwrap_or(i32::MAX))
    .bind(priced.as_ref().map(|value| value.cost_usd))
    .bind(priced.as_ref().map(|value| value.pricing_version.as_str()))
    .bind(priced.as_ref().map(|_| "public_list_estimate"))
    .bind(metadata)
    .fetch_optional(&mut **transaction)
    .await?;
    Ok(inserted.is_some())
}

/// Records the X `/users/me` lookup in its own short transaction.
pub async fn record_x_user_lookup_usage(
    transaction: &mut Transaction<'_, Postgres>,
    usage: &NewXUserLookupUsage<'_>,
) -> Result<bool, VendorUsageRepositoryError> {
    let metadata = serde_json::json!({
        "resource_ids": [usage.provider_user_id],
        "resource_count_unit": "resource",
    });
    let inserted = sqlx::query_scalar::<_, i64>(
        r#"
        INSERT INTO vendor_usage_records (
            provider,
            model,
            feature,
            operation,
            source,
            request_id,
            user_id,
            request_count,
            resource_count,
            currency,
            pricing_version,
            metadata,
            created_at
        )
        SELECT
            'x',
            'users.read',
            'x_oauth',
            'x_oauth.get_authenticated_user',
            'api',
            $1,
            users.id,
            1,
            1,
            'USD',
            '2026-08-02',
            $3,
            timezone('UTC', clock_timestamp())
        FROM users
        WHERE users.id = $2
          AND users.is_active IS TRUE
        RETURNING id::bigint
        "#,
    )
    .bind(usage.request_id)
    .bind(usage.user_id)
    .bind(metadata)
    .fetch_optional(&mut **transaction)
    .await?;
    Ok(inserted.is_some())
}

#[derive(Debug, Error)]
pub enum VendorUsageRepositoryError {
    #[error("PostgreSQL vendor usage insert failed")]
    Sqlx(#[from] sqlx::Error),
}
