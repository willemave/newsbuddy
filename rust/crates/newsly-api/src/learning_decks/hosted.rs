use std::env;
use std::path::{Component, Path as FilePath};

use axum::body::Body;
use axum::extract::{Path, State};
use axum::http::header::{CACHE_CONTROL, CONTENT_TYPE, X_CONTENT_TYPE_OPTIONS};
use axum::http::{HeaderMap, HeaderValue, Response, StatusCode};
use newsly_db::{HostedLearningDeckProjection, get_hosted_learning_deck};
use sha2::{Digest, Sha256};
use subtle::ConstantTimeEq as _;

use crate::error::ApiError;
use crate::learning_deck_artifacts::LearningDeckArtifactStore;
use crate::learning_deck_tokens::hash_learning_deck_token;
use crate::write_support::internal_error;
use crate::{AppState, request_id_from_headers};

use super::support::{repository_error, token_signer};

const MAX_HOSTED_TOKEN_CHARS: usize = 4_096;
const MAX_HOSTED_ASSET_PATH_CHARS: usize = 2_048;
const DEFAULT_INDEX_HTML_BYTES: usize = 2_000_000;
const DEFAULT_SOURCE_NOTES_BYTES: usize = 1_000_000;
const DEFAULT_ASSET_BYTES: usize = 5_000_000;

pub(super) async fn serve_shared_deck(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(token): Path<String>,
) -> Result<Response<Body>, ApiError> {
    serve_hosted_object(
        &state,
        &headers,
        &token,
        HostedAccess::Share,
        HostedTarget::Deck,
    )
    .await
}

pub(super) async fn serve_shared_source_notes(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(token): Path<String>,
) -> Result<Response<Body>, ApiError> {
    serve_hosted_object(
        &state,
        &headers,
        &token,
        HostedAccess::Share,
        HostedTarget::SourceNotes,
    )
    .await
}

pub(super) async fn serve_shared_asset(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path((token, asset_path)): Path<(String, String)>,
) -> Result<Response<Body>, ApiError> {
    serve_hosted_object(
        &state,
        &headers,
        &token,
        HostedAccess::Share,
        HostedTarget::Asset(asset_path),
    )
    .await
}

pub(super) async fn serve_private_deck(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(token): Path<String>,
) -> Result<Response<Body>, ApiError> {
    serve_hosted_object(
        &state,
        &headers,
        &token,
        HostedAccess::Private,
        HostedTarget::Deck,
    )
    .await
}

pub(super) async fn serve_private_source_notes(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(token): Path<String>,
) -> Result<Response<Body>, ApiError> {
    serve_hosted_object(
        &state,
        &headers,
        &token,
        HostedAccess::Private,
        HostedTarget::SourceNotes,
    )
    .await
}

pub(super) async fn serve_private_asset(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path((token, asset_path)): Path<(String, String)>,
) -> Result<Response<Body>, ApiError> {
    serve_hosted_object(
        &state,
        &headers,
        &token,
        HostedAccess::Private,
        HostedTarget::Asset(asset_path),
    )
    .await
}

#[derive(Clone, Copy)]
enum HostedAccess {
    Share,
    Private,
}

enum HostedTarget {
    Deck,
    SourceNotes,
    Asset(String),
}

async fn serve_hosted_object(
    state: &AppState,
    headers: &HeaderMap,
    token: &str,
    access: HostedAccess,
    target: HostedTarget,
) -> Result<Response<Body>, ApiError> {
    let request_id = request_id_from_headers(headers);
    if token.is_empty() || token.chars().count() > MAX_HOSTED_TOKEN_CHARS {
        return Err(hosted_access_error(access, &request_id));
    }
    let signer = token_signer(&request_id)?;
    let deck = match access {
        HostedAccess::Private => {
            let claims = signer
                .decode_private_token(token)
                .map_err(|_| hosted_access_error(access, &request_id))?;
            let deck = load_hosted_deck(state, claims.deck_id, &request_id).await?;
            if deck.user_id != claims.user_id {
                return Err(hosted_unavailable_error(access, &request_id));
            }
            deck
        }
        HostedAccess::Share => {
            let claims = signer
                .decode_share_token(token)
                .map_err(|_| hosted_access_error(access, &request_id))?;
            let deck = load_hosted_deck(state, claims.deck_id, &request_id).await?;
            let expected_hash = hash_learning_deck_token(token);
            if !deck.share_enabled
                || deck
                    .share_token_nonce
                    .as_deref()
                    .is_none_or(|nonce| !constant_time_text_eq(nonce, &claims.nonce))
                || deck
                    .share_token_hash
                    .as_deref()
                    .is_none_or(|hash| !constant_time_text_eq(hash, &expected_hash))
            {
                return Err(hosted_unavailable_error(access, &request_id));
            }
            deck
        }
    };
    if deck.latest_successful_attempt_id.is_none() {
        return Err(hosted_unavailable_error(access, &request_id));
    }

    let (object_key, content_type, maximum_bytes) = match target {
        HostedTarget::Deck => (
            deck.deck_object_key
                .as_deref()
                .ok_or_else(|| hosted_object_missing("Learning Deck is not ready", &request_id))?,
            "text/html; charset=utf-8",
            hosted_byte_limit(
                "LEARNING_DECK_MAX_INDEX_HTML_BYTES",
                DEFAULT_INDEX_HTML_BYTES,
                10_000,
                10_000_000,
                &request_id,
            )?,
        ),
        HostedTarget::SourceNotes => (
            deck.source_notes_html_object_key
                .as_deref()
                .ok_or_else(|| {
                    hosted_object_missing("Learning Deck source notes are not ready", &request_id)
                })?,
            "text/html; charset=utf-8",
            hosted_byte_limit(
                "LEARNING_DECK_MAX_SOURCE_NOTES_BYTES",
                DEFAULT_SOURCE_NOTES_BYTES,
                1_000,
                5_000_000,
                &request_id,
            )?,
        ),
        HostedTarget::Asset(asset_path) => {
            let relative_path = normalize_hosted_asset_path(&asset_path).ok_or_else(|| {
                hosted_object_missing("Learning Deck asset is not available", &request_id)
            })?;
            let prefix = deck
                .artifact_storage_prefix
                .as_deref()
                .ok_or_else(|| hosted_object_missing("Learning Deck is not ready", &request_id))?;
            let object_key = format!("{prefix}/assets/{relative_path}");
            if !deck.artifact_object_keys.contains(&object_key) {
                return Err(hosted_object_missing(
                    "Learning Deck asset is not available",
                    &request_id,
                ));
            }
            let content_type = hosted_asset_content_type(&relative_path);
            let maximum_bytes = hosted_byte_limit(
                "LEARNING_DECK_MAX_ASSET_BYTES",
                DEFAULT_ASSET_BYTES,
                1_000,
                20_000_000,
                &request_id,
            )?;
            return read_hosted_response(&object_key, content_type, maximum_bytes, &request_id)
                .await;
        }
    };
    read_hosted_response(object_key, content_type, maximum_bytes, &request_id).await
}

async fn load_hosted_deck(
    state: &AppState,
    deck_id: i64,
    request_id: &str,
) -> Result<HostedLearningDeckProjection, ApiError> {
    get_hosted_learning_deck(state.database.pool(), deck_id)
        .await
        .map_err(|error| repository_error(error, request_id))?
        .ok_or_else(|| hosted_object_missing("Learning Deck is not available", request_id))
}

async fn read_hosted_response(
    object_key: &str,
    content_type: &'static str,
    maximum_bytes: usize,
    request_id: &str,
) -> Result<Response<Body>, ApiError> {
    let store = LearningDeckArtifactStore::from_environment()
        .map_err(|error| internal_error(error, request_id))?;
    let bytes = store
        .read_bounded(object_key, maximum_bytes)
        .await
        .map_err(|error| internal_error(error, request_id))?
        .ok_or_else(|| hosted_object_missing("Learning Deck artifact not found", request_id))?;
    Response::builder()
        .status(StatusCode::OK)
        .header(CONTENT_TYPE, HeaderValue::from_static(content_type))
        .header(CACHE_CONTROL, HeaderValue::from_static("private, no-store"))
        .header(X_CONTENT_TYPE_OPTIONS, HeaderValue::from_static("nosniff"))
        .body(Body::from(bytes))
        .map_err(|error| internal_error(error, request_id))
}

fn constant_time_text_eq(left: &str, right: &str) -> bool {
    let left = Sha256::digest(left.as_bytes());
    let right = Sha256::digest(right.as_bytes());
    bool::from(left.ct_eq(&right))
}

fn normalize_hosted_asset_path(asset_path: &str) -> Option<String> {
    if asset_path.is_empty()
        || asset_path.chars().count() > MAX_HOSTED_ASSET_PATH_CHARS
        || asset_path.contains(['\0', '\\'])
    {
        return None;
    }
    let path = FilePath::new(asset_path);
    if path.is_absolute()
        || path
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return None;
    }
    let parts = path
        .components()
        .map(|component| match component {
            Component::Normal(part) => part.to_str(),
            _ => None,
        })
        .collect::<Option<Vec<_>>>()?;
    (!parts.is_empty()).then(|| parts.join("/"))
}

fn hosted_asset_content_type(relative_path: &str) -> &'static str {
    match relative_path
        .rsplit_once('.')
        .map(|(_, extension)| extension.to_ascii_lowercase())
        .as_deref()
    {
        Some("css") => "text/css",
        Some("js" | "mjs") => "text/javascript",
        Some("json") => "application/json",
        Some("svg") => "image/svg+xml",
        Some("png") => "image/png",
        Some("jpg" | "jpeg") => "image/jpeg",
        Some("gif") => "image/gif",
        Some("webp") => "image/webp",
        Some("avif") => "image/avif",
        Some("ico") => "image/vnd.microsoft.icon",
        Some("pdf") => "application/pdf",
        Some("txt") => "text/plain",
        Some("md") => "text/markdown",
        Some("csv") => "text/csv",
        Some("xml") => "application/xml",
        Some("woff") => "font/woff",
        Some("woff2") => "font/woff2",
        Some("ttf") => "font/ttf",
        Some("otf") => "font/otf",
        Some("mp4") => "video/mp4",
        Some("webm") => "video/webm",
        Some("mp3") => "audio/mpeg",
        Some("wav") => "audio/x-wav",
        _ => "application/octet-stream",
    }
}

fn hosted_byte_limit(
    name: &'static str,
    default: usize,
    minimum: usize,
    maximum: usize,
    request_id: &str,
) -> Result<usize, ApiError> {
    let value = env::var(name)
        .ok()
        .map(|value| {
            value
                .parse::<usize>()
                .map_err(|error| internal_error(error, request_id))
        })
        .transpose()?
        .unwrap_or(default);
    if !(minimum..=maximum).contains(&value) {
        return Err(internal_error(
            format!("{name} is outside its supported range"),
            request_id,
        ));
    }
    Ok(value)
}

fn hosted_access_error(access: HostedAccess, request_id: &str) -> ApiError {
    match access {
        HostedAccess::Share => ApiError::new(
            StatusCode::NOT_FOUND,
            "not_found",
            "Invalid share link",
            request_id,
        ),
        HostedAccess::Private => ApiError::new(
            StatusCode::FORBIDDEN,
            "forbidden",
            "Invalid or expired Learning Deck URL",
            request_id,
        ),
    }
}

fn hosted_unavailable_error(access: HostedAccess, request_id: &str) -> ApiError {
    let message = match access {
        HostedAccess::Share => "Share link is not available",
        HostedAccess::Private => "Learning Deck is not available",
    };
    hosted_object_missing(message, request_id)
}

fn hosted_object_missing(message: &str, request_id: &str) -> ApiError {
    ApiError::new(StatusCode::NOT_FOUND, "not_found", message, request_id)
}

#[cfg(test)]
mod hosted_tests {
    use super::*;

    #[test]
    fn hosted_asset_paths_stay_inside_the_assets_directory() {
        assert_eq!(
            normalize_hosted_asset_path("figures/chart 1.svg"),
            Some("figures/chart 1.svg".to_owned())
        );
        assert_eq!(normalize_hosted_asset_path("../secret"), None);
        assert_eq!(normalize_hosted_asset_path("figures/../../secret"), None);
        assert_eq!(normalize_hosted_asset_path("/absolute.png"), None);
        assert_eq!(normalize_hosted_asset_path("figures\\secret.png"), None);
    }

    #[test]
    fn hosted_asset_content_types_preserve_the_viewer_contract() {
        assert_eq!(hosted_asset_content_type("deck.css"), "text/css");
        assert_eq!(hosted_asset_content_type("deck.js"), "text/javascript");
        assert_eq!(hosted_asset_content_type("diagram.svg"), "image/svg+xml");
        assert_eq!(hosted_asset_content_type("photo.JPEG"), "image/jpeg");
        assert_eq!(
            hosted_asset_content_type("unknown.custom"),
            "application/octet-stream"
        );
    }

    #[test]
    fn token_field_comparison_is_value_sensitive() {
        assert!(constant_time_text_eq("same", "same"));
        assert!(!constant_time_text_eq("same", "different"));
    }
}
