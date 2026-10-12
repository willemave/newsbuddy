use super::*;
use newsly_extraction::DocumentExtractorConfig;
use secrecy::SecretString;
use serde_json::{Value, json};
use std::time::Duration;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;
use url::Url;

async fn runtime(payload: Value) -> (ContentExtractionRuntime, tokio::task::JoinHandle<()>) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = Url::parse(&format!("http://{}/", listener.local_addr().unwrap())).unwrap();
    let server = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.unwrap();
        let mut buffer = [0u8; 4096];
        let read = stream.read(&mut buffer).await.unwrap();
        assert!(read > 0);
        let body = payload.to_string();
        let response = format!(
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
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
    (ContentExtractionRuntime::new(extractor, firecrawl), server)
}

#[tokio::test]
async fn firecrawl_billing_survives_empty_body_and_unsafe_final_url() {
    for data in [
        json!({"metadata":{"creditsUsed":296,"numPages":296}}),
        json!({"markdown":"Body", "metadata":{"creditsUsed":296,"sourceURL":"http://127.0.0.1/private"}}),
    ] {
        let (runtime, server) = runtime(json!({"success":true,"data":data})).await;
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
