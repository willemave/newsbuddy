use std::fmt::Write as _;

use axum::extract::rejection::PathRejection;
use axum::extract::{Form, FromRequest, OriginalUri, Path, State};
use axum::http::header::{COOKIE, LOCATION};
use axum::http::{HeaderMap, HeaderValue, StatusCode};
use axum::response::{Html, IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Router, body::Body, http::Request};
use chrono::{DateTime, Timelike as _, Utc};
use newsly_contracts::{ApiKeyCreateResponse, ApiKeySummaryResponse};
use newsly_db::{
    ApiKeySummaryProjection, ApiKeyTargetUser, create_api_key, ensure_system_admin_user,
    list_api_key_target_users, list_api_keys, revoke_api_key,
};
use secrecy::ExposeSecret as _;
use serde::Deserialize;
use utoipa::ToSchema;

use crate::error::ApiError;
use crate::gateway::RouteOwnershipStamp;
use crate::write_support::{internal_error, not_found, require_operation, verify_stamp};
use crate::{AppState, request_id_from_headers};

const ADMIN_SESSION_COOKIE: &str = "admin_session";
const PAGE_OPERATION_ID: &str = "adminApiKeysPage";
const CREATE_OPERATION_ID: &str = "adminApiKeysCreate";
const REVOKE_OPERATION_ID: &str = "adminApiKeysRevoke";
const PAGE_HEADER: &str = include_str!("templates/admin_api_keys_header.html");

#[derive(Debug, Deserialize, ToSchema)]
#[schema(as = Body_adminApiKeysCreate)]
struct CreateApiKeyForm {
    user_id: i64,
}

pub(super) fn router() -> Router<AppState> {
    Router::new()
        .route("/admin/api-keys", get(api_keys_page))
        .route("/admin/api-keys/create", post(create_key))
        .route("/admin/api-keys/{api_key_id}/revoke", post(revoke_key))
}

#[utoipa::path(
    get,
    path = "/admin/api-keys",
    operation_id = "adminApiKeysPage",
    tag = "admin",
    responses(
        (status = 200, description = "Admin API-key page", content_type = "text/html", body = String),
        (status = 303, description = "Admin login required"),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn api_keys_page(
    State(state): State<AppState>,
    OriginalUri(uri): OriginalUri,
    headers: HeaderMap,
    axum::extract::Extension(stamp): axum::extract::Extension<RouteOwnershipStamp>,
) -> Result<Response, ApiError> {
    if !has_valid_admin_session(&state, &headers) {
        return Ok(admin_login_redirect(uri.path()));
    }
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, PAGE_OPERATION_ID, &request_id)?;

    // The established admin boundary lazily creates its attribution identity.
    // Keep that compatibility write short and fenced even though this is a GET.
    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    ensure_system_admin_user(&mut transaction)
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;

    let (users, keys) = tokio::try_join!(
        list_api_key_target_users(state.database.pool()),
        list_api_keys(state.database.pool(), None),
    )
    .map_err(|error| internal_error(error, &request_id))?;
    Ok(Html(render_api_keys_page(&users, &keys, None)).into_response())
}

#[utoipa::path(
    post,
    path = "/admin/api-keys/create",
    operation_id = "adminApiKeysCreate",
    tag = "admin",
    request_body(content = CreateApiKeyForm, content_type = "application/x-www-form-urlencoded"),
    responses(
        (status = 200, description = "API key created and revealed once", content_type = "text/html", body = String),
        (status = 303, description = "Admin login required"),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Invalid form", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn create_key(
    State(state): State<AppState>,
    request: Request<Body>,
) -> Result<Response, ApiError> {
    let headers = request.headers().clone();
    let path = request.uri().path().to_owned();
    if !has_valid_admin_session(&state, &headers) {
        return Ok(admin_login_redirect(&path));
    }
    let request_id = request_id_from_headers(&headers);
    let stamp = request
        .extensions()
        .get::<RouteOwnershipStamp>()
        .cloned()
        .ok_or_else(|| crate::write_support::stale_owner(&request_id))?;
    require_operation(&stamp, CREATE_OPERATION_ID, &request_id)?;
    let Form(form) = Form::<CreateApiKeyForm>::from_request(request, &state)
        .await
        .map_err(|rejection| form_validation_error(rejection.body_text(), &request_id))?;
    let user_id = form.user_id;

    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    let admin_user_id = ensure_system_admin_user(&mut transaction)
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    let created = create_api_key(&mut transaction, user_id, Some(admin_user_id))
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;

    let raw_key = created.raw_key.expose_secret().to_owned();
    let created_response = ApiKeyCreateResponse {
        api_key: raw_key.clone(),
        key: raw_key,
        key_prefix: created.record.key_prefix.clone(),
        record: summary_response(created.record),
    };
    let (users, keys) = tokio::try_join!(
        list_api_key_target_users(state.database.pool()),
        list_api_keys(state.database.pool(), None),
    )
    .map_err(|error| internal_error(error, &request_id))?;
    Ok(Html(render_api_keys_page(&users, &keys, Some(&created_response))).into_response())
}

#[utoipa::path(
    post,
    path = "/admin/api-keys/{api_key_id}/revoke",
    operation_id = "adminApiKeysRevoke",
    tag = "admin",
    params(("api_key_id" = i64, Path, description = "API-key record id")),
    responses(
        (status = 303, description = "API key revoked or admin login required"),
        (status = 404, description = "API key not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Invalid path", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn revoke_key(
    State(state): State<AppState>,
    OriginalUri(uri): OriginalUri,
    headers: HeaderMap,
    axum::extract::Extension(stamp): axum::extract::Extension<RouteOwnershipStamp>,
    path: Result<Path<i64>, PathRejection>,
) -> Result<Response, ApiError> {
    if !has_valid_admin_session(&state, &headers) {
        return Ok(admin_login_redirect(uri.path()));
    }
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, REVOKE_OPERATION_ID, &request_id)?;
    let Path(api_key_id) =
        path.map_err(|rejection| form_validation_error(rejection.body_text(), &request_id))?;

    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    let record = revoke_api_key(&mut transaction, api_key_id)
        .await
        .map_err(|error| internal_error(error, &request_id))?
        .ok_or_else(|| not_found("API key", &request_id))?;
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    tracing::info!(api_key_id = record.id, "admin API key revoked");
    Ok(redirect("/admin/api-keys"))
}

pub(super) fn has_valid_admin_session(state: &AppState, headers: &HeaderMap) -> bool {
    admin_session_cookie(headers).is_some_and(|token| state.auth.is_valid_admin_session(token))
}

fn admin_session_cookie(headers: &HeaderMap) -> Option<&str> {
    headers
        .get_all(COOKIE)
        .iter()
        .filter_map(|header| header.to_str().ok())
        .flat_map(|header| header.split(';'))
        .filter_map(|cookie| cookie.trim().split_once('='))
        .filter_map(|(name, value)| {
            (name.trim() == ADMIN_SESSION_COOKIE)
                .then(|| value.trim().trim_matches('"'))
                .filter(|value| !value.is_empty())
        })
        .next_back()
}

pub(super) fn admin_login_redirect(path: &str) -> Response {
    redirect(&format!(
        "/auth/admin/login?next={}",
        percent_encode_path(path)
    ))
}

pub(super) fn redirect(location: &str) -> Response {
    let mut response = StatusCode::SEE_OTHER.into_response();
    let location = HeaderValue::from_str(location).expect("internal redirect location is valid");
    response.headers_mut().insert(LOCATION, location);
    response
}

fn percent_encode_path(path: &str) -> String {
    const HEX: &[u8; 16] = b"0123456789ABCDEF";
    let mut encoded = String::with_capacity(path.len());
    for byte in path.bytes() {
        if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'.' | b'_' | b'~') {
            encoded.push(char::from(byte));
        } else {
            encoded.push('%');
            encoded.push(char::from(HEX[usize::from(byte >> 4)]));
            encoded.push(char::from(HEX[usize::from(byte & 0x0f)]));
        }
    }
    encoded
}

fn form_validation_error(message: impl Into<String>, request_id: &str) -> ApiError {
    ApiError::new(
        StatusCode::UNPROCESSABLE_ENTITY,
        "validation_error",
        "Request validation failed",
        request_id.to_owned(),
    )
    .with_details(
        serde_json::json!({"errors": [{"message": message.into()}]})
            .as_object()
            .expect("validation details are an object")
            .clone(),
    )
}

fn summary_response(record: ApiKeySummaryProjection) -> ApiKeySummaryResponse {
    ApiKeySummaryResponse {
        id: record.id,
        user_id: record.user_id,
        key_prefix: record.key_prefix,
        created_at: record.created_at,
        revoked_at: record.revoked_at,
        last_used_at: record.last_used_at,
        created_by_admin_user_id: record.created_by_admin_user_id,
    }
}

fn render_api_keys_page(
    users: &[ApiKeyTargetUser],
    keys: &[ApiKeySummaryProjection],
    created: Option<&ApiKeyCreateResponse>,
) -> String {
    let mut html = String::from(PAGE_HEADER);
    if let Some(created) = created {
        let _ = write!(
            html,
            "  <div class=\"rounded border border-green-500 p-4\">\n    <h2>New Key</h2>\n    <p>Copy this key now. It will not be shown again.</p>\n    <pre>{}</pre>\n  </div>\n",
            escape_html(&created.api_key)
        );
    }
    html.push_str(
        "  <form method=\"post\" action=\"/admin/api-keys/create\" class=\"space-y-3\">\n    <label for=\"user_id\">Target user</label>\n    <select id=\"user_id\" name=\"user_id\">\n",
    );
    for user in users {
        let _ = writeln!(
            html,
            "      <option value=\"{}\">{} ({})</option>",
            user.id,
            escape_html(&user.email),
            user.id
        );
    }
    html.push_str(
        r#"    </select>
    <button type="submit">Create API Key</button>
  </form>

  <table>
    <thead>
      <tr>
        <th>ID</th><th>User</th><th>Prefix</th><th>Created</th><th>Last Used</th><th>Revoked</th><th></th>
      </tr>
    </thead>
    <tbody>
"#,
    );
    for key in keys {
        let last_used = key
            .last_used_at
            .as_ref()
            .map_or_else(|| "-".to_owned(), format_python_datetime);
        let revoked = key
            .revoked_at
            .as_ref()
            .map_or_else(|| "-".to_owned(), format_python_datetime);
        let _ = write!(
            html,
            "      <tr>\n        <td>{}</td><td>{}</td><td><code>{}</code></td><td>{}</td><td>{}</td><td>{}</td><td>",
            key.id,
            key.user_id,
            escape_html(&key.key_prefix),
            format_python_datetime(&key.created_at),
            last_used,
            revoked,
        );
        if key.revoked_at.is_none() {
            let _ = write!(
                html,
                "<form method=\"post\" action=\"/admin/api-keys/{}/revoke\"><button type=\"submit\">Revoke</button></form>",
                key.id
            );
        } else {
            html.push_str("revoked");
        }
        html.push_str("</td>\n      </tr>\n");
    }
    html.push_str(
        r#"    </tbody>
  </table>
</section>
  </main>
  <footer class="border-t border-gray-200 mt-auto"><div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-4"><p class="text-xs text-gray-400 text-center">Newsbuddy Admin</p></div></footer>
</body>
</html>
"#,
    );
    html
}

pub(super) fn escape_html(value: &str) -> String {
    let mut escaped = String::with_capacity(value.len());
    for character in value.chars() {
        match character {
            '&' => escaped.push_str("&amp;"),
            '<' => escaped.push_str("&lt;"),
            '>' => escaped.push_str("&gt;"),
            '"' => escaped.push_str("&quot;"),
            '\'' => escaped.push_str("&#x27;"),
            _ => escaped.push(character),
        }
    }
    escaped
}

fn format_python_datetime(value: &DateTime<Utc>) -> String {
    if value.nanosecond() == 0 {
        value.format("%Y-%m-%d %H:%M:%S").to_string()
    } else {
        value.format("%Y-%m-%d %H:%M:%S%.6f").to_string()
    }
}

#[cfg(test)]
mod tests {
    use axum::http::{HeaderMap, HeaderValue, header::COOKIE};

    use super::{admin_session_cookie, escape_html, percent_encode_path};

    #[test]
    fn admin_cookie_and_redirect_encoding_match_python_boundary() {
        let mut headers = HeaderMap::new();
        headers.insert(
            COOKIE,
            HeaderValue::from_static("other=x; admin_session=header.payload.signature"),
        );
        assert_eq!(
            admin_session_cookie(&headers),
            Some("header.payload.signature")
        );
        assert_eq!(
            percent_encode_path("/admin/api-keys/42/revoke"),
            "%2Fadmin%2Fapi-keys%2F42%2Frevoke"
        );
    }

    #[test]
    fn rendered_values_are_html_escaped() {
        assert_eq!(
            escape_html("<x a='b'>&\""),
            "&lt;x a=&#x27;b&#x27;&gt;&amp;&quot;"
        );
    }
}
