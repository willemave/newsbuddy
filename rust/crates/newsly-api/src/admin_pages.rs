use std::fmt::Write as _;

use axum::Router;
use axum::extract::{Extension, OriginalUri, Query, State};
use axum::http::HeaderMap;
use axum::response::{Html, IntoResponse, Response};
use axum::routing::get;
use chrono::{DateTime, Duration, NaiveDate, NaiveTime, Utc};
use newsly_db::{
    AdminDashboardSnapshot, AdminFeedbackRow, AdminVendorUsageFilter, AdminVendorUsageSnapshot,
    list_admin_feedback, load_admin_dashboard, load_admin_vendor_usage,
};
use serde::Deserialize;

use crate::admin_api_keys::{admin_login_redirect, escape_html, has_valid_admin_session, redirect};
use crate::error::ApiError;
use crate::gateway::RouteOwnershipStamp;
use crate::write_support::{internal_error, require_operation};
use crate::{AppState, request_id_from_headers};

const DASHBOARD_OPERATION_ID: &str = "adminDashboard";
const FEEDBACK_OPERATION_ID: &str = "adminFeedbackPage";
const VENDOR_USAGE_OPERATION_ID: &str = "vendorUsageDashboard";
const LEGACY_USAGE_OPERATION_ID: &str = "legacyLlmUsageRedirect";

#[derive(Debug, Deserialize)]
pub(super) struct DashboardQuery {
    stats_range: Option<String>,
    #[allow(dead_code)]
    cost_bucket: Option<String>,
}

#[derive(Debug, Deserialize)]
pub(super) struct VendorUsageQuery {
    provider: Option<String>,
    model: Option<String>,
    feature: Option<String>,
    user_id: Option<String>,
    start_date: Option<String>,
    end_date: Option<String>,
    limit: Option<String>,
}

pub(super) fn router() -> Router<AppState> {
    Router::new()
        .route("/admin/", get(dashboard))
        .route("/admin/feedback", get(feedback))
        .route("/admin/vendor-usage", get(vendor_usage))
        .route("/admin/llm-usage", get(legacy_llm_usage))
}

#[utoipa::path(
    get,
    path = "/admin/",
    operation_id = "adminDashboard",
    tag = "admin",
    params(
        ("stats_range" = Option<String>, Query, description = "24h, 7d, 30d, or all"),
        ("cost_bucket" = Option<String>, Query, description = "day, week, or month")
    ),
    responses(
        (status = 200, description = "Admin dashboard", content_type = "text/html", body = String),
        (status = 303, description = "Admin login required"),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn dashboard(
    State(state): State<AppState>,
    OriginalUri(uri): OriginalUri,
    headers: HeaderMap,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    Query(query): Query<DashboardQuery>,
) -> Result<Response, ApiError> {
    if !has_valid_admin_session(&state, &headers) {
        return Ok(admin_login_redirect(uri.path()));
    }
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, DASHBOARD_OPERATION_ID, &request_id)?;
    let (range, cutoff) = dashboard_cutoff(query.stats_range.as_deref());
    let snapshot = load_admin_dashboard(state.database.pool(), cutoff)
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    Ok(Html(render_dashboard(&snapshot, range)).into_response())
}

#[utoipa::path(
    get,
    path = "/admin/feedback",
    operation_id = "adminFeedbackPage",
    tag = "admin",
    responses(
        (status = 200, description = "Recent user feedback", content_type = "text/html", body = String),
        (status = 303, description = "Admin login required"),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn feedback(
    State(state): State<AppState>,
    OriginalUri(uri): OriginalUri,
    headers: HeaderMap,
    Extension(stamp): Extension<RouteOwnershipStamp>,
) -> Result<Response, ApiError> {
    if !has_valid_admin_session(&state, &headers) {
        return Ok(admin_login_redirect(uri.path()));
    }
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, FEEDBACK_OPERATION_ID, &request_id)?;
    let rows = list_admin_feedback(state.database.pool())
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    Ok(Html(render_feedback(&rows)).into_response())
}

#[utoipa::path(
    get,
    path = "/admin/vendor-usage",
    operation_id = "vendorUsageDashboard",
    tag = "admin",
    params(
        ("provider" = Option<String>, Query),
        ("model" = Option<String>, Query),
        ("feature" = Option<String>, Query),
        ("user_id" = Option<String>, Query),
        ("start_date" = Option<String>, Query, description = "YYYY-MM-DD"),
        ("end_date" = Option<String>, Query, description = "YYYY-MM-DD"),
        ("limit" = Option<String>, Query)
    ),
    responses(
        (status = 200, description = "Vendor usage dashboard", content_type = "text/html", body = String),
        (status = 303, description = "Admin login required"),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn vendor_usage(
    State(state): State<AppState>,
    OriginalUri(uri): OriginalUri,
    headers: HeaderMap,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    Query(query): Query<VendorUsageQuery>,
) -> Result<Response, ApiError> {
    if !has_valid_admin_session(&state, &headers) {
        return Ok(admin_login_redirect(uri.path()));
    }
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, VENDOR_USAGE_OPERATION_ID, &request_id)?;
    let filter = vendor_filter(&query);
    let snapshot = load_admin_vendor_usage(state.database.pool(), &filter)
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    Ok(Html(render_vendor_usage(&snapshot, &query)).into_response())
}

#[utoipa::path(
    get,
    path = "/admin/llm-usage",
    operation_id = "legacyLlmUsageRedirect",
    tag = "admin",
    responses(
        (status = 307, description = "Redirected to vendor usage"),
        (status = 303, description = "Admin login required"),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn legacy_llm_usage(
    State(state): State<AppState>,
    OriginalUri(uri): OriginalUri,
    headers: HeaderMap,
    Extension(stamp): Extension<RouteOwnershipStamp>,
) -> Result<Response, ApiError> {
    if !has_valid_admin_session(&state, &headers) {
        return Ok(admin_login_redirect(uri.path()));
    }
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, LEGACY_USAGE_OPERATION_ID, &request_id)?;
    let target = uri.query().map_or_else(
        || "/admin/vendor-usage".to_owned(),
        |query| format!("/admin/vendor-usage?{query}"),
    );
    let mut response = redirect(&target);
    *response.status_mut() = axum::http::StatusCode::TEMPORARY_REDIRECT;
    Ok(response)
}

fn dashboard_cutoff(value: Option<&str>) -> (&'static str, Option<DateTime<Utc>>) {
    match value {
        Some("7d") => ("7d", Some(Utc::now() - Duration::days(7))),
        Some("30d") => ("30d", Some(Utc::now() - Duration::days(30))),
        Some("all") => ("all", None),
        _ => ("24h", Some(Utc::now() - Duration::hours(24))),
    }
}

fn vendor_filter(query: &VendorUsageQuery) -> AdminVendorUsageFilter {
    AdminVendorUsageFilter {
        provider: clean(query.provider.as_deref()),
        model: clean(query.model.as_deref()),
        feature: clean(query.feature.as_deref()),
        user_id: query
            .user_id
            .as_deref()
            .and_then(|value| value.trim().parse().ok()),
        start_at: parse_date(query.start_date.as_deref(), false),
        end_at: parse_date(query.end_date.as_deref(), true),
        limit: query
            .limit
            .as_deref()
            .and_then(|value| value.trim().parse().ok())
            .unwrap_or(100),
    }
}

fn clean(value: Option<&str>) -> Option<String> {
    value
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
}

fn parse_date(value: Option<&str>, end_of_day: bool) -> Option<DateTime<Utc>> {
    let date = NaiveDate::parse_from_str(value?.trim(), "%Y-%m-%d").ok()?;
    let time = if end_of_day {
        NaiveTime::from_hms_nano_opt(23, 59, 59, 999_999_999)?
    } else {
        NaiveTime::MIN
    };
    Some(DateTime::from_naive_utc_and_offset(
        date.and_time(time),
        Utc,
    ))
}

fn render_dashboard(snapshot: &AdminDashboardSnapshot, range: &str) -> String {
    let mut html = document_start("Dashboard");
    let total_content = snapshot
        .content_counts
        .iter()
        .map(|row| row.count)
        .sum::<i64>();
    let total_tasks = snapshot
        .task_counts
        .iter()
        .map(|row| row.count)
        .sum::<i64>();
    let _ = write!(
        html,
        "<main><nav><a href=\"/admin/\">Dashboard</a> · <a href=\"/admin/feedback\">Feedback</a> · <a href=\"/admin/vendor-usage\">Usage</a> · <a href=\"/admin/logs\">Logs</a></nav><h1>Newsly Admin</h1><p>Selected window: <strong>{range}</strong> — <a href=\"?stats_range=24h\">24h</a> · <a href=\"?stats_range=7d\">7d</a> · <a href=\"?stats_range=30d\">30d</a> · <a href=\"?stats_range=all\">all</a></p><section class=\"cards\"><div><strong>{total_content}</strong><span>Content rows</span></div><div><strong>{total_tasks}</strong><span>Tasks</span></div><div><strong>{}</strong><span>Active users</span></div><div><strong>{}</strong><span>Onboarded</span></div></section>",
        snapshot.user_stats.active_users, snapshot.user_stats.onboarding_completed,
    );
    html.push_str("<h2>Content</h2>");
    render_counts(
        &mut html,
        snapshot
            .content_counts
            .iter()
            .map(|row| (&row.label, row.count)),
    );
    html.push_str("<h2>Tasks</h2>");
    render_counts(
        &mut html,
        snapshot
            .task_counts
            .iter()
            .map(|row| (&row.label, row.count)),
    );
    html.push_str("<h2>Queue state</h2><table><thead><tr><th>Queue</th><th>Status</th><th>Count</th></tr></thead><tbody>");
    for row in &snapshot.queue_counts {
        let _ = write!(
            html,
            "<tr><td>{}</td><td>{}</td><td>{}</td></tr>",
            escape_html(&row.queue_name),
            escape_html(&row.status),
            row.count
        );
    }
    html.push_str("</tbody></table><h2>Provider cost (30 days)</h2><table><thead><tr><th>Provider</th><th>Rows</th><th>Requests</th><th>Resources</th><th>Cost</th></tr></thead><tbody>");
    for row in &snapshot.provider_costs {
        let _ = write!(
            html,
            "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>",
            escape_html(&row.provider),
            row.row_count,
            row.request_count,
            row.resource_count,
            render_usage_cost(row.cost_usd, row.known_cost_usd, row.unpriced_call_count)
        );
    }
    html.push_str("</tbody></table><h2>Recent task failures</h2>");
    for row in &snapshot.recent_failures {
        let _ = write!(
            html,
            "<article><h3>#{} {} / {}</h3><p>{}</p><pre>{}</pre></article>",
            row.id,
            escape_html(&row.queue_name),
            escape_html(&row.task_type),
            row.created_at.to_rfc3339(),
            escape_html(row.error_message.as_deref().unwrap_or("No error message"))
        );
    }
    html.push_str("</main></body></html>");
    html
}

fn render_counts<'a>(html: &mut String, rows: impl Iterator<Item = (&'a String, i64)>) {
    html.push_str("<table><thead><tr><th>Type</th><th>Count</th></tr></thead><tbody>");
    for (label, count) in rows {
        let _ = write!(
            html,
            "<tr><td>{}</td><td>{count}</td></tr>",
            escape_html(label)
        );
    }
    html.push_str("</tbody></table>");
}

fn render_feedback(rows: &[AdminFeedbackRow]) -> String {
    let mut html = document_start("Feedback");
    html.push_str("<main><p><a href=\"/admin/\">Dashboard</a></p><h1>User feedback</h1>");
    for row in rows {
        let user = row
            .full_name
            .as_deref()
            .or(row.email.as_deref())
            .map_or_else(
                || format!("User {}", row.user_id),
                |label| format!("{label} ({})", row.user_id),
            );
        let _ = write!(
            html,
            "<article><h2>{}</h2><p>{} · {} · {}</p><blockquote>{}</blockquote><small>{}</small></article>",
            escape_html(&user),
            escape_html(&row.source),
            escape_html(row.platform.as_deref().unwrap_or("unknown platform")),
            escape_html(row.app_version.as_deref().unwrap_or("unknown version")),
            escape_html(&row.message),
            row.created_at.to_rfc3339()
        );
    }
    html.push_str("</main></body></html>");
    html
}

fn render_vendor_usage(snapshot: &AdminVendorUsageSnapshot, query: &VendorUsageQuery) -> String {
    let mut html = document_start("Vendor Usage");
    let _ = write!(
        html,
        "<main><p><a href=\"/admin/\">Dashboard</a></p><h1>Vendor usage</h1><form method=\"get\"><input name=\"provider\" placeholder=\"provider\" value=\"{}\"><input name=\"model\" placeholder=\"model\" value=\"{}\"><input name=\"feature\" placeholder=\"feature\" value=\"{}\"><input name=\"user_id\" placeholder=\"user id\" value=\"{}\"><input name=\"start_date\" type=\"date\" value=\"{}\"><input name=\"end_date\" type=\"date\" value=\"{}\"><button>Filter</button></form><section class=\"cards\"><div><strong>{}</strong><span>Rows</span></div><div><strong>{}</strong><span>Tokens</span></div><div><strong>{}</strong><span>Requests</span></div><div><strong>{}</strong><span>Cost</span></div></section>",
        escape_html(query.provider.as_deref().unwrap_or("")),
        escape_html(query.model.as_deref().unwrap_or("")),
        escape_html(query.feature.as_deref().unwrap_or("")),
        escape_html(query.user_id.as_deref().unwrap_or("")),
        escape_html(query.start_date.as_deref().unwrap_or("")),
        escape_html(query.end_date.as_deref().unwrap_or("")),
        snapshot.totals.row_count,
        snapshot.totals.total_tokens,
        snapshot.totals.request_count,
        render_usage_cost(
            snapshot.totals.cost_usd,
            snapshot.totals.known_cost_usd,
            snapshot.totals.unpriced_call_count
        )
    );
    html.push_str("<h2>Daily</h2><table><thead><tr><th>Date</th><th>Rows</th><th>Tokens</th><th>Requests</th><th>Resources</th><th>Cost</th></tr></thead><tbody>");
    for row in &snapshot.daily {
        let _ = write!(
            html,
            "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>",
            row.usage_day,
            row.row_count,
            row.total_tokens,
            row.request_count,
            row.resource_count,
            render_usage_cost(row.cost_usd, row.known_cost_usd, row.unpriced_call_count)
        );
    }
    html.push_str("</tbody></table><h2>Recent calls</h2><table><thead><tr><th>Time</th><th>Provider</th><th>Model</th><th>Feature</th><th>User</th><th>Tokens</th><th>Cost</th></tr></thead><tbody>");
    for row in &snapshot.rows {
        let user = row
            .user_name
            .as_deref()
            .or(row.user_email.as_deref())
            .map_or_else(
                || {
                    row.user_id
                        .map_or_else(|| "-".to_owned(), |id| id.to_string())
                },
                std::borrow::ToOwned::to_owned,
            );
        let _ = write!(
            html,
            "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>",
            row.created_at.to_rfc3339(),
            escape_html(&row.provider),
            escape_html(&row.model),
            escape_html(&row.feature),
            escape_html(&user),
            row.total_tokens.unwrap_or_default(),
            row.cost_usd
                .map_or_else(|| "Unknown".to_owned(), |cost| format!("${cost:.6}"))
        );
    }
    html.push_str("</tbody></table></main></body></html>");
    html
}

fn render_usage_cost(cost: Option<f64>, known_cost: f64, unpriced_calls: i64) -> String {
    cost.map_or_else(
        || format!("Unknown (${known_cost:.6} known; {unpriced_calls} unpriced calls)"),
        |cost| format!("${cost:.6}"),
    )
}

fn document_start(title: &str) -> String {
    format!(
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>{}</title><style>body{{font:15px system-ui;margin:0;background:#f7f7f5;color:#191919}}main{{max-width:1150px;margin:3rem auto;padding:0 1rem}}nav{{margin-bottom:2rem}}table{{border-collapse:collapse;width:100%;background:white;margin-bottom:2rem}}th,td{{text-align:left;padding:.65rem;border:1px solid #ddd;vertical-align:top}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1rem;margin:1.5rem 0}}.cards div{{background:white;border:1px solid #ddd;padding:1rem}}.cards strong,.cards span{{display:block}}.cards strong{{font-size:1.6rem}}article{{background:white;border:1px solid #ddd;padding:1rem;margin:1rem 0}}blockquote{{white-space:pre-wrap}}input,button{{font:inherit;padding:.55rem;margin:.25rem}}</style></head><body>",
        escape_html(title)
    )
}

#[cfg(test)]
mod tests {
    use super::{dashboard_cutoff, parse_date};

    #[test]
    fn normalizes_dashboard_ranges_and_usage_dates() {
        assert_eq!(dashboard_cutoff(Some("bogus")).0, "24h");
        assert!(dashboard_cutoff(Some("all")).1.is_none());
        let end = parse_date(Some("2026-08-30"), true).unwrap();
        assert_eq!(end.date_naive().to_string(), "2026-08-30");
        assert_eq!(end.time().hour(), 23);
    }

    use chrono::Timelike as _;
}

#[cfg(test)]
mod usage_tests {
    use super::*;

    #[test]
    fn usage_page_labels_missing_costs_without_hiding_known_subtotals() {
        let snapshot = AdminVendorUsageSnapshot {
            rows: Vec::new(),
            daily: Vec::new(),
            totals: newsly_db::AdminVendorUsageTotals {
                row_count: 2,
                attributed_row_count: 0,
                input_tokens: 0,
                output_tokens: 0,
                total_tokens: 0,
                request_count: 2,
                resource_count: 0,
                cost_usd: None,
                known_cost_usd: 0.25,
                unpriced_call_count: 1,
            },
        };
        let query: VendorUsageQuery = serde_json::from_value(serde_json::json!({})).unwrap();
        let html = render_vendor_usage(&snapshot, &query);
        assert!(html.contains("Unknown ($0.250000 known; 1 unpriced calls)"));
        assert!(!html.contains("<strong>$0.000000</strong>"));
        assert_eq!(render_usage_cost(Some(0.0), 0.0, 0), "$0.000000");
    }
}
