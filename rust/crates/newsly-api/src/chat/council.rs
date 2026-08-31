use std::collections::HashSet;
use std::time::Duration;

use axum::extract::rejection::{JsonRejection, PathRejection};
use axum::extract::{Extension, Path, State};
use axum::http::{HeaderMap, StatusCode};
use axum::routing::post;
use axum::{Json, Router};
use newsly_contracts::{
    ChatSessionDetailDto, CouncilPersonaConfig, CouncilRetryRequest, CouncilSelectRequest,
    CouncilStartRequest,
};
use newsly_db::{
    ChatRecordAccess, CouncilPersonaSeed, CouncilSelectOutcome, CouncilStageOutcome,
    SelectCouncilBranchInput, StageCouncilRetryInput, StageCouncilStartInput, StagedCouncilWork,
    find_user_profile, get_chat_message_status, get_chat_session_detail,
    prepare_agent_data_sync_dedupe_key, select_council_branch, stage_council_retry,
    stage_council_start,
};
use newsly_queue::{EnqueueRequest, QueueKernel, TaskType};
use serde_json::json;
use sha2::{Digest, Sha256};
use tokio::time::{Instant, sleep};

use super::{
    begin_write, enqueue_chat_turn, forbidden_session, inactive_user, not_found, positive_path,
    queue_error, validation_error,
};
use crate::auth::AuthenticatedUser;
use crate::encoding::hex_encode;
use crate::error::ApiError;
use crate::gateway::RouteOwnershipStamp;
use crate::write_support::{
    bad_request, decode_json, internal_error, require_operation, verify_stamp,
};
use crate::{AppState, request_id_from_headers};

const START_COUNCIL_OPERATION_ID: &str = "startContentChatSessionsCouncilMode";
const SELECT_COUNCIL_OPERATION_ID: &str = "selectContentChatSessionsCouncilModeBranch";
const RETRY_COUNCIL_OPERATION_ID: &str = "retryContentChatSessionsCouncilModeBranch";
const COUNCIL_WAIT_TIMEOUT: Duration = Duration::from_secs(300);
const COUNCIL_POLL_INTERVAL: Duration = Duration::from_millis(250);
const COUNCIL_PROMPT: &str = include_str!("../../../../assets/prompts/chat/council.md");

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

#[utoipa::path(
    post,
    path = "/api/content/chat/sessions/{session_id}/council/start",
    operation_id = "startContentChatSessionsCouncilMode",
    tag = "content",
    params(("session_id" = i64, Path, description = "Chat session ID", minimum = 1)),
    request_body = CouncilStartRequest,
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = ChatSessionDetailDto),
        (status = 400, description = "Council mode cannot be started", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 403, description = "Not authorized", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Session not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Archived session or stale owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(crate) async fn start_council_mode(
    State(state): State<AppState>,
    headers: HeaderMap,
    path: Result<Path<i64>, PathRejection>,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    payload: Result<Json<CouncilStartRequest>, JsonRejection>,
) -> Result<Json<ChatSessionDetailDto>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, START_COUNCIL_OPERATION_ID, &request_id)?;
    let session_id = positive_path(path, "session_id", &request_id)?;
    let Json(payload) = decode_json(payload, &request_id)?;
    validate_prompt(&payload.message, &request_id)?;
    let profile = find_user_profile(state.database.pool(), current_user.id)
        .await
        .map_err(|error| internal_error(error, &request_id))?
        .ok_or_else(|| inactive_user(&request_id))?;
    let personas = persona_seeds(profile.council_personas.as_ref(), &request_id)?;

    let mut transaction = begin_write(&state, &request_id).await?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    let outcome = stage_council_start(
        &mut transaction,
        &StageCouncilStartInput {
            user_id: current_user.id,
            session_id,
            user_prompt: &payload.message,
            personas: &personas,
        },
    )
    .await
    .map_err(|error| internal_error(error, &request_id))?;
    let staged = staged_outcome(outcome, &request_id)?;
    enqueue_council_turns(
        &state,
        &mut transaction,
        current_user.id,
        &staged,
        &request_id,
    )
    .await?;
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    wait_for_council_settlement(
        &state,
        current_user.id,
        staged.parent_message_id,
        &request_id,
    )
    .await?;
    session_detail(&state, current_user.id, session_id, &request_id).await
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
        (status = 200, description = "Successful Response", body = ChatSessionDetailDto),
        (status = 400, description = "Invalid council branch", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 403, description = "Not authorized", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Session not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Archived session or stale owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(crate) async fn select_council_mode_branch(
    State(state): State<AppState>,
    headers: HeaderMap,
    path: Result<Path<i64>, PathRejection>,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    payload: Result<Json<CouncilSelectRequest>, JsonRejection>,
) -> Result<Json<ChatSessionDetailDto>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, SELECT_COUNCIL_OPERATION_ID, &request_id)?;
    let session_id = positive_path(path, "session_id", &request_id)?;
    let Json(payload) = decode_json(payload, &request_id)?;
    validate_child_id(payload.child_session_id, &request_id)?;
    let mut transaction = begin_write(&state, &request_id).await?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    let outcome = select_council_branch(
        &mut transaction,
        SelectCouncilBranchInput {
            user_id: current_user.id,
            session_id,
            child_session_id: payload.child_session_id,
        },
    )
    .await
    .map_err(|error| internal_error(error, &request_id))?;
    let changed = select_outcome(outcome, &request_id)?;
    if changed {
        enqueue_session_sync(
            &state,
            &mut transaction,
            current_user.id,
            session_id,
            &request_id,
        )
        .await?;
    }
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    session_detail(&state, current_user.id, session_id, &request_id).await
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
        (status = 200, description = "Successful Response", body = ChatSessionDetailDto),
        (status = 400, description = "Invalid council branch", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 403, description = "Not authorized", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Session not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Archived session, active retry, or stale owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(crate) async fn retry_council_mode_branch(
    State(state): State<AppState>,
    headers: HeaderMap,
    path: Result<Path<i64>, PathRejection>,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    payload: Result<Json<CouncilRetryRequest>, JsonRejection>,
) -> Result<Json<ChatSessionDetailDto>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, RETRY_COUNCIL_OPERATION_ID, &request_id)?;
    let session_id = positive_path(path, "session_id", &request_id)?;
    let Json(payload) = decode_json(payload, &request_id)?;
    validate_child_id(payload.child_session_id, &request_id)?;
    let mut transaction = begin_write(&state, &request_id).await?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    let outcome = stage_council_retry(
        &mut transaction,
        StageCouncilRetryInput {
            user_id: current_user.id,
            session_id,
            child_session_id: payload.child_session_id,
        },
    )
    .await
    .map_err(|error| internal_error(error, &request_id))?;
    let staged = staged_outcome(outcome, &request_id)?;
    enqueue_council_turns(
        &state,
        &mut transaction,
        current_user.id,
        &staged,
        &request_id,
    )
    .await?;
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    wait_for_council_settlement(
        &state,
        current_user.id,
        staged.parent_message_id,
        &request_id,
    )
    .await?;
    session_detail(&state, current_user.id, session_id, &request_id).await
}

fn persona_seeds(
    raw: Option<&serde_json::Value>,
    request_id: &str,
) -> Result<Vec<CouncilPersonaSeed>, ApiError> {
    let personas = raw
        .cloned()
        .and_then(|value| serde_json::from_value::<Vec<CouncilPersonaConfig>>(value).ok())
        .unwrap_or_default();
    if !(2..=3).contains(&personas.len()) {
        return Err(bad_request(
            "Add at least two experts in Settings before using the council",
            request_id,
        ));
    }
    let impersonation_template = prompt_section(COUNCIL_PROMPT, "impersonation")
        .map_err(|error| internal_error(error, request_id))?;
    let response_style = prompt_section(COUNCIL_PROMPT, "response_style")
        .map_err(|error| internal_error(error, request_id))?;
    let mut normalized = Vec::with_capacity(personas.len());
    let mut ids = HashSet::new();
    for mut persona in personas {
        persona.id = String::from(persona.id.trim());
        persona.display_name = String::from(persona.display_name.trim());
        persona.instruction_prompt = persona.instruction_prompt.trim().to_owned();
        if persona.id.is_empty()
            || persona.id.chars().count() > 50
            || !ids.insert(persona.id.clone())
            || persona.display_name.is_empty()
            || persona.display_name.chars().count() > 80
            || persona.instruction_prompt.chars().count() > 1_500
            || !(0..=2).contains(&persona.sort_order)
        {
            return Err(bad_request(
                "Add at least two experts in Settings before using the council",
                request_id,
            ));
        }
        let impersonation_prompt = impersonation_template.replace("$name", &persona.display_name);
        normalized.push(CouncilPersonaSeed {
            id: persona.id,
            display_name: persona.display_name,
            context_suffix: format!("{impersonation_prompt}\n\n{response_style}"),
            impersonation_prompt,
            sort_order: persona.sort_order,
        });
    }
    normalized.sort_by_key(|persona| persona.sort_order);
    if normalized
        .iter()
        .enumerate()
        .any(|(index, persona)| usize::try_from(persona.sort_order) != Ok(index))
    {
        return Err(bad_request(
            "Add at least two experts in Settings before using the council",
            request_id,
        ));
    }
    Ok(normalized)
}

fn prompt_section<'a>(value: &'a str, name: &str) -> Result<&'a str, String> {
    let start_marker = format!("<!-- prompt-section: {name} -->");
    let end_marker = "<!-- /prompt-section -->";
    let start = value
        .find(&start_marker)
        .map(|offset| offset + start_marker.len())
        .ok_or_else(|| format!("council prompt section {name} is missing"))?;
    let end = value[start..]
        .find(end_marker)
        .map(|offset| start + offset)
        .ok_or_else(|| format!("council prompt section {name} is unterminated"))?;
    Ok(value[start..end].trim())
}

async fn enqueue_council_turns(
    state: &AppState,
    transaction: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    user_id: i64,
    staged: &StagedCouncilWork,
    request_id: &str,
) -> Result<(), ApiError> {
    for turn in &staged.turns {
        enqueue_chat_turn(state, transaction, user_id, turn, request_id).await?;
    }
    Ok(())
}

async fn enqueue_session_sync(
    state: &AppState,
    transaction: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    user_id: i64,
    session_id: i64,
    request_id: &str,
) -> Result<(), ApiError> {
    let payload = json!({
        "user_id": user_id,
        "content_ids": [],
        "news_item_ids": [],
        "chat_session_ids": [session_id],
        "briefing_dates": [],
    });
    let digest = Sha256::digest(
        serde_json::to_vec(&payload).map_err(|error| internal_error(error, request_id))?,
    );
    let base_key = format!(
        "agent-sync|user:{user_id}|payload:{}",
        &hex_encode(&digest)[..24]
    );
    let dedupe_key = prepare_agent_data_sync_dedupe_key(transaction, user_id, &base_key)
        .await
        .map_err(|error| internal_error(error, request_id))?;
    let mut request = EnqueueRequest::new(TaskType::SyncAgentData);
    request.payload = payload.as_object().cloned();
    request.owner_user_id = Some(user_id);
    request.dedupe = Some(true);
    request.dedupe_key = Some(dedupe_key);
    QueueKernel::new(state.database.pool().clone())
        .enqueue_many_in_transaction(transaction, vec![request])
        .await
        .map_err(|error| queue_error(error, request_id))?;
    Ok(())
}

async fn wait_for_council_settlement(
    state: &AppState,
    user_id: i64,
    message_id: i64,
    request_id: &str,
) -> Result<(), ApiError> {
    let deadline = Instant::now() + COUNCIL_WAIT_TIMEOUT;
    loop {
        match get_chat_message_status(state.database.pool(), user_id, message_id)
            .await
            .map_err(|error| internal_error(error, request_id))?
        {
            ChatRecordAccess::Found(status) if status.status == "processing" => {}
            ChatRecordAccess::Found(_) => return Ok(()),
            ChatRecordAccess::NotFound => {
                return Err(internal_error("council message disappeared", request_id));
            }
            ChatRecordAccess::Forbidden => return Err(forbidden_session(request_id)),
        }
        if Instant::now() >= deadline {
            tracing::warn!(
                message_id,
                "council request wait timed out; work remains durable"
            );
            return Ok(());
        }
        sleep(COUNCIL_POLL_INTERVAL).await;
    }
}

async fn session_detail(
    state: &AppState,
    user_id: i64,
    session_id: i64,
    request_id: &str,
) -> Result<Json<ChatSessionDetailDto>, ApiError> {
    match get_chat_session_detail(state.database.pool(), user_id, session_id)
        .await
        .map_err(|error| internal_error(error, request_id))?
    {
        ChatRecordAccess::Found(detail) => Ok(Json(
            super::presentation::detail(detail)
                .map_err(|error| internal_error(error, request_id))?,
        )),
        ChatRecordAccess::NotFound => Err(not_found("Session not found", request_id)),
        ChatRecordAccess::Forbidden => Err(forbidden_session(request_id)),
    }
}

fn staged_outcome(
    outcome: CouncilStageOutcome,
    request_id: &str,
) -> Result<StagedCouncilWork, ApiError> {
    match outcome {
        CouncilStageOutcome::Staged(staged) => Ok(staged),
        CouncilStageOutcome::NotFound | CouncilStageOutcome::Hidden => {
            Err(not_found("Session not found", request_id))
        }
        CouncilStageOutcome::Forbidden => Err(forbidden_session(request_id)),
        CouncilStageOutcome::Archived => Err(ApiError::new(
            StatusCode::CONFLICT,
            "archived_session",
            "Archived sessions cannot start or retry council mode",
            request_id.to_owned(),
        )),
        CouncilStageOutcome::AlreadyActive => Err(bad_request(
            "Council mode already started for this chat",
            request_id,
        )),
        CouncilStageOutcome::CouncilInactive => Err(bad_request(
            "Council mode is not active for this chat",
            request_id,
        )),
        CouncilStageOutcome::UnsupportedSessionType => Err(bad_request(
            "Council mode is unavailable for this chat type",
            request_id,
        )),
        CouncilStageOutcome::InvalidPersonas => Err(bad_request(
            "Add at least two experts in Settings before using the council",
            request_id,
        )),
        CouncilStageOutcome::BranchNotFound => {
            Err(bad_request("Council branch not found", request_id))
        }
        CouncilStageOutcome::CandidateNotFound => {
            Err(bad_request("Council candidate not found", request_id))
        }
        CouncilStageOutcome::CouncilMessageNotFound => {
            Err(bad_request("Council message not found", request_id))
        }
        CouncilStageOutcome::AlreadyProcessing => Err(ApiError::new(
            StatusCode::CONFLICT,
            "council_branch_processing",
            "Council branch is already processing",
            request_id.to_owned(),
        )),
        CouncilStageOutcome::UserInactive => Err(inactive_user(request_id)),
    }
}

fn select_outcome(outcome: CouncilSelectOutcome, request_id: &str) -> Result<bool, ApiError> {
    match outcome {
        CouncilSelectOutcome::Applied => Ok(true),
        CouncilSelectOutcome::Unchanged => Ok(false),
        CouncilSelectOutcome::NotFound => Err(not_found("Session not found", request_id)),
        CouncilSelectOutcome::Forbidden => Err(forbidden_session(request_id)),
        CouncilSelectOutcome::Archived => Err(ApiError::new(
            StatusCode::CONFLICT,
            "archived_session",
            "Archived sessions cannot switch council branches",
            request_id.to_owned(),
        )),
        CouncilSelectOutcome::CouncilInactive => Err(bad_request(
            "Council mode is not active for this chat",
            request_id,
        )),
        CouncilSelectOutcome::BranchNotFound => {
            Err(bad_request("Council branch not found", request_id))
        }
        CouncilSelectOutcome::CandidateNotFound => {
            Err(bad_request("Council candidate not found", request_id))
        }
        CouncilSelectOutcome::CouncilMessageNotFound => {
            Err(bad_request("Council message not found", request_id))
        }
    }
}

fn validate_prompt(message: &str, request_id: &str) -> Result<(), ApiError> {
    let length = message.chars().count();
    if (1..=10_000).contains(&length) {
        Ok(())
    } else {
        Err(validation_error(
            "message must contain between 1 and 10000 characters",
            request_id,
        ))
    }
}

fn validate_child_id(child_session_id: i64, request_id: &str) -> Result<(), ApiError> {
    if child_session_id > 0 {
        Ok(())
    } else {
        Err(validation_error(
            "child_session_id must be greater than zero",
            request_id,
        ))
    }
}
