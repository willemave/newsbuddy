use std::collections::BTreeMap;
use std::env;
use std::fmt::Write as _;
use std::path::{Path, PathBuf};
use std::time::SystemTime;

use axum::Router;
use axum::body::Body;
use axum::extract::{Extension, OriginalUri, Path as AxumPath, Query, State};
use axum::http::header::{CONTENT_DISPOSITION, CONTENT_TYPE};
use axum::http::{HeaderMap, HeaderValue};
use axum::response::{Html, IntoResponse, Response};
use axum::routing::{get, post};
use chrono::{DateTime, Utc};
use percent_encoding::{NON_ALPHANUMERIC, utf8_percent_encode};
use serde::Deserialize;
use serde_json::Value;
use tokio::fs;

use crate::admin_api_keys::{admin_login_redirect, escape_html, has_valid_admin_session, redirect};
use crate::error::ApiError;
use crate::gateway::RouteOwnershipStamp;
use crate::write_support::{internal_error, not_found, require_operation, verify_stamp};
use crate::{AppState, request_id_from_headers};

const LIST_OPERATION_ID: &str = "listLogs";
const VIEW_OPERATION_ID: &str = "viewLog";
const DOWNLOAD_OPERATION_ID: &str = "downloadLog";
const ERRORS_OPERATION_ID: &str = "errorsDashboard";
const RESET_OPERATION_ID: &str = "resetErrorLogs";
const MAX_VIEW_BYTES: usize = 5 * 1024 * 1024;
const MAX_ERROR_SCAN_BYTES: usize = 2 * 1024 * 1024;

#[derive(Debug, Deserialize)]
pub(super) struct ErrorDashboardQuery {
    #[serde(default = "default_hours")]
    hours: i64,
    #[serde(default = "default_min_errors")]
    min_errors: usize,
    component: Option<String>,
}

#[derive(Debug)]
struct LogEntry {
    relative_name: String,
    size: u64,
    modified: SystemTime,
}

#[derive(Debug)]
struct ErrorSample {
    category: String,
    component: String,
    message: String,
    source_file: String,
}

pub(super) fn router() -> Router<AppState> {
    Router::new()
        .route("/admin/logs", get(list_logs))
        .route("/admin/logs/{filename}/download", get(download_log))
        .route("/admin/logs/{filename}", get(view_log))
        .route("/admin/errors", get(errors_dashboard))
        .route("/admin/errors/reset", post(reset_error_logs))
}

#[utoipa::path(
    get,
    path = "/admin/logs",
    operation_id = "listLogs",
    tag = "admin",
    responses(
        (status = 200, description = "Log index", content_type = "text/html", body = String),
        (status = 303, description = "Admin login required"),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn list_logs(
    State(state): State<AppState>,
    OriginalUri(uri): OriginalUri,
    headers: HeaderMap,
    Extension(stamp): Extension<RouteOwnershipStamp>,
) -> Result<Response, ApiError> {
    if !has_valid_admin_session(&state, &headers) {
        return Ok(admin_login_redirect(uri.path()));
    }
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, LIST_OPERATION_ID, &request_id)?;
    let entries = discover_logs(&logs_root())
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    Ok(Html(render_log_index(&entries)).into_response())
}

#[utoipa::path(
    get,
    path = "/admin/logs/{filename}",
    operation_id = "viewLog",
    tag = "admin",
    params(("filename" = String, Path, description = "Percent-encoded path relative to the log root")),
    responses(
        (status = 200, description = "Log contents", content_type = "text/html", body = String),
        (status = 303, description = "Admin login required"),
        (status = 404, description = "Log file not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn view_log(
    State(state): State<AppState>,
    OriginalUri(uri): OriginalUri,
    headers: HeaderMap,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    AxumPath(filename): AxumPath<String>,
) -> Result<Response, ApiError> {
    if !has_valid_admin_session(&state, &headers) {
        return Ok(admin_login_redirect(uri.path()));
    }
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, VIEW_OPERATION_ID, &request_id)?;
    let path = resolve_log_file(&logs_root(), &filename)
        .await
        .map_err(|_| not_found("Log file", &request_id))?;
    let bytes = read_bounded(&path, MAX_VIEW_BYTES)
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    let content = String::from_utf8_lossy(&bytes);
    Ok(Html(render_log_detail(&filename, &content)).into_response())
}

#[utoipa::path(
    get,
    path = "/admin/logs/{filename}/download",
    operation_id = "downloadLog",
    tag = "admin",
    params(("filename" = String, Path, description = "Percent-encoded path relative to the log root")),
    responses(
        (status = 200, description = "Log file download", content_type = "text/plain", body = Vec<u8>),
        (status = 303, description = "Admin login required"),
        (status = 404, description = "Log file not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn download_log(
    State(state): State<AppState>,
    OriginalUri(uri): OriginalUri,
    headers: HeaderMap,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    AxumPath(filename): AxumPath<String>,
) -> Result<Response, ApiError> {
    if !has_valid_admin_session(&state, &headers) {
        return Ok(admin_login_redirect(uri.path()));
    }
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, DOWNLOAD_OPERATION_ID, &request_id)?;
    let path = resolve_log_file(&logs_root(), &filename)
        .await
        .map_err(|_| not_found("Log file", &request_id))?;
    let bytes = fs::read(&path)
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    let safe_name = path
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("newsly.log")
        .replace(['\"', '\r', '\n'], "_");
    let mut response = Response::new(Body::from(bytes));
    response.headers_mut().insert(
        CONTENT_TYPE,
        HeaderValue::from_static("text/plain; charset=utf-8"),
    );
    response.headers_mut().insert(
        CONTENT_DISPOSITION,
        HeaderValue::from_str(&format!("attachment; filename=\"{safe_name}\""))
            .map_err(|error| internal_error(error, &request_id))?,
    );
    Ok(response)
}

#[utoipa::path(
    get,
    path = "/admin/errors",
    operation_id = "errorsDashboard",
    tag = "admin",
    params(
        ("hours" = Option<i64>, Query, description = "Lookback window in hours"),
        ("min_errors" = Option<usize>, Query, description = "Minimum category count"),
        ("component" = Option<String>, Query, description = "Optional component filter")
    ),
    responses(
        (status = 200, description = "Error dashboard", content_type = "text/html", body = String),
        (status = 303, description = "Admin login required"),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn errors_dashboard(
    State(state): State<AppState>,
    OriginalUri(uri): OriginalUri,
    headers: HeaderMap,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    Query(query): Query<ErrorDashboardQuery>,
) -> Result<Response, ApiError> {
    if !has_valid_admin_session(&state, &headers) {
        return Ok(admin_login_redirect(uri.path()));
    }
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, ERRORS_OPERATION_ID, &request_id)?;
    let entries = discover_logs(&logs_root())
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    let samples = scan_error_samples(&logs_root(), &entries, &query)
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    Ok(Html(render_error_dashboard(&samples, &query)).into_response())
}

#[utoipa::path(
    post,
    path = "/admin/errors/reset",
    operation_id = "resetErrorLogs",
    tag = "admin",
    responses(
        (status = 303, description = "Error logs removed or admin login required"),
        (status = 404, description = "Error directory not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn reset_error_logs(
    State(state): State<AppState>,
    OriginalUri(uri): OriginalUri,
    headers: HeaderMap,
    Extension(stamp): Extension<RouteOwnershipStamp>,
) -> Result<Response, ApiError> {
    if !has_valid_admin_session(&state, &headers) {
        return Ok(admin_login_redirect(uri.path()));
    }
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, RESET_OPERATION_ID, &request_id)?;
    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;

    let root = logs_root();
    let errors = root.join("errors");
    if fs::metadata(&errors).await.is_err() {
        return Err(not_found("Errors directory", &request_id));
    }
    let mut deleted = 0_u64;
    deleted += delete_matching_files(&errors).await.map_err(|error| {
        tracing::error!(error = %error, "admin error-log reset failed");
        internal_error(error, &request_id)
    })?;
    deleted += delete_matching_files(&root)
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    tracing::info!(deleted_files = deleted, "admin reset error logs");
    Ok(redirect("/admin/errors"))
}

fn logs_root() -> PathBuf {
    env::var_os("LOGS_BASE_DIR").map_or_else(|| PathBuf::from("logs"), PathBuf::from)
}

async fn discover_logs(root: &Path) -> std::io::Result<Vec<LogEntry>> {
    let mut entries = Vec::new();
    collect_dir(root, root, &mut entries, false).await?;
    collect_dir(root, &root.join("errors"), &mut entries, true).await?;
    collect_dir(root, &root.join("structured"), &mut entries, true).await?;
    entries.sort_by_key(|entry| std::cmp::Reverse(entry.modified));
    Ok(entries)
}

async fn collect_dir(
    root: &Path,
    directory: &Path,
    entries: &mut Vec<LogEntry>,
    jsonl_allowed: bool,
) -> std::io::Result<()> {
    let Ok(mut reader) = fs::read_dir(directory).await else {
        return Ok(());
    };
    while let Some(entry) = reader.next_entry().await? {
        let path = entry.path();
        let extension = path.extension().and_then(|value| value.to_str());
        if extension != Some("log") && !(jsonl_allowed && extension == Some("jsonl")) {
            continue;
        }
        let metadata = entry.metadata().await?;
        if !metadata.is_file() {
            continue;
        }
        let Ok(relative) = path.strip_prefix(root) else {
            continue;
        };
        entries.push(LogEntry {
            relative_name: relative.to_string_lossy().replace('\\', "/"),
            size: metadata.len(),
            modified: metadata.modified().unwrap_or(SystemTime::UNIX_EPOCH),
        });
    }
    Ok(())
}

async fn resolve_log_file(root: &Path, relative: &str) -> std::io::Result<PathBuf> {
    if relative.is_empty() || Path::new(relative).is_absolute() {
        return Err(std::io::Error::from(std::io::ErrorKind::NotFound));
    }
    let root = fs::canonicalize(root).await?;
    let candidate = fs::canonicalize(root.join(relative)).await?;
    if candidate == root || !candidate.starts_with(&root) {
        return Err(std::io::Error::from(std::io::ErrorKind::NotFound));
    }
    let metadata = fs::metadata(&candidate).await?;
    if !metadata.is_file() {
        return Err(std::io::Error::from(std::io::ErrorKind::NotFound));
    }
    Ok(candidate)
}

async fn read_bounded(path: &Path, limit: usize) -> std::io::Result<Vec<u8>> {
    let bytes = fs::read(path).await?;
    if bytes.len() <= limit {
        return Ok(bytes);
    }
    let mut bounded = b"[earlier log content omitted]\n".to_vec();
    bounded.extend_from_slice(&bytes[bytes.len() - limit..]);
    Ok(bounded)
}

async fn scan_error_samples(
    root: &Path,
    entries: &[LogEntry],
    query: &ErrorDashboardQuery,
) -> std::io::Result<Vec<ErrorSample>> {
    let cutoff = Utc::now() - chrono::Duration::hours(query.hours.clamp(1, 24 * 90));
    let component_filter = query
        .component
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_ascii_lowercase);
    let mut samples = Vec::new();
    for entry in entries.iter().take(100) {
        let modified: DateTime<Utc> = entry.modified.into();
        if modified < cutoff {
            continue;
        }
        let path = root.join(&entry.relative_name);
        let bytes = read_bounded(&path, MAX_ERROR_SCAN_BYTES).await?;
        let text = String::from_utf8_lossy(&bytes);
        for line in text.lines().rev().take(2_000) {
            let sample = parse_error_line(line, &entry.relative_name);
            let Some(sample) = sample else { continue };
            if component_filter
                .as_ref()
                .is_some_and(|filter| sample.component.to_ascii_lowercase() != *filter)
            {
                continue;
            }
            samples.push(sample);
            if samples.len() >= 1_000 {
                return Ok(samples);
            }
        }
    }
    Ok(samples)
}

fn parse_error_line(line: &str, source_file: &str) -> Option<ErrorSample> {
    let trimmed = line.trim();
    if trimmed.is_empty() {
        return None;
    }
    if let Ok(value) = serde_json::from_str::<Value>(trimmed) {
        let level = string_field(&value, &["level", "levelname", "severity"])
            .unwrap_or_default()
            .to_ascii_lowercase();
        let message = string_field(&value, &["error_message", "message", "event"])
            .unwrap_or_else(|| trimmed.to_owned());
        if !matches!(level.as_str(), "error" | "critical" | "fatal") && !is_error_text(&message) {
            return None;
        }
        let component = string_field(&value, &["component", "target", "logger"])
            .unwrap_or_else(|| "unknown".to_owned());
        let category = string_field(&value, &["error_type", "exception_type"])
            .unwrap_or_else(|| classify_error(&message).to_owned());
        return Some(ErrorSample {
            category,
            component,
            message: message.chars().take(1_000).collect(),
            source_file: source_file.to_owned(),
        });
    }
    if !is_error_text(trimmed) {
        return None;
    }
    Some(ErrorSample {
        category: classify_error(trimmed).to_owned(),
        component: "unknown".to_owned(),
        message: trimmed.chars().take(1_000).collect(),
        source_file: source_file.to_owned(),
    })
}

fn string_field(value: &Value, names: &[&str]) -> Option<String> {
    names
        .iter()
        .find_map(|name| value.get(*name).and_then(Value::as_str).map(str::to_owned))
}

fn is_error_text(value: &str) -> bool {
    let lowered = value.to_ascii_lowercase();
    (lowered.contains(" error")
        || lowered.starts_with("error")
        || lowered.contains("exception")
        || lowered.contains("traceback")
        || lowered.contains("fatal"))
        && !lowered.contains("errors: 0")
}

fn classify_error(value: &str) -> &'static str {
    let lowered = value.to_ascii_lowercase();
    if lowered.contains("timeout") {
        "timeout"
    } else if lowered.contains("rate limit") || lowered.contains("429") {
        "rate_limit"
    } else if lowered.contains("connection") {
        "connection"
    } else if lowered.contains("validation") {
        "validation"
    } else if lowered.contains("json") {
        "json_parse"
    } else if lowered.contains("http") {
        "http_error"
    } else {
        "unknown"
    }
}

async fn delete_matching_files(directory: &Path) -> std::io::Result<u64> {
    let Ok(mut reader) = fs::read_dir(directory).await else {
        return Ok(0);
    };
    let mut deleted = 0;
    while let Some(entry) = reader.next_entry().await? {
        let path = entry.path();
        let extension = path.extension().and_then(|value| value.to_str());
        if !matches!(extension, Some("log" | "jsonl")) || !entry.metadata().await?.is_file() {
            continue;
        }
        fs::remove_file(path).await?;
        deleted += 1;
    }
    Ok(deleted)
}

fn render_log_index(entries: &[LogEntry]) -> String {
    let mut html = document_start("Logs");
    html.push_str("<main><h1>Logs</h1><p><a href=\"/admin/errors\">Error dashboard</a></p><table><thead><tr><th>File</th><th>Size</th><th>Modified</th><th></th></tr></thead><tbody>");
    for entry in entries {
        let encoded = utf8_percent_encode(&entry.relative_name, NON_ALPHANUMERIC);
        let modified: DateTime<Utc> = entry.modified.into();
        let _ = write!(
            html,
            "<tr><td><a href=\"/admin/logs/{encoded}\">{}</a></td><td>{:.1} KB</td><td>{}</td><td><a href=\"/admin/logs/{encoded}/download\">Download</a></td></tr>",
            escape_html(&entry.relative_name),
            size_kibibytes(entry.size),
            modified.format("%Y-%m-%d %H:%M:%S UTC")
        );
    }
    html.push_str("</tbody></table></main></body></html>");
    html
}

// Log sizes are bounded display values; fractional KiB is the only required precision.
#[allow(clippy::cast_precision_loss)]
fn size_kibibytes(bytes: u64) -> f64 {
    bytes as f64 / 1024.0
}

fn render_log_detail(filename: &str, content: &str) -> String {
    let mut html = document_start(filename);
    let _ = write!(
        html,
        "<main><p><a href=\"/admin/logs\">Back to logs</a></p><h1>{}</h1><pre>{}</pre></main></body></html>",
        escape_html(filename),
        escape_html(content)
    );
    html
}

fn render_error_dashboard(samples: &[ErrorSample], query: &ErrorDashboardQuery) -> String {
    let mut counts = BTreeMap::<(&str, &str), usize>::new();
    for sample in samples {
        *counts
            .entry((sample.category.as_str(), sample.component.as_str()))
            .or_default() += 1;
    }
    let minimum = query.min_errors.max(1);
    let mut html = document_start("Errors");
    let _ = write!(
        html,
        "<main><h1>Errors</h1><p>{} matching events in the last {} hours.</p><form method=\"post\" action=\"/admin/errors/reset\"><button type=\"submit\">Reset error logs</button></form><h2>Categories</h2><table><thead><tr><th>Category</th><th>Component</th><th>Count</th></tr></thead><tbody>",
        samples.len(),
        query.hours.clamp(1, 24 * 90)
    );
    for ((category, component), count) in counts {
        if count < minimum {
            continue;
        }
        let _ = write!(
            html,
            "<tr><td>{}</td><td>{}</td><td>{count}</td></tr>",
            escape_html(category),
            escape_html(component)
        );
    }
    html.push_str("</tbody></table><h2>Recent samples</h2>");
    for sample in samples.iter().take(50) {
        let _ = write!(
            html,
            "<article><h3>{} / {}</h3><p><code>{}</code></p><pre>{}</pre></article>",
            escape_html(&sample.category),
            escape_html(&sample.component),
            escape_html(&sample.source_file),
            escape_html(&sample.message)
        );
    }
    html.push_str("</main></body></html>");
    html
}

fn document_start(title: &str) -> String {
    format!(
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>{}</title><style>body{{font:15px system-ui;margin:0;background:#f7f7f5;color:#191919}}main{{max-width:1100px;margin:3rem auto;padding:0 1rem}}table{{border-collapse:collapse;width:100%;background:white}}th,td{{text-align:left;padding:.65rem;border:1px solid #ddd;vertical-align:top}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:white;padding:1rem;border:1px solid #ddd}}article{{margin:1rem 0}}button{{font:inherit;padding:.6rem}}</style></head><body>",
        escape_html(title)
    )
}

const fn default_hours() -> i64 {
    24
}

const fn default_min_errors() -> usize {
    1
}

#[cfg(test)]
mod tests {
    use super::{classify_error, parse_error_line};

    #[test]
    fn classifies_structured_and_plain_errors() {
        let structured = parse_error_line(
            r#"{"level":"ERROR","component":"chat","message":"request timeout"}"#,
            "structured/newsly.jsonl",
        )
        .unwrap();
        assert_eq!(structured.category, "timeout");
        assert_eq!(structured.component, "chat");
        assert_eq!(classify_error("HTTP 429"), "rate_limit");
        assert!(parse_error_line("errors: 0", "newsly.log").is_none());
    }
}
