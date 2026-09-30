use std::future::Future;
use std::path::{Path, PathBuf};
use std::pin::Pin;
use std::sync::Arc;

use axum::extract::{DefaultBodyLimit, Extension, Multipart, State};
use axum::http::{HeaderMap, StatusCode};
use axum::routing::{get, post};
use axum::{Json, Router};
use newsly_contracts::{AudioTranscriptionHealthResponse, AudioTranscriptionResponse};
use newsly_db::{NewTranscriptionUsage, record_transcription_usage};
use newsly_providers::{
    OpenAiTranscriptionError, TranscriptionChunkUsage, TranscriptionUsageObserver,
};
use sqlx::PgPool;
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
        .transcribe_upload_observed(
            &upload.path,
            &upload.filename,
            Arc::new(ApiTranscriptionUsageObserver {
                pool: state.database.pool().clone(),
                request_id: request_id.clone(),
                user_id: current_user.id,
                file_name: upload.filename.clone(),
                content_type: upload.content_type.clone(),
            }),
        )
        .await
        .map_err(|error| provider_error(error, &request_id))?;

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
        });
    }
    Err(validation_error(
        "Multipart field 'file' is required",
        request_id,
    ))
}

#[derive(Debug, Clone)]
struct ApiTranscriptionUsageObserver {
    pool: PgPool,
    request_id: String,
    user_id: i64,
    file_name: String,
    content_type: Option<String>,
}

impl TranscriptionUsageObserver for ApiTranscriptionUsageObserver {
    fn observe(
        &self,
        usage: TranscriptionChunkUsage,
    ) -> Pin<Box<dyn Future<Output = Result<(), String>> + Send + '_>> {
        let observer = self.clone();
        Box::pin(async move {
            tokio::spawn(async move {
                let chunk_request_id =
                    format!("{}:chunk:{}", observer.request_id, usage.chunk_index);
                let mut transaction = observer
                    .pool
                    .begin()
                    .await
                    .map_err(|error| error.to_string())?;
                record_transcription_usage(
                    &mut transaction,
                    &NewTranscriptionUsage {
                        request_id: &chunk_request_id,
                        user_id: observer.user_id,
                        model: usage.model,
                        request_count: 1,
                        audio_duration_ms: usage.audio_duration_ms,
                        audio_duration_estimate_ms: usage.audio_duration_estimate_ms,
                        duration_source: usage.audio_duration_source.as_str(),
                        standard_pricing: usage.standard_pricing,
                        metadata: serde_json::json!({
                            "file_name": observer.file_name,
                            "audio_format": audio_format(&observer.file_name),
                            "audio_size_bytes": usage.audio_size_bytes,
                            "content_type": observer.content_type,
                            "chunk_index": usage.chunk_index,
                            "prompt_chars": usage.prompt_chars,
                        }),
                    },
                )
                .await
                .map_err(|error| error.to_string())?;
                transaction
                    .commit()
                    .await
                    .map_err(|error| error.to_string())
            })
            .await
            .map_err(|error| format!("transcription usage persistence task failed: {error}"))?
        })
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
