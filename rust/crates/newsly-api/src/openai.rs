use std::path::{Path, PathBuf};

use axum::extract::{DefaultBodyLimit, Extension, Multipart, State};
use axum::http::{HeaderMap, StatusCode};
use axum::routing::{get, post};
use axum::{Json, Router};
use newsly_contracts::{AudioTranscriptionHealthResponse, AudioTranscriptionResponse};
use newsly_db::{NewTranscriptionUsage, record_transcription_usage};
use newsly_providers::OpenAiTranscriptionError;
use tempfile::{Builder as TempFileBuilder, NamedTempFile};
use tokio::io::AsyncWriteExt;
use utoipa::ToSchema;

use crate::auth::AuthenticatedUser;
use crate::error::ApiError;
use crate::gateway::RouteOwnershipStamp;
use crate::write_support::{internal_error, require_operation, verify_stamp};
use crate::{AppState, request_id_from_headers};

const TRANSCRIBE_OPERATION_ID: &str = "transcribeOpenaiTranscriptionsAudio";
const MAX_UPLOAD_BYTES: u64 = 500_000_000;
const MULTIPART_BODY_BYTES: usize = 500_000_000 + 1024 * 1024;

pub(super) fn router() -> Router<AppState> {
    Router::new()
        .route(
            "/api/openai/transcriptions/health",
            get(transcription_health),
        )
        .route(
            "/api/openai/transcriptions",
            post(transcribe_audio).layer(DefaultBodyLimit::max(MULTIPART_BODY_BYTES)),
        )
}

#[utoipa::path(
    get,
    path = "/api/openai/transcriptions/health",
    operation_id = "transcriptionOpenaiHealth",
    tag = "openai",
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = AudioTranscriptionHealthResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn transcription_health(
    State(state): State<AppState>,
    _current_user: AuthenticatedUser,
) -> Json<AudioTranscriptionHealthResponse> {
    Json(AudioTranscriptionHealthResponse {
        available: state.transcription.is_some(),
    })
}

#[derive(ToSchema)]
#[allow(dead_code)]
struct TranscriptionUploadForm {
    #[schema(value_type = String, format = Binary)]
    file: Vec<u8>,
}

#[utoipa::path(
    post,
    path = "/api/openai/transcriptions",
    operation_id = "transcribeOpenaiTranscriptionsAudio",
    tag = "openai",
    request_body(content = inline(TranscriptionUploadForm), content_type = "multipart/form-data"),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = AudioTranscriptionResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 409, description = "Stale runtime owner", body = newsly_contracts::ErrorEnvelope),
        (status = 413, description = "Audio upload too large", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 502, description = "Transcription provider failure", body = newsly_contracts::ErrorEnvelope),
        (status = 503, description = "Transcription unavailable", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn transcribe_audio(
    State(state): State<AppState>,
    headers: HeaderMap,
    current_user: AuthenticatedUser,
    Extension(stamp): Extension<RouteOwnershipStamp>,
    multipart: Multipart,
) -> Result<Json<AudioTranscriptionResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_operation(&stamp, TRANSCRIBE_OPERATION_ID, &request_id)?;
    let upload = store_upload(multipart, &request_id).await?;
    let provider = state.transcription.as_ref().ok_or_else(|| {
        ApiError::new(
            StatusCode::SERVICE_UNAVAILABLE,
            "transcription_unavailable",
            "OpenAI API key is required for transcription service",
            request_id.clone(),
        )
    })?;

    // Verify ownership in a short prepare transaction. The gateway's request permit remains held
    // while the provider runs, so a route transition cannot complete around the external call.
    let mut prepare = state
        .database
        .pool()
        .begin()
        .await
        .map_err(|error| internal_error(error, &request_id))?;
    verify_stamp(&mut prepare, &stamp, &request_id).await?;
    prepare
        .commit()
        .await
        .map_err(|error| internal_error(error, &request_id))?;

    let result = provider
        .transcribe_upload(&upload.path, &upload.filename)
        .await
        .map_err(|error| provider_error(error, &request_id))?;

    persist_usage_best_effort(
        &state,
        &stamp,
        &request_id,
        current_user.id,
        &upload,
        &result,
    )
    .await;

    Ok(Json(AudioTranscriptionResponse {
        transcript: result.transcript,
        language: result.language,
    }))
}

#[derive(Debug)]
struct StoredUpload {
    _temporary_file: NamedTempFile,
    path: PathBuf,
    filename: String,
    content_type: Option<String>,
    size_bytes: u64,
}

async fn store_upload(
    mut multipart: Multipart,
    request_id: &str,
) -> Result<StoredUpload, ApiError> {
    while let Some(mut field) = multipart.next_field().await.map_err(|error| {
        validation_error(format!("Invalid multipart upload: {error}"), request_id)
    })? {
        if field.name() != Some("file") {
            continue;
        }
        let filename = field.file_name().unwrap_or("audio.m4a").to_owned();
        let content_type = field.content_type().map(str::to_owned);
        let suffix = safe_suffix(&filename);
        let temporary_file = TempFileBuilder::new()
            .prefix("newsly-transcription-")
            .suffix(&suffix)
            .tempfile()
            .map_err(|error| internal_error(error, request_id))?;
        let path = temporary_file.path().to_path_buf();
        let mut output = tokio::fs::OpenOptions::new()
            .write(true)
            .truncate(true)
            .open(&path)
            .await
            .map_err(|error| internal_error(error, request_id))?;
        let mut size_bytes = 0_u64;
        while let Some(chunk) = field.chunk().await.map_err(|error| {
            validation_error(format!("Invalid multipart upload: {error}"), request_id)
        })? {
            size_bytes = size_bytes
                .checked_add(chunk.len() as u64)
                .ok_or_else(|| upload_too_large(request_id))?;
            if size_bytes > MAX_UPLOAD_BYTES {
                return Err(upload_too_large(request_id));
            }
            output
                .write_all(&chunk)
                .await
                .map_err(|error| internal_error(error, request_id))?;
        }
        output
            .flush()
            .await
            .map_err(|error| internal_error(error, request_id))?;
        drop(output);
        if size_bytes == 0 {
            return Err(validation_error("Uploaded audio file is empty", request_id));
        }
        return Ok(StoredUpload {
            _temporary_file: temporary_file,
            path,
            filename,
            content_type,
            size_bytes,
        });
    }
    Err(validation_error(
        "Multipart field 'file' is required",
        request_id,
    ))
}

async fn persist_usage_best_effort(
    state: &AppState,
    stamp: &RouteOwnershipStamp,
    request_id: &str,
    user_id: i64,
    upload: &StoredUpload,
    result: &newsly_providers::TranscriptionResult,
) {
    let mut transaction = match state.database.pool().begin().await {
        Ok(transaction) => transaction,
        Err(error) => {
            tracing::warn!(error = %error, request_id, "transcription usage transaction unavailable");
            return;
        }
    };
    if let Err(error) = verify_stamp(&mut transaction, stamp, request_id).await {
        tracing::warn!(
            ?error,
            request_id,
            "skipping transcription usage after ownership change"
        );
        return;
    }
    let metadata = serde_json::json!({
        "file_name": upload.filename,
        "audio_format": audio_format(&upload.filename),
        "audio_size_bytes": upload.size_bytes,
        "content_type": upload.content_type,
        "language": result.language,
        "chunk_count": result.chunk_count,
        "prompt_chars": result.prompt_chars,
    });
    let usage = NewTranscriptionUsage {
        request_id,
        user_id,
        model: &result.model,
        metadata,
    };
    if let Err(error) = record_transcription_usage(&mut transaction, &usage).await {
        tracing::warn!(error = %error, request_id, user_id, "transcription usage insert failed");
        return;
    }
    if let Err(error) = transaction.commit().await {
        tracing::warn!(error = %error, request_id, user_id, "transcription usage commit failed");
    }
}

fn provider_error(error: OpenAiTranscriptionError, request_id: &str) -> ApiError {
    match error {
        OpenAiTranscriptionError::InvalidConfiguration(message) => ApiError::new(
            StatusCode::SERVICE_UNAVAILABLE,
            "transcription_unavailable",
            message,
            request_id.to_owned(),
        ),
        OpenAiTranscriptionError::InvalidAudio(message) => validation_error(message, request_id),
        other => {
            tracing::warn!(error = %other, request_id, "OpenAI transcription failed");
            ApiError::new(
                StatusCode::BAD_GATEWAY,
                "transcription_failed",
                other.to_string(),
                request_id.to_owned(),
            )
            .with_retryable(true)
        }
    }
}

fn safe_suffix(filename: &str) -> String {
    let extension = Path::new(filename)
        .extension()
        .and_then(|value| value.to_str())
        .filter(|value| {
            !value.is_empty()
                && value.len() <= 10
                && value
                    .chars()
                    .all(|character| character.is_ascii_alphanumeric())
        })
        .unwrap_or("m4a");
    format!(".{extension}")
}

fn audio_format(filename: &str) -> &'static str {
    match Path::new(filename)
        .extension()
        .and_then(|value| value.to_str())
        .map(str::to_ascii_lowercase)
        .as_deref()
    {
        Some("mp4" | "m4a") => "mp4",
        Some("wav") => "wav",
        Some("webm") => "webm",
        Some("ogg") => "ogg",
        Some("opus") => "opus",
        Some("flac") => "flac",
        _ => "mp3",
    }
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

fn upload_too_large(request_id: &str) -> ApiError {
    ApiError::new(
        StatusCode::PAYLOAD_TOO_LARGE,
        "payload_too_large",
        format!("Audio upload exceeds {MAX_UPLOAD_BYTES} bytes"),
        request_id.to_owned(),
    )
}
