use axum::http::StatusCode;
use newsly_contracts::LearningDeckCreateRequest;
use newsly_db::{LearningDeckSourceProjection, VisibleNewsItemProjection};
use percent_encoding::percent_decode_str;
use reqwest::Url;
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};

use crate::encoding::hex_encode;
use crate::error::ApiError;
use crate::write_support::internal_error;

use super::support::{clean_optional, deck_error, validation_error};

const MAX_URL_CHARS: usize = 2_048;
const MAX_INTERESTS_CHARS: usize = 4_000;

pub(super) struct ValidatedCreateRequest {
    pub(super) source: CreateSource,
    pub(super) interests_prompt: Option<String>,
}

pub(super) enum CreateSource {
    Content(i64),
    NewsItem(i64),
    Url(String),
}

impl ValidatedCreateRequest {
    pub(super) fn try_from(
        payload: LearningDeckCreateRequest,
        request_id: &str,
    ) -> Result<Self, ApiError> {
        if payload
            .interests_prompt
            .as_ref()
            .is_some_and(|value| value.chars().count() > MAX_INTERESTS_CHARS)
        {
            return Err(validation_error(
                "interests_prompt must contain at most 4000 characters",
                request_id,
            ));
        }
        if payload
            .url
            .as_ref()
            .is_some_and(|value| value.chars().count() > MAX_URL_CHARS)
        {
            return Err(validation_error(
                "url must contain at most 2048 characters",
                request_id,
            ));
        }
        if payload.content_id.is_some_and(|value| value <= 0)
            || payload.news_item_id.is_some_and(|value| value <= 0)
        {
            return Err(validation_error(
                "content_id and news_item_id must be greater than zero",
                request_id,
            ));
        }
        let source_count = usize::from(payload.content_id.is_some())
            + usize::from(payload.news_item_id.is_some())
            + usize::from(payload.url.is_some());
        if source_count != 1 {
            return Err(validation_error(
                "Provide exactly one of content_id, news_item_id, or url",
                request_id,
            ));
        }
        let source = if let Some(content_id) = payload.content_id {
            CreateSource::Content(content_id)
        } else if let Some(news_item_id) = payload.news_item_id {
            CreateSource::NewsItem(news_item_id)
        } else {
            let url = payload.url.expect("source count proves URL exists");
            if url.is_empty() {
                return Err(validation_error(
                    "url must contain at least one character",
                    request_id,
                ));
            }
            CreateSource::Url(url)
        };
        Ok(Self {
            source,
            interests_prompt: clean_optional(payload.interests_prompt),
        })
    }
}

#[expect(
    clippy::too_many_lines,
    reason = "GitHub URL normalization implements one ordered grammar with shared validation"
)]
pub(super) fn github_learning_deck_source(
    raw_url: &str,
    request_id: &str,
) -> Result<Option<LearningDeckSourceProjection>, ApiError> {
    let Ok(url) = Url::parse(raw_url.trim()) else {
        return Ok(None);
    };
    if !matches!(url.scheme(), "http" | "https") {
        return Ok(None);
    }
    let host = url.host_str().unwrap_or_default().to_ascii_lowercase();
    let raw_parts = url
        .path_segments()
        .into_iter()
        .flatten()
        .filter(|part| !part.is_empty())
        .map(|part| {
            percent_decode_str(part)
                .decode_utf8()
                .map(std::borrow::Cow::into_owned)
                .map_err(|error| internal_error(error, request_id))
        })
        .collect::<Result<Vec<_>, _>>()?;
    let file = if matches!(host.as_str(), "github.com" | "www.github.com")
        && raw_parts.len() >= 5
        && matches!(raw_parts[2].as_str(), "blob" | "raw")
    {
        Some((
            raw_parts[0].clone(),
            raw_parts[1].trim_end_matches(".git").to_owned(),
            raw_parts[3].clone(),
            raw_parts[4..].join("/"),
        ))
    } else if host == "raw.githubusercontent.com" && raw_parts.len() >= 4 {
        Some((
            raw_parts[0].clone(),
            raw_parts[1].trim_end_matches(".git").to_owned(),
            raw_parts[2].clone(),
            raw_parts[3..].join("/"),
        ))
    } else {
        None
    };
    if let Some((owner, repo, git_ref, path)) = file {
        if owner.is_empty() || repo.is_empty() || git_ref.is_empty() || path.is_empty() {
            return Ok(None);
        }
        let canonical_blob_url =
            github_file_url("https://github.com", &owner, &repo, "blob", &git_ref, &path);
        let raw_url = github_file_url(
            "https://raw.githubusercontent.com",
            &owner,
            &repo,
            "",
            &git_ref,
            &path,
        );
        let filename = path.rsplit('/').next().unwrap_or(&path);
        let title = format!("{owner}/{repo}: {filename}");
        let identity = bounded_github_identity(
            format!(
                "github:{}/{}:file:{git_ref}/{path}",
                owner.to_ascii_lowercase(),
                repo.to_ascii_lowercase()
            ),
            &owner,
            &repo,
        );
        return Ok(Some(LearningDeckSourceProjection {
            source_kind: "github_repo".to_owned(),
            source_identity: identity,
            source_url: Some(canonical_blob_url.clone()),
            source_content_id: None,
            source_title: title.clone(),
            source_metadata: Map::from_iter([
                ("owner".to_owned(), Value::from(owner.clone())),
                ("repo".to_owned(), Value::from(repo.clone())),
                (
                    "repo_url".to_owned(),
                    Value::from(format!("https://github.com/{owner}/{repo}")),
                ),
                ("title".to_owned(), Value::from(title)),
                (
                    "linked_artifact".to_owned(),
                    json!({
                        "url": canonical_blob_url,
                        "raw_url": raw_url,
                        "path": path,
                        "filename": filename,
                        "ref": git_ref,
                        "content_type": filename.to_ascii_lowercase().ends_with(".pdf").then_some("pdf"),
                    }),
                ),
            ]),
        }));
    }
    if !matches!(host.as_str(), "github.com" | "www.github.com") {
        return Ok(None);
    }
    if raw_parts.len() < 2 {
        return Err(deck_error(
            StatusCode::BAD_REQUEST,
            "GitHub URL must include owner and repository",
            request_id,
        ));
    }
    let owner = raw_parts[0].clone();
    let repo = raw_parts[1].trim_end_matches(".git").to_owned();
    if owner.is_empty() || repo.is_empty() {
        return Err(deck_error(
            StatusCode::BAD_REQUEST,
            "GitHub URL must include owner and repository",
            request_id,
        ));
    }
    Ok(Some(LearningDeckSourceProjection {
        source_kind: "github_repo".to_owned(),
        source_identity: format!(
            "github:{}/{}",
            owner.to_ascii_lowercase(),
            repo.to_ascii_lowercase()
        ),
        source_url: Some(format!("https://github.com/{owner}/{repo}")),
        source_content_id: None,
        source_title: format!("{owner}/{repo}"),
        source_metadata: Map::from_iter([
            ("owner".to_owned(), Value::from(owner)),
            ("repo".to_owned(), Value::from(repo)),
        ]),
    }))
}

fn github_file_url(
    origin: &str,
    owner: &str,
    repo: &str,
    marker: &str,
    git_ref: &str,
    path: &str,
) -> String {
    let mut url = Url::parse(origin).expect("static GitHub origin is valid");
    {
        let mut segments = url
            .path_segments_mut()
            .expect("GitHub URL supports path segments");
        segments.push(owner).push(repo);
        if !marker.is_empty() {
            segments.push(marker);
        }
        segments.push(git_ref);
        for part in path.split('/') {
            segments.push(part);
        }
    }
    url.to_string().trim_end_matches('/').to_owned()
}

fn bounded_github_identity(identity: String, owner: &str, repo: &str) -> String {
    if identity.len() <= 512 {
        return identity;
    }
    let digest = Sha256::digest(identity.as_bytes());
    format!(
        "github:{}/{}:file:{}",
        owner.to_ascii_lowercase(),
        repo.to_ascii_lowercase(),
        hex_encode(&digest)
    )
}

pub(super) fn normalize_submitted_url(raw_url: &str, request_id: &str) -> Result<String, ApiError> {
    let parsed = Url::parse(raw_url.trim())
        .map_err(|_| validation_error("url must be a valid HTTP URL", request_id))?;
    if !matches!(parsed.scheme(), "http" | "https") || parsed.host().is_none() {
        return Err(validation_error(
            "url must use http or https and include a host",
            request_id,
        ));
    }
    Ok(parsed.to_string())
}

pub(super) fn normalize_news_article_url(
    item: &VisibleNewsItemProjection,
    request_id: &str,
) -> Result<String, ApiError> {
    let candidate = item
        .article_url
        .as_deref()
        .or(item.canonical_story_url.as_deref())
        .unwrap_or_default()
        .trim();
    let candidate = if let Some(rest) = candidate.strip_prefix("//") {
        format!("https://{rest}")
    } else if Url::parse(candidate).is_ok() {
        candidate.to_owned()
    } else {
        format!("https://{candidate}")
    };
    let mut url = Url::parse(&candidate).map_err(|_| {
        deck_error(
            StatusCode::BAD_REQUEST,
            "No article URL found for Fast Read",
            request_id,
        )
    })?;
    if !matches!(url.scheme(), "http" | "https") || url.host().is_none() {
        return Err(deck_error(
            StatusCode::BAD_REQUEST,
            "No article URL found for Fast Read",
            request_id,
        ));
    }
    url.set_scheme("https").map_err(|()| {
        deck_error(
            StatusCode::BAD_REQUEST,
            "No article URL found for Fast Read",
            request_id,
        )
    })?;
    Ok(url.to_string())
}
