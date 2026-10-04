use std::env;
use std::fmt::Write as _;
use std::path::{Path, PathBuf};
use std::time::SystemTime;

use axum::Router;
use axum::body::Body;
use axum::extract::{OriginalUri, Path as AxumPath, State};
use axum::http::header::{CONTENT_DISPOSITION, CONTENT_TYPE};
use axum::http::{HeaderMap, HeaderValue};
use axum::response::{Html, IntoResponse, Response};
use axum::routing::get;
use chrono::{DateTime, Utc};
use percent_encoding::{NON_ALPHANUMERIC, utf8_percent_encode};
use tokio::fs;

use crate::admin_api_keys::{admin_login_redirect, escape_html, has_valid_admin_session};
use crate::error::ApiError;
use crate::gateway::RouteOwnershipStamp;
use crate::write_support::{internal_error, not_found};
use crate::{AppState, request_id_from_headers};

const MAX_VIEW_BYTES: usize = 5 * 1024 * 1024;

#[derive(Debug)]
struct LogEntry {
    relative_name: String,
    size: u64,
    modified: SystemTime,
}

pub(super) fn router() -> Router<AppState> {
    Router::new()
        .route("/admin/logs", get(list_logs))
        .route("/admin/logs/{filename}/download", get(download_log))
        .route("/admin/logs/{filename}", get(view_log))
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
    _stamp: RouteOwnershipStamp,
) -> Result<Response, ApiError> {
    if !has_valid_admin_session(&state, &headers) {
        return Ok(admin_login_redirect(uri.path()));
    }
    let request_id = request_id_from_headers(&headers);
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
    _stamp: RouteOwnershipStamp,
    AxumPath(filename): AxumPath<String>,
) -> Result<Response, ApiError> {
    if !has_valid_admin_session(&state, &headers) {
        return Ok(admin_login_redirect(uri.path()));
    }
    let request_id = request_id_from_headers(&headers);
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
    _stamp: RouteOwnershipStamp,
    AxumPath(filename): AxumPath<String>,
) -> Result<Response, ApiError> {
    if !has_valid_admin_session(&state, &headers) {
        return Ok(admin_login_redirect(uri.path()));
    }
    let request_id = request_id_from_headers(&headers);
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

fn render_log_index(entries: &[LogEntry]) -> String {
    let mut html = crate::admin_shell::admin_shell("Logs");
    html.push_str("<main><h1>Logs</h1><table><thead><tr><th>File</th><th>Size</th><th>Modified</th><th></th></tr></thead><tbody>");
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
    let mut html = crate::admin_shell::admin_shell(filename);
    let _ = write!(
        html,
        "<main><p><a href=\"/admin/logs\">Back to logs</a></p><h1>{}</h1><pre>{}</pre></main></body></html>",
        escape_html(filename),
        escape_html(content)
    );
    html
}
