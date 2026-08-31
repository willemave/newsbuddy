use std::fmt::Write as _;

use axum::extract::rejection::JsonRejection;
use axum::extract::{Extension, OriginalUri, State};
use axum::http::HeaderMap;
use axum::response::{Html, IntoResponse, Response};
use axum::routing::get;
use axum::{Json, Router};
use newsly_contracts::{
    OnboardingAudioDiscoverRequest, OnboardingAudioLanePreview, OnboardingAudioLanePreviewResponse,
    OnboardingAudioLaneTarget,
};

use crate::admin_api_keys::{admin_login_redirect, escape_html, has_valid_admin_session};
use crate::error::ApiError;
use crate::gateway::RouteOwnershipStamp;
use crate::write_support::{decode_json, require_operation, verify_stamp};
use crate::{AppState, request_id_from_headers};

const PAGE_OPERATION_ID: &str = "onboardingLanePreviewPage";
const PREVIEW_OPERATION_ID: &str = "onboardingLanePreview";

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
    Extension(stamp): Extension<RouteOwnershipStamp>,
) -> Result<Response, ApiError> {
    if !has_valid_admin_session(&state, &headers) {
        return Ok(admin_login_redirect(uri.path()));
    }
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, PAGE_OPERATION_ID, &request_id)?;
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
    Extension(stamp): Extension<RouteOwnershipStamp>,
    payload: Result<Json<OnboardingAudioDiscoverRequest>, JsonRejection>,
) -> Result<Response, ApiError> {
    if !has_valid_admin_session(&state, &headers) {
        return Ok(admin_login_redirect(uri.path()));
    }
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, PREVIEW_OPERATION_ID, &request_id)?;
    let Json(payload) = decode_json(payload, &request_id)?;

    // This endpoint makes an external LLM call, so fence ownership in a short transaction and
    // release the connection before the provider request begins.
    let mut transaction = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| crate::write_support::internal_error(error, &request_id))?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    transaction
        .commit()
        .await
        .map_err(|error| crate::write_support::internal_error(error, &request_id))?;

    let (plan, used_fallback, fallback_reason) = state
        .onboarding
        .build_audio_plan_with_metadata(payload.transcript.trim(), payload.locale.as_deref())
        .await;
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
    let mut html = admin_document_start("Onboarding Lane Preview");
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

fn admin_document_start(title: &str) -> String {
    let mut html = String::new();
    let _ = write!(
        html,
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>{}</title><style>body{{font:16px system-ui;margin:0;background:#f7f7f5;color:#191919}}main{{max-width:900px;margin:3rem auto;padding:0 1rem}}label{{display:block;margin-top:1rem;font-weight:600}}input,textarea,button{{font:inherit;padding:.7rem;margin-top:.35rem;box-sizing:border-box}}input,textarea{{width:100%}}button{{cursor:pointer}}pre{{white-space:pre-wrap;background:white;padding:1rem;border:1px solid #ddd}}</style></head><body>",
        escape_html(title)
    );
    html
}
