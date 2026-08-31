use axum::extract::rejection::QueryRejection;
use axum::extract::{Extension, Query, State};
use axum::http::{HeaderMap, StatusCode};
use axum::routing::{get, post};
use axum::{Json, Router};
use newsly_contracts::{
    OnboardingDiscoveryLaneStatus, OnboardingDiscoveryStatusResponse,
    OnboardingFastDiscoverResponse, OnboardingSuggestion, OnboardingSuggestionType,
    OnboardingTutorialResponse,
};
use newsly_db::{
    OnboardingDiscoveryStatusProjection, OnboardingSuggestionProjection,
    complete_onboarding_tutorial, find_onboarding_discovery_status,
};
use serde::Deserialize;

use crate::auth::AuthenticatedUser;
use crate::error::ApiError;
use crate::gateway::RouteOwnershipStamp;
use crate::write_support::{internal_error, not_found, require_operation, verify_stamp};
use crate::{AppState, request_id_from_headers};

const OPERATION_ID: &str = "tutorialOnboardingComplete";

pub(super) fn router() -> Router<AppState> {
    Router::new()
        .route("/api/onboarding/tutorial-complete", post(tutorial_complete))
        .route("/api/onboarding/discovery-status", get(discovery_status))
}

#[derive(Debug, Deserialize)]
pub(super) struct DiscoveryStatusQuery {
    run_id: i64,
}

#[utoipa::path(
    get,
    path = "/api/onboarding/discovery-status",
    operation_id = "onboardingDiscoveryStatus",
    tag = "onboarding",
    params(("run_id" = i64, Query, description = "Onboarding discovery run ID")),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Latest onboarding discovery status", body = OnboardingDiscoveryStatusResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Discovery run not found", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn discovery_status(
    State(state): State<AppState>,
    headers: HeaderMap,
    query: Result<Query<DiscoveryStatusQuery>, QueryRejection>,
    current_user: AuthenticatedUser,
) -> Result<Json<OnboardingDiscoveryStatusResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let Query(query) =
        query.map_err(|rejection| validation_error(rejection.body_text(), &request_id))?;
    if query.run_id <= 0 {
        return Err(validation_error(
            "run_id must be greater than zero",
            &request_id,
        ));
    }

    let status =
        find_onboarding_discovery_status(state.database.pool(), current_user.id, query.run_id)
            .await
            .map_err(|error| internal_error(error, &request_id))?
            .ok_or_else(|| not_found("Discovery run", &request_id))?;

    Ok(Json(present_discovery_status(status)))
}

#[utoipa::path(
    post,
    path = "/api/onboarding/tutorial-complete",
    operation_id = "tutorialOnboardingComplete",
    tag = "onboarding",
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Tutorial completion persisted", body = OnboardingTutorialResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "User not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn tutorial_complete(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
) -> Result<Json<OnboardingTutorialResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, OPERATION_ID, &request_id)?;

    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    let completed = complete_onboarding_tutorial(&mut transaction, current_user.id)
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    if !completed {
        return Err(not_found("User", &request_id));
    }
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;

    Ok(Json(OnboardingTutorialResponse {
        has_completed_new_user_tutorial: true,
    }))
}

fn present_discovery_status(
    status: OnboardingDiscoveryStatusProjection,
) -> OnboardingDiscoveryStatusResponse {
    let suggestions = status.suggestions.map(present_suggestions);
    OnboardingDiscoveryStatusResponse {
        run_id: status.run_id,
        run_status: status.run_status,
        topic_summary: status.topic_summary,
        inferred_topics: status.inferred_topics,
        lanes: status
            .lanes
            .into_iter()
            .map(|lane| OnboardingDiscoveryLaneStatus {
                name: lane.name,
                status: lane.status,
                completed_queries: lane.completed_queries,
                query_count: lane.query_count,
            })
            .collect(),
        suggestions,
        error_message: status.error_message,
    }
}

fn present_suggestions(
    suggestions: Vec<OnboardingSuggestionProjection>,
) -> OnboardingFastDiscoverResponse {
    let mut response = OnboardingFastDiscoverResponse {
        recommended_pods: Vec::new(),
        recommended_substacks: Vec::new(),
        recommended_subreddits: Vec::new(),
    };
    for suggestion in suggestions {
        let Ok(suggestion_type) =
            OnboardingSuggestionType::try_from(suggestion.suggestion_type.as_str())
        else {
            tracing::warn!(
                suggestion_type = suggestion.suggestion_type,
                "skipping legacy onboarding suggestion with unsupported type"
            );
            continue;
        };
        let item = OnboardingSuggestion {
            id: Some(suggestion.id),
            suggestion_type,
            title: suggestion.title,
            site_url: suggestion.site_url,
            feed_url: suggestion.feed_url,
            subreddit: suggestion.subreddit,
            rationale: suggestion.rationale,
            score: suggestion.score,
            is_default: false,
        };
        match suggestion_type {
            OnboardingSuggestionType::PodcastRss => response.recommended_pods.push(item),
            OnboardingSuggestionType::Reddit => response.recommended_subreddits.push(item),
            OnboardingSuggestionType::Substack | OnboardingSuggestionType::Atom => {
                response.recommended_substacks.push(item);
            }
        }
    }
    response
}

fn validation_error(message: impl Into<String>, request_id: &str) -> ApiError {
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
