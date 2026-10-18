//! Durable provider-attempt accounting, independent of product finalization.

use serde_json::{Value, json};
use sqlx::PgPool;
use uuid::Uuid;

use super::model::FirecrawlUsage;

#[derive(Debug, Clone, Copy)]
pub(crate) struct FirecrawlAttribution {
    pub task: i64,
    pub content: Option<i64>,
    pub news_item: Option<i64>,
    pub user: Option<i64>,
}

#[derive(Debug, Clone)]
pub(crate) struct FirecrawlRecorder {
    pool: PgPool,
    attribution: FirecrawlAttribution,
    request_id: String,
    fallback_reason: String,
}

impl FirecrawlRecorder {
    pub(crate) fn new(
        pool: PgPool,
        attribution: FirecrawlAttribution,
        request_id: String,
        fallback_reason: String,
    ) -> Self {
        Self {
            pool,
            attribution,
            request_id,
            fallback_reason,
        }
    }

    /// Commit the attempt before dispatch. A dropped future leaves an explicitly unpriced row.
    /// This method releases its database connection before returning to the provider adapter.
    pub(crate) async fn begin(
        &self,
        url: &str,
        configured_credit_cost_usd: f64,
    ) -> Result<FirecrawlRecordedAttempt, sqlx::Error> {
        let idempotency_key = format!("firecrawl:{}", Uuid::new_v4());
        let metadata = json!({
            "url": url,
            "status_code": null,
            "unit": "credit",
            "quantity": null,
            "credits_used": null,
            "configured_credit_cost_usd": configured_credit_cost_usd,
            "num_pages": null,
            "total_pages": null,
            "provider_scrape_id": null,
            "credit_usage_basis": "unknown",
            "cost_basis": "unknown",
            "cost_estimate_type": "marginal_topup_estimate",
            "phase": "dispatched",
            "fallback_reason": self.fallback_reason,
            "news_item_id": self.attribution.news_item,
            "error_code": null,
        });
        sqlx::query(
            r"INSERT INTO vendor_usage_records (
                provider, model, feature, operation, source, request_id,
                task_id, content_id, user_id, request_count, resource_count,
                cost_usd, currency, pricing_version, cost_basis, metadata,
                idempotency_key, created_at
            ) VALUES (
                'firecrawl', 'scrape-v2', 'html_extraction', 'firecrawl_scrape',
                'queue', $1, $2::bigint::integer, $3::bigint::integer,
                $4::bigint::integer, 1, NULL, NULL, 'USD', NULL, NULL, $5,
                $6, timezone('UTC', clock_timestamp())
            )",
        )
        .bind(&self.request_id)
        .bind(self.attribution.task)
        .bind(self.attribution.content)
        .bind(self.attribution.user)
        .bind(metadata)
        .bind(&idempotency_key)
        .execute(&self.pool)
        .await?;
        Ok(FirecrawlRecordedAttempt {
            pool: self.pool.clone(),
            idempotency_key,
            attribution: self.attribution,
            fallback_reason: self.fallback_reason.clone(),
            configured_credit_cost_usd,
        })
    }
}

#[derive(Debug)]
pub(crate) struct FirecrawlRecordedAttempt {
    pool: PgPool,
    idempotency_key: String,
    attribution: FirecrawlAttribution,
    fallback_reason: String,
    configured_credit_cost_usd: f64,
}

impl FirecrawlRecordedAttempt {
    /// Observe billing before content decoding, URL validation, or fenced publication.
    /// The first complete observation is authoritative; duplicate observations are harmless.
    pub(crate) async fn observe(&self, usage: &FirecrawlUsage) -> Result<(), sqlx::Error> {
        let cost_usd = usage.credits_used.and_then(|credits| {
            if credits == 0 {
                return Some(0.0);
            }
            let cost = credits as f64 * self.configured_credit_cost_usd;
            (self.configured_credit_cost_usd.is_finite()
                && self.configured_credit_cost_usd > 0.0
                && cost.is_finite())
            .then_some(cost)
        });
        let mut metadata = usage.metadata(self.attribution.news_item);
        metadata["configured_credit_cost_usd"] = json!(self.configured_credit_cost_usd);
        metadata["cost_basis"] = json!(if cost_usd.is_some() {
            "configured_rate"
        } else {
            "unknown"
        });
        metadata["cost_estimate_type"] = json!("marginal_topup_estimate");
        metadata["phase"] = json!("completed");
        metadata["fallback_reason"] = json!(self.fallback_reason);
        metadata["error_code"] = Value::Null;
        sqlx::query(
            r"UPDATE vendor_usage_records SET
                resource_count = $2, cost_usd = $3,
                cost_basis = $4, pricing_version = $5,
                metadata = (metadata::jsonb || $6::jsonb)::json
              WHERE idempotency_key = $1
                AND metadata::jsonb ->> 'phase' IS DISTINCT FROM 'completed'",
        )
        .bind(&self.idempotency_key)
        .bind(usage.resource_count())
        .bind(cost_usd)
        .bind(cost_usd.map(|_| "configured_rate"))
        .bind(cost_usd.map(|_| "configured-marginal-per-credit-v3"))
        .bind(metadata)
        .execute(&self.pool)
        .await?;
        Ok(())
    }

    /// Preserve an explicitly unknown outcome for timeout, malformed JSON, or a bounded read
    /// failure. A late failure cannot downgrade an already-completed provider observation.
    pub(crate) async fn unknown_completion(
        &self,
        status: Option<u16>,
        error_code: &str,
    ) -> Result<(), sqlx::Error> {
        let metadata = json!({
            "phase": "completed_unknown",
            "status_code": status,
            "error_code": error_code,
        });
        sqlx::query(
            r"UPDATE vendor_usage_records
              SET metadata = (metadata::jsonb || $2::jsonb)::json
              WHERE idempotency_key = $1
                AND metadata::jsonb ->> 'phase' IS DISTINCT FROM 'completed'",
        )
        .bind(&self.idempotency_key)
        .bind(metadata)
        .execute(&self.pool)
        .await?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    type BillingRow = (Option<i32>, Option<f64>, Option<String>, Value);

    fn recorder(pool: &PgPool) -> FirecrawlRecorder {
        FirecrawlRecorder::new(
            pool.clone(),
            FirecrawlAttribution {
                task: 17,
                content: Some(23),
                news_item: Some(31),
                user: None,
            },
            "reused-task-request".to_owned(),
            "local_extraction_failed".to_owned(),
        )
    }

    fn usage(credits_used: Option<u64>) -> FirecrawlUsage {
        FirecrawlUsage {
            request_id: "reused-task-request".to_owned(),
            url: "https://example.com/book.pdf".to_owned(),
            status_code: 200,
            credit_cost_usd: 0.004,
            credits_used,
            num_pages: Some(296),
            total_pages: Some(296),
            scrape_id: Some("provider-scrape-id".to_owned()),
            cost_usd: credits_used.map(|credits| credits as f64 * 0.004),
        }
    }

    #[sqlx::test(migrations = false)]
    async fn canceled_attempt_remains_dispatched_and_unpriced(pool: PgPool) {
        newsly_db::run_migrations(&pool).await.unwrap();
        let attempt = recorder(&pool)
            .begin("https://example.com/book.pdf", 0.004)
            .await
            .unwrap();
        drop(attempt);
        let (requests, credits, cost, metadata): (i32, Option<i32>, Option<f64>, Value) =
            sqlx::query_as("SELECT request_count, resource_count, cost_usd, metadata FROM vendor_usage_records WHERE provider='firecrawl'")
                .fetch_one(&pool).await.unwrap();
        assert_eq!(requests, 1);
        assert_eq!(credits, None);
        assert_eq!(cost, None);
        assert_eq!(metadata["phase"], "dispatched");
        assert_eq!(metadata["credit_usage_basis"], "unknown");
        assert_eq!(metadata["fallback_reason"], "local_extraction_failed");
    }

    #[sqlx::test(migrations = false)]
    async fn complete_response_survives_validation_and_repeated_observation(pool: PgPool) {
        newsly_db::run_migrations(&pool).await.unwrap();
        let attempt = recorder(&pool)
            .begin("https://example.com/book.pdf", 0.004)
            .await
            .unwrap();
        attempt.observe(&usage(Some(296))).await.unwrap();
        attempt.observe(&usage(Some(296))).await.unwrap();
        attempt
            .unknown_completion(None, "lease_lost")
            .await
            .unwrap();
        let (count, requests, credits, cost): (i64, i64, i64, f64) =
            sqlx::query_as("SELECT count(*)::bigint, sum(request_count)::bigint, sum(resource_count)::bigint, sum(cost_usd)::float8 FROM vendor_usage_records WHERE provider='firecrawl'")
                .fetch_one(&pool).await.unwrap();
        assert_eq!((count, requests, credits), (1, 1, 296));
        assert!((cost - 1.184).abs() < 1e-12);
        let (basis, metadata): (String, Value) = sqlx::query_as(
            "SELECT cost_basis, metadata FROM vendor_usage_records WHERE provider='firecrawl'",
        )
        .fetch_one(&pool)
        .await
        .unwrap();
        assert_eq!(basis, "configured_rate");
        assert_eq!(metadata["phase"], "completed");
        assert_eq!(metadata["credit_usage_basis"], "provider_reported");
        assert_eq!(metadata["cost_estimate_type"], "marginal_topup_estimate");
        assert_eq!(metadata["quantity"], 296);
        assert_eq!(metadata["total_pages"], 296);
        assert_eq!(metadata["news_item_id"], 31);
        assert!(metadata["error_code"].is_null());
    }

    #[sqlx::test(migrations = false)]
    async fn retries_with_one_request_id_record_distinct_invocations(pool: PgPool) {
        newsly_db::run_migrations(&pool).await.unwrap();
        let recorder = recorder(&pool);
        for _ in 0..2 {
            let attempt = recorder.begin("https://example.com", 0.004).await.unwrap();
            attempt.observe(&usage(Some(1))).await.unwrap();
        }
        let counts: (i64, i64) = sqlx::query_as("SELECT count(*)::bigint, count(DISTINCT idempotency_key)::bigint FROM vendor_usage_records WHERE request_id='reused-task-request'")
            .fetch_one(&pool).await.unwrap();
        assert_eq!(counts, (2, 2));
    }

    #[sqlx::test(migrations = false)]
    async fn unknown_completion_and_missing_response_usage_stay_null(pool: PgPool) {
        newsly_db::run_migrations(&pool).await.unwrap();
        let recorder = recorder(&pool);
        let timeout = recorder.begin("https://example.com", 0.004).await.unwrap();
        timeout
            .unknown_completion(None, "transport_timeout")
            .await
            .unwrap();
        let malformed = recorder.begin("https://example.com", 0.004).await.unwrap();
        malformed
            .unknown_completion(Some(200), "invalid_json")
            .await
            .unwrap();
        let missing = recorder.begin("https://example.com", 0.004).await.unwrap();
        missing.observe(&usage(None)).await.unwrap();
        let rows: Vec<BillingRow> = sqlx::query_as(
            "SELECT resource_count, cost_usd, cost_basis, metadata FROM vendor_usage_records WHERE provider='firecrawl' ORDER BY id",
        ).fetch_all(&pool).await.unwrap();
        assert_eq!(rows.len(), 3);
        for (credits, cost, basis, metadata) in &rows {
            assert_eq!((*credits, *cost, basis.as_deref()), (None, None, None));
            assert_eq!(metadata["credit_usage_basis"], "unknown");
            assert!(metadata["quantity"].is_null());
        }
        assert!(rows[0].3["status_code"].is_null());
        assert_eq!(rows[1].3["status_code"], 200);
        assert_eq!(rows[1].3["error_code"], "invalid_json");
        assert_eq!(rows[2].3["phase"], "completed");
    }

    #[sqlx::test(migrations = false)]
    async fn unconfigured_price_is_unknown_but_reported_zero_is_known(pool: PgPool) {
        newsly_db::run_migrations(&pool).await.unwrap();
        let recorder = recorder(&pool);
        for credits in [Some(296), Some(0)] {
            let attempt = recorder.begin("https://example.com", 0.0).await.unwrap();
            attempt.observe(&usage(credits)).await.unwrap();
        }
        let rows: Vec<(Option<i32>, Option<f64>)> = sqlx::query_as(
            "SELECT resource_count, cost_usd FROM vendor_usage_records WHERE provider='firecrawl' ORDER BY id",
        ).fetch_all(&pool).await.unwrap();
        assert_eq!(rows, vec![(Some(296), None), (Some(0), Some(0.0))]);
    }
}
