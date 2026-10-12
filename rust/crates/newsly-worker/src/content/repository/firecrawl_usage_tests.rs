use super::*;
use crate::content::FirecrawlUsage;

#[sqlx::test(migrations = false)]
async fn firecrawl_content_persistence_uses_observed_credits_on_failed_content_validation(
    pool: PgPool,
) {
    newsly_db::run_migrations(&pool).await.unwrap();
    let plan = ContentFinalizationPlan {
        task_id: 1,
        content_id: 1,
        mutation: ContentMutation::ExtractionFailure {
            stage: "process",
            reason: "empty provider markdown".to_owned(),
            code: "firecrawl".to_owned(),
            terminal: true,
            scrub_instruction: false,
        },
        usage: vec![UsageWrite::Firecrawl(FirecrawlUsage {
            request_id: "billed-rejected-content".to_owned(),
            url: "https://example.com/book.pdf".to_owned(),
            status_code: 200,
            credit_cost_usd: 0.0032,
            credits_used: Some(296),
            num_pages: Some(296),
            total_pages: Some(296),
            scrape_id: Some("provider-id".to_owned()),
            cost_usd: Some(0.9472),
        })],
    };
    let mut transaction = pool.begin().await.unwrap();
    persist_usage(&mut transaction, &plan, None, None)
        .await
        .unwrap();
    transaction.commit().await.unwrap();
    let (requests, credits, cost, basis, metadata): (i32, i32, f64, String, Value) = sqlx::query_as(
        "SELECT request_count, resource_count, cost_usd::float8, cost_basis, metadata FROM vendor_usage_records WHERE request_id = 'billed-rejected-content'",
    ).fetch_one(&pool).await.unwrap();
    assert_eq!(requests, 1);
    assert_eq!(credits, 296);
    assert!((cost - 0.9472).abs() < 1e-12);
    assert_eq!(basis, "configured_rate");
    assert_eq!(metadata["quantity"], 296);
    assert_eq!(metadata["unit"], "credit");
    assert_eq!(metadata["total_pages"], 296);
}
