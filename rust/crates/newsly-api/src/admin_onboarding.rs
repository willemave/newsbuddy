use axum::extract::rejection::JsonRejection;
use axum::extract::{OriginalUri, State};
use axum::http::HeaderMap;
use axum::response::{Html, IntoResponse, Response};
use axum::routing::get;
use axum::{Json, Router};
use newsly_contracts::{
    OnboardingAudioDiscoverRequest, OnboardingAudioLanePreview, OnboardingAudioLanePreviewResponse,
    OnboardingAudioLaneTarget,
};

use crate::admin_api_keys::{admin_login_redirect, has_valid_admin_session};
use crate::error::ApiError;
use crate::gateway::RouteOwnershipStamp;
use crate::model_usage::onboarding_events_with_usage;
use crate::write_support::decode_json;
use crate::{AppState, request_id_from_headers};

pub(super) fn router() -> Router<AppState> {
    Router::new().route(
        "/admin/onboarding/lane-preview",
        get(preview_page).post(preview),
    )
}

#[utoipa::path(
    get,
    path = "/admin/onboarding/lane-preview",
    operation_id = "onboardingLanePreviewPage",
    tag = "admin",
    responses(
        (status = 200, description = "Onboarding lane preview page", content_type = "text/html", body = String),
        (status = 303, description = "Admin login required"),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn preview_page(
    State(state): State<AppState>,
    OriginalUri(uri): OriginalUri,
    headers: HeaderMap,
    _stamp: RouteOwnershipStamp,
) -> Result<Response, ApiError> {
    if !has_valid_admin_session(&state, &headers) {
        return Ok(admin_login_redirect(uri.path()));
    }
    Ok(Html(render_preview_page()).into_response())
}

#[utoipa::path(
    post,
    path = "/admin/onboarding/lane-preview",
    operation_id = "onboardingLanePreview",
    tag = "admin",
    request_body = OnboardingAudioDiscoverRequest,
    responses(
        (status = 200, description = "Generated onboarding lane preview", body = OnboardingAudioLanePreviewResponse),
        (status = 303, description = "Admin login required"),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn preview(
    State(state): State<AppState>,
    OriginalUri(uri): OriginalUri,
    headers: HeaderMap,
    stamp: RouteOwnershipStamp,
    payload: Result<Json<OnboardingAudioDiscoverRequest>, JsonRejection>,
) -> Result<Response, ApiError> {
    if !has_valid_admin_session(&state, &headers) {
        return Ok(admin_login_redirect(uri.path()));
    }
    let request_id = request_id_from_headers(&headers);
    let Json(payload) = decode_json(payload, &request_id)?;

    crate::write_support::verify_route_now(state.database.pool(), &stamp, &request_id).await?;

    let (events, usage) =
        onboarding_events_with_usage(&state, None, "onboarding.admin_audio_plan_preview");
    let (plan, used_fallback, fallback_reason) = state
        .onboarding
        .build_audio_plan_with_metadata(
            payload.transcript.trim(),
            payload.locale.as_deref(),
            events,
        )
        .await;
    usage.finish().await;
    let response = OnboardingAudioLanePreviewResponse {
        topic_summary: plan.topic_summary,
        inferred_topics: plan.inferred_topics,
        lanes: plan
            .lanes
            .into_iter()
            .map(|lane| OnboardingAudioLanePreview {
                name: lane.name,
                goal: lane.goal,
                target: match lane.target {
                    newsly_providers::OnboardingLaneTarget::Feeds => {
                        OnboardingAudioLaneTarget::Feeds
                    }
                    newsly_providers::OnboardingLaneTarget::Podcasts => {
                        OnboardingAudioLaneTarget::Podcasts
                    }
                    newsly_providers::OnboardingLaneTarget::Reddit => {
                        OnboardingAudioLaneTarget::Reddit
                    }
                },
                queries: lane.queries,
                include_social: false,
                exa_results_per_query: 0,
            })
            .collect(),
        used_fallback,
        fallback_reason,
    };
    Ok(Json(response).into_response())
}

fn render_preview_page() -> String {
    let mut html = crate::admin_shell::admin_shell("Onboarding Lane Preview");
    html.push_str(
        r#"<main><h1>Onboarding Lane Preview</h1>
<p>Generate the same discovery plan used by native onboarding without creating a run.</p>
<form id="preview-form">
<label for="locale">Locale</label><input id="locale" name="locale" value="en-US">
<label for="transcript">Transcript</label><textarea id="transcript" name="transcript" rows="12" required></textarea>
<button type="submit">Generate preview</button></form><pre id="result"></pre>
<script>
document.getElementById('preview-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const transcript = document.getElementById('transcript').value;
  const locale = document.getElementById('locale').value || null;
  const response = await fetch('/admin/onboarding/lane-preview', {
    method: 'POST', headers: {'content-type': 'application/json'},
    body: JSON.stringify({transcript, locale})
  });
  document.getElementById('result').textContent = JSON.stringify(await response.json(), null, 2);
});
</script></main>"#,
    );
    html.push_str("</body></html>");
    html
}
