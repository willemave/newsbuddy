use newsly_providers::OnboardingExaUsage;
use serde_json::json;

use crate::AppState;

pub(super) async fn record_onboarding_exa_usage(
    state: &AppState,
    user_id: i64,
    request_id: &str,
    operation: &str,
    usage: OnboardingExaUsage,
) {
    if usage.request_count == 0 {
        return;
    }
    let metadata = json!({
        "result_count": usage.result_count,
        "summary_count": usage.summary_count,
        "text_count": usage.text_count,
        "search_type": "auto",
        "contents_text_requested": true,
        "contents_summary_requested": false,
        "livecrawl": false,
        "cost_status": "unpriced",
        "cost_reason": "Exa response does not expose all billable search and contents units or the account pricing plan",
        "cost_source_url": "https://exa.ai/pricing"
    });
    let result = sqlx::query(
        r"
        INSERT INTO vendor_usage_records (
            provider, model, feature, operation, source, request_id, user_id,
            request_count, resource_count, cost_usd, currency, pricing_version,
            cost_basis, metadata, idempotency_key, created_at
        ) VALUES (
            'exa', 'search', 'onboarding', $1, 'api', $2, $3::bigint::integer,
            $4, $5, NULL, 'USD', NULL,
            'unpriced_incomplete_provider_units', $6,
            concat('exa:', $1::text, ':', $2::text), timezone('UTC', clock_timestamp())
        )
        ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING
        ",
    )
    .bind(operation)
    .bind(request_id)
    .bind(user_id)
    .bind(i32::try_from(usage.request_count).unwrap_or(i32::MAX))
    .bind(i32::try_from(usage.result_count).unwrap_or(i32::MAX))
    .bind(metadata)
    .execute(state.database.pool())
    .await;
    if let Err(error) = result {
        tracing::error!(error = %error, operation, request_id, "failed to record onboarding Exa usage");
    }
}
