use std::collections::BTreeMap;

use chrono::{DateTime, Utc};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use utoipa::ToSchema;

use crate::{ContentClassification, ContentStatus, ContentType, PaginationMetadata};

/// Canonical short-form News card data.
///
/// `content_type` and `is_saved_to_knowledge` remain on the wire while installed clients still
/// decode this endpoint through their legacy Content card adapter. They have one truthful value
/// for this News-owned response and are not used as a cross-product identity fallback.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct NewsItemSummaryResponse {
    pub id: i64,
    pub content_type: ContentType,
    pub url: String,
    pub source_url: Option<String>,
    pub discussion_url: Option<String>,
    pub title: String,
    pub source: Option<String>,
    pub platform: Option<String>,
    pub status: ContentStatus,
    pub short_summary: Option<String>,
    #[schemars(with = "String")]
    #[schema(value_type = String, format = DateTime)]
    pub created_at: DateTime<Utc>,
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = DateTime)]
    pub processed_at: Option<DateTime<Utc>>,
    pub classification: Option<ContentClassification>,
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = DateTime)]
    pub publication_date: Option<DateTime<Utc>>,
    #[serde(default)]
    pub is_read: bool,
    #[serde(default)]
    pub is_saved_to_knowledge: bool,
    pub news_article_url: Option<String>,
    pub news_discussion_url: Option<String>,
    pub news_key_points: Option<Vec<String>>,
    pub news_summary: Option<String>,
    pub top_comment: Option<BTreeMap<String, String>>,
    pub comment_count: Option<i64>,
}

/// Canonical short-form News detail data.
///
/// The constant compatibility fields keep already-installed Content-detail decoders working while
/// the response omits Content-only processing, artwork, feed, and long-form summary state.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema, ToSchema)]
#[allow(clippy::struct_excessive_bools)]
pub struct NewsItemDetailResponse {
    pub id: i64,
    pub content_type: ContentType,
    pub url: String,
    pub source_url: Option<String>,
    pub discussion_url: Option<String>,
    pub title: String,
    pub display_title: String,
    pub source: Option<String>,
    pub status: ContentStatus,
    pub retry_count: i32,
    pub metadata: Map<String, Value>,
    #[schemars(with = "String")]
    #[schema(value_type = String, format = DateTime)]
    pub created_at: DateTime<Utc>,
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = DateTime)]
    pub updated_at: Option<DateTime<Utc>>,
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = DateTime)]
    pub processed_at: Option<DateTime<Utc>>,
    #[schemars(with = "Option<String>")]
    #[schema(value_type = Option<String>, format = DateTime)]
    pub publication_date: Option<DateTime<Utc>>,
    #[serde(default)]
    pub is_read: bool,
    #[serde(default)]
    pub is_saved_to_knowledge: bool,
    pub summary: Option<String>,
    pub short_summary: Option<String>,
    #[serde(default)]
    pub body_available: bool,
    pub body_kind: Option<String>,
    pub body_format: Option<String>,
    pub news_article_url: Option<String>,
    pub news_discussion_url: Option<String>,
    pub news_key_points: Option<Vec<String>>,
    pub news_summary: Option<String>,
    #[serde(default)]
    pub can_subscribe: bool,
}

/// Paginated canonical News feed.
///
/// The wrapper key names intentionally remain compatible with installed clients. The item type is
/// News-owned, and `content_types` truthfully contains only `news`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema, ToSchema)]
pub struct NewsItemListResponse {
    pub contents: Vec<NewsItemSummaryResponse>,
    pub available_dates: Vec<String>,
    pub content_types: Vec<ContentType>,
    pub meta: PaginationMetadata,
}

#[cfg(test)]
mod tests {
    use chrono::TimeZone as _;
    use serde_json::json;

    use super::*;

    #[test]
    fn news_detail_keeps_required_legacy_keys_without_the_content_bag() {
        let response = NewsItemDetailResponse {
            id: 42,
            content_type: ContentType::News,
            url: "https://example.com/story".to_owned(),
            source_url: Some("https://example.com/discussion".to_owned()),
            discussion_url: Some("https://example.com/discussion".to_owned()),
            title: "Canonical title".to_owned(),
            display_title: "Canonical title".to_owned(),
            source: Some("Example".to_owned()),
            status: ContentStatus::Completed,
            retry_count: 0,
            metadata: Map::new(),
            created_at: Utc
                .with_ymd_and_hms(2026, 8, 31, 12, 0, 0)
                .single()
                .expect("valid timestamp"),
            updated_at: None,
            processed_at: None,
            publication_date: None,
            is_read: false,
            is_saved_to_knowledge: false,
            summary: Some("Summary".to_owned()),
            short_summary: Some("Summary".to_owned()),
            body_available: false,
            body_kind: None,
            body_format: None,
            news_article_url: Some("https://example.com/story".to_owned()),
            news_discussion_url: Some("https://example.com/discussion".to_owned()),
            news_key_points: Some(vec!["Point".to_owned()]),
            news_summary: Some("Summary".to_owned()),
            can_subscribe: false,
        };

        let value = serde_json::to_value(response).expect("serialize News detail");
        assert_eq!(value["content_type"], "news");
        assert_eq!(value["retry_count"], 0);
        assert_eq!(value["metadata"], json!({}));
        assert_eq!(value["is_saved_to_knowledge"], false);
        assert_eq!(value["body_available"], false);
        assert_eq!(value["can_subscribe"], false);
        for content_only_field in [
            "checked_out_by",
            "checked_out_at",
            "structured_summary",
            "longform_artifact",
            "feed_preview",
            "artifact_type",
            "bullet_points",
            "quotes",
            "topics",
            "detected_feed",
        ] {
            assert!(
                value.get(content_only_field).is_none(),
                "{content_only_field} must not leak into canonical News detail"
            );
        }
    }

    #[test]
    fn news_list_preserves_the_installed_client_wrapper() {
        let response = NewsItemListResponse {
            contents: Vec::new(),
            available_dates: vec!["2026-08-31".to_owned()],
            content_types: vec![ContentType::News],
            meta: PaginationMetadata {
                next_cursor: None,
                has_more: false,
                page_size: 0,
                total: Some(0),
            },
        };

        assert_eq!(
            serde_json::to_value(response).expect("serialize News list"),
            json!({
                "contents": [],
                "available_dates": ["2026-08-31"],
                "content_types": ["news"],
                "meta": {
                    "next_cursor": null,
                    "has_more": false,
                    "page_size": 0,
                    "total": 0
                }
            })
        );
    }
}
