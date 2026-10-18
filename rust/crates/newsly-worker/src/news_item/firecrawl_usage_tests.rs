use super::*;
use crate::content::FirecrawlUsage;
use sqlx::PgPool;

#[derive(sqlx::FromRow)]
struct BillingRow {
    request_id: String,
    request_count: i32,
    resource_count: Option<i32>,
    cost_usd: Option<f64>,
    cost_basis: Option<String>,
    pricing_version: Option<String>,
    metadata: Value,
}

#[sqlx::test(migrations = false)]
async fn firecrawl_news_persistence_distinguishes_requests_credits_and_unknown_usage(pool: PgPool) {
    newsly_db::run_migrations(&pool).await.unwrap();
    let mut transaction = pool.begin().await.unwrap();
    for (request, credits, cost) in [
        ("known", Some(296), Some(0.9472)),
        ("unknown", None, None),
        ("cached", Some(0), Some(0.0)),
    ] {
        let write = UsageWrite::Firecrawl(FirecrawlUsage {
            request_id: request.to_owned(),
            url: "https://example.com/book.pdf".to_owned(),
            status_code: 200,
            credit_cost_usd: 0.0032,
            credits_used: credits,
            num_pages: Some(296),
            total_pages: Some(296),
            scrape_id: Some("provider-id".to_owned()),
            cost_usd: cost,
        });
        persist_extraction_usage(&mut transaction, 1, None, 42, &[write])
            .await
            .unwrap();
    }
    transaction.commit().await.unwrap();
    let rows: Vec<BillingRow> = sqlx::query_as(
        "SELECT request_id, request_count, resource_count, cost_usd::float8, cost_basis, pricing_version, metadata FROM vendor_usage_records WHERE provider = 'firecrawl' ORDER BY request_id",
    ).fetch_all(&pool).await.unwrap();
    assert_eq!(rows.len(), 3);
    for row in rows {
        let BillingRow {
            request_id: request,
            request_count: calls,
            resource_count: credits,
            cost_usd: cost,
            cost_basis: basis,
            pricing_version: version,
            metadata,
        } = row;
        assert_eq!(calls, 1);
        assert_eq!(metadata["unit"], "credit");
        assert_eq!(metadata["num_pages"], 296);
        assert_eq!(metadata["news_item_id"], 42);
        if request == "unknown" {
            assert_eq!(credits, None);
            assert_eq!(cost, None);
            assert_eq!(basis, None);
            assert_eq!(version, None);
            assert!(metadata["quantity"].is_null());
        } else {
            assert_eq!(credits, Some(if request == "cached" { 0 } else { 296 }));
            assert_eq!(cost, Some(if request == "cached" { 0.0 } else { 0.9472 }));
            assert_eq!(basis.as_deref(), Some("configured_rate"));
            assert_eq!(
                version.as_deref(),
                Some("configured-marginal-per-credit-v3")
            );
        }
    }
}
