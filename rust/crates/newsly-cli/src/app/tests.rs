use std::collections::HashMap;
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

async fn spawn_server(router: Router) -> (String, tokio::task::JoinHandle<()>) {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let handle = tokio::spawn(async move {
        axum::serve(listener, router).await.unwrap();
    });
    (format!("http://{address}"), handle)
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
