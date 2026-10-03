use axum::Router;
use axum::http::{HeaderMap, StatusCode};
use axum::routing::post;
use newsly_contracts::{CouncilRetryRequest, CouncilSelectRequest, CouncilStartRequest};

use crate::auth::AuthenticatedUser;
use crate::error::ApiError;
use crate::{AppState, request_id_from_headers};

const FEATURE_REMOVED_MESSAGE: &str =
    "This feature has been removed. Continue with a regular chat.";

#[allow(deprecated)]
pub(super) fn router() -> Router<AppState> {
    Router::new()
        .route(
            "/api/content/chat/sessions/{session_id}/council/start",
            post(start_council_mode),
        )
        .route(
            "/api/content/chat/sessions/{session_id}/council/select",
            post(select_council_mode_branch),
        )
        .route(
            "/api/content/chat/sessions/{session_id}/council/retry",
            post(retry_council_mode_branch),
        )
}

// Compatibility owner: the public Newsly API. Keep these routes and operation IDs until the
// minimum supported client version no longer calls council endpoints, then remove this module and
// the matching manifest entries together.
#[utoipa::path(
    post,
    path = "/api/content/chat/sessions/{session_id}/council/start",
    operation_id = "startContentChatSessionsCouncilMode",
    tag = "content",
    params(("session_id" = i64, Path, description = "Chat session ID", minimum = 1)),
    request_body = CouncilStartRequest,
    security(("HTTPBearer" = [])),
    responses(
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 410, description = "Council mode has been removed", body = newsly_contracts::ErrorEnvelope)
    )
)]
#[deprecated(note = "Council mode was removed; use regular chat")]
pub(crate) async fn start_council_mode(
    headers: HeaderMap,
    _current_user: AuthenticatedUser,
) -> ApiError {
    feature_removed(&headers)
}

#[utoipa::path(
    post,
    path = "/api/content/chat/sessions/{session_id}/council/select",
    operation_id = "selectContentChatSessionsCouncilModeBranch",
    tag = "content",
    params(("session_id" = i64, Path, description = "Chat session ID", minimum = 1)),
    request_body = CouncilSelectRequest,
    security(("HTTPBearer" = [])),
    responses(
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 410, description = "Council mode has been removed", body = newsly_contracts::ErrorEnvelope)
    )
)]
#[deprecated(note = "Council mode was removed; use regular chat")]
pub(crate) async fn select_council_mode_branch(
    headers: HeaderMap,
    _current_user: AuthenticatedUser,
) -> ApiError {
    feature_removed(&headers)
}

#[utoipa::path(
    post,
    path = "/api/content/chat/sessions/{session_id}/council/retry",
    operation_id = "retryContentChatSessionsCouncilModeBranch",
    tag = "content",
    params(("session_id" = i64, Path, description = "Chat session ID", minimum = 1)),
    request_body = CouncilRetryRequest,
    security(("HTTPBearer" = [])),
    responses(
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 410, description = "Council mode has been removed", body = newsly_contracts::ErrorEnvelope)
    )
)]
#[deprecated(note = "Council mode was removed; use regular chat")]
pub(crate) async fn retry_council_mode_branch(
    headers: HeaderMap,
    _current_user: AuthenticatedUser,
) -> ApiError {
    feature_removed(&headers)
}

fn feature_removed(headers: &HeaderMap) -> ApiError {
    ApiError::new(
        StatusCode::GONE,
        "feature_removed",
        FEATURE_REMOVED_MESSAGE,
        request_id_from_headers(headers),
    )
}

#[cfg(test)]
mod tests {
    use axum::http::{HeaderMap, StatusCode};
    use axum::response::IntoResponse;

    use super::{FEATURE_REMOVED_MESSAGE, feature_removed};

    #[tokio::test]
    async fn removed_response_is_stable_and_not_retryable() {
        let response = feature_removed(&HeaderMap::new()).into_response();
        assert_eq!(response.status(), StatusCode::GONE);

        let body = axum::body::to_bytes(response.into_body(), usize::MAX)
            .await
            .expect("error response body");
        let envelope: newsly_contracts::ErrorEnvelope =
            serde_json::from_slice(&body).expect("error envelope");
        assert_eq!(envelope.code, "feature_removed");
        assert_eq!(envelope.message, FEATURE_REMOVED_MESSAGE);
        assert!(!envelope.retryable);
    }
}
