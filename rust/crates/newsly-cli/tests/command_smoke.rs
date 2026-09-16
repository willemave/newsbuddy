use std::collections::BTreeMap;
use std::process::{Command, Output};
use std::sync::atomic::{AtomicU8, Ordering};
use std::sync::{Arc, Mutex};

use axum::Router;
use axum::body::{Body, Bytes};
use axum::extract::State;
use axum::http::{HeaderMap, Method, StatusCode, Uri};
use axum::response::{IntoResponse, Response};
use axum::routing::any;
use serde_json::{Value, json};
use tempfile::TempDir;

const API_KEY: &str = "fixture-key";
const NORMAL: u8 = 0;
const API_ERROR: u8 = 1;
const MALFORMED: u8 = 2;
const PENDING_JOB: u8 = 3;

#[derive(Clone, Debug)]
struct CapturedRequest {
    method: Method,
    path: String,
    query: BTreeMap<String, Vec<String>>,
    authorization: Option<String>,
    client: Option<String>,
    version: Option<String>,
    body: Value,
}

#[derive(Clone, Default)]
struct MockState {
    requests: Arc<Mutex<Vec<CapturedRequest>>>,
    mode: Arc<AtomicU8>,
}

struct MockServer {
    origin: String,
    state: MockState,
    task: tokio::task::JoinHandle<()>,
}

impl Drop for MockServer {
    fn drop(&mut self) {
        self.task.abort();
    }
}

impl MockServer {
    async fn spawn() -> Self {
        let state = MockState::default();
        let app = Router::new()
            .fallback(any(capture_request))
            .with_state(state.clone());
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind mock CLI server");
        let address = listener.local_addr().expect("mock server address");
        let task = tokio::spawn(async move {
            axum::serve(listener, app)
                .await
                .expect("serve mock CLI requests");
        });
        Self {
            origin: format!("http://{address}"),
            state,
            task,
        }
    }

    fn set_mode(&self, mode: u8) {
        self.state.mode.store(mode, Ordering::SeqCst);
    }

    fn take_requests(&self) -> Vec<CapturedRequest> {
        std::mem::take(&mut *self.state.requests.lock().expect("request lock"))
    }
}

async fn capture_request(
    State(state): State<MockState>,
    method: Method,
    uri: Uri,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let path = uri.path().to_owned();
    let query = parse_query(&uri);
    let request = CapturedRequest {
        method,
        path: path.clone(),
        query,
        authorization: header(&headers, "authorization"),
        client: header(&headers, "x-newsly-client"),
        version: header(&headers, "x-newsly-client-version"),
        body: if body.is_empty() {
            Value::Null
        } else {
            serde_json::from_slice(&body).expect("CLI sent valid JSON")
        },
    };
    state.requests.lock().expect("request lock").push(request);

    match state.mode.load(Ordering::SeqCst) {
        API_ERROR => (
            StatusCode::UNAUTHORIZED,
            axum::Json(json!({
                "code": "unauthorized",
                "message": "test denial",
                "details": {"reason": "fixture"},
                "retryable": false,
                "request_id": "smoke-request"
            })),
        )
            .into_response(),
        MALFORMED => Response::builder()
            .status(StatusCode::OK)
            .header("content-type", "application/json")
            .body(Body::from("invalid json"))
            .expect("malformed response"),
        mode => axum::Json(response_for(&path, mode)).into_response(),
    }
}

fn response_for(path: &str, mode: u8) -> Value {
    match path {
        "/api/content/submit" => json!({"content_id": 42, "task_id": 7}),
        "/api/content/42" => {
            json!({"id": 42, "title": "Fixture article", "content_type": "article"})
        }
        "/api/content/submissions/list" => json!({"submissions": []}),
        "/api/jobs/7" => json!({
            "id": 7,
            "status": if mode == PENDING_JOB { "processing" } else { "completed" }
        }),
        "/api/agent/onboarding" => json!({"run_id": 9}),
        "/api/agent/onboarding/9" => json!({"run_id": 9, "run_status": "completed"}),
        "/api/agent/library/manifest" => json!({
            "generated_at": "2026-09-15T00:00:00Z",
            "include_source": true,
            "documents": []
        }),
        _ => json!({"items": []}),
    }
}

fn header(headers: &HeaderMap, name: &str) -> Option<String> {
    headers
        .get(name)
        .and_then(|value| value.to_str().ok())
        .map(str::to_owned)
}

fn parse_query(uri: &Uri) -> BTreeMap<String, Vec<String>> {
    let url = reqwest::Url::parse(&format!("http://fixture{uri}"))
        .expect("captured request URI is valid");
    let mut query = BTreeMap::<String, Vec<String>>::new();
    for (name, value) in url.query_pairs() {
        query
            .entry(name.into_owned())
            .or_default()
            .push(value.into_owned());
    }
    query
}

async fn run_cli(temp: &TempDir, origin: &str, arguments: &[&str]) -> Output {
    let mut arguments_with_auth = vec!["--server", origin, "--api-key", API_KEY];
    arguments_with_auth.extend_from_slice(arguments);
    run_local_cli(temp, &arguments_with_auth).await
}

async fn run_local_cli(temp: &TempDir, arguments: &[&str]) -> Output {
    let mut command = Command::new(env!("CARGO_BIN_EXE_newsbuddy"));
    command.arg("--config").arg(temp.path().join("config.json"));
    remove_cli_environment(&mut command);
    command.args(arguments);
    tokio::task::spawn_blocking(move || command.output().expect("run newsbuddy"))
        .await
        .expect("join newsbuddy process")
}

fn remove_cli_environment(command: &mut Command) {
    for name in [
        "NEWSBUDDY_CONFIG",
        "NEWSBUDDY_CONFIG_PATH",
        "NEWSBUDDY_SERVER",
        "NEWSBUDDY_API_KEY",
        "NEWSLY_AGENT_CONFIG",
        "NEWSLY_AGENT_CONFIG_PATH",
        "NEWSLY_AGENT_SERVER",
        "NEWSLY_AGENT_API_KEY",
    ] {
        command.env_remove(name);
    }
}

fn assert_success(output: &Output, command: &str) -> Value {
    assert!(
        output.status.success(),
        "{command} failed: stdout={} stderr={}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(output.stderr.is_empty(), "{command} wrote to stderr");
    let envelope: Value = serde_json::from_slice(&output.stdout).expect("JSON CLI envelope");
    assert_eq!(envelope["command"], command);
    assert_eq!(envelope["ok"], true);
    envelope
}

fn assert_request(
    request: &CapturedRequest,
    method: &Method,
    path: &str,
    query: &[(&str, &[&str])],
    body: Value,
) {
    assert_eq!(&request.method, method, "request for {path}");
    assert_eq!(request.path, path);
    for (name, expected) in query {
        let expected = expected.iter().map(ToString::to_string).collect::<Vec<_>>();
        assert_eq!(request.query.get(*name), Some(&expected), "query {name}");
    }
    if let Value::Object(expected) = body {
        for (name, value) in expected {
            assert_eq!(request.body.get(&name), Some(&value), "body field {name}");
        }
    } else {
        assert_eq!(request.body, body);
    }
    assert_eq!(request.authorization.as_deref(), Some("Bearer fixture-key"));
    assert_eq!(request.client.as_deref(), Some("rust_cli"));
    assert!(
        request
            .version
            .as_deref()
            .is_some_and(|value| !value.is_empty()),
        "client version header should be present"
    );
}

#[tokio::test(flavor = "multi_thread")]
#[expect(
    clippy::too_many_lines,
    reason = "the route matrix is clearer as one contiguous table"
)]
async fn command_families_preserve_the_http_contract() {
    struct Case {
        name: &'static str,
        arguments: &'static [&'static str],
        command: &'static str,
        method: Method,
        path: &'static str,
        query: &'static [(&'static str, &'static [&'static str])],
        body: Value,
    }

    let server = MockServer::spawn().await;
    let temp = tempfile::tempdir().expect("temporary CLI home");
    let cases = [
        Case {
            name: "content list",
            arguments: &[
                "content",
                "list",
                "--limit",
                "10",
                "--content-type",
                "article,podcast",
                "--cursor",
                "opaque+/=",
                "--date",
                "2026-09-15",
                "--read-filter",
                "read",
            ],
            command: "content.list",
            method: Method::GET,
            path: "/api/content/",
            query: &[
                ("limit", &["10"]),
                ("content_type", &["article", "podcast"]),
                ("cursor", &["opaque+/="]),
                ("date", &["2026-09-15"]),
                ("read_filter", &["read"]),
            ],
            body: Value::Null,
        },
        Case {
            name: "content get",
            arguments: &["content", "get", "42"],
            command: "content.get",
            method: Method::GET,
            path: "/api/content/42",
            query: &[],
            body: Value::Null,
        },
        Case {
            name: "submission list",
            arguments: &[
                "content",
                "submissions",
                "list",
                "--limit",
                "8",
                "--cursor",
                "next",
            ],
            command: "content.submissions.list",
            method: Method::GET,
            path: "/api/content/submissions/list",
            query: &[("limit", &["8"]), ("cursor", &["next"])],
            body: Value::Null,
        },
        Case {
            name: "submit",
            arguments: &[
                "content",
                "submit",
                "https://example.com/article",
                "--note",
                "focus",
                "--crawl-links",
                "--title",
                "Example",
                "--platform",
                "web",
                "--content-type",
                "article",
            ],
            command: "content.submit",
            method: Method::POST,
            path: "/api/content/submit",
            query: &[],
            body: json!({
                "url": "https://example.com/article",
                "instruction": "focus",
                "crawl_links": true,
                "title": "Example",
                "platform": "web",
                "content_type": "article",
                "save_to_knowledge_and_mark_read": false
            }),
        },
        Case {
            name: "search",
            arguments: &[
                "search",
                "rust agents",
                "--limit",
                "3",
                "--include-podcasts=false",
            ],
            command: "search",
            method: Method::POST,
            path: "/api/agent/search",
            query: &[],
            body: json!({"query": "rust agents", "limit": 3, "include_podcasts": false}),
        },
        Case {
            name: "sources list",
            arguments: &["sources", "list", "--type", "atom"],
            command: "sources.list",
            method: Method::GET,
            path: "/api/scrapers/",
            query: &[("type", &["atom"])],
            body: Value::Null,
        },
        Case {
            name: "sources add",
            arguments: &[
                "sources",
                "add",
                "https://example.com/feed",
                "--feed-type",
                "atom",
                "--display-name",
                "Fixture",
            ],
            command: "sources.add",
            method: Method::POST,
            path: "/api/scrapers/subscribe",
            query: &[],
            body: json!({
                "feed_url": "https://example.com/feed",
                "feed_type": "atom",
                "display_name": "Fixture"
            }),
        },
        Case {
            name: "news list",
            arguments: &[
                "news",
                "list",
                "--limit",
                "10",
                "--cursor",
                "next",
                "--read-filter",
                "unread",
            ],
            command: "news.list",
            method: Method::GET,
            path: "/api/news/items",
            query: &[
                ("limit", &["10"]),
                ("cursor", &["next"]),
                ("read_filter", &["unread"]),
            ],
            body: Value::Null,
        },
        Case {
            name: "news get",
            arguments: &["news", "get", "42"],
            command: "news.get",
            method: Method::GET,
            path: "/api/news/items/42",
            query: &[],
            body: Value::Null,
        },
        Case {
            name: "news convert",
            arguments: &["news", "convert", "42"],
            command: "news.convert",
            method: Method::POST,
            path: "/api/news/items/42/convert-to-article",
            query: &[],
            body: Value::Null,
        },
        Case {
            name: "news mark read",
            arguments: &["news", "mark-read", "42", "43"],
            command: "news.mark-read",
            method: Method::POST,
            path: "/api/news/items/mark-read",
            query: &[],
            body: json!({"content_ids": [42, 43]}),
        },
        Case {
            name: "onboarding status",
            arguments: &["onboarding", "status", "9"],
            command: "onboarding.status",
            method: Method::GET,
            path: "/api/agent/onboarding/9",
            query: &[],
            body: Value::Null,
        },
        Case {
            name: "onboarding complete",
            arguments: &[
                "onboarding",
                "complete",
                "9",
                "--suggestion-id",
                "1,2",
                "--aggregator",
                "tech,world",
            ],
            command: "onboarding.complete",
            method: Method::POST,
            path: "/api/agent/onboarding/9/complete",
            query: &[],
            body: json!({
                "accept_all": false,
                "selected_suggestion_ids": [1, 2],
                "selected_aggregators": [
                    {"key": "tech", "title": null, "topics": []},
                    {"key": "world", "title": null, "topics": []}
                ]
            }),
        },
        Case {
            name: "jobs get",
            arguments: &["jobs", "get", "7"],
            command: "jobs.get",
            method: Method::GET,
            path: "/api/jobs/7",
            query: &[],
            body: Value::Null,
        },
    ];

    for case in cases {
        let output = run_cli(&temp, &server.origin, case.arguments).await;
        assert_success(&output, case.command);
        let requests = server.take_requests();
        assert_eq!(requests.len(), 1, "{} request count", case.name);
        assert_request(&requests[0], &case.method, case.path, case.query, case.body);
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn waited_commands_poll_the_documented_resources() {
    let server = MockServer::spawn().await;
    let temp = tempfile::tempdir().expect("temporary CLI home");

    for (verb, command, summarize) in [
        ("submit", "content.submit", false),
        ("summarize", "content.summarize", true),
    ] {
        let output = run_cli(
            &temp,
            &server.origin,
            &[
                "content",
                verb,
                "https://example.com/article",
                "--wait",
                "--wait-interval",
                "1ms",
            ],
        )
        .await;
        let envelope = assert_success(&output, command);
        assert_eq!(envelope["job"]["status"], "completed");
        let requests = server.take_requests();
        assert_eq!(requests.len(), 3, "{verb} polling request count");
        assert_request(
            &requests[0],
            &Method::POST,
            "/api/content/submit",
            &[],
            json!({"save_to_knowledge_and_mark_read": summarize}),
        );
        assert_request(&requests[1], &Method::GET, "/api/jobs/7", &[], Value::Null);
        assert_request(
            &requests[2],
            &Method::GET,
            "/api/content/42",
            &[],
            Value::Null,
        );
    }

    let output = run_cli(
        &temp,
        &server.origin,
        &[
            "onboarding",
            "start",
            "--brief",
            "Rust and systems",
            "--seed-url",
            "https://example.com/topic",
            "--seed-feed",
            "https://example.com/feed",
            "--wait",
            "--wait-interval",
            "1ms",
        ],
    )
    .await;
    let envelope = assert_success(&output, "onboarding.start");
    assert_eq!(envelope["job"]["run_status"], "completed");
    let requests = server.take_requests();
    assert_eq!(requests.len(), 2);
    assert_request(
        &requests[0],
        &Method::POST,
        "/api/agent/onboarding",
        &[],
        json!({
            "brief": "Rust and systems",
            "seed_urls": ["https://example.com/topic"],
            "seed_feeds": ["https://example.com/feed"]
        }),
    );
    assert_request(
        &requests[1],
        &Method::GET,
        "/api/agent/onboarding/9",
        &[],
        Value::Null,
    );

    let output = run_cli(
        &temp,
        &server.origin,
        &["jobs", "wait", "7", "--wait-interval", "1ms"],
    )
    .await;
    assert_success(&output, "jobs.wait");
    let requests = server.take_requests();
    assert_eq!(requests.len(), 1);
    assert_request(&requests[0], &Method::GET, "/api/jobs/7", &[], Value::Null);
}

#[tokio::test(flavor = "multi_thread")]
async fn process_exit_and_error_envelopes_cover_remote_and_local_failures() {
    let server = MockServer::spawn().await;
    let temp = tempfile::tempdir().expect("temporary CLI home");

    server.set_mode(API_ERROR);
    let output = run_cli(&temp, &server.origin, &["content", "list"]).await;
    assert_eq!(output.status.code(), Some(1));
    assert!(output.stderr.is_empty());
    let envelope: Value = serde_json::from_slice(&output.stdout).expect("API error envelope");
    assert_eq!(envelope["command"], "content.list");
    assert_eq!(envelope["ok"], false);
    assert_eq!(envelope["error"]["status_code"], 401);
    assert_eq!(envelope["error"]["code"], "unauthorized");
    assert_eq!(envelope["error"]["details"]["reason"], "fixture");
    assert_eq!(envelope["error"]["retryable"], false);
    assert_eq!(envelope["error"]["request_id"], "smoke-request");
    server.take_requests();

    server.set_mode(MALFORMED);
    let output = run_cli(&temp, &server.origin, &["content", "list"]).await;
    assert_eq!(output.status.code(), Some(1));
    let envelope: Value = serde_json::from_slice(&output.stdout).expect("decode error envelope");
    assert_eq!(envelope["ok"], false);
    assert!(
        envelope["error"]["message"]
            .as_str()
            .is_some_and(|message| message.contains("decode response"))
    );
    server.take_requests();

    server.set_mode(PENDING_JOB);
    let output = run_cli(
        &temp,
        &server.origin,
        &[
            "jobs",
            "wait",
            "7",
            "--wait-interval",
            "1ms",
            "--wait-timeout",
            "3ms",
        ],
    )
    .await;
    assert_eq!(output.status.code(), Some(1));
    let envelope: Value = serde_json::from_slice(&output.stdout).expect("timeout envelope");
    assert_eq!(envelope["command"], "jobs.wait");
    assert!(
        envelope["error"]["message"]
            .as_str()
            .is_some_and(|message| message.contains("timed out waiting for job 7"))
    );
    let requests = server.take_requests();
    assert!(!requests.is_empty(), "wait should fetch the job");
    assert!(
        requests.iter().all(|request| request.path == "/api/jobs/7"),
        "wait should only poll its requested job"
    );

    server.set_mode(NORMAL);
    for arguments in [
        &["content", "submit", "ftp://example.com"][..],
        &[
            "sources",
            "add",
            "https://example.com/feed",
            "--feed-type",
            "unsupported",
        ][..],
        &["jobs", "wait", "7", "--wait-interval", "0s"][..],
    ] {
        let output = run_cli(&temp, &server.origin, arguments).await;
        assert_eq!(output.status.code(), Some(1), "arguments={arguments:?}");
        let envelope: Value = serde_json::from_slice(&output.stdout).expect("local error envelope");
        assert_eq!(envelope["ok"], false);
    }
    assert!(
        server.take_requests().is_empty(),
        "local validation must precede HTTP"
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn completion_version_config_and_library_sync_run_as_real_processes() {
    let temp = tempfile::tempdir().expect("temporary CLI home");

    for shell in ["bash", "zsh", "fish", "powershell"] {
        let output = run_local_cli(&temp, &["completion", shell]).await;
        assert!(output.status.success(), "completion {shell}");
        assert!(!output.stdout.is_empty());
        assert!(
            !String::from_utf8_lossy(&output.stdout)
                .trim_start()
                .starts_with('{')
        );
    }

    let output = run_local_cli(&temp, &["version", "--output", "text"]).await;
    assert!(output.status.success());
    let stdout = String::from_utf8(output.stdout).expect("version text output");
    assert!(stdout.starts_with("command: version\nok: true\n"));

    let library_root = temp.path().join("library");
    for arguments in [
        vec!["config", "set", "server", "http://127.0.0.1:8000"],
        vec!["config", "set", "api-key", "temporary-test-key"],
        vec![
            "config",
            "set",
            "library-root",
            library_root.to_str().expect("UTF-8 library path"),
        ],
    ] {
        let output = run_local_cli(&temp, &arguments).await;
        assert!(output.status.success(), "config set: {arguments:?}");
    }
    let output = run_local_cli(&temp, &["config", "show"]).await;
    let envelope = assert_success(&output, "config.show");
    assert_eq!(envelope["data"]["server_url"], "http://127.0.0.1:8000");
    assert_eq!(envelope["data"]["api_key_set"], true);
    assert_eq!(
        envelope["data"]["library_root"],
        json!(library_root.to_string_lossy())
    );
    assert_ne!(envelope["data"]["api_key_mask"], "temporary-test-key");

    let server = MockServer::spawn().await;
    let sync_root = temp.path().join("synced-library");
    let output = run_cli(
        &temp,
        &server.origin,
        &[
            "library",
            "sync",
            "--dir",
            sync_root.to_str().expect("UTF-8 sync path"),
        ],
    )
    .await;
    assert_success(&output, "library.sync");
    let requests = server.take_requests();
    assert_eq!(requests.len(), 1);
    assert_request(
        &requests[0],
        &Method::GET,
        "/api/agent/library/manifest",
        &[("include_source", &["true"])],
        Value::Null,
    );
}
