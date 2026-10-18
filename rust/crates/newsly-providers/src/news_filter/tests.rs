use super::*;
use serde_json::{Value, json};

fn input() -> NewsFilterInput {
    NewsFilterInput {
        filter: "  AI news except product launches\n".to_owned(),
        title: "News title".to_owned(),
        summary: "A summary".to_owned(),
        key_points: vec!["A point".to_owned()],
    }
}

fn response() -> Value {
    json!({
        "id": "generation-123", "model": NEWS_FILTER_RESPONSE_MODEL, "provider": "TypeSafe",
        "answers": {"filter": {"type": "choice", "choice": "no_match",
            "probabilities": {"match": 0.05, "no_match": 0.9, "insufficient_evidence": 0.05}}},
        "usage": {"input_tokens": 1000, "output_tokens": 0, "cost": 0.000_042}
    })
}

fn validate(value: &Value) -> Result<NewsFilterDecision, NewsFilterGatewayError> {
    validate_response(value, 10_000)
}

#[test]
fn request_preserves_filter_and_evaluated_protocol() {
    let input = input();
    let request = serde_json::to_value(DecisionRequest::new(&input)).unwrap();
    assert_eq!(request["state"]["filter"], input.filter);
    assert_eq!(
        request["state"]["news"],
        json!({"title": input.title, "summary": input.summary, "key_points": input.key_points})
    );
    assert_eq!(request["model"], NEWS_FILTER_REQUEST_MODEL);
    assert_eq!(
        request["provider"],
        json!({"only": ["typesafe"], "allow_fallbacks": false, "require_parameters": true, "data_collection": "deny", "zdr": true})
    );
    assert_eq!(request["questions"]["filter"]["instructions"], INSTRUCTIONS);
    let serialized = serde_json::to_string(&DecisionRequest::new(&input)).unwrap();
    assert!(serialized.find("\"match\":").unwrap() < serialized.find("\"no_match\":").unwrap());
}

#[test]
fn exact_threshold_and_conservative_choices() {
    assert!(validate(&response()).unwrap().should_hide());
    let mut raw = response();
    raw["answers"]["filter"]["probabilities"] =
        json!({"match": 0.06, "no_match": 0.89, "insufficient_evidence": 0.05});
    assert!(!validate(&raw).unwrap().should_hide());
    for choice in ["match", "insufficient_evidence"] {
        let mut raw = response();
        raw["answers"]["filter"]["choice"] = json!(choice);
        raw["answers"]["filter"]["probabilities"] =
            json!({"match": 0.5, "no_match": 0.0, "insufficient_evidence": 0.5});
        assert!(!validate(&raw).unwrap().should_hide());
    }
}

#[test]
fn cent_rounding_preserves_reported_scores_but_rejects_other_bad_sums() {
    for values in [(0.04, 0.9, 0.05), (0.05, 0.9, 0.06)] {
        let mut raw = response();
        raw["answers"]["filter"]["probabilities"] =
            json!({"match": values.0, "no_match": values.1, "insufficient_evidence": values.2});
        assert_eq!(
            validate(&raw).unwrap().probabilities.no_match.to_bits(),
            0.9_f64.to_bits()
        );
    }
    let mut raw = response();
    raw["answers"]["filter"]["probabilities"] =
        json!({"match": 0.045, "no_match": 0.9, "insufficient_evidence": 0.05});
    assert!(validate(&raw).is_err());
}

#[test]
fn inconsistent_choices_are_unavailable_and_never_rewritten() {
    let mut raw = response();
    raw["answers"]["filter"]["choice"] = json!("match");
    let error = validate(&raw).unwrap_err();
    assert!(matches!(
        error,
        NewsFilterGatewayError::MeteredProtocol {
            code: "choice_probability_mismatch",
            ..
        }
    ));
    assert_eq!(
        error.metered_usage().unwrap().cost_usd.to_bits(),
        0.000_042_f64.to_bits()
    );
    assert_eq!(error.generation_id(), Some("generation-123"));
}

#[test]
fn rejects_invalid_route_schema_probabilities_and_usage() {
    for (pointer, value) in [
        ("/model", json!(NEWS_FILTER_REQUEST_MODEL)),
        ("/provider", json!("Other")),
        ("/id", json!(" ")),
        ("/id", json!("x".repeat(101))),
        ("/answers/filter/type", json!("other")),
        ("/answers/filter/choice", json!("other")),
        ("/answers/filter/probabilities/match", json!(true)),
        ("/answers/filter/probabilities/match", json!(-0.05)),
        ("/usage/input_tokens", json!(true)),
        ("/usage/input_tokens", json!(20_000)),
        ("/usage/output_tokens", json!(-1)),
        ("/usage/cost", json!(-1)),
        ("/usage/cost", json!(0.01)),
    ] {
        let mut raw = response();
        *raw.pointer_mut(pointer).unwrap() = value;
        assert!(validate(&raw).is_err(), "accepted {pointer}");
    }
    let mut raw = response();
    raw["usage"]["is_byok"] = json!(true);
    assert!(validate(&raw).is_err());
    let mut raw = response();
    raw["answers"]["filter"]["probabilities"]["other"] = json!(0);
    assert!(validate(&raw).is_err());
}

#[test]
fn rejects_oversize_inputs_without_truncating_preferences() {
    let mut input = input();
    input.filter = "a".repeat(8_001);
    assert!(input.validate().is_err());
    input.filter = "界".repeat(3_000);
    assert!(input.validate().is_err());
    input.filter = "a".repeat(8_000);
    assert!(input.validate().is_ok());
    input.summary = "a".repeat(8_193);
    assert!(input.validate().is_err());
}

#[tokio::test]
async fn missing_credentials_never_sends_a_request() {
    let gateway = NewsFilterGateway::new(None).unwrap();
    let attempt = NewsFilterAttempt::default();
    assert!(matches!(
        gateway.decide_with_attempt(&input(), &attempt).await,
        Err(NewsFilterGatewayError::Unavailable)
    ));
    assert!(!attempt.http_started());
}

#[test]
fn reported_billing_survives_wrong_route_price_or_malformed_answer() {
    for (pointer, value) in [
        ("/model", json!("wrong-model")),
        ("/provider", json!("Wrong provider")),
        ("/usage/cost", json!(0.01)),
        ("/answers/filter/choice", json!("unknown")),
        ("/answers/filter/probabilities/match", json!(true)),
    ] {
        let mut raw = response();
        *raw.pointer_mut(pointer).unwrap() = value;
        let expected = raw["usage"]["cost"].as_f64().unwrap();
        let error = validate(&raw).unwrap_err();
        assert_eq!(
            error.metered_usage().unwrap().cost_usd.to_bits(),
            expected.to_bits()
        );
        assert_eq!(error.generation_id(), Some("generation-123"));
    }
    let mut raw = response();
    raw["usage"]["cost"] = json!(-0.1);
    assert!(validate(&raw).unwrap_err().metered_usage().is_none());
    let mut raw = response();
    raw["usage"]["cost"] = json!(101.0);
    assert!(validate(&raw).unwrap_err().metered_usage().is_none());
}

#[tokio::test]
async fn dispatch_marker_survives_cancellation_after_http_starts() {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!("http://{}", listener.local_addr().unwrap());
    let (accepted_tx, accepted_rx) = tokio::sync::oneshot::channel();
    let server = tokio::spawn(async move {
        let (_socket, _) = listener.accept().await.unwrap();
        accepted_tx.send(()).unwrap();
        std::future::pending::<()>().await;
    });
    let gateway = NewsFilterGateway::new(Some(SecretString::from("fixture"))).unwrap();
    let attempt = NewsFilterAttempt::default();
    let input = input();
    let mut call = Box::pin(gateway.decide_at(&input, &endpoint, &attempt));
    tokio::select! {
        result = &mut call => panic!("unexpected response: {result:?}"),
        () = async { accepted_rx.await.unwrap(); } => {},
    }
    drop(call);
    assert!(attempt.http_started());
    server.abort();
}

async fn fixture(status: &str, body: String) -> (String, tokio::task::JoinHandle<()>) {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!("http://{}", listener.local_addr().unwrap());
    let response = format!(
        "HTTP/1.1 {status}\r\nContent-Length: {}\r\nLocation: http://127.0.0.1:1/forbidden\r\nConnection: close\r\n\r\n{body}",
        body.len()
    );
    let server = tokio::spawn(async move {
        let (mut socket, _) = listener.accept().await.unwrap();
        let mut request = vec![0; 16_384];
        assert!(socket.read(&mut request).await.unwrap() > 0);
        // A bounded reader may close early on oversized fixture responses.
        let _ = socket.write_all(response.as_bytes()).await;
    });
    (endpoint, server)
}

#[tokio::test]
async fn transport_does_not_retry_http_failures_or_follow_redirects() {
    let gateway = NewsFilterGateway::new(Some(SecretString::from("fixture"))).unwrap();
    for status in ["503 Service Unavailable", "302 Found"] {
        let (endpoint, server) = fixture(status, "fixture".to_owned()).await;
        assert!(matches!(
            gateway
                .decide_at(&input(), &endpoint, &NewsFilterAttempt::default())
                .await,
            Err(NewsFilterGatewayError::Provider(_))
        ));
        server.await.unwrap();
    }
}

#[tokio::test]
async fn transport_validates_success_and_bounds_streamed_response() {
    let gateway = NewsFilterGateway::new(Some(SecretString::from("fixture"))).unwrap();
    let (endpoint, server) = fixture("200 OK", response().to_string()).await;
    assert!(
        gateway
            .decide_at(&input(), &endpoint, &NewsFilterAttempt::default())
            .await
            .unwrap()
            .should_hide()
    );
    server.await.unwrap();
    let (endpoint, server) = fixture("200 OK", "x".repeat(MAX_RESPONSE_BYTES + 1)).await;
    assert!(matches!(
        gateway
            .decide_at(&input(), &endpoint, &NewsFilterAttempt::default())
            .await,
        Err(NewsFilterGatewayError::InvalidResponse("response_size"))
    ));
    server.await.unwrap();
}

#[tokio::test]
async fn metered_http_failure_preserves_usage_without_accepting_decision() {
    let gateway = NewsFilterGateway::new(Some(SecretString::from("fixture"))).unwrap();
    let (endpoint, server) = fixture("503 Service Unavailable", response().to_string()).await;
    let error = gateway
        .decide_at(&input(), &endpoint, &NewsFilterAttempt::default())
        .await
        .unwrap_err();
    assert_eq!(error.code(), "provider_http_error");
    assert_eq!(error.generation_id(), Some("generation-123"));
    let usage = error.metered_usage().unwrap();
    assert_eq!(usage.input_tokens, 1000);
    assert_eq!(usage.cost_usd.to_bits(), 0.000_042_f64.to_bits());
    server.await.unwrap();
}
