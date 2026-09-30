use newsly_agent_runtime::AgentModelUsageObservation;
use serde_json::{Map, Value};
use sqlx::{Postgres, Transaction};
use thiserror::Error;

#[derive(Debug, Clone)]
pub struct NewAgentModelUsage<'a> {
    pub observation: &'a AgentModelUsageObservation,
    pub operation: &'a str,
    pub source: &'a str,
    pub task_id: Option<i64>,
    pub content_id: Option<i64>,
    pub session_id: Option<i64>,
    pub message_id: Option<i64>,
    pub user_id: Option<i64>,
}

/// Persists one completed provider response independently of product publication.
///
/// The provider response id is the preferred identity. Providers that omit it fall back to the
/// worker-owned agent run id plus model-request sequence. The database's partial unique index
/// makes replaying a drained observation harmless.
pub async fn record_agent_model_usage(
    transaction: &mut Transaction<'_, Postgres>,
    usage: &NewAgentModelUsage<'_>,
) -> Result<bool, AgentUsageRepositoryError> {
    let observation = usage.observation;
    let response_identity = observation
        .response_id
        .as_deref()
        .or(observation.provider_request_id.as_deref());
    let task_scope = usage
        .task_id
        .map_or_else(|| "none".to_owned(), |id| id.to_string());
    let idempotency_key = response_identity.map_or_else(
        || {
            format!(
                "agent:{}:{}:{}:{}",
                task_scope, observation.run_id, observation.sequence, observation.provider
            )
        },
        |identity| format!("agent:{}:{}:{}", task_scope, observation.provider, identity),
    );
    let request_id = response_identity.filter(|identity| identity.len() <= 100);
    let mut metadata = Map::from_iter([
        (
            "agent_run_id".to_owned(),
            Value::from(observation.run_id.to_string()),
        ),
        (
            "model_request_sequence".to_owned(),
            Value::from(observation.sequence),
        ),
        (
            "endpoint".to_owned(),
            Value::from(observation.endpoint.clone()),
        ),
        (
            "feature_route".to_owned(),
            Value::from(observation.feature.clone()),
        ),
        (
            "provider_response_id".to_owned(),
            observation
                .response_id
                .clone()
                .map_or(Value::Null, Value::from),
        ),
        (
            "provider_request_id".to_owned(),
            observation
                .provider_request_id
                .clone()
                .map_or(Value::Null, Value::from),
        ),
        (
            "reasoning_tokens".to_owned(),
            Value::from(observation.usage.reasoning_tokens),
        ),
        (
            "service_tier".to_owned(),
            Value::from(observation.processing_tier.as_deref().unwrap_or("unknown")),
        ),
        (
            "usage_is_observed".to_owned(),
            Value::from(observation.usage_is_observed),
        ),
    ]);
    if let Some(tier) = observation.requested_service_tier.as_ref() {
        metadata.insert(
            "requested_service_tier".to_owned(),
            Value::from(tier.clone()),
        );
    }
    let total_tokens = observation
        .usage
        .input_tokens
        .saturating_add(observation.usage.output_tokens);
    let inserted = sqlx::query_scalar::<_, i64>(
        r#"
        INSERT INTO vendor_usage_records (
            provider, model, feature, operation, source, request_id, idempotency_key,
            task_id, content_id, session_id, message_id, user_id,
            input_tokens, output_tokens, total_tokens, cache_read_tokens, cache_write_tokens,
            request_count, cost_usd, currency, pricing_version, cost_basis, metadata, created_at
        )
        SELECT
            $1, $2, $3, $4, $5, $6, $7,
            $8::bigint::integer, $9::bigint::integer, $10::bigint::integer,
            $11::bigint::integer, $18::bigint::integer,
            $12::bigint::integer, $13::bigint::integer, $14::bigint::integer,
            $15::bigint::integer, $16::bigint::integer, 1, $19, 'USD',
            CASE WHEN $19::double precision IS NULL THEN NULL ELSE 'provider-response' END,
            CASE WHEN $19::double precision IS NULL THEN NULL ELSE 'provider_reported' END,
            $17,
            timezone('UTC', clock_timestamp())
        WHERE $18::bigint IS NULL OR EXISTS (
            SELECT 1 FROM users AS account
            WHERE account.id::bigint = $18 AND account.is_active IS TRUE
        )
        ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING
        RETURNING id::bigint
        "#,
    )
    .bind(&observation.provider)
    .bind(&observation.model)
    .bind(&observation.feature)
    .bind(usage.operation)
    .bind(usage.source)
    .bind(request_id)
    .bind(idempotency_key)
    .bind(usage.task_id)
    .bind(usage.content_id)
    .bind(usage.session_id)
    .bind(usage.message_id)
    .bind(i64_bound(observation.usage.input_tokens))
    .bind(i64_bound(observation.usage.output_tokens))
    .bind(i64_bound(total_tokens))
    .bind(i64_bound(observation.usage.cached_input_tokens))
    .bind(i64_bound(observation.usage.cache_write_tokens))
    .bind(Value::Object(metadata))
    .bind(usage.user_id)
    .bind(observation.provider_reported_cost_usd)
    .fetch_optional(&mut **transaction)
    .await?;
    Ok(inserted.is_some())
}

fn i64_bound(value: u64) -> i64 {
    i64::try_from(value).unwrap_or(i64::MAX)
}

#[derive(Debug, Error)]
pub enum AgentUsageRepositoryError {
    #[error("PostgreSQL agent usage insert failed")]
    Sqlx(#[from] sqlx::Error),
}

#[cfg(test)]
#[path = "agent_usage/tests.rs"]
mod tests;
