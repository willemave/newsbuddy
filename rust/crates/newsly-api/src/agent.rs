use std::env;
use std::sync::OnceLock;

use axum::extract::rejection::{JsonRejection, QueryRejection};
use axum::extract::{Query, State};
use axum::http::{HeaderMap, StatusCode};
use axum::routing::{get, post};
use axum::{Json, Router};
use chrono::{DateTime, Utc};
use newsly_contracts::{
    AgentLibraryDocumentResponse, AgentLibraryDocumentVariant, AgentLibraryFileResponse,
    AgentLibraryManifestResponse, AgentSearchRequest, AgentSearchResponse, AgentSearchResultKind,
    AgentSearchResultResponse,
};
use newsly_db::{AgentLibraryContentProjection, list_agent_library_content};
use newsly_providers::{
    BriefingDigGateway, BriefingDigGatewayError, BriefingWebSearchResult, ContentMiscGatewayError,
    PodcastEpisodeHit,
};
use serde::Deserialize;
use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::auth::AuthenticatedUser;
use crate::encoding::hex_encode;
use crate::error::ApiError;
use crate::write_support::{decode_json, internal_error};
use crate::{AppState, request_id_from_headers};

static SEARCH_GATEWAY: OnceLock<Result<BriefingDigGateway, String>> = OnceLock::new();

pub(super) fn router() -> Router<AppState> {
    Router::new()
        .route("/api/agent/search", post(search_agent))
        .route(
            "/api/agent/library/manifest",
            get(get_agent_library_manifest),
        )
        .route("/api/agent/library/file", get(get_agent_library_file))
}

#[utoipa::path(
    post,
    path = "/api/agent/search",
    operation_id = "searchAgent",
    tag = "agent",
    request_body = AgentSearchRequest,
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = AgentSearchResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 502, description = "Search provider failed", body = newsly_contracts::ErrorEnvelope),
        (status = 503, description = "Search provider unavailable", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn search_agent(
    State(state): State<AppState>,
    headers: HeaderMap,
    _current_user: AuthenticatedUser,
    payload: Result<Json<AgentSearchRequest>, JsonRejection>,
) -> Result<Json<AgentSearchResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    let Json(payload) = decode_json(payload, &request_id)?;
    let query = payload.query.trim();
    if !(2..=200).contains(&query.chars().count()) || !(1..=25).contains(&payload.limit) {
        return Err(ApiError::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            "validation_error",
            "query must contain 2-200 characters and limit must be 1-25",
            request_id,
        ));
    }

    let web = search_gateway(&request_id)?
        .search_limit(query, payload.limit)
        .await
        .map_err(|error| search_provider_error(&error, &request_id))?;
    let podcasts = if payload.include_podcasts {
        state
            .content_misc
            .search_podcast_episodes(query, payload.limit)
            .await
            .map_err(|error| podcast_provider_error(&error, &request_id))?
    } else {
        Vec::new()
    };
    let mut results = web.into_iter().map(present_web).collect::<Vec<_>>();
    results.extend(podcasts.into_iter().map(present_podcast));
    results.truncate(payload.limit);
    Ok(Json(AgentSearchResponse { results }))
}

#[derive(Debug, Deserialize)]
pub(super) struct AgentLibraryManifestQuery {
    #[serde(default = "default_true")]
    include_source: bool,
}

#[utoipa::path(
    get,
    path = "/api/agent/library/manifest",
    operation_id = "getAgentLibraryManifest",
    tag = "agent",
    params(("include_source" = Option<bool>, Query, description = "Include full source documents")),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = AgentLibraryManifestResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 503, description = "Personal markdown library disabled", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn get_agent_library_manifest(
    State(state): State<AppState>,
    headers: HeaderMap,
    query: Result<Query<AgentLibraryManifestQuery>, QueryRejection>,
    current_user: AuthenticatedUser,
) -> Result<Json<AgentLibraryManifestResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_personal_markdown(&request_id)?;
    let Query(query) = query.map_err(|rejection| {
        ApiError::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            "validation_error",
            rejection.body_text(),
            request_id.clone(),
        )
    })?;
    let documents =
        render_library_documents(&state, current_user.id, query.include_source, &request_id)
            .await?;
    Ok(Json(AgentLibraryManifestResponse {
        generated_at: Utc::now(),
        include_source: query.include_source,
        documents: documents
            .into_iter()
            .map(RenderedDocument::manifest)
            .collect(),
    }))
}

#[derive(Debug, Deserialize)]
pub(super) struct AgentLibraryFileQuery {
    path: String,
}

#[utoipa::path(
    get,
    path = "/api/agent/library/file",
    operation_id = "getAgentLibraryFile",
    tag = "agent",
    params(("path" = String, Query, min_length = 1, max_length = 1024)),
    security(("HTTPBearer" = [])),
    responses(
        (status = 200, description = "Successful Response", body = AgentLibraryFileResponse),
        (status = 401, description = "Invalid credentials", body = newsly_contracts::ErrorEnvelope),
        (status = 404, description = "Library document not found", body = newsly_contracts::ErrorEnvelope),
        (status = 422, description = "Validation Error", body = newsly_contracts::ErrorEnvelope),
        (status = 503, description = "Personal markdown library disabled", body = newsly_contracts::ErrorEnvelope),
        (status = 500, description = "Internal server error", body = newsly_contracts::ErrorEnvelope)
    )
)]
pub(super) async fn get_agent_library_file(
    State(state): State<AppState>,
    headers: HeaderMap,
    query: Result<Query<AgentLibraryFileQuery>, QueryRejection>,
    current_user: AuthenticatedUser,
) -> Result<Json<AgentLibraryFileResponse>, ApiError> {
    let request_id = request_id_from_headers(&headers);
    require_personal_markdown(&request_id)?;
    let Query(query) = query.map_err(|rejection| {
        ApiError::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            "validation_error",
            rejection.body_text(),
            request_id.clone(),
        )
    })?;
    if query.path.is_empty() || query.path.len() > 1_024 {
        return Err(ApiError::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            "validation_error",
            "path must contain 1-1024 bytes",
            request_id,
        ));
    }
    let document = render_library_documents(&state, current_user.id, true, &request_id)
        .await?
        .into_iter()
        .find(|document| document.relative_path == query.path)
        .ok_or_else(|| {
            ApiError::new(
                StatusCode::NOT_FOUND,
                "not_found",
                "Library document not found",
                request_id.clone(),
            )
        })?;
    Ok(Json(document.file()))
}

#[derive(Debug, Clone)]
struct RenderedDocument {
    relative_path: String,
    content_id: i64,
    variant: AgentLibraryDocumentVariant,
    updated_at: Option<DateTime<Utc>>,
    checksum_sha256: String,
    text: String,
}

impl RenderedDocument {
    fn manifest(self) -> AgentLibraryDocumentResponse {
        AgentLibraryDocumentResponse {
            relative_path: self.relative_path,
            content_id: self.content_id,
            variant: self.variant,
            updated_at: self.updated_at,
            size_bytes: self.text.len(),
            checksum_sha256: self.checksum_sha256,
        }
    }

    fn file(self) -> AgentLibraryFileResponse {
        AgentLibraryFileResponse {
            relative_path: self.relative_path,
            content_id: self.content_id,
            variant: self.variant,
            updated_at: self.updated_at,
            checksum_sha256: self.checksum_sha256,
            text: self.text,
        }
    }
}

async fn render_library_documents(
    state: &AppState,
    user_id: i64,
    include_source: bool,
    request_id: &str,
) -> Result<Vec<RenderedDocument>, ApiError> {
    let rows = list_agent_library_content(state.database.pool(), user_id)
        .await
        .map_err(|error| internal_error(error, request_id))?;
    let mut documents = Vec::with_capacity(rows.len() * usize::from(include_source) + rows.len());
    for row in rows {
        let rendered_stored =
            load_pointer_text(state, row.rendered_body.as_ref(), request_id).await?;
        let source_stored = if include_source {
            load_pointer_text(state, row.source_body.as_ref(), request_id).await?
        } else {
            None
        };
        if include_source
            && let Some(body) = source_stored.or_else(|| fallback_source(&row))
            && !body.trim().is_empty()
        {
            documents.push(render_document(
                user_id,
                &row,
                AgentLibraryDocumentVariant::Source,
                body.trim(),
                row.source_body
                    .as_ref()
                    .and_then(|pointer| pointer.updated_at)
                    .or(row.updated_at),
            ));
        }
        if let Some(body) = rendered_stored.or_else(|| fallback_summary(&row))
            && !body.trim().is_empty()
        {
            documents.push(render_document(
                user_id,
                &row,
                AgentLibraryDocumentVariant::Summary,
                body.trim(),
                row.rendered_body
                    .as_ref()
                    .and_then(|pointer| pointer.updated_at)
                    .or(row.updated_at),
            ));
        }
    }
    Ok(documents)
}

async fn load_pointer_text(
    state: &AppState,
    pointer: Option<&newsly_db::AgentLibraryBodyPointer>,
    request_id: &str,
) -> Result<Option<String>, ApiError> {
    let Some(pointer) = pointer else {
        return Ok(None);
    };
    state
        .content_body_store
        .get_text(&pointer.storage_key)
        .await
        .map_err(|error| internal_error(error, request_id))
}

fn render_document(
    user_id: i64,
    row: &AgentLibraryContentProjection,
    variant: AgentLibraryDocumentVariant,
    body: &str,
    updated_at: Option<DateTime<Utc>>,
) -> RenderedDocument {
    let source = content_source(row);
    let title = clean(row.title.as_deref()).unwrap_or_else(|| "Untitled".to_owned());
    let date = content_date(row);
    let variant_name = match variant {
        AgentLibraryDocumentVariant::Source => "source",
        AgentLibraryDocumentVariant::Summary => "summary",
    };
    let relative_path = format!(
        "{}/{}/{}__{}__{}__c{}.md",
        slug(&row.content_type, None),
        slug(&source, None),
        slug(&title, Some(max_slug_length())),
        date.format("%Y-%m-%d"),
        variant_name,
        row.content_id,
    );
    let mut reasons = Vec::new();
    if row.saved_to_knowledge {
        reasons.push("saved_to_knowledge");
    }
    if !row.chat_session_ids.is_empty() {
        reasons.push("chatted");
    }
    let published_at = row.publication_date.or(Some(date));
    let saved_at = row.saved_at.or(Some(row.created_at));
    let frontmatter = format!(
        concat!(
            "content_id: {}\n",
            "user_id: {}\n",
            "content_type: {}\n",
            "variant: {}\n",
            "title: {}\n",
            "source: {}\n",
            "url: {}\n",
            "published_at: {}\n",
            "saved_at: {}\n",
            "reasons: {}\n",
            "chat_session_ids: {}"
        ),
        row.content_id,
        user_id,
        yaml_string(&row.content_type),
        yaml_string(variant_name),
        yaml_string(&title),
        yaml_string(&source),
        yaml_string(&row.url),
        yaml_optional_datetime(published_at),
        yaml_optional_datetime(saved_at),
        serde_json::to_string(&reasons).expect("string slices serialize"),
        serde_json::to_string(&row.chat_session_ids).expect("integer ids serialize"),
    );
    let text = format!("---\n{frontmatter}\n---\n\n{body}\n");
    let checksum_sha256 = hex_encode(&Sha256::digest(text.as_bytes()));
    RenderedDocument {
        relative_path,
        content_id: row.content_id,
        variant,
        updated_at,
        checksum_sha256,
        text,
    }
}

fn fallback_source(row: &AgentLibraryContentProjection) -> Option<String> {
    let metadata = row.content_metadata.as_object()?;
    if row.content_type == "podcast" {
        clean_value(metadata.get("transcript"))
            .or_else(|| clean_value(metadata.get("content_to_summarize")))
    } else {
        clean_value(metadata.get("content_to_summarize"))
            .or_else(|| clean_value(metadata.get("content")))
    }
}

fn fallback_summary(row: &AgentLibraryContentProjection) -> Option<String> {
    let summary = row.content_metadata.get("summary")?;
    if let Some(text) = summary.as_str().and_then(|value| clean(Some(value))) {
        return Some(text);
    }
    let summary = summary.as_object()?;
    if let Some(markdown) = clean_value(summary.get("full_markdown")) {
        return Some(markdown);
    }
    let mut sections = Vec::new();
    let title = clean_value(summary.get("title")).or_else(|| clean(row.title.as_deref()));
    if let Some(title) = title {
        sections.push(format!("# {title}"));
    }
    if let Some(overview) = summary_text(summary) {
        sections.push(overview);
    }
    let points = summary
        .get("bullet_points")
        .or_else(|| summary.get("key_points"))
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(|item| {
                    item.as_object()
                        .and_then(|item| {
                            clean_value(item.get("text").or_else(|| item.get("point")))
                        })
                        .or_else(|| item.as_str().and_then(|value| clean(Some(value))))
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    if !points.is_empty() {
        sections.push("## Key Points".to_owned());
        sections.extend(points.into_iter().map(|point| format!("- {point}")));
    }
    let topics = summary
        .get("topics")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(|item| item.as_str().and_then(|value| clean(Some(value))))
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    if !topics.is_empty() {
        sections.push("## Topics".to_owned());
        sections.extend(topics.into_iter().map(|topic| format!("- {topic}")));
    }
    (!sections.is_empty()).then(|| sections.join("\n\n"))
}

fn summary_text(summary: &serde_json::Map<String, Value>) -> Option<String> {
    clean_value(summary.get("one_line"))
        .or_else(|| {
            summary
                .get("artifact")
                .and_then(Value::as_object)
                .and_then(|artifact| artifact.get("payload"))
                .and_then(Value::as_object)
                .and_then(|payload| clean_value(payload.get("overview")))
        })
        .or_else(|| clean_value(summary.get("overview")))
        .or_else(|| clean_value(summary.get("summary")))
        .or_else(|| clean_value(summary.get("hook")))
        .or_else(|| clean_value(summary.get("takeaway")))
}

fn content_source(row: &AgentLibraryContentProjection) -> String {
    clean(row.source.as_deref())
        .or_else(|| {
            let metadata = row.content_metadata.as_object()?;
            ["podcast_title", "show_name", "source"]
                .into_iter()
                .find_map(|key| clean_value(metadata.get(key)))
        })
        .unwrap_or_else(|| "unknown-source".to_owned())
}

fn content_date(row: &AgentLibraryContentProjection) -> DateTime<Utc> {
    row.publication_date
        .or(Some(row.created_at))
        .or(row.updated_at)
        .unwrap_or_else(Utc::now)
}

fn slug(value: &str, max_length: Option<usize>) -> String {
    let mut result = String::with_capacity(value.len());
    let mut pending_separator = false;
    for character in value.trim().to_ascii_lowercase().chars() {
        if character.is_ascii_alphanumeric() {
            if pending_separator && !result.is_empty() {
                result.push('-');
            }
            result.push(character);
            pending_separator = false;
        } else {
            pending_separator = true;
        }
        if max_length.is_some_and(|limit| result.len() >= limit) {
            break;
        }
    }
    let result = result.trim_matches('-');
    if result.is_empty() {
        "untitled".to_owned()
    } else {
        result.to_owned()
    }
}

fn clean(value: Option<&str>) -> Option<String> {
    value
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
}

fn clean_value(value: Option<&Value>) -> Option<String> {
    value
        .and_then(Value::as_str)
        .and_then(|value| clean(Some(value)))
}

fn yaml_string(value: &str) -> String {
    serde_json::to_string(value).expect("string serializes")
}

fn yaml_optional_datetime(value: Option<DateTime<Utc>>) -> String {
    value.map_or_else(
        || "null".to_owned(),
        |value| yaml_string(&value.to_rfc3339()),
    )
}

fn max_slug_length() -> usize {
    env::var("PERSONAL_MARKDOWN_MAX_SLUG_LENGTH")
        .ok()
        .and_then(|value| value.parse::<usize>().ok())
        .filter(|value| (16..=160).contains(value))
        .unwrap_or(80)
}

fn require_personal_markdown(request_id: &str) -> Result<(), ApiError> {
    let enabled = env::var("PERSONAL_MARKDOWN_ENABLED")
        .ok()
        .is_none_or(|value| {
            matches!(
                value.trim().to_ascii_lowercase().as_str(),
                "1" | "true" | "yes" | "on"
            )
        });
    if enabled {
        Ok(())
    } else {
        Err(ApiError::new(
            StatusCode::SERVICE_UNAVAILABLE,
            "personal_markdown_disabled",
            "Personal markdown library is disabled",
            request_id,
        ))
    }
}

fn search_gateway(request_id: &str) -> Result<&'static BriefingDigGateway, ApiError> {
    SEARCH_GATEWAY
        .get_or_init(|| BriefingDigGateway::from_env().map_err(|error| error.to_string()))
        .as_ref()
        .map_err(|message| {
            tracing::error!(error = %message, "agent search gateway configuration failed");
            ApiError::new(
                StatusCode::SERVICE_UNAVAILABLE,
                "search_unavailable",
                "External search is unavailable",
                request_id,
            )
            .with_retryable(true)
        })
}

fn search_provider_error(error: &BriefingDigGatewayError, request_id: &str) -> ApiError {
    let unavailable = matches!(error, BriefingDigGatewayError::ExaUnavailable);
    tracing::error!(error = %error, "agent web search failed");
    ApiError::new(
        if unavailable {
            StatusCode::SERVICE_UNAVAILABLE
        } else {
            StatusCode::BAD_GATEWAY
        },
        if unavailable {
            "search_unavailable"
        } else {
            "search_provider_error"
        },
        if unavailable {
            "External search is unavailable"
        } else {
            "External search provider failed"
        },
        request_id,
    )
    .with_retryable(true)
}

fn podcast_provider_error(error: &ContentMiscGatewayError, request_id: &str) -> ApiError {
    tracing::error!(error = %error, "agent podcast search failed");
    ApiError::new(
        StatusCode::BAD_GATEWAY,
        "search_provider_error",
        "Podcast search provider failed",
        request_id,
    )
    .with_retryable(true)
}

fn present_web(hit: BriefingWebSearchResult) -> AgentSearchResultResponse {
    AgentSearchResultResponse {
        kind: AgentSearchResultKind::Web,
        title: hit.title,
        url: hit.url,
        snippet: hit.snippet,
        source: Some("exa".to_owned()),
        provider: Some("exa".to_owned()),
        feed_url: None,
        published_at: hit.published_date,
        score: None,
    }
}

fn present_podcast(hit: PodcastEpisodeHit) -> AgentSearchResultResponse {
    AgentSearchResultResponse {
        kind: AgentSearchResultKind::Podcast,
        title: hit.title,
        url: hit.episode_url,
        snippet: hit.snippet,
        source: hit.source,
        provider: Some(hit.provider),
        feed_url: hit.feed_url,
        published_at: hit.published_at,
        score: hit.score,
    }
}

const fn default_true() -> bool {
    true
}
