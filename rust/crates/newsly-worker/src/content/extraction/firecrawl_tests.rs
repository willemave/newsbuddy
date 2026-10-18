use super::*;
use newsly_extraction::{DocumentExtractorConfig, ExtractionFallbackRequired};
use secrecy::SecretString;
use serde_json::{Value, json};
use std::time::Duration;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;
use url::Url;

async fn fixture_runtime(
    payload: Value,
) -> (ContentExtractionRuntime, tokio::task::JoinHandle<()>) {
    runtime_raw(200, payload.to_string()).await
}

async fn runtime_raw(
    status: u16,
    body: String,
) -> (ContentExtractionRuntime, tokio::task::JoinHandle<()>) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = Url::parse(&format!("http://{}/", listener.local_addr().unwrap())).unwrap();
    let server = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.unwrap();
        let mut buffer = [0u8; 4096];
        let read = stream.read(&mut buffer).await.unwrap();
        assert!(read > 0);
        let response = format!(
            "HTTP/1.1 {status} Fixture\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
            body.len()
        );
        stream.write_all(response.as_bytes()).await.unwrap();
    });
    let extractor = DocumentExtractorClient::new(
        DocumentExtractorConfig::new(endpoint.clone(), SecretString::from("fixture")).unwrap(),
    )
    .unwrap();
    let firecrawl = FirecrawlClient::new(
        endpoint,
        Some(SecretString::from("fixture")),
        Duration::from_secs(5),
        0.0032,
    )
    .unwrap();
    (
        ContentExtractionRuntime::fixture(extractor, firecrawl),
        server,
    )
}

#[tokio::test]
async fn firecrawl_billing_survives_empty_body_and_unsafe_final_url() {
    for data in [
        json!({"metadata":{"creditsUsed":296,"numPages":296}}),
        json!({"markdown":"Body", "metadata":{"creditsUsed":296,"sourceURL":"http://127.0.0.1/private"}}),
    ] {
        let (runtime, server) = fixture_runtime(json!({"success":true,"data":data})).await;
        let fallback = ExtractionFallbackRequired {
            schema_version: 1,
            request_id: "fallback-fixture".to_owned(),
            fallback: newsly_extraction::FallbackKind::Firecrawl,
            url: PublicUrl::parse("https://8.8.8.8/book.pdf").unwrap(),
            reason: "PDF requires OCR".to_owned(),
            retryable: false,
            usage_events: Vec::new(),
            timings: Vec::new(),
        };
        let attempt = runtime
            .firecrawl_fallback(
                fallback,
                ExtractIntent::ExtractArticle,
                "https://8.8.8.8/book.pdf",
                "fixture",
                Utc::now() + chrono::Duration::seconds(10),
                Vec::new(),
            )
            .await;
        server.await.unwrap();
        let ExtractionAttempt::Failure { usage, .. } = attempt else {
            panic!("invalid provider content must not be published");
        };
        assert_eq!(usage.len(), 1);
        let UsageWrite::Firecrawl(observed) = &usage[0] else {
            panic!("billing must survive rejection")
        };
        assert_eq!(observed.credits_used, Some(296));
        assert_eq!(observed.resource_count(), Some(296));
        assert!((observed.cost_usd.unwrap() - 0.9472).abs() < 1e-12);
    }
}

fn fallback_fixture() -> ExtractionFallbackRequired {
    ExtractionFallbackRequired {
        schema_version: 1,
        request_id: "local-fixture".to_owned(),
        fallback: newsly_extraction::FallbackKind::Firecrawl,
        url: PublicUrl::parse("https://8.8.8.8/article").unwrap(),
        reason: "local_extraction_failed; static_fetch:http_403".to_owned(),
        retryable: false,
        usage_events: Vec::new(),
        timings: Vec::new(),
    }
}

async fn run_fallback(runtime: &ContentExtractionRuntime) -> ExtractionAttempt {
    runtime
        .firecrawl_fallback(
            fallback_fixture(),
            ExtractIntent::ExtractArticle,
            "https://8.8.8.8/article",
            "queue-fixture",
            Utc::now() + chrono::Duration::seconds(5),
            Vec::new(),
        )
        .await
}

fn attributed(runtime: ContentExtractionRuntime, pool: &sqlx::PgPool) -> ContentExtractionRuntime {
    runtime
        .with_credit_control(pool.clone())
        .for_task(FirecrawlAttribution {
            task: 17,
            content: None,
            news_item: Some(42),
            user: None,
        })
}

#[sqlx::test(migrations = false)]
async fn no_subscriber_demand_skips_paid_dispatch_and_credit_probe(pool: sqlx::PgPool) {
    newsly_db::run_migrations(&pool).await.unwrap();
    sqlx::query("UPDATE firecrawl_credit_state SET retry_after=now()-interval '1 second'")
        .execute(&pool)
        .await
        .unwrap();
    let (runtime, server) = fixture_runtime(json!({"success":true})).await;
    let runtime =
        attributed(runtime, &pool).with_demand_policy(FirecrawlDemandPolicy::NoSubscriberDemand);
    let mut fallback = fallback_fixture();
    fallback.usage_events = serde_json::from_value(json!([
        {"kind":"downloaded_body","quantity":24,"unit":"byte"}
    ]))
    .unwrap();
    let attempt = runtime
        .firecrawl_fallback(
            fallback,
            ExtractIntent::ExtractArticle,
            "https://8.8.8.8/article",
            "demand-fixture",
            Utc::now() + chrono::Duration::seconds(5),
            Vec::new(),
        )
        .await;
    let ExtractionAttempt::Failure {
        code,
        retryable,
        usage,
        ..
    } = attempt
    else {
        panic!("no demand must finish metadata-only rather than defer for credit");
    };
    assert_eq!(code, "fallback_not_permitted");
    assert!(!retryable);
    assert_eq!(usage.len(), 1, "local extractor usage must survive denial");
    assert!(matches!(usage[0], UsageWrite::Extraction(_)));
    assert!(
        !server.is_finished(),
        "no provider request may reach the listener"
    );
    server.abort();
    let probe: Option<uuid::Uuid> =
        sqlx::query_scalar("SELECT probe_token FROM firecrawl_credit_state")
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(
        probe, None,
        "a denied request must not claim the shared recovery probe"
    );
    let billed: i64 =
        sqlx::query_scalar("SELECT count(*) FROM vendor_usage_records WHERE provider='firecrawl'")
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(billed, 0);
}

#[tokio::test]
async fn no_subscriber_demand_keeps_free_article_extraction() {
    let payload = json!({
        "schema_version":1,"request_id":"free-fixture-article","kind":"success",
        "final_url":"https://8.8.8.8/article","title":"Free article",
        "author":null,"published_at":null,"markdown":"Complete free article body",
        "tables":[],"feed_links":[],"method":"static_readability","warnings":[],
        "usage_events":[],"timings":[]
    });
    let (runtime, server) = fixture_runtime(payload).await;
    let attempt = runtime
        .with_demand_policy(FirecrawlDemandPolicy::NoSubscriberDemand)
        .process_article(
            "https://8.8.8.8/article",
            "article",
            "free-fixture",
            Utc::now() + chrono::Duration::seconds(5),
        )
        .await;
    server.await.unwrap();
    let ExtractionAttempt::Success { article, .. } = attempt else {
        panic!("subscriber demand gates only paid fallback");
    };
    assert!(!article.used_firecrawl);
    assert_eq!(article.body, "Complete free article body");
}

#[sqlx::test(migrations = false)]
async fn firecrawl_runtime_exhaustion_defers_without_another_call_and_recovers(pool: sqlx::PgPool) {
    newsly_db::run_migrations(&pool).await.unwrap();
    let (runtime, server) = runtime_raw(
        402,
        json!({"success":false,"error":"Insufficient credits"}).to_string(),
    )
    .await;
    let runtime = attributed(runtime, &pool);
    assert!(matches!(
        run_fallback(&runtime).await,
        ExtractionAttempt::Deferred { .. }
    ));
    server.await.unwrap();
    assert!(matches!(
        run_fallback(&runtime).await,
        ExtractionAttempt::Deferred { .. }
    ));
    let requests: i64 =
        sqlx::query_scalar("SELECT count(*) FROM vendor_usage_records WHERE provider='firecrawl'")
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(requests, 1);
    sqlx::query("UPDATE firecrawl_credit_state SET retry_after=now()-interval '1 second'")
        .execute(&pool)
        .await
        .unwrap();
    let (runtime, server) = fixture_runtime(json!({"success":true,"data":{"markdown":"Complete article evidence", "metadata":{"creditsUsed":1}}})).await;
    let runtime = attributed(runtime, &pool);
    let ExtractionAttempt::Success { article, usage } = run_fallback(&runtime).await else {
        panic!("funded probe must publish article");
    };
    server.await.unwrap();
    assert_eq!(article.body, "Complete article evidence");
    assert!(
        usage.is_empty(),
        "durable billing must not be counted again at finalization"
    );
    let rows: i64 =
        sqlx::query_scalar("SELECT count(*) FROM vendor_usage_records WHERE provider='firecrawl'")
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(rows, 2);
    assert!(matches!(
        newsly_db::acquire_firecrawl_admission(&pool).await.unwrap(),
        newsly_db::FirecrawlAdmission::Allowed { probe_token: None }
    ));
}

#[sqlx::test(migrations = false)]
async fn firecrawl_runtime_invalid_json_402_still_pauses_and_records_unknown(pool: sqlx::PgPool) {
    newsly_db::run_migrations(&pool).await.unwrap();
    let (runtime, server) = runtime_raw(402, "not-json".to_owned()).await;
    assert!(matches!(
        run_fallback(&attributed(runtime, &pool)).await,
        ExtractionAttempt::Deferred { .. }
    ));
    server.await.unwrap();
    let row: (Option<i32>, Option<f64>, Value) = sqlx::query_as("SELECT resource_count,cost_usd,metadata FROM vendor_usage_records WHERE provider='firecrawl'").fetch_one(&pool).await.unwrap();
    assert!(row.0.is_none() && row.1.is_none());
    assert_eq!(row.2["status_code"], 402);
    assert_eq!(row.2["error_code"], "invalid_json");
    assert!(matches!(
        newsly_db::acquire_firecrawl_admission(&pool).await.unwrap(),
        newsly_db::FirecrawlAdmission::Paused { .. }
    ));
}

#[sqlx::test(migrations = false)]
async fn firecrawl_runtime_probe_server_failure_keeps_article_pending(pool: sqlx::PgPool) {
    newsly_db::run_migrations(&pool).await.unwrap();
    newsly_db::observe_firecrawl_response(&pool, None, 402)
        .await
        .unwrap();
    sqlx::query("UPDATE firecrawl_credit_state SET retry_after=now()-interval '1 second'")
        .execute(&pool)
        .await
        .unwrap();
    let (runtime, server) = runtime_raw(
        500,
        json!({"success":false,"error":"Transient failure"}).to_string(),
    )
    .await;
    assert!(matches!(
        run_fallback(&attributed(runtime, &pool)).await,
        ExtractionAttempt::Deferred { .. }
    ));
    server.await.unwrap();
}

#[sqlx::test(migrations = false)]
async fn firecrawl_runtime_cancelled_dispatch_remains_visible_unpriced(pool: sqlx::PgPool) {
    newsly_db::run_migrations(&pool).await.unwrap();
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = Url::parse(&format!("http://{}/", listener.local_addr().unwrap())).unwrap();
    let server = tokio::spawn(async move {
        let (_stream, _) = listener.accept().await.unwrap();
        std::future::pending::<()>().await;
    });
    let extractor = DocumentExtractorClient::new(
        DocumentExtractorConfig::new(endpoint.clone(), SecretString::from("fixture")).unwrap(),
    )
    .unwrap();
    let firecrawl = FirecrawlClient::new(
        endpoint,
        Some(SecretString::from("fixture")),
        Duration::from_secs(5),
        0.004,
    )
    .unwrap();
    let runtime = attributed(
        ContentExtractionRuntime::fixture(extractor, firecrawl),
        &pool,
    );
    let call = tokio::spawn(async move { run_fallback(&runtime).await });
    tokio::time::timeout(Duration::from_secs(3), async {
        loop {
            let requests: i64 = sqlx::query_scalar(
                "SELECT count(*) FROM vendor_usage_records WHERE provider='firecrawl'",
            )
            .fetch_one(&pool)
            .await
            .unwrap();
            if requests == 1 {
                break;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .unwrap();
    call.abort();
    assert!(call.await.unwrap_err().is_cancelled());
    server.abort();
    let _ = server.await;
    let row:(Option<i32>,Option<f64>,Value)=sqlx::query_as("SELECT resource_count,cost_usd,metadata FROM vendor_usage_records WHERE provider='firecrawl'").fetch_one(&pool).await.unwrap();
    assert!(row.0.is_none() && row.1.is_none());
    assert_eq!(row.2["phase"], "dispatched");
    assert!(
        row.2["fallback_reason"]
            .as_str()
            .unwrap()
            .contains("static_fetch:http_403")
    );
}

#[sqlx::test(migrations = false)]
async fn firecrawl_runtime_oversized_402_still_pauses_before_body_read(pool: sqlx::PgPool) {
    newsly_db::run_migrations(&pool).await.unwrap();
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = Url::parse(&format!("http://{}/", listener.local_addr().unwrap())).unwrap();
    let server = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.unwrap();
        let mut buffer = [0; 4096];
        let read = stream.read(&mut buffer).await.unwrap();
        assert!(read > 0);
        stream.write_all(b"HTTP/1.1 402 Fixture\r\nContent-Type: application/json\r\nContent-Length: 2500001\r\nConnection: close\r\n\r\n").await.unwrap();
    });
    let extractor = DocumentExtractorClient::new(
        DocumentExtractorConfig::new(endpoint.clone(), SecretString::from("fixture")).unwrap(),
    )
    .unwrap();
    let firecrawl = FirecrawlClient::new(
        endpoint,
        Some(SecretString::from("fixture")),
        Duration::from_secs(5),
        0.004,
    )
    .unwrap();
    let runtime = attributed(
        ContentExtractionRuntime::fixture(extractor, firecrawl),
        &pool,
    );
    assert!(matches!(
        run_fallback(&runtime).await,
        ExtractionAttempt::Deferred { .. }
    ));
    server.await.unwrap();
    let metadata: Value =
        sqlx::query_scalar("SELECT metadata FROM vendor_usage_records WHERE provider='firecrawl'")
            .fetch_one(&pool)
            .await
            .unwrap();
    assert_eq!(metadata["status_code"], 402);
    assert_eq!(metadata["error_code"], "response_too_large");
    assert!(matches!(
        newsly_db::acquire_firecrawl_admission(&pool).await.unwrap(),
        newsly_db::FirecrawlAdmission::Paused { .. }
    ));
}
