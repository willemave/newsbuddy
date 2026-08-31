use serde_json::Value;
use sqlx::{Postgres, Transaction};
use thiserror::Error;

#[derive(Debug, Clone, PartialEq)]
pub struct NewTranscriptionUsage<'a> {
    pub request_id: &'a str,
    pub user_id: i64,
    pub model: &'a str,
    pub metadata: Value,
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
            currency,
            pricing_version,
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
            1,
            'USD',
            '2026-08-02',
            $4,
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
    .bind(&usage.metadata)
    .fetch_optional(&mut **transaction)
    .await?;
    Ok(inserted.is_some())
}

/// Records the X `/users/me` lookup in its own short transaction.
pub async fn record_x_user_lookup_usage(
    transaction: &mut Transaction<'_, Postgres>,
    usage: &NewXUserLookupUsage<'_>,
) -> Result<bool, VendorUsageRepositoryError> {
    let metadata = serde_json::json!({"resource_ids": [usage.provider_user_id]});
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
