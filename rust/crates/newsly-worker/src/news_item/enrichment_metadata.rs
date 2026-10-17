//! Product projection for article evidence and demand-scoped paid fallback.
use super::{EnrichmentMutation, NewsSnapshot, normalize_http_url};
use chrono::Utc;
use serde::Serialize;
use serde_json::{Map, Value, json};

#[derive(Serialize)]
struct EnrichmentMetadata {
    status: &'static str,
    source: Option<&'static str>,
    article_url: Option<String>,
    final_url: Option<String>,
    strategy: Option<String>,
    error: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error_code: Option<&'static str>,
    extracted_chars: i32,
    updated_at: String,
}

#[allow(clippy::too_many_lines)]
pub(super) fn apply_enrichment_metadata(
    metadata: &mut Map<String, Value>,
    snapshot: &NewsSnapshot,
    mutation: &EnrichmentMutation,
    finalized_at: chrono::DateTime<Utc>,
) -> Value {
    // A newly available full article must replace the metadata-only summary.
    if metadata
        .get("article_extraction")
        .and_then(|value| value.get("error_code"))
        .and_then(Value::as_str)
        == Some(newsly_db::aggregator_corpus::FIRECRAWL_NO_DEMAND_ERROR_CODE)
        && matches!(
            mutation,
            EnrichmentMutation::Storage { .. } | EnrichmentMutation::Content { .. }
        )
    {
        metadata.remove("summary_version");
    }
    let updated_at = finalized_at.to_rfc3339();
    let result = match mutation {
        EnrichmentMutation::Existing => {
            let url = metadata
                .get("article_body_ref")
                .and_then(Value::as_object)
                .and_then(|reference| {
                    reference
                        .get("final_url")
                        .or_else(|| reference.get("source_url"))
                })
                .and_then(Value::as_str)
                .and_then(normalize_http_url);
            let article_url = snapshot
                .article_url
                .clone()
                .or_else(|| snapshot.canonical_story_url.clone())
                .or_else(|| url.clone());
            EnrichmentMetadata {
                status: "completed",
                source: Some("existing"),
                article_url,
                final_url: url,
                strategy: None,
                error: None,
                error_code: None,
                extracted_chars: 0,
                updated_at,
            }
        }
        EnrichmentMutation::Metadata { text, source_url } => {
            metadata.insert(
                "article_body_ref".to_owned(),
                json!({
                    "kind": "inline",
                    "text": text,
                    "source_url": source_url,
                    "updated_at": updated_at,
                }),
            );
            EnrichmentMetadata {
                status: "completed",
                source: Some("metadata"),
                article_url: source_url.clone(),
                final_url: source_url.clone(),
                strategy: None,
                error: None,
                error_code: None,
                extracted_chars: i32::try_from(text.chars().count()).unwrap_or(i32::MAX),
                updated_at,
            }
        }
        EnrichmentMutation::Content {
            content_id,
            article_url,
            final_url,
            extracted_chars,
            source_metadata,
        } => {
            metadata.insert(
                "article_body_ref".to_owned(),
                json!({
                    "kind": "content",
                    "content_id": content_id,
                    "variant": "source",
                    "source_url": article_url,
                    "updated_at": updated_at,
                }),
            );
            if let Some(source_metadata) =
                source_metadata.as_ref().filter(|value| value.is_object())
            {
                metadata.insert("source_metadata".to_owned(), source_metadata.clone());
            }
            EnrichmentMetadata {
                status: "completed",
                source: Some("content"),
                article_url: Some(article_url.clone()),
                final_url: final_url.clone(),
                strategy: None,
                error: None,
                error_code: None,
                extracted_chars: *extracted_chars,
                updated_at,
            }
        }
        EnrichmentMutation::Storage {
            article_url,
            final_url,
            title,
            extraction_method,
            body,
            ..
        } => {
            metadata.insert(
                "article_body_ref".to_owned(),
                json!({
                    "kind": "storage",
                    "storage_provider": body.storage_provider,
                    "storage_bucket": body.storage_bucket,
                    "storage_key": body.storage_key,
                    "content_format": body.content_format,
                    "sha256": body.sha256,
                    "byte_size": body.byte_size,
                    "char_count": body.char_count,
                    "source_url": article_url,
                    "final_url": final_url,
                    "updated_at": updated_at,
                }),
            );
            if let Some(title) = title {
                let article = metadata
                    .entry("article".to_owned())
                    .or_insert_with(|| Value::Object(Map::new()));
                if let Some(article) = article.as_object_mut()
                    && !article.get("title").is_some_and(Value::is_string)
                {
                    article.insert("title".to_owned(), Value::String(title.clone()));
                }
            }
            EnrichmentMetadata {
                status: "completed",
                source: Some("storage"),
                article_url: Some(article_url.clone()),
                final_url: Some(final_url.clone()),
                strategy: Some(extraction_method.clone()),
                error: None,
                error_code: None,
                extracted_chars: body.char_count,
                updated_at,
            }
        }
        EnrichmentMutation::Skipped {
            article_url,
            reason,
        } => EnrichmentMetadata {
            status: "skipped",
            source: None,
            article_url: article_url.clone(),
            final_url: article_url.clone(),
            strategy: None,
            error: Some(reason.clone()),
            error_code: None,
            extracted_chars: 0,
            updated_at,
        },
        EnrichmentMutation::MetadataOnly {
            article_url,
            reason,
        } => EnrichmentMetadata {
            status: "failed",
            source: None,
            article_url: Some(article_url.clone()),
            final_url: Some(article_url.clone()),
            strategy: Some("metadata_only".to_owned()),
            error: Some(reason.clone()),
            error_code: Some(newsly_db::aggregator_corpus::FIRECRAWL_NO_DEMAND_ERROR_CODE),
            extracted_chars: 0,
            updated_at,
        },
        EnrichmentMutation::Failed {
            article_url,
            final_url,
            strategy,
            reason,
        } => EnrichmentMetadata {
            status: "failed",
            source: None,
            article_url: article_url.clone(),
            final_url: final_url.clone(),
            strategy: strategy.clone(),
            error: Some(reason.clone()),
            error_code: None,
            extracted_chars: 0,
            updated_at,
        },
    };
    let article_extraction = serde_json::to_value(result).expect("enrichment metadata serializes");
    metadata.insert("article_extraction".to_owned(), article_extraction.clone());
    article_extraction
}

pub(super) fn enrichment_domain(mutation: &EnrichmentMutation) -> Option<&str> {
    match mutation {
        EnrichmentMutation::Storage { article_domain, .. } => article_domain.as_deref(),
        _ => None,
    }
}

pub(super) fn published_final_url(mutation: &EnrichmentMutation) -> Option<&str> {
    match mutation {
        EnrichmentMutation::Storage { final_url, .. } => Some(final_url),
        _ => None,
    }
}
