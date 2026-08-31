use axum::extract::rejection::JsonRejection;
use axum::extract::{Extension, State};
use axum::http::header::{CACHE_CONTROL, REFERRER_POLICY, SET_COOKIE, X_CONTENT_TYPE_OPTIONS};
use axum::http::{HeaderMap, HeaderValue, StatusCode};
use axum::response::{Html, IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use chrono::Utc;
use newsly_contracts::{AdminLoginRequest, AdminLoginResponse};

use crate::error::ApiError;
use crate::gateway::RouteOwnershipStamp;
use crate::write_support::{decode_json, internal_error, require_operation};
use crate::{AppState, request_id_from_headers};

const LOGIN_PAGE_OPERATION_ID: &str = "adminLoginPage";
const LOGIN_OPERATION_ID: &str = "adminLogin";
const LOGOUT_OPERATION_ID: &str = "adminLogout";
const ADMIN_LOGIN_PAGE: &str = include_str!("../templates/admin_login.html");

pub(super) fn router() -> Router<AppState> {
    Router::new()
        .route("/auth/admin/login", get(login_page).post(login))
        .route("/auth/admin/logout", post(logout))
}

#[utoipa::path(
    get,
    path = "/auth/admin/login",
    operation_id = "adminLoginPage",
    tag = "auth",
    responses(
        (status = 200, description = "Admin login page", content_type = "text/html", body = String),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn login_page(
    headers: HeaderMap,
    Extension(stamp): Extension<RouteOwnershipStamp>,
) -> Result<Response, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, LOGIN_PAGE_OPERATION_ID, &request_id)?;
    let mut response = Html(ADMIN_LOGIN_PAGE).into_response();
    response
        .headers_mut()
        .insert(CACHE_CONTROL, HeaderValue::from_static("no-store"));
    response
        .headers_mut()
        .insert(REFERRER_POLICY, HeaderValue::from_static("no-referrer"));
    response
        .headers_mut()
        .insert(X_CONTENT_TYPE_OPTIONS, HeaderValue::from_static("nosniff"));
    response.headers_mut().insert(
        "x-robots-tag",
        HeaderValue::from_static("noindex, nofollow, noarchive"),
    );
    Ok(response)
}

#[utoipa::path(
    post,
    path = "/auth/admin/login",
    operation_id = "adminLogin",
    tag = "auth",
    request_body = AdminLoginRequest,
    responses(
        (status = 200, description = "Admin session created", body = AdminLoginResponse),
        (status = 401, description = "Invalid admin password", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn login(
    State(state): State<AppState>,
    headers: HeaderMap,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    payload: Result<Json<AdminLoginRequest>, JsonRejection>,
) -> Result<Response, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, LOGIN_OPERATION_ID, &request_id)?;
    let Json(payload) = decode_json(payload, &request_id)?;
    if !state.auth.verify_admin_password(&payload.password) {
        return Err(ApiError::new(
            StatusCode::UNAUTHORIZED,
            "authentication_required",
            "Invalid admin password",
            request_id,
        ));
    }

    let token = state
        .auth
        .issue_admin_session(Utc::now())
        .map_err(|error| internal_error(error, &request_id))?;
    let secure = if state.secure_admin_cookie {
        "; Secure"
    } else {
        ""
    };
    let cookie = format!(
        "admin_session={token}; HttpOnly; Max-Age={}; Path=/; SameSite=lax{secure}",
        state.auth.admin_session_max_age_seconds()
    );
    let mut response = Json(AdminLoginResponse {
        message: "Logged in as admin".to_owned(),
    })
    .into_response();
    response.headers_mut().insert(
        SET_COOKIE,
        HeaderValue::from_str(&cookie).map_err(|error| internal_error(error, &request_id))?,
    );
    Ok(response)
}

#[utoipa::path(
    post,
    path = "/auth/admin/logout",
    operation_id = "adminLogout",
    tag = "auth",
    responses(
        (status = 200, description = "Admin session cleared", body = AdminLoginResponse),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn logout(
    State(state): State<AppState>,
    headers: HeaderMap,
    Extension(stamp): Extension<RouteOwnershipStamp>,
) -> Result<Response, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, LOGOUT_OPERATION_ID, &request_id)?;
    let secure = if state.secure_admin_cookie {
        "; Secure"
    } else {
        ""
    };
    let cookie = format!("admin_session=; HttpOnly; Max-Age=0; Path=/; SameSite=lax{secure}");
    let mut response = Json(AdminLoginResponse {
        message: "Logged out".to_owned(),
    })
    .into_response();
    response.headers_mut().insert(
        SET_COOKIE,
        HeaderValue::from_str(&cookie).map_err(|error| internal_error(error, &request_id))?,
    );
    Ok(response)
}
