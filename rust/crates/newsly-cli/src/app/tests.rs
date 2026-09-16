use std::collections::{HashMap, VecDeque};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use axum::Router;
use axum::extract::{Json, Path as AxumPath, Query, State};
use axum::http::{HeaderMap, StatusCode};
use axum::routing::{get, post};
use tempfile::tempdir;

use super::*;

#[derive(Debug, Clone)]
struct CapturedRequest {
    authorization: Option<String>,
    client: Option<String>,
    version: Option<String>,
    body: Value,
}

type AuthObservation = Arc<Mutex<Vec<(String, bool)>>>;
type FavoritesRequests = Arc<Mutex<Vec<(Option<String>, HashMap<String, String>)>>>;

#[derive(Debug, Clone)]
struct FavoritesState {
    pages: Arc<Mutex<VecDeque<Value>>>,
    requests: FavoritesRequests,
}

async fn spawn_server(router: Router) -> (String, tokio::task::JoinHandle<()>) {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let handle = tokio::spawn(async move {
        axum::serve(listener, router).await.unwrap();
    });
    (format!("http://{address}"), handle)
}

fn content(id: i64, content_type: &str, title: &str, saved_at: &str) -> Value {
    json!({
        "id": id,
        "content_type": content_type,
        "title": title,
        "url": format!("https://example.com/{id}"),
        "knowledge_saved_at": saved_at,
    })
}

fn knowledge_page(
    contents: impl IntoIterator<Item = Value>,
    has_more: bool,
    next_cursor: impl Into<Value>,
) -> Value {
    let contents = contents.into_iter().collect::<Vec<_>>();
    let next_cursor = next_cursor.into();
    let page_size = contents.len();
    json!({
        "contents": contents,
        "available_dates": [],
        "content_types": ["article", "podcast"],
        "meta": {
            "has_more": has_more,
            "next_cursor": next_cursor,
            "page_size": page_size,
            "total": null,
        }
    })
}

async fn spawn_favorites_server(
    pages: Vec<Value>,
) -> (String, tokio::task::JoinHandle<()>, FavoritesRequests) {
    let requests = Arc::new(Mutex::new(Vec::new()));
    let state = FavoritesState {
        pages: Arc::new(Mutex::new(pages.into())),
        requests: Arc::clone(&requests),
    };
    let router = Router::new()
        .route(
            "/api/content/knowledge/list",
            get(
                |State(state): State<FavoritesState>,
                 headers: HeaderMap,
                 Query(query): Query<HashMap<String, String>>| async move {
                    let authorization = headers
                        .get("authorization")
                        .and_then(|value| value.to_str().ok())
                        .map(str::to_owned);
                    state.requests.lock().unwrap().push((authorization, query));
                    Json(state.pages.lock().unwrap().pop_front().unwrap())
                },
            ),
        )
        .with_state(state);
    let (server, handle) = spawn_server(router).await;
    (server, handle, requests)
}

async fn run_favorites(server: String, arguments: &[&str]) -> (u8, Vec<u8>, Vec<u8>) {
    let directory = tempdir().unwrap();
    let config_path = directory.path().join("config.json");
    let mut command = vec![
        "newsbuddy".to_owned(),
        "--config".to_owned(),
        config_path.to_string_lossy().into_owned(),
        "--server".to_owned(),
        server,
        "--api-key".to_owned(),
        "newsly_ak_favorites".to_owned(),
        "content".to_owned(),
        "favorites".to_owned(),
    ];
    command.extend(arguments.iter().map(|value| (*value).to_owned()));
    let mut stdout = Vec::new();
    let mut stderr = Vec::new();
    let exit = run(command, &mut stdout, &mut stderr, "test").await;
    (exit, stdout, stderr)
}

#[test]
fn url_validation_matches_the_cli_contract() {
    assert!(parse_http_url("https://example.com/article").is_ok());
    assert!(matches!(
        parse_http_url("ftp://example.com"),
        Err(CommandError::Local(message)) if message == "url must use http or https"
    ));
    assert!(parse_http_url("not-a-url").is_err());
}

#[test]
fn wait_validation_is_only_applied_when_waiting() {
    let disabled = optional_wait_options(OptionalWaitArgs {
        wait: false,
        wait_interval: Duration::ZERO,
        wait_timeout: Duration::ZERO,
    });
    assert!(matches!(disabled, Ok(None)));
    let enabled = optional_wait_options(OptionalWaitArgs {
        wait: true,
        wait_interval: Duration::ZERO,
        wait_timeout: Duration::ZERO,
    });
    assert!(matches!(enabled, Err(CommandError::Local(_))));
}

#[tokio::test]
async fn search_preserves_headers_body_and_json_envelope() {
    let captured = Arc::new(Mutex::new(None::<CapturedRequest>));
    let state = Arc::clone(&captured);
    let router = Router::new()
        .route(
            "/api/agent/search",
            post(
                |State(state): State<Arc<Mutex<Option<CapturedRequest>>>>,
                 headers: HeaderMap,
                 Json(body): Json<Value>| async move {
                    *state.lock().unwrap() = Some(CapturedRequest {
                        authorization: headers
                            .get("authorization")
                            .and_then(|value| value.to_str().ok())
                            .map(str::to_owned),
                        client: headers
                            .get("x-newsly-client")
                            .and_then(|value| value.to_str().ok())
                            .map(str::to_owned),
                        version: headers
                            .get("x-newsly-client-version")
                            .and_then(|value| value.to_str().ok())
                            .map(str::to_owned),
                        body,
                    });
                    Json(json!({"results": []}))
                },
            ),
        )
        .with_state(state);
    let (server, handle) = spawn_server(router).await;
    let directory = tempdir().unwrap();
    let config_path = directory.path().join("config.json");
    let mut stdout = Vec::new();
    let mut stderr = Vec::new();

    let exit = run(
        [
            "newsbuddy".to_owned(),
            "--config".to_owned(),
            config_path.to_string_lossy().into_owned(),
            "--server".to_owned(),
            server,
            "--api-key".to_owned(),
            "newsly_ak_test".to_owned(),
            "search".to_owned(),
            "rust agents".to_owned(),
            "--limit".to_owned(),
            "3".to_owned(),
            "--include-podcasts=false".to_owned(),
        ],
        &mut stdout,
        &mut stderr,
        "1.2.3",
    )
    .await;
    handle.abort();

    assert_eq!(exit, 0, "stderr={}", String::from_utf8_lossy(&stderr));
    let envelope: Value = serde_json::from_slice(&stdout).unwrap();
    assert_eq!(envelope["command"], "search");
    assert_eq!(envelope["ok"], true);
    let request = captured.lock().unwrap().clone().unwrap();
    assert_eq!(
        request.authorization.as_deref(),
        Some("Bearer newsly_ak_test")
    );
    assert_eq!(request.client.as_deref(), Some("rust_cli"));
    assert_eq!(request.version.as_deref(), Some("1.2.3"));
    assert_eq!(request.body["query"], "rust agents");
    assert_eq!(request.body["limit"], 3);
    assert_eq!(request.body["include_podcasts"], false);
}

#[tokio::test]
async fn canonical_api_error_is_preserved_in_the_cli_envelope() {
    let router = Router::new().route(
        "/api/jobs/{job_id}",
        get(|AxumPath(_): AxumPath<i64>| async {
            (
                StatusCode::CONFLICT,
                Json(json!({
                    "code": "stale_owner",
                    "message": "runtime owner changed",
                    "details": {"expected": "rust"},
                    "retryable": true,
                    "request_id": "request-42"
                })),
            )
        }),
    );
    let (server, handle) = spawn_server(router).await;
    let directory = tempdir().unwrap();
    let config_path = directory.path().join("config.json");
    let mut stdout = Vec::new();
    let mut stderr = Vec::new();
    let exit = run(
        [
            "newsbuddy".to_owned(),
            "--config".to_owned(),
            config_path.to_string_lossy().into_owned(),
            "--server".to_owned(),
            server,
            "--api-key".to_owned(),
            "newsly_ak_test".to_owned(),
            "jobs".to_owned(),
            "get".to_owned(),
            "42".to_owned(),
        ],
        &mut stdout,
        &mut stderr,
        "test",
    )
    .await;
    handle.abort();

    assert_eq!(exit, 1);
    assert!(stderr.is_empty());
    let envelope: Value = serde_json::from_slice(&stdout).unwrap();
    assert_eq!(envelope["command"], "jobs.get");
    assert_eq!(envelope["error"]["status_code"], 409);
    assert_eq!(envelope["error"]["code"], "stale_owner");
    assert_eq!(envelope["error"]["details"]["expected"], "rust");
    assert_eq!(envelope["error"]["retryable"], true);
    assert_eq!(envelope["error"]["request_id"], "request-42");
    assert_eq!(
        envelope["config_path"],
        config_path.to_string_lossy().as_ref()
    );
}

#[tokio::test]
async fn qr_login_is_unauthenticated_and_persists_the_claimed_key() {
    let observed = Arc::new(Mutex::new(Vec::<(String, bool)>::new()));
    let state = Arc::clone(&observed);
    let router = Router::new()
        .route(
            "/api/agent/cli/link/start",
            post(
                |State(state): State<AuthObservation>,
                 headers: HeaderMap,
                 Json(body): Json<Value>| async move {
                    state
                        .lock()
                        .unwrap()
                        .push(("start".to_owned(), headers.contains_key("authorization")));
                    assert_eq!(body["device_name"], "test-device");
                    Json(json!({
                        "session_id": "session-1",
                        "status": "pending",
                        "poll_token": "poll-1",
                        "approve_url": "newsly://cli-link?token=approve-1",
                        "expires_at": "2026-08-31T12:00:00Z",
                        "poll_interval_seconds": 2
                    }))
                },
            ),
        )
        .route(
            "/api/agent/cli/link/{session_id}",
            get(
                |State(state): State<AuthObservation>,
                 AxumPath(session_id): AxumPath<String>,
                 Query(query): Query<HashMap<String, String>>,
                 headers: HeaderMap| async move {
                    state
                        .lock()
                        .unwrap()
                        .push(("poll".to_owned(), headers.contains_key("authorization")));
                    assert_eq!(session_id, "session-1");
                    assert_eq!(query.get("poll_token").map(String::as_str), Some("poll-1"));
                    Json(json!({
                        "session_id": "session-1",
                        "status": "approved",
                        "expires_at": "2026-08-31T12:00:00Z",
                        "api_key": "newsly_ak_claimed",
                        "key_prefix": "newsly_ak_cl"
                    }))
                },
            ),
        )
        .with_state(state);
    let (server, handle) = spawn_server(router).await;
    let directory = tempdir().unwrap();
    let config_path = directory.path().join("config.json");
    let mut stdout = Vec::new();
    let mut stderr = Vec::new();
    let exit = run(
        [
            "newsbuddy".to_owned(),
            "--config".to_owned(),
            config_path.to_string_lossy().into_owned(),
            "--server".to_owned(),
            server.clone(),
            "auth".to_owned(),
            "login".to_owned(),
            "--device-name".to_owned(),
            "test-device".to_owned(),
            "--poll-interval".to_owned(),
            "1ms".to_owned(),
            "--poll-timeout".to_owned(),
            "1s".to_owned(),
        ],
        &mut stdout,
        &mut stderr,
        "test",
    )
    .await;
    handle.abort();

    assert_eq!(exit, 0, "stdout={}", String::from_utf8_lossy(&stdout));
    let calls = observed.lock().unwrap().clone();
    assert_eq!(
        calls,
        vec![("start".to_owned(), false), ("poll".to_owned(), false)]
    );
    let saved = config::load(&config_path).unwrap();
    assert_eq!(saved.server_url, server);
    assert_eq!(saved.api_key, "newsly_ak_claimed");
    let stderr = String::from_utf8(stderr).unwrap();
    assert!(stderr.contains("Scan this QR code"));
    assert!(stderr.contains("newsly://cli-link?token=approve-1"));
}

#[tokio::test]
async fn completion_is_raw_shell_output_not_an_envelope() {
    let mut stdout = Vec::new();
    let mut stderr = Vec::new();
    let exit = run(
        ["newsbuddy", "completion", "bash"],
        &mut stdout,
        &mut stderr,
        "test",
    )
    .await;
    assert_eq!(exit, 0);
    assert!(stderr.is_empty());
    let output = String::from_utf8(stdout).unwrap();
    assert!(output.contains("_newsbuddy"));
    assert!(!output.trim_start().starts_with('{'));
}

#[tokio::test]
async fn favorites_collects_articles_across_mixed_and_empty_article_pages() {
    let tied = "2026-09-15T12:00:00Z";
    let mut first_article = content(1, "article", "First", tied);
    first_article["future_field"] = json!({"preserved": true});
    let pages = vec![
        knowledge_page(
            vec![
                json!({
                    "content_type": "future_content_type",
                    "future_payload": {"ignored": true}
                }),
                first_article,
            ],
            true,
            json!("cursor-1"),
        ),
        knowledge_page(
            vec![content(91, "podcast", "Only podcast", tied)],
            true,
            json!("cursor-2"),
        ),
        knowledge_page(
            vec![
                content(2, "article", "Second", tied),
                content(3, "article", "Third", "2026-09-14T12:00:00Z"),
            ],
            true,
            json!("unused-cursor"),
        ),
    ];
    let (server, handle, requests) = spawn_favorites_server(pages).await;
    let (exit, stdout, stderr) = run_favorites(server, &["--limit", "3"]).await;
    handle.abort();

    assert_eq!(exit, 0, "stderr={}", String::from_utf8_lossy(&stderr));
    let envelope: Value = serde_json::from_slice(&stdout).unwrap();
    assert_eq!(envelope["command"], "content.favorites");
    assert_eq!(envelope["data"]["count"], 3);
    assert_eq!(envelope["data"]["requested_limit"], 3);
    assert_eq!(
        envelope["data"]["articles"]
            .as_array()
            .unwrap()
            .iter()
            .map(|item| item["id"].as_i64().unwrap())
            .collect::<Vec<_>>(),
        vec![1, 2, 3]
    );
    assert_eq!(
        envelope["data"]["articles"][0]["future_field"]["preserved"],
        true
    );
    assert_eq!(envelope["data"]["articles"][0]["knowledge_saved_at"], tied);
    assert_eq!(envelope["data"]["articles"][1]["knowledge_saved_at"], tied);

    let requests = requests.lock().unwrap();
    assert_eq!(requests.len(), 3);
    assert!(
        requests.iter().all(
            |(authorization, _)| authorization.as_deref() == Some("Bearer newsly_ak_favorites")
        )
    );
    assert_eq!(requests[0].1.get("limit").map(String::as_str), Some("100"));
    assert_eq!(requests[0].1.get("cursor"), None);
    assert_eq!(requests[1].1.get("limit").map(String::as_str), Some("100"));
    assert_eq!(
        requests[1].1.get("cursor").map(String::as_str),
        Some("cursor-1")
    );
    assert_eq!(requests[2].1.get("limit").map(String::as_str), Some("100"));
    assert_eq!(
        requests[2].1.get("cursor").map(String::as_str),
        Some("cursor-2")
    );
}

#[tokio::test]
async fn favorites_uses_full_api_pages_for_sparse_articles_without_extra_requests() {
    let saved_at = "2026-09-15T12:00:00Z";
    let mut pages = Vec::new();
    let mut first_page: Vec<_> = (1..=9)
        .map(|id| content(id, "article", &format!("Article {id}"), saved_at))
        .collect();
    first_page.extend((1..=91).map(|id| content(1_000 + id, "podcast", "Podcast", saved_at)));
    pages.push(knowledge_page(first_page, true, json!("cursor-1")));
    for page in 2..=9 {
        let podcasts = (0..100)
            .map(|offset| content(2_000 + page * 100 + offset, "podcast", "Podcast", saved_at));
        pages.push(knowledge_page(
            podcasts,
            true,
            json!(format!("cursor-{page}")),
        ));
    }
    let mut last_page: Vec<_> = (0..9)
        .map(|id| content(4_000 + id, "podcast", "Podcast", saved_at))
        .collect();
    last_page.push(content(10, "article", "Article 10", saved_at));
    pages.push(knowledge_page(last_page, false, Value::Null));

    let (server, handle, requests) = spawn_favorites_server(pages).await;
    let (exit, stdout, stderr) = run_favorites(server, &[]).await;
    handle.abort();

    assert_eq!(exit, 0, "stderr={}", String::from_utf8_lossy(&stderr));
    let envelope: Value = serde_json::from_slice(&stdout).unwrap();
    assert_eq!(envelope["data"]["count"], 10);
    assert_eq!(envelope["data"]["requested_limit"], 10);
    assert_eq!(
        envelope["data"]["articles"]
            .as_array()
            .unwrap()
            .iter()
            .map(|article| article["id"].as_i64().unwrap())
            .collect::<Vec<_>>(),
        (1..=10).collect::<Vec<_>>()
    );

    let requests = requests.lock().unwrap();
    assert_eq!(requests.len(), 10);
    assert!(
        requests
            .iter()
            .all(|(_, query)| query.get("limit").map(String::as_str) == Some("100"))
    );
    assert_eq!(requests[0].1.get("cursor"), None);
    for (index, (_, query)) in requests.iter().enumerate().skip(1) {
        assert_eq!(
            query.get("cursor"),
            Some(&format!("cursor-{index}")),
            "request {} used the wrong continuation",
            index + 1
        );
    }
}

#[tokio::test]
async fn favorites_truncates_an_overfull_article_page_to_the_exact_limit() {
    let articles = (1..=5).map(|id| {
        content(
            id,
            "article",
            &format!("Article {id}"),
            "2026-09-15T12:00:00Z",
        )
    });
    let pages = vec![knowledge_page(
        articles,
        true,
        json!("must-not-be-requested"),
    )];
    let (server, handle, requests) = spawn_favorites_server(pages).await;
    let (exit, stdout, stderr) = run_favorites(server, &["--limit", "3"]).await;
    handle.abort();

    assert_eq!(exit, 0, "stderr={}", String::from_utf8_lossy(&stderr));
    let envelope: Value = serde_json::from_slice(&stdout).unwrap();
    assert_eq!(envelope["data"]["count"], 3);
    assert_eq!(
        envelope["data"]["articles"]
            .as_array()
            .unwrap()
            .iter()
            .map(|article| article["id"].as_i64().unwrap())
            .collect::<Vec<_>>(),
        vec![1, 2, 3]
    );
    let requests = requests.lock().unwrap();
    assert_eq!(requests.len(), 1);
    assert_eq!(requests[0].1.get("limit").map(String::as_str), Some("100"));
}

#[tokio::test]
async fn favorites_succeeds_with_fewer_articles_only_when_feed_is_exhausted() {
    let pages = vec![knowledge_page(
        vec![content(
            1,
            "article",
            "Only article",
            "2026-09-15T12:00:00Z",
        )],
        false,
        Value::Null,
    )];
    let (server, handle, requests) = spawn_favorites_server(pages).await;
    let (exit, stdout, stderr) = run_favorites(server, &[]).await;
    handle.abort();

    assert_eq!(exit, 0, "stderr={}", String::from_utf8_lossy(&stderr));
    let envelope: Value = serde_json::from_slice(&stdout).unwrap();
    assert_eq!(envelope["data"]["count"], 1);
    assert_eq!(envelope["data"]["requested_limit"], 10);
    assert_eq!(
        requests.lock().unwrap()[0]
            .1
            .get("limit")
            .map(String::as_str),
        Some("100")
    );
}

#[tokio::test]
async fn favorites_default_limit_returns_ten_articles() {
    let articles: Vec<_> = (1..=10)
        .map(|id| {
            content(
                id,
                "article",
                &format!("Article {id}"),
                "2026-09-15T12:00:00Z",
            )
        })
        .collect();
    let pages = vec![knowledge_page(articles, false, Value::Null)];
    let (server, handle, requests) = spawn_favorites_server(pages).await;
    let (exit, stdout, stderr) = run_favorites(server, &[]).await;
    handle.abort();

    assert_eq!(exit, 0, "stderr={}", String::from_utf8_lossy(&stderr));
    let envelope: Value = serde_json::from_slice(&stdout).unwrap();
    assert_eq!(envelope["data"]["count"], 10);
    assert_eq!(envelope["data"]["articles"].as_array().unwrap().len(), 10);
    assert_eq!(
        requests.lock().unwrap()[0]
            .1
            .get("limit")
            .map(String::as_str),
        Some("100")
    );
}

#[tokio::test]
async fn favorites_text_output_uses_the_null_title_fallback() {
    let mut untitled = content(42, "article", "replaced below", "2026-09-15T12:00:00Z");
    untitled["title"] = Value::Null;
    let pages = vec![knowledge_page(vec![untitled], false, Value::Null)];
    let (server, handle, _) = spawn_favorites_server(pages).await;
    let (exit, stdout, stderr) = run_favorites(server, &["--output", "text"]).await;
    handle.abort();

    assert_eq!(exit, 0, "stderr={}", String::from_utf8_lossy(&stderr));
    assert_eq!(
        String::from_utf8(stdout).unwrap(),
        "command: content.favorites\nok: true\narticles: 1\n1. (untitled)\n   id: 42\n   url: https://example.com/42\n   knowledge_saved_at: 2026-09-15T12:00:00Z\n"
    );
}

#[tokio::test]
async fn favorites_rejects_missing_or_malformed_required_titles() {
    let mut missing = content(1, "article", "removed below", "2026-09-15T12:00:00Z");
    missing.as_object_mut().unwrap().remove("title");
    let mut malformed = content(2, "article", "replaced below", "2026-09-15T12:00:00Z");
    malformed["title"] = json!(42);

    for article in [missing, malformed] {
        let pages = vec![knowledge_page(vec![article], false, Value::Null)];
        let (server, handle, _) = spawn_favorites_server(pages).await;
        let (exit, stdout, _) = run_favorites(server, &[]).await;
        handle.abort();

        assert_eq!(exit, 1);
        let envelope: Value = serde_json::from_slice(&stdout).unwrap();
        assert!(envelope.get("data").is_none());
        assert!(
            envelope["error"]["message"]
                .as_str()
                .unwrap()
                .contains("invalid Knowledge list response")
        );
    }
}

#[tokio::test]
async fn favorites_empty_library_is_a_successful_empty_collection() {
    let pages = vec![knowledge_page(Vec::new(), false, Value::Null)];
    let (server, handle, _) = spawn_favorites_server(pages).await;
    let (exit, stdout, stderr) = run_favorites(server, &["--limit", "4"]).await;
    handle.abort();

    assert_eq!(exit, 0, "stderr={}", String::from_utf8_lossy(&stderr));
    let envelope: Value = serde_json::from_slice(&stdout).unwrap();
    assert_eq!(envelope["data"]["articles"], json!([]));
    assert_eq!(envelope["data"]["count"], 0);
}

#[tokio::test]
async fn favorites_rejects_missing_malformed_and_repeated_continuations() {
    let cases = [
        vec![knowledge_page(Vec::new(), true, Value::Null)],
        vec![knowledge_page(Vec::new(), true, json!(42))],
        vec![
            knowledge_page(Vec::new(), true, json!("same")),
            knowledge_page(Vec::new(), true, json!("same")),
        ],
    ];
    for pages in cases {
        let (server, handle, _) = spawn_favorites_server(pages).await;
        let (exit, stdout, _) = run_favorites(server, &["--limit", "1"]).await;
        handle.abort();
        assert_eq!(exit, 1);
        let envelope: Value = serde_json::from_slice(&stdout).unwrap();
        assert_eq!(envelope["command"], "content.favorites");
        assert!(
            envelope["error"]["message"]
                .as_str()
                .unwrap()
                .contains("invalid Knowledge list response")
        );
    }
}

#[tokio::test]
async fn favorites_enforces_one_deadline_across_all_pages() {
    #[derive(Clone)]
    struct SlowState(Arc<Mutex<VecDeque<Value>>>);

    let pages = VecDeque::from(vec![
        knowledge_page(Vec::new(), true, json!("next")),
        knowledge_page(
            vec![content(1, "article", "Too late", "2026-09-15T12:00:00Z")],
            false,
            Value::Null,
        ),
    ]);
    let router = Router::new()
        .route(
            "/api/content/knowledge/list",
            get(|State(state): State<SlowState>| async move {
                tokio::time::sleep(Duration::from_millis(20)).await;
                Json(state.0.lock().unwrap().pop_front().unwrap())
            }),
        )
        .with_state(SlowState(Arc::new(Mutex::new(pages))));
    let (server, handle) = spawn_server(router).await;
    let (exit, stdout, _) = run_favorites(server, &["--limit", "1", "--timeout", "30ms"]).await;
    handle.abort();

    assert_eq!(exit, 1);
    let envelope: Value = serde_json::from_slice(&stdout).unwrap();
    assert!(
        envelope["error"]["message"]
            .as_str()
            .unwrap()
            .contains("content favorites timed out")
    );
}

#[tokio::test]
async fn favorites_preserves_http_authentication_errors() {
    let router = Router::new().route(
        "/api/content/knowledge/list",
        get(|| async {
            (
                StatusCode::UNAUTHORIZED,
                Json(json!({
                    "code": "invalid_api_key",
                    "message": "invalid API key",
                    "retryable": false,
                    "request_id": "request-favorites"
                })),
            )
        }),
    );
    let (server, handle) = spawn_server(router).await;
    let (exit, stdout, _) = run_favorites(server, &[]).await;
    handle.abort();

    assert_eq!(exit, 1);
    let envelope: Value = serde_json::from_slice(&stdout).unwrap();
    assert_eq!(envelope["error"]["status_code"], 401);
    assert_eq!(envelope["error"]["code"], "invalid_api_key");
    assert_eq!(envelope["error"]["request_id"], "request-favorites");
}

#[tokio::test]
async fn favorites_discards_collected_articles_when_a_later_page_fails() {
    #[derive(Clone)]
    struct SequencedState(Arc<Mutex<VecDeque<(StatusCode, Value)>>>);

    let responses = VecDeque::from(vec![
        (
            StatusCode::OK,
            knowledge_page(
                vec![content(
                    1,
                    "article",
                    "Collected but not returned",
                    "2026-09-15T12:00:00Z",
                )],
                true,
                json!("next"),
            ),
        ),
        (
            StatusCode::UNAUTHORIZED,
            json!({
                "code": "invalid_api_key",
                "message": "API key expired",
                "retryable": false,
                "request_id": "request-later-page"
            }),
        ),
    ]);
    let router = Router::new()
        .route(
            "/api/content/knowledge/list",
            get(|State(state): State<SequencedState>| async move {
                let (status, body) = state.0.lock().unwrap().pop_front().unwrap();
                (status, Json(body))
            }),
        )
        .with_state(SequencedState(Arc::new(Mutex::new(responses))));
    let (server, handle) = spawn_server(router).await;
    let (exit, stdout, _) = run_favorites(server, &["--limit", "2"]).await;
    handle.abort();

    assert_eq!(exit, 1);
    let envelope: Value = serde_json::from_slice(&stdout).unwrap();
    assert_eq!(envelope["ok"], false);
    assert!(envelope.get("data").is_none());
    assert_eq!(envelope["error"]["status_code"], 401);
    assert_eq!(envelope["error"]["request_id"], "request-later-page");
}
