use super::*;
use serde_json::{Value, json};

fn input() -> NewsCategoryInput {
    NewsCategoryInput {
        title: "New chips".into(),
        summary: "A chip maker builds faster AI chips.".into(),
        key_points: vec!["New factory".into()],
        categories: vec![
            NewsCategory {
                key: "news-ai-industry".into(),
                title: "AI".into(),
                deck: "AI industry".into(),
                routing_rule: Some("AI hardware and models".into()),
            },
            NewsCategory {
                key: "news-space".into(),
                title: "Space".into(),
                deck: "Space exploration".into(),
                routing_rule: None,
            },
        ],
    }
}
fn response() -> Value {
    json!({"id":"gen-123","model":NEWS_CATEGORY_RESPONSE_MODEL,"provider":"TypeSafe",
        "answers":{"category":{"type":"choice","choice":"news-ai-industry","probabilities":{"news-ai-industry":0.9,"news-space":0.05,"other":0.05}}},
        "usage":{"input_tokens":1000,"output_tokens":0,"cost":0.000_042}})
}
fn validate(raw: &Value) -> Result<NewsCategoryDecision, NewsCategoryGatewayError> {
    validate_response(raw, &input(), 10_000)
}

#[test]
fn preserves_input_order_privacy_and_category_keys() {
    let input = input();
    let wire = serde_json::to_string(&DecisionRequest::new(&input)).unwrap();
    assert!(wire.find("\"news-ai-industry\":").unwrap() < wire.find("\"news-space\":").unwrap());
    assert!(wire.find("\"news-space\":").unwrap() < wire.find("\"other\":").unwrap());
    assert!(!wire.contains("\"c0\":"));
    let mut reversed = input.clone();
    reversed.categories.reverse();
    let reversed_wire = serde_json::to_string(&DecisionRequest::new(&reversed)).unwrap();
    assert!(
        reversed_wire.find("\"news-space\":").unwrap()
            < reversed_wire.find("\"news-ai-industry\":").unwrap()
    );
    let request: Value = serde_json::from_str(&wire).unwrap();
    assert_eq!(request["model"], NEWS_CATEGORY_REQUEST_MODEL);
    assert_eq!(
        request["state"]["news"]["key_points"],
        json!(input.key_points)
    );
    assert_eq!(
        request["provider"],
        json!({"only":["typesafe"],"allow_fallbacks":false,"require_parameters":true,"data_collection":"deny","zdr":true})
    );
    assert!(
        request["questions"]["category"]["criteria"]["news-ai-industry"]
            .as_str()
            .unwrap()
            .contains("AI hardware and models")
    );
    let decision = validate(&response()).unwrap();
    assert_eq!(decision.selected_key.as_deref(), Some("news-ai-industry"));
    assert_eq!(decision.selected_probability.to_bits(), 0.9_f64.to_bits());
    assert_eq!(decision.usage.request_count, 1);
    let mut raw = response();
    raw["answers"]["category"]["choice"] = json!("other");
    raw["answers"]["category"]["probabilities"] =
        json!({"news-ai-industry":0.05,"news-space":0.05,"other":0.9});
    assert!(validate(&raw).unwrap().selected_key.is_none());
}

#[test]
fn malformed_route_answers_and_price_preserve_billing() {
    for (pointer, value) in [
        ("/model", json!(NEWS_CATEGORY_REQUEST_MODEL)),
        ("/provider", json!("Wrong")),
        ("/answers/category/type", json!("other")),
        ("/answers/category/choice", json!("news-space")),
        (
            "/answers/category/probabilities/news-ai-industry",
            json!(true),
        ),
        (
            "/answers/category/probabilities/news-ai-industry",
            json!(-1),
        ),
        (
            "/answers/category/probabilities",
            json!({"news-ai-industry":1.0}),
        ),
        ("/usage/cost", json!(0.01)),
    ] {
        let mut raw = response();
        *raw.pointer_mut(pointer).unwrap() = value;
        let err = validate(&raw).unwrap_err();
        assert_eq!(err.observed_usage().unwrap().input_tokens, 1000);
        assert_eq!(err.observed_cost_usd(), raw["usage"]["cost"].as_f64());
        assert_eq!(err.generation_id(), Some("gen-123"));
    }
    let mut raw = response();
    raw["error"] = json!({"message": "provider rejected request"});
    let error = validate(&raw).unwrap_err();
    assert_eq!(error.code(), "provider_error");
    assert_eq!(error.observed_cost_usd(), Some(0.000_042));
    let mut raw = response();
    raw["usage"]["cost"] = json!(-1);
    assert!(validate(&raw).unwrap_err().observed_usage().is_none());
}

#[test]
fn requires_exact_option_set_normalized_scores_and_argmax() {
    for probabilities in [
        json!({"news-ai-industry":0.9,"news-space":0.05,"other":0.05,"extra":0}),
        json!({"news-ai-industry":0.9,"news-space":0.05,"wrong":0.05}),
        json!({"news-ai-industry":0.9,"news-space":0.9,"other":0.05}),
        json!({"news-ai-industry":0.899,"news-space":0.05,"other":0.05}),
        json!({"news-ai-industry":0.1,"news-space":0.8,"other":0.1}),
    ] {
        let mut raw = response();
        raw["answers"]["category"]["probabilities"] = probabilities;
        assert!(validate(&raw).is_err());
    }
    for probabilities in [
        json!({"news-ai-industry":0.9,"news-space":0.05,"other":0.04}),
        json!({"news-ai-industry":0.9,"news-space":0.05,"other":0.06}),
    ] {
        let mut raw = response();
        raw["answers"]["category"]["probabilities"] = probabilities;
        assert_eq!(
            validate(&raw).unwrap().selected_probability.to_bits(),
            0.9_f64.to_bits()
        );
    }
}

#[test]
fn input_bounds_and_duplicate_keys() {
    let mut reserved = input();
    reserved.categories[0].key = "other".into();
    assert!(reserved.validate().is_err());
    let mut value = input();
    value.categories[1].key = value.categories[0].key.clone();
    assert!(value.validate().is_err());
    let mut value = input();
    value.categories = vec![value.categories[0].clone(); 11];
    assert!(value.validate().is_err());
    let mut value = input();
    value.summary = "x".repeat(8193);
    assert!(value.validate().is_err());
    let mut value = input();
    value.key_points = vec!["x".into(); 17];
    assert!(value.validate().is_err());
}

async fn fixture(status: &str, body: String) -> (String, tokio::task::JoinHandle<Value>) {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!("http://{}", listener.local_addr().unwrap());
    let wire = format!(
        "HTTP/1.1 {status}\r\nContent-Length: {}\r\nLocation: http://127.0.0.1:1/forbidden\r\nConnection: close\r\n\r\n{body}",
        body.len()
    );
    let server = tokio::spawn(async move {
        let (mut socket, _) = listener.accept().await.unwrap();
        let mut bytes = Vec::new();
        let header_end = loop {
            let mut chunk = [0; 4096];
            let n = socket.read(&mut chunk).await.unwrap();
            assert!(n > 0);
            bytes.extend_from_slice(&chunk[..n]);
            if let Some(i) = bytes.windows(4).position(|w| w == b"\r\n\r\n") {
                break i + 4;
            }
        };
        let header = String::from_utf8_lossy(&bytes[..header_end]).to_lowercase();
        let length: usize = header
            .lines()
            .find_map(|l| l.strip_prefix("content-length: "))
            .unwrap()
            .parse()
            .unwrap();
        while bytes.len() < header_end + length {
            let mut chunk = [0; 4096];
            let n = socket.read(&mut chunk).await.unwrap();
            assert!(n > 0);
            bytes.extend_from_slice(&chunk[..n]);
        }
        assert!(header.contains("authorization: bearer fixture"));
        let request = serde_json::from_slice(&bytes[header_end..]).unwrap();
        let _ = socket.write_all(wire.as_bytes()).await;
        // A retry would require a second connection, which this single-use listener rejects.
        request
    });
    (endpoint, server)
}

#[tokio::test]
async fn http_success_privacy_errors_redirects_malformed_and_size() {
    let gateway = NewsCategoryGateway::new(Some(SecretString::from("fixture"))).unwrap();
    let (url, server) = fixture("200 OK", response().to_string()).await;
    assert_eq!(
        gateway
            .decide_at(&input(), &url)
            .await
            .unwrap()
            .selected_key
            .as_deref(),
        Some("news-ai-industry")
    );
    let request = server.await.unwrap();
    assert_eq!(request["provider"]["zdr"], true);
    assert_eq!(request["provider"]["data_collection"], "deny");
    for status in ["503 Service Unavailable", "302 Found"] {
        let (url, server) = fixture(status, "failure".into()).await;
        assert!(matches!(
            gateway.decide_at(&input(), &url).await,
            Err(NewsCategoryGatewayError::Provider(_))
        ));
        server.await.unwrap();
    }
    let (url, server) = fixture("503 Service Unavailable", response().to_string()).await;
    assert!(
        gateway
            .decide_at(&input(), &url)
            .await
            .unwrap_err()
            .observed_usage()
            .is_some()
    );
    server.await.unwrap();
    let (url, server) = fixture("200 OK", "malformed".into()).await;
    assert!(matches!(
        gateway.decide_at(&input(), &url).await,
        Err(NewsCategoryGatewayError::Json(_))
    ));
    server.await.unwrap();
    let (url, server) = fixture("200 OK", "x".repeat(MAX_BYTES + 1)).await;
    assert!(matches!(
        gateway.decide_at(&input(), &url).await,
        Err(NewsCategoryGatewayError::InvalidResponse("response_size"))
    ));
    server.await.unwrap();
}

#[tokio::test]
async fn missing_credentials_and_hard_timeout() {
    for key in [None, Some(SecretString::from(" "))] {
        let gateway = NewsCategoryGateway::new(key).unwrap();
        assert!(matches!(
            gateway.decide_at(&input(), "http://127.0.0.1:1").await,
            Err(NewsCategoryGatewayError::Unavailable)
        ));
    }
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(async move {
        let (_socket, _) = listener.accept().await.unwrap();
        std::future::pending::<()>().await;
    });
    let gateway = NewsCategoryGateway::new(Some(SecretString::from("fixture"))).unwrap();
    let start = std::time::Instant::now();
    let error = gateway.decide_at(&input(), &url).await.unwrap_err();
    assert!(matches!(error, NewsCategoryGatewayError::Http(ref e) if e.is_timeout()));
    assert!(start.elapsed() < Duration::from_secs(7));
    server.abort();
}
