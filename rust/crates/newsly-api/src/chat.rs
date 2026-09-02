pub(crate) mod council;
mod cursor;
mod presentation;

use axum::extract::rejection::{JsonRejection, PathRejection, QueryRejection};
use axum::extract::{Extension, Path, Query, State};
use axum::http::{HeaderMap, StatusCode};
use axum::routing::{get, post};
use axum::{Json, Router};
use newsly_contracts::{
    AssistantTurnRequest, AssistantTurnResponse, ChatSessionDetailDto, ChatSessionListResponse,
    ChatSessionSummaryDto, CreateChatSessionRequest, CreateChatSessionResponse, LlmProvider,
    MessageProcessingStatus, MessageStatusResponse, PaginationMetadata, SendChatMessageRequest,
    SendMessageResponse, UpdateChatSessionRequest,
};
use newsly_db::{
    ChatMutationOutcome, ChatRecordAccess, CreateChatSessionInput, CreateChatSessionOutcome,
    StageAssistantTurnInput, StageChatMessageInput, StageChatTurnOutcome, StagedChatTurn,
    UpdateChatSessionInput, archive_chat_session, create_chat_session, get_chat_message_status,
    get_chat_session_detail, get_chat_session_summary, list_chat_sessions, stage_assistant_turn,
    stage_chat_message, update_chat_session,
};
use newsly_queue::{EnqueueRequest, QueueError, QueueKernel, TaskType};
use serde::Deserialize;
use serde_json::{Map, Value};

use crate::auth::AuthenticatedUser;
use crate::error::ApiError;
use crate::gateway::RouteOwnershipStamp;
use crate::write_support::{
    bad_request, decode_json, internal_error, require_operation, verify_stamp,
};
use crate::{AppState, request_id_from_headers};

const CREATE_SESSION_OPERATION_ID: &str = "createContentChatSession";
const UPDATE_SESSION_OPERATION_ID: &str = "updateContentChatSession";
const DELETE_SESSION_OPERATION_ID: &str = "deleteContentChatSession";
const SEND_MESSAGE_OPERATION_ID: &str = "sendContentChatSessionsMessage";
const ASSISTANT_TURN_OPERATION_ID: &str = "createContentChatAssistantTurn";

pub(super) fn router() -> Router<AppState> {
    Router::new()
        .route(
            "/api/content/chat/sessions",
            get(list_sessions).post(create_session),
        )
        .route("/api/content/chat/sessions/list", get(list_sessions_page))
        .route(
            "/api/content/chat/sessions/{session_id}",
            get(get_session)
                .patch(update_session)
                .delete(delete_session),
        )
        .route(
            "/api/content/chat/sessions/{session_id}/messages",
            post(send_message),
        )
        .route(
            "/api/content/chat/assistant/turns",
            post(create_assistant_turn),
        )
        .route(
            "/api/content/chat/messages/{message_id}/status",
            get(get_message_status),
        )
        .merge(council::router())
}

#[derive(Debug, Deserialize)]
pub(super) struct SessionListQuery {
    content_id: Option<i64>,
    news_item_id: Option<i64>,
    cursor: Option<String>,
    limit: Option<usize>,
}

#[utoipa::path(
    get,
    path = "/api/content/chat/sessions",
    operation_id = "listContentChatSessions",
    tag = "content",
    params(
        ("content_id" = Option<i64>, Query, description = "Filter by content ID"),
        ("news_item_id" = Option<i64>, Query, description = "Filter by news item ID"),
        ("limit" = Option<usize>, Query, description = "Maximum sessions to return", minimum = 1, maximum = 100)
    ),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = Vec<ChatSessionSummaryDto>),
        (status = 400, description = "Invalid filters", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn list_sessions(
    State(state): State<AppState>,
    headers: HeaderMap,
    query: Result<Query<SessionListQuery>, QueryRejection>,
    current_user: AuthenticatedUser,
) -> Result<Json<Vec<ChatSessionSummaryDto>>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let query = validated_list_query(query, 50, false, &request_id)?;
    let sessions = list_chat_sessions(
        state.database.pool(),
        current_user.id,
        query.content_id,
        query.news_item_id,
        None,
        i64::try_from(query.limit).expect("validated chat limit fits i64"),
    )
    .await
    .map_err(|error| internal_error(error, &request_id))?;
    Ok(Json(
        sessions.into_iter().map(presentation::session).collect(),
    ))
}

#[utoipa::path(
    get,
    path = "/api/content/chat/sessions/list",
    operation_id = "listContentChatSessionsPage",
    tag = "content",
    params(
        ("content_id" = Option<i64>, Query, description = "Filter by content ID"),
        ("news_item_id" = Option<i64>, Query, description = "Filter by news item ID"),
        ("cursor" = Option<String>, Query, description = "Pagination cursor for next page"),
        ("limit" = Option<usize>, Query, description = "Maximum sessions to return", minimum = 1, maximum = 100)
    ),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = ChatSessionListResponse),
        (status = 400, description = "Invalid cursor or filters", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn list_sessions_page(
    State(state): State<AppState>,
    headers: HeaderMap,
    query: Result<Query<SessionListQuery>, QueryRejection>,
    current_user: AuthenticatedUser,
) -> Result<Json<ChatSessionListResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let query = validated_list_query(query, 25, true, &request_id)?;
    let decoded_cursor = query
        .cursor
        .as_deref()
        .map(|value| cursor::decode(value, query.content_id, query.news_item_id))
        .transpose()
        .map_err(|message| bad_request(message, &request_id))?;
    let overfetch = query.limit + 1;
    let mut sessions = list_chat_sessions(
        state.database.pool(),
        current_user.id,
        query.content_id,
        query.news_item_id,
        decoded_cursor,
        i64::try_from(overfetch).expect("validated chat limit fits i64"),
    )
    .await
    .map_err(|error| internal_error(error, &request_id))?;
    let has_more = sessions.len() > query.limit;
    if has_more {
        sessions.truncate(query.limit);
    }
    let next_cursor = has_more.then(|| {
        sessions.last().map(|session| {
            cursor::encode(
                session.id,
                session.last_message_at.unwrap_or(session.created_at),
                query.content_id,
                query.news_item_id,
            )
        })
    });
    let next_cursor = next_cursor.flatten();
    let sessions = sessions
        .into_iter()
        .map(presentation::session)
        .collect::<Vec<_>>();
    Ok(Json(ChatSessionListResponse {
        meta: PaginationMetadata {
            next_cursor,
            has_more,
            page_size: sessions.len(),
            total: None,
        },
        sessions,
    }))
}

#[utoipa::path(
    post,
    path = "/api/content/chat/sessions",
    operation_id = "createContentChatSession",
    tag = "content",
    request_body = CreateChatSessionRequest,
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = CreateChatSessionResponse),
        (status = 400, description = "Invalid session request", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Linked resource not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn create_session(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    payload: Result<Json<CreateChatSessionRequest>, JsonRejection>,
) -> Result<Json<CreateChatSessionResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, CREATE_SESSION_OPERATION_ID, &request_id)?;
    let Json(payload) = decode_json(payload, &request_id)?;
    validate_create_request(&payload, &request_id)?;
    if payload.content_id.is_some() && payload.news_item_id.is_some() {
        return Err(bad_request(
            "Use either content_id or news_item_id",
            &request_id,
        ));
    }
    let (provider, model) = resolve_model(payload.llm_provider, payload.llm_model_hint.as_deref());
    let session_type = if payload.llm_provider == Some(LlmProvider::DeepResearch) {
        "deep_research"
    } else {
        "knowledge_chat"
    };
    let mut transaction = begin_write(&state, &request_id).await?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    let outcome = create_chat_session(
        &mut transaction,
        &CreateChatSessionInput {
            user_id: current_user.id,
            content_id: payload.content_id,
            news_item_id: payload.news_item_id,
            topic: payload.topic.as_deref(),
            initial_message: payload.initial_message.as_deref(),
            session_type,
            llm_provider: provider,
            llm_model: &model,
        },
    )
    .await
    .map_err(|error| internal_error(error, &request_id))?;
    let session_id = match outcome {
        CreateChatSessionOutcome::Created { session_id } => session_id,
        CreateChatSessionOutcome::ContentNotFound => {
            return Err(not_found("Content not found", &request_id));
        }
        CreateChatSessionOutcome::NewsItemNotFound => {
            return Err(not_found("News item not found", &request_id));
        }
        CreateChatSessionOutcome::UserInactive => return Err(inactive_user(&request_id)),
    };
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;

    let session = require_session_summary(&state, current_user.id, session_id, &request_id).await?;
    Ok(Json(CreateChatSessionResponse { session }))
}

#[utoipa::path(
    get,
    path = "/api/content/chat/sessions/{session_id}",
    operation_id = "getContentChatSession",
    tag = "content",
    params(("session_id" = i64, Path, description = "Chat session ID", minimum = 1)),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = ChatSessionDetailDto),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 403, description = "Not authorized", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Session not found", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn get_session(
    State(state): State<AppState>,
    headers: HeaderMap,
    path: Result<Path<i64>, PathRejection>,
    current_user: AuthenticatedUser,
) -> Result<Json<ChatSessionDetailDto>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let session_id = positive_path(path, "session_id", &request_id)?;
    let access = get_chat_session_detail(state.database.pool(), current_user.id, session_id)
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    let detail = match access {
        ChatRecordAccess::Found(detail) => detail,
        ChatRecordAccess::NotFound => return Err(not_found("Session not found", &request_id)),
        ChatRecordAccess::Forbidden => return Err(forbidden_session(&request_id)),
    };
    Ok(Json(
        presentation::detail(detail).map_err(|error| internal_error(error, &request_id))?,
    ))
}

#[utoipa::path(
    patch,
    path = "/api/content/chat/sessions/{session_id}",
    operation_id = "updateContentChatSession",
    tag = "content",
    params(("session_id" = i64, Path, description = "Chat session ID", minimum = 1)),
    request_body = UpdateChatSessionRequest,
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = ChatSessionSummaryDto),
        (status = 400, description = "Invalid provider", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 403, description = "Not authorized", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Session not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn update_session(
    State(state): State<AppState>,
    headers: HeaderMap,
    path: Result<Path<i64>, PathRejection>,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    payload: Result<Json<UpdateChatSessionRequest>, JsonRejection>,
) -> Result<Json<ChatSessionSummaryDto>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, UPDATE_SESSION_OPERATION_ID, &request_id)?;
    let session_id = positive_path(path, "session_id", &request_id)?;
    let Json(payload) = decode_json(payload, &request_id)?;
    validate_update_request(&payload, &request_id)?;
    if payload.llm_provider == Some(LlmProvider::DeepResearch) {
        return Err(bad_request(
            "Deep research must be started as a dedicated deep research session",
            &request_id,
        ));
    }
    let resolved = payload
        .llm_provider
        .map(|provider| resolve_model(Some(provider), payload.llm_model_hint.as_deref()));
    let mut transaction = begin_write(&state, &request_id).await?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    let outcome = update_chat_session(
        &mut transaction,
        &UpdateChatSessionInput {
            user_id: current_user.id,
            session_id,
            llm_provider: resolved.as_ref().map(|(provider, _)| *provider),
            llm_model: resolved.as_ref().map(|(_, model)| model.as_str()),
        },
    )
    .await
    .map_err(|error| internal_error(error, &request_id))?;
    mutation_result(outcome, &request_id)?;
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    Ok(Json(
        require_session_summary(&state, current_user.id, session_id, &request_id).await?,
    ))
}

#[utoipa::path(
    delete,
    path = "/api/content/chat/sessions/{session_id}",
    operation_id = "deleteContentChatSession",
    tag = "content",
    params(("session_id" = i64, Path, description = "Chat session ID", minimum = 1)),
    security(("HTTPBearer" = [])),
    responses(
        (status = 204, description = "Successful Response"),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 403, description = "Not authorized", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Session not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn delete_session(
    State(state): State<AppState>,
    headers: HeaderMap,
    path: Result<Path<i64>, PathRejection>,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
) -> Result<StatusCode, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, DELETE_SESSION_OPERATION_ID, &request_id)?;
    let session_id = positive_path(path, "session_id", &request_id)?;
    let mut transaction = begin_write(&state, &request_id).await?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    let outcome = archive_chat_session(&mut transaction, current_user.id, session_id)
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    mutation_result(outcome, &request_id)?;
    transaction
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    Ok(StatusCode::NO_CONTENT)
}

#[utoipa::path(
    post,
    path = "/api/content/chat/sessions/{session_id}/messages",
    operation_id = "sendContentChatSessionsMessage",
    tag = "content",
    params(("session_id" = i64, Path, description = "Chat session ID", minimum = 1)),
    request_body = SendChatMessageRequest,
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = SendMessageResponse),
        (status = 400, description = "Invalid council branch", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 403, description = "Not authorized", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Session not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Archived or stale owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn send_message(
    State(state): State<AppState>,
    headers: HeaderMap,
    path: Result<Path<i64>, PathRejection>,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    payload: Result<Json<SendChatMessageRequest>, JsonRejection>,
) -> Result<Json<SendMessageResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, SEND_MESSAGE_OPERATION_ID, &request_id)?;
    let session_id = positive_path(path, "session_id", &request_id)?;
    let Json(payload) = decode_json(payload, &request_id)?;
    validate_message(&payload.message, &request_id)?;
    let mut transaction = begin_write(&state, &request_id).await?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    let outcome = stage_chat_message(
        &mut transaction,
        &StageChatMessageInput {
            user_id: current_user.id,
            session_id,
            user_prompt: &payload.message,
        },
    )
    .await
    .map_err(|error| internal_error(error, &request_id))?;
    let staged = staged_result(outcome, &request_id)?;
    enqueue_chat_turn(
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
    Ok(Json(SendMessageResponse {
        session_id: staged.visible_session_id,
        user_message: presentation::processing_user(&staged),
        message_id: staged.message_id,
        status: MessageProcessingStatus::Processing,
    }))
}

#[utoipa::path(
    post,
    path = "/api/content/chat/assistant/turns",
    operation_id = "createContentChatAssistantTurn",
    tag = "content",
    request_body = AssistantTurnRequest,
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = AssistantTurnResponse),
        (status = 400, description = "Invalid assistant context", body = newsly_contracts::ErrorEnvelope),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 403, description = "Not authorized", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Session or news item not found", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Archived or stale owner", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn create_assistant_turn(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    payload: Result<Json<AssistantTurnRequest>, JsonRejection>,
) -> Result<Json<AssistantTurnResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, ASSISTANT_TURN_OPERATION_ID, &request_id)?;
    let Json(mut payload) = decode_json(payload, &request_id)?;
    normalize_and_validate_assistant(&mut payload, &request_id)?;
    let screen_context = serde_json::to_value(&payload.screen_context)
        .map_err(|error| internal_error(error, &request_id))?;
    let mut transaction = begin_write(&state, &request_id).await?;
    verify_stamp(&mut transaction, &stamp, &request_id).await?;
    let outcome = stage_assistant_turn(
        &mut transaction,
        &StageAssistantTurnInput {
            user_id: current_user.id,
            session_id: payload.session_id,
            user_prompt: &payload.message,
            screen_context: &screen_context,
        },
    )
    .await
    .map_err(|error| internal_error(error, &request_id))?;
    let staged = staged_result(outcome, &request_id)?;
    enqueue_chat_turn(
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
    let session = require_session_summary(
        &state,
        current_user.id,
        staged.visible_session_id,
        &request_id,
    )
    .await?;
    Ok(Json(AssistantTurnResponse {
        session,
        user_message: presentation::processing_user(&staged),
        message_id: staged.message_id,
        status: MessageProcessingStatus::Processing,
    }))
}

#[utoipa::path(
    get,
    path = "/api/content/chat/messages/{message_id}/status",
    operation_id = "getContentChatMessageStatus",
    tag = "content",
    params(("message_id" = i64, Path, description = "Message ID to poll", minimum = 1)),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = MessageStatusResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 403, description = "Not authorized", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Message not found", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn get_message_status(
    State(state): State<AppState>,
    headers: HeaderMap,
    path: Result<Path<i64>, PathRejection>,
    current_user: AuthenticatedUser,
) -> Result<Json<MessageStatusResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let message_id = positive_path(path, "message_id", &request_id)?;
    let access = get_chat_message_status(state.database.pool(), current_user.id, message_id)
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    let status = match access {
        ChatRecordAccess::Found(status) => status,
        ChatRecordAccess::NotFound => return Err(not_found("Message not found", &request_id)),
        ChatRecordAccess::Forbidden => {
            return Err(ApiError::new(
                StatusCode::FORBIDDEN,
                "forbidden",
                "Not authorized to access this message",
                request_id,
            ));
        }
    };
    Ok(Json(
        presentation::message_status(status).map_err(|error| internal_error(error, &request_id))?,
    ))
}

#[derive(Debug)]
struct ValidatedListQuery {
    content_id: Option<i64>,
    news_item_id: Option<i64>,
    cursor: Option<String>,
    limit: usize,
}

fn validated_list_query(
    query: Result<Query<SessionListQuery>, QueryRejection>,
    default_limit: usize,
    allow_cursor: bool,
    request_id: &str,
) -> Result<ValidatedListQuery, ApiError> {
    let Query(query) = query.map_err(|rejection| {
        validation_error(
            format!("Request validation failed: {}", rejection.body_text()),
            request_id,
        )
    })?;
    if query.content_id.is_some() && query.news_item_id.is_some() {
        return Err(bad_request(
            "Use either content_id or news_item_id",
            request_id,
        ));
    }
    let limit = query.limit.unwrap_or(default_limit);
    if !(1..=100).contains(&limit) {
        return Err(validation_error(
            "limit must be between 1 and 100",
            request_id,
        ));
    }
    Ok(ValidatedListQuery {
        content_id: query.content_id,
        news_item_id: query.news_item_id,
        cursor: allow_cursor.then_some(query.cursor).flatten(),
        limit,
    })
}

fn validate_create_request(
    payload: &CreateChatSessionRequest,
    request_id: &str,
) -> Result<(), ApiError> {
    validate_optional_len(payload.topic.as_deref(), 500, "topic", request_id)?;
    validate_optional_len(
        payload.llm_model_hint.as_deref(),
        100,
        "llm_model_hint",
        request_id,
    )?;
    validate_optional_len(
        payload.initial_message.as_deref(),
        2_000,
        "initial_message",
        request_id,
    )?;
    reject_google_model(payload.llm_model_hint.as_deref(), request_id)
}

fn validate_update_request(
    payload: &UpdateChatSessionRequest,
    request_id: &str,
) -> Result<(), ApiError> {
    validate_optional_len(
        payload.llm_model_hint.as_deref(),
        100,
        "llm_model_hint",
        request_id,
    )?;
    reject_google_model(payload.llm_model_hint.as_deref(), request_id)
}

fn validate_message(message: &str, request_id: &str) -> Result<(), ApiError> {
    let length = message.chars().count();
    if !(1..=10_000).contains(&length) {
        return Err(validation_error(
            "message must contain between 1 and 10000 characters",
            request_id,
        ));
    }
    Ok(())
}

fn normalize_and_validate_assistant(
    payload: &mut AssistantTurnRequest,
    request_id: &str,
) -> Result<(), ApiError> {
    validate_message(&payload.message, request_id)?;
    if payload.session_id.is_some_and(|value| value <= 0) {
        return Err(validation_error(
            "session_id must be greater than zero",
            request_id,
        ));
    }
    let context = &mut payload.screen_context;
    context.visible_content_ids.truncate(12);
    context.visible_news_item_ids.truncate(12);
    if context.content_id.is_some_and(|value| value <= 0)
        || context.news_item_id.is_some_and(|value| value <= 0)
    {
        return Err(validation_error(
            "content_id and news_item_id must be greater than zero",
            request_id,
        ));
    }
    if context.content_id.is_some() && context.news_item_id.is_some() {
        return Err(validation_error(
            "content_id and news_item_id are mutually exclusive",
            request_id,
        ));
    }
    validate_len(&context.screen_type, 64, "screen_type", request_id)?;
    validate_optional_len(
        context.screen_title.as_deref(),
        200,
        "screen_title",
        request_id,
    )?;
    validate_optional_len(
        context.selected_topic.as_deref(),
        200,
        "selected_topic",
        request_id,
    )?;
    validate_optional_len(context.query.as_deref(), 200, "query", request_id)?;
    validate_optional_len(context.note.as_deref(), 1_500, "note", request_id)?;
    validate_optional_len(
        context.assistant_action.as_deref(),
        100,
        "assistant_action",
        request_id,
    )
}

fn validate_optional_len(
    value: Option<&str>,
    max: usize,
    field: &str,
    request_id: &str,
) -> Result<(), ApiError> {
    if let Some(value) = value {
        validate_len(value, max, field, request_id)?;
    }
    Ok(())
}

fn validate_len(value: &str, max: usize, field: &str, request_id: &str) -> Result<(), ApiError> {
    if value.chars().count() > max {
        Err(validation_error(
            format!("{field} must contain at most {max} characters"),
            request_id,
        ))
    } else {
        Ok(())
    }
}

fn reject_google_model(model_hint: Option<&str>, request_id: &str) -> Result<(), ApiError> {
    let is_google = model_hint.is_some_and(|value| {
        let normalized = value.trim().to_ascii_lowercase();
        normalized.starts_with("google:")
            || normalized.starts_with("google-gla:")
            || normalized.starts_with("gemini")
    });
    if is_google {
        Err(validation_error(
            "Google models are not available for chat sessions",
            request_id,
        ))
    } else {
        Ok(())
    }
}

fn resolve_model(
    provider: Option<LlmProvider>,
    model_hint: Option<&str>,
) -> (&'static str, String) {
    let provider = provider.unwrap_or(LlmProvider::Openai).as_str();
    if let Some(model_hint) = model_hint {
        if let Some((prefix, _)) = model_hint.split_once(':') {
            let resolved_provider = match prefix {
                "openai" => "openai",
                "anthropic" => "anthropic",
                "openrouter" => "openrouter",
                "deep_research" => "deep_research",
                _ => provider,
            };
            return (resolved_provider, model_hint.to_owned());
        }
        return (provider, format!("{provider}:{model_hint}"));
    }
    let model = match provider {
        "anthropic" => "anthropic:claude-opus-4-6",
        "openrouter" => "openrouter:deepseek/deepseek-v4-flash",
        "deep_research" => "deep_research:o4-mini-deep-research-2025-06-26",
        _ => "openai:gpt-5.6-terra",
    };
    (provider, model.to_owned())
}

async fn enqueue_chat_turn(
    state: &AppState,
    transaction: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    user_id: i64,
    staged: &StagedChatTurn,
    request_id: &str,
) -> Result<(), ApiError> {
    let mut request = EnqueueRequest::new(TaskType::ChatTurn);
    request.payload = Some(Map::from_iter([
        ("user_id".to_owned(), Value::from(user_id)),
        (
            "session_id".to_owned(),
            Value::from(staged.effective_session_id),
        ),
        ("message_id".to_owned(), Value::from(staged.message_id)),
    ]));
    request.dedupe_key = Some(format!("chat-turn:message:{}", staged.message_id));
    request.owner_user_id = Some(user_id);
    QueueKernel::new(state.database.pool().clone())
        .enqueue_many_in_transaction(transaction, vec![request])
        .await
        .map_err(|error| queue_error(error, request_id))?;
    Ok(())
}

fn staged_result(
    outcome: StageChatTurnOutcome,
    request_id: &str,
) -> Result<StagedChatTurn, ApiError> {
    match outcome {
        StageChatTurnOutcome::Staged(staged) => Ok(staged),
        StageChatTurnOutcome::NotFound | StageChatTurnOutcome::Hidden => {
            Err(not_found("Session not found", request_id))
        }
        StageChatTurnOutcome::Forbidden => Err(forbidden_session(request_id)),
        StageChatTurnOutcome::Archived => Err(ApiError::new(
            StatusCode::CONFLICT,
            "archived_session",
            "Archived sessions cannot accept messages",
            request_id.to_owned(),
        )),
        StageChatTurnOutcome::NoActiveCouncilBranch => {
            Err(bad_request("No active council branch selected", request_id))
        }
        StageChatTurnOutcome::NewsItemNotFound => Err(not_found("News item not found", request_id)),
        StageChatTurnOutcome::UserInactive => Err(inactive_user(request_id)),
    }
}

fn mutation_result(outcome: ChatMutationOutcome, request_id: &str) -> Result<(), ApiError> {
    match outcome {
        ChatMutationOutcome::Applied => Ok(()),
        ChatMutationOutcome::NotFound => Err(not_found("Session not found", request_id)),
        ChatMutationOutcome::Forbidden => Err(forbidden_session(request_id)),
    }
}

async fn require_session_summary(
    state: &AppState,
    user_id: i64,
    session_id: i64,
    request_id: &str,
) -> Result<ChatSessionSummaryDto, ApiError> {
    match get_chat_session_summary(state.database.pool(), user_id, session_id)
        .await
        .map_err(|error| internal_error(error, request_id))?
    {
        ChatRecordAccess::Found(session) => Ok(presentation::session(session)),
        ChatRecordAccess::NotFound => Err(not_found("Session not found", request_id)),
        ChatRecordAccess::Forbidden => Err(forbidden_session(request_id)),
    }
}

fn positive_path(
    path: Result<Path<i64>, PathRejection>,
    field: &str,
    request_id: &str,
) -> Result<i64, ApiError> {
    let Path(value) =
        path.map_err(|rejection| validation_error(rejection.body_text(), request_id))?;
    if value <= 0 {
        return Err(validation_error(
            format!("{field} must be greater than zero"),
            request_id,
        ));
    }
    Ok(value)
}

async fn begin_write<'a>(
    state: &'a AppState,
    request_id: &str,
) -> Result<sqlx::Transaction<'a, sqlx::Postgres>, ApiError> {
    state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, request_id))
}

fn validation_error(message: impl Into<String>, request_id: &str) -> ApiError {
    ApiError::new(
        StatusCode::UNPROCESSABLE_ENTITY,
        "validation_error",
        message,
        request_id.to_owned(),
    )
}

fn not_found(message: &str, request_id: &str) -> ApiError {
    ApiError::new(
        StatusCode::NOT_FOUND,
        "not_found",
        message,
        request_id.to_owned(),
    )
}

fn forbidden_session(request_id: &str) -> ApiError {
    ApiError::new(
        StatusCode::FORBIDDEN,
        "forbidden",
        "Not authorized to access this session",
        request_id.to_owned(),
    )
}

fn inactive_user(request_id: &str) -> ApiError {
    ApiError::new(
        StatusCode::BAD_REQUEST,
        "inactive_user",
        "Task user is missing or inactive",
        request_id.to_owned(),
    )
}

fn queue_error(error: QueueError, request_id: &str) -> ApiError {
    match error {
        QueueError::UserMissingOrInactive => inactive_user(request_id),
        other => internal_error(other, request_id),
    }
}
