use std::collections::{BTreeMap, HashSet};
use std::str::FromStr;

use newsly_contracts::{
    ContentClassification, ContentDetailResponse, ContentStatus, ContentSummaryBulletPoint,
    ContentSummaryQuote, ContentSummaryResponse, ContentType, DetectedFeed, NewsItemDetailResponse,
    NewsItemSummaryResponse, SavedSource, SummaryKind, SummaryVersion,
};
use newsly_db::{ContentDetailProjection, NewsItemProjection};
use serde_json::{Map, Number, Value};
use thiserror::Error;

const API_METADATA_REDACT_KEYS: &[&str] = &[
    "content",
    "transcript",
    "content_to_summarize",
    "file_path",
    "transcript_path",
    "full_text",
    "storage_key",
    "storage_bucket",
    "article_body_ref",
];
const API_METADATA_INTERNAL_KEYS: &[&str] = &["domain", "processing"];
const API_METADATA_LARGE_VALUE_ALLOWLIST: &[&str] = &[
    "summary",
    "article",
    "aggregator",
    "discussion_url",
    "source",
    "platform",
    "discovery_time",
    "publication_date",
    "top_comment",
    "comment_count",
    "detected_feed",
    "source_type",
    "source_label",
    "source_external_id",
    "author",
    "content_type",
    "workflow_from",
    "workflow_to",
    "workflow_transition",
    "summary_kind",
    "summary_version",
    "summarization_date",
    "interesting_external_links",
    "source_metadata",
];
const API_METADATA_MAX_VALUE_CHARS: usize = 12_000;
const PROCESSING_FIELD_NAMES: &[&str] = &[
    "subscribe_to_feed",
    "feed_subscription",
    "detected_feed",
    "all_detected_feeds",
    "share_and_chat_user_ids",
    "share_and_chat_requests",
    "submitted_by_user_id",
    "submitted_via",
    "platform_hint",
    "content_to_summarize",
    "processing_errors",
    "canonical_content_id",
    "tweet_enrichment",
    "tweet_only",
];
const DISPLAY_SUMMARY_KINDS: &[SummaryKind] = &[
    SummaryKind::LongStructured,
    SummaryKind::LongInterleaved,
    SummaryKind::LongBullets,
    SummaryKind::LongEditorialNarrative,
    SummaryKind::LongformArtifact,
];

pub(crate) fn present_content_detail(
    row: ContentDetailProjection,
) -> Result<ContentDetailResponse, PresentationError> {
    let content_type = ContentType::from_str(&row.content_type)
        .map_err(|_| PresentationError::UnknownContentType(row.content_type.clone()))?;
    let status = ContentStatus::from_str(&row.status)
        .map_err(|_| PresentationError::UnknownContentStatus(row.status.clone()))?;
    let mut metadata = runtime_metadata(&row.content_metadata);
    insert_string_if_missing(&mut metadata, "platform", row.platform.as_deref());
    insert_string_if_missing(&mut metadata, "source", row.source.as_deref());
    backfill_legacy_news_article(&mut metadata, &row, content_type);
    normalize_summary_contract(&mut metadata, content_type);

    let resolved_url = resolve_content_url(&row.url, &metadata, content_type)
        .ok_or_else(|| PresentationError::InvalidContentUrl(row.url.clone()))?;
    let summary_kind = parse_summary_kind(metadata.get("summary_kind"));
    let summary_version = parse_summary_version(metadata.get("summary_version"));
    let summary = object_field(&metadata, "summary");
    let summary_text = extract_short_summary(summary.cloned().map(Value::Object));
    let display_title = resolve_content_display_title(row.title.as_deref(), &metadata);
    let artifact = longform_artifact_fields(&metadata);
    let is_legacy_news = content_type == ContentType::News && artifact.longform_artifact.is_none();

    let (structured_summary, bullet_points, quotes, topics) = if is_legacy_news {
        (None, Vec::new(), Vec::new(), Vec::new())
    } else {
        (
            structured_summary(&metadata, summary_kind),
            project_bullet_points(&metadata, summary_kind, summary_version),
            project_quotes(&metadata, summary_kind, summary_version),
            project_topics(&metadata, summary_kind, summary_version),
        )
    };
    let news_fields = is_legacy_news.then(|| legacy_content_news_fields(&metadata, &resolved_url));
    let discussion_url = if let Some(fields) = &news_fields {
        fields.discussion_url.clone()
    } else {
        string_field(&metadata, "discussion_url")
    };
    let (image_url, thumbnail_url) = resolve_content_image_urls(row.id, content_type, &metadata);
    let detected_feed = detected_feed(&metadata);

    Ok(ContentDetailResponse {
        id: row.id,
        content_type,
        url: resolved_url,
        source_url: row.source_url.or(Some(row.url)),
        discussion_url,
        title: row.title,
        display_title,
        source: row.source,
        status,
        error_message: row.error_message,
        retry_count: row.retry_count,
        metadata: sanitize_metadata_for_api(metadata),
        created_at: row.created_at,
        updated_at: row.updated_at,
        processed_at: row.processed_at,
        checked_out_by: row.checked_out_by,
        checked_out_at: row.checked_out_at,
        publication_date: row.publication_date,
        is_read: row.is_read,
        is_saved_to_knowledge: row.is_saved_to_knowledge,
        summary: summary_text.clone(),
        short_summary: summary_text.clone(),
        summary_kind,
        summary_version,
        structured_summary,
        longform_artifact: artifact.longform_artifact,
        feed_preview: artifact.feed_preview,
        artifact_type: artifact.artifact_type,
        preview_bullets: artifact.preview_bullets,
        reason_to_read: artifact.reason_to_read,
        bullet_points,
        quotes,
        topics,
        full_markdown: None,
        body_available: row.body_available,
        body_kind: row.body_available.then(|| {
            if content_type == ContentType::Podcast {
                "transcript".to_owned()
            } else {
                "article".to_owned()
            }
        }),
        body_format: row.body_format,
        news_article_url: news_fields
            .as_ref()
            .and_then(|fields| fields.article_url.clone()),
        news_discussion_url: news_fields
            .as_ref()
            .and_then(|fields| fields.discussion_url.clone()),
        news_key_points: news_fields
            .as_ref()
            .and_then(|fields| fields.key_points.clone()),
        news_summary: summary_text,
        image_url,
        thumbnail_url,
        detected_feed,
        can_subscribe: false,
    })
}

pub(crate) fn present_content_summary(
    row: ContentDetailProjection,
    knowledge_saved_at: Option<chrono::DateTime<chrono::Utc>>,
    saved_source_override: Option<SavedSource>,
) -> Result<ContentSummaryResponse, PresentationError> {
    let platform = row.platform.clone();
    let detail = present_content_detail(row)?;
    let classification = detail
        .structured_summary
        .as_ref()
        .and_then(|summary| summary.get("classification"))
        .and_then(Value::as_str)
        .and_then(|value| match value {
            "to_read" => Some(ContentClassification::ToRead),
            "skip" => Some(ContentClassification::Skip),
            _ => None,
        });
    let primary_topic = detail
        .topics
        .first()
        .and_then(|value| clean_optional_str(Some(value.as_str())))
        .or_else(|| {
            (detail.content_type == ContentType::News)
                .then(|| platform.clone())
                .flatten()
                .and_then(|value| clean_optional_str(Some(&value)))
        });
    let top_comment = if should_suppress_top_comment(
        platform.as_deref(),
        detail.discussion_url.as_deref(),
        &detail.metadata,
    ) {
        None
    } else {
        object_field(&detail.metadata, "top_comment").and_then(|comment| {
            let text = comment.get("text").and_then(value_as_clean_string)?;
            let author = comment
                .get("author")
                .and_then(value_as_clean_string)
                .unwrap_or_else(|| "unknown".to_owned());
            Some(BTreeMap::from([
                ("author".to_owned(), author),
                ("text".to_owned(), text),
            ]))
        })
    };
    let comment_count = detail.metadata.get("comment_count").and_then(Value::as_i64);
    let saved_source = saved_source_override.or_else(|| {
        if !detail.is_saved_to_knowledge {
            return None;
        }
        let submitted_via = string_field(&detail.metadata, "submitted_via")
            .unwrap_or_default()
            .to_lowercase();
        let snapshot_source = string_field(&detail.metadata, "tweet_snapshot_source")
            .unwrap_or_default()
            .to_lowercase();
        if submitted_via == "x_bookmarks" || snapshot_source == "x_bookmarks_sync" {
            Some(SavedSource::XBookmark)
        } else {
            Some(SavedSource::Knowledge)
        }
    });
    let key_takeaway = extract_key_takeaway(&detail.metadata);
    let user_status = matches!(
        detail.content_type,
        ContentType::Article | ContentType::Podcast
    )
    .then(|| "inbox".to_owned());
    Ok(ContentSummaryResponse {
        id: detail.id,
        content_type: detail.content_type,
        url: detail.url,
        source_url: detail.source_url,
        discussion_url: detail.discussion_url,
        title: Some(detail.display_title),
        source: detail.source,
        platform,
        status: detail.status,
        short_summary: detail.short_summary.clone(),
        created_at: detail.created_at,
        processed_at: detail.processed_at,
        classification,
        publication_date: detail.publication_date,
        is_read: detail.is_read,
        is_saved_to_knowledge: detail.is_saved_to_knowledge,
        knowledge_saved_at,
        news_article_url: detail.news_article_url,
        news_discussion_url: detail.news_discussion_url,
        news_key_points: detail.news_key_points,
        news_summary: detail.news_summary.or(detail.short_summary),
        user_status,
        image_url: detail.image_url,
        thumbnail_url: detail.thumbnail_url,
        primary_topic,
        top_comment,
        comment_count,
        feed_preview: detail.feed_preview,
        artifact_type: detail.artifact_type,
        preview_bullets: detail.preview_bullets,
        reason_to_read: detail.reason_to_read,
        key_takeaway,
        saved_source,
    })
}

fn should_suppress_top_comment(
    platform: Option<&str>,
    discussion_url: Option<&str>,
    metadata: &Map<String, Value>,
) -> bool {
    platform.is_some_and(|value| value.eq_ignore_ascii_case("techmeme"))
        || discussion_url.is_some_and(|value| value.to_lowercase().contains("techmeme.com"))
        || object_field(metadata, "discussion_payload")
            .and_then(|payload| payload.get("mode"))
            .and_then(Value::as_str)
            == Some("discussion_list")
}

fn clean_optional_str(value: Option<&str>) -> Option<String> {
    value
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
}

fn extract_key_takeaway(metadata: &Map<String, Value>) -> Option<String> {
    let summary = object_field(metadata, "summary")?;
    if let Some(takeaway) = object_field(summary, "artifact")
        .and_then(|artifact| object_field(artifact, "payload"))
        .and_then(|payload| payload.get("takeaway"))
        .and_then(value_as_clean_string)
        .or_else(|| summary.get("takeaway").and_then(value_as_clean_string))
    {
        return Some(takeaway);
    }
    for (collection, fields) in [
        ("key_points", &["point", "text"][..]),
        ("points", &["text"][..]),
        ("bullet_points", &["text"][..]),
    ] {
        let Some(items) = summary.get(collection).and_then(Value::as_array) else {
            continue;
        };
        for item in items.iter().filter_map(Value::as_object) {
            for field in fields {
                if let Some(takeaway) = item.get(*field).and_then(value_as_clean_string) {
                    return Some(takeaway);
                }
            }
        }
    }
    None
}

pub(super) fn present_news_summary(item: &NewsItemProjection) -> NewsItemSummaryResponse {
    let metadata = value_object(&item.raw_metadata);
    let cluster = object_field(&metadata, "cluster");
    let mut top_comment = news_top_comment(item, &metadata);
    if top_comment.is_none()
        && let Some(Value::Array(snippets)) =
            cluster.and_then(|value| value.get("discussion_snippets"))
        && let Some(snippet) = snippets.first().and_then(value_as_clean_string)
    {
        top_comment = Some(BTreeMap::from([
            ("author".to_owned(), "Related".to_owned()),
            ("text".to_owned(), snippet),
        ]));
    }
    let display_title =
        resolve_news_display_title(&metadata, item.summary_text.as_deref(), item.id);
    let key_points = string_array(&item.summary_key_points);

    NewsItemSummaryResponse {
        id: item.id,
        content_type: ContentType::News,
        url: resolve_news_item_url(item),
        source_url: item
            .canonical_item_url
            .clone()
            .or_else(|| item.discussion_url.clone()),
        discussion_url: item.discussion_url.clone(),
        title: display_title,
        source: item.source_label.clone(),
        platform: item.platform.clone(),
        status: news_content_status(&item.status),
        short_summary: item.summary_text.clone(),
        created_at: item.ingested_at,
        processed_at: item.processed_at,
        classification: news_classification(&metadata),
        publication_date: item.published_at,
        is_read: item.is_read,
        is_saved_to_knowledge: false,
        news_article_url: item
            .article_url
            .clone()
            .or_else(|| item.canonical_story_url.clone()),
        news_discussion_url: item
            .discussion_url
            .clone()
            .or_else(|| item.canonical_item_url.clone()),
        news_key_points: (!key_points.is_empty()).then_some(key_points),
        news_summary: item.summary_text.clone(),
        top_comment,
        comment_count: news_comment_count(item, &metadata),
    }
}

pub(super) fn present_news_detail(item: NewsItemProjection) -> NewsItemDetailResponse {
    let mut metadata = value_object(&item.raw_metadata);
    let display_title =
        resolve_news_display_title(&metadata, item.summary_text.as_deref(), item.id);
    let article_url = item
        .article_url
        .clone()
        .or_else(|| item.canonical_story_url.clone());
    let key_points = string_array(&item.summary_key_points);
    insert_news_article_metadata(&mut metadata, &item, &display_title, article_url.as_deref());
    insert_news_summary_metadata(&mut metadata, &item, article_url.as_deref(), &key_points);
    insert_news_context_metadata(&mut metadata, &item, article_url.as_deref());

    let body_available = item.body_available();
    NewsItemDetailResponse {
        id: item.id,
        content_type: ContentType::News,
        url: resolve_news_item_url(&item),
        source_url: item
            .canonical_item_url
            .clone()
            .or_else(|| item.discussion_url.clone()),
        discussion_url: item.discussion_url.clone(),
        title: display_title.clone(),
        display_title,
        source: item.source_label,
        status: news_content_status(&item.status),
        retry_count: 0,
        metadata: sanitize_metadata_for_api(metadata),
        created_at: item.ingested_at,
        updated_at: item.updated_at,
        processed_at: item.processed_at,
        publication_date: item.published_at,
        is_read: item.is_read,
        is_saved_to_knowledge: false,
        summary: item.summary_text.clone(),
        short_summary: item.summary_text.clone(),
        body_available,
        body_kind: body_available.then(|| "article".to_owned()),
        body_format: item.body_format,
        news_article_url: article_url,
        news_discussion_url: item.discussion_url.or(item.canonical_item_url),
        news_key_points: (!key_points.is_empty()).then_some(key_points),
        news_summary: item.summary_text,
        can_subscribe: false,
    }
}

fn insert_news_article_metadata(
    metadata: &mut Map<String, Value>,
    item: &NewsItemProjection,
    display_title: &str,
    article_url: Option<&str>,
) {
    let mut article = object_field(metadata, "article")
        .cloned()
        .unwrap_or_default();
    article.entry("url".to_owned()).or_insert_with(|| {
        article_url.map_or(Value::Null, |value| Value::String(value.to_owned()))
    });
    article.insert(
        "title".to_owned(),
        Value::String(
            nested_clean_title(metadata, "article").unwrap_or_else(|| display_title.to_owned()),
        ),
    );
    article
        .entry("source_domain".to_owned())
        .or_insert_with(|| {
            item.article_domain
                .as_deref()
                .map_or(Value::Null, |value| Value::String(value.to_owned()))
        });
    metadata.insert("article".to_owned(), Value::Object(article));
}

fn insert_news_summary_metadata(
    metadata: &mut Map<String, Value>,
    item: &NewsItemProjection,
    article_url: Option<&str>,
    key_points: &[String],
) {
    let mut summary = object_field(metadata, "summary")
        .cloned()
        .unwrap_or_default();
    let summary_text = summary
        .get("summary")
        .and_then(Value::as_str)
        .or(item.summary_text.as_deref());
    if let Some(summary_title) = resolve_news_summary_title(metadata, summary_text) {
        summary.insert("title".to_owned(), Value::String(summary_title));
    }
    if let Some(article_url) = article_url.filter(|_| !is_truthy(summary.get("article_url"))) {
        summary.insert(
            "article_url".to_owned(),
            Value::String(article_url.to_owned()),
        );
    }
    if !key_points.is_empty() && !is_truthy(summary.get("key_points")) {
        summary.insert(
            "key_points".to_owned(),
            Value::Array(key_points.iter().cloned().map(Value::String).collect()),
        );
    }
    if let Some(summary_text) = item
        .summary_text
        .as_deref()
        .filter(|_| !is_truthy(summary.get("summary")))
    {
        summary.insert("summary".to_owned(), Value::String(summary_text.to_owned()));
    }
    metadata.insert("summary".to_owned(), Value::Object(summary));
}

fn insert_news_context_metadata(
    metadata: &mut Map<String, Value>,
    item: &NewsItemProjection,
    article_url: Option<&str>,
) {
    metadata
        .entry("discussion_url".to_owned())
        .or_insert_with(|| {
            item.discussion_url
                .as_deref()
                .map_or(Value::Null, |value| Value::String(value.to_owned()))
        });
    metadata.entry("cluster".to_owned()).or_insert_with(|| {
        Value::Object(
            object_field(&value_object(&item.raw_metadata), "cluster")
                .cloned()
                .unwrap_or_default(),
        )
    });
    let relevant_links = news_relevant_links(
        metadata,
        article_url,
        item.discussion_url
            .as_deref()
            .or(item.canonical_item_url.as_deref()),
        item.discussion_summary.as_ref(),
    );
    if relevant_links.is_empty() {
        metadata.remove("relevant_links");
    } else {
        metadata.insert("relevant_links".to_owned(), Value::Array(relevant_links));
    }
}

fn runtime_metadata(raw_metadata: &Value) -> Map<String, Value> {
    let stored = value_object(raw_metadata);
    let mut domain = object_field(&stored, "domain").cloned().unwrap_or_default();
    let mut processing = object_field(&stored, "processing")
        .cloned()
        .unwrap_or_default();
    for (key, value) in &stored {
        if matches!(key.as_str(), "domain" | "processing") {
            continue;
        }
        let target = if PROCESSING_FIELD_NAMES.contains(&key.as_str()) {
            &mut processing
        } else {
            &mut domain
        };
        target.entry(key.clone()).or_insert_with(|| value.clone());
    }
    domain.extend(processing);
    domain
}

fn backfill_legacy_news_article(
    metadata: &mut Map<String, Value>,
    row: &ContentDetailProjection,
    content_type: ContentType,
) {
    if content_type != ContentType::News
        || object_field(metadata, "article")
            .and_then(|article| article.get("url"))
            .and_then(Value::as_str)
            .is_some()
    {
        return;
    }
    let article_url = [
        row.source_url.as_deref(),
        string_field(metadata, "final_url_after_redirects").as_deref(),
        string_field(metadata, "final_url").as_deref(),
        string_field(metadata, "url").as_deref(),
        Some(row.url.as_str()),
    ]
    .into_iter()
    .flatten()
    .find_map(valid_http_url);
    let Some(article_url) = article_url else {
        return;
    };
    let mut article = object_field(metadata, "article")
        .cloned()
        .unwrap_or_default();
    article.insert("url".to_owned(), Value::String(article_url.clone()));
    if let Some(title) = &row.title {
        article
            .entry("title".to_owned())
            .or_insert_with(|| Value::String(title.clone()));
    }
    let source_domain = string_field(metadata, "source").or_else(|| {
        reqwest::Url::parse(&article_url)
            .ok()
            .and_then(|url| url.host_str().map(str::to_owned))
    });
    if let Some(source_domain) = source_domain {
        article
            .entry("source_domain".to_owned())
            .or_insert(Value::String(source_domain));
    }
    metadata.insert("article".to_owned(), Value::Object(article));
}

fn normalize_summary_contract(metadata: &mut Map<String, Value>, content_type: ContentType) {
    let Some(summary) = object_field(metadata, "summary").cloned() else {
        return;
    };
    let kind = parse_summary_kind(metadata.get("summary_kind"))
        .or_else(|| infer_summary_kind(&summary))
        .or((content_type == ContentType::News).then_some(SummaryKind::ShortNews));
    let Some(kind) = kind else {
        return;
    };
    metadata
        .entry("summary_kind".to_owned())
        .or_insert_with(|| Value::String(summary_kind_str(kind).to_owned()));
    let version = parse_summary_version(metadata.get("summary_version")).unwrap_or_else(|| {
        let has_v2_shape = (kind == SummaryKind::LongInterleaved
            && summary.contains_key("key_points"))
            || (kind == SummaryKind::LongEditorialNarrative
                && summary.contains_key("source_details"));
        if has_v2_shape {
            SummaryVersion::V2
        } else {
            SummaryVersion::V1
        }
    });
    metadata
        .entry("summary_version".to_owned())
        .or_insert_with(|| Value::Number(Number::from(version.as_i32())));
}

fn infer_summary_kind(summary: &Map<String, Value>) -> Option<SummaryKind> {
    if summary.contains_key("artifact") && summary.contains_key("selection_trace") {
        return Some(SummaryKind::LongformArtifact);
    }
    if summary.get("summary_type").and_then(Value::as_str) == Some("interleaved")
        || (summary.contains_key("key_points") && summary.contains_key("topics"))
        || summary.contains_key("insights")
    {
        return Some(SummaryKind::LongInterleaved);
    }
    if summary.contains_key("points") {
        return Some(SummaryKind::LongBullets);
    }
    if summary.contains_key("editorial_narrative") {
        return Some(SummaryKind::LongEditorialNarrative);
    }
    if summary.contains_key("summary") && summary.contains_key("key_points") {
        return Some(SummaryKind::ShortNews);
    }
    if summary.contains_key("overview") && summary.contains_key("bullet_points") {
        return Some(SummaryKind::LongStructured);
    }
    if summary.contains_key("bullet_points") {
        return Some(SummaryKind::LongBullets);
    }
    summary
        .contains_key("summary")
        .then_some(SummaryKind::ShortNews)
}

fn parse_summary_kind(value: Option<&Value>) -> Option<SummaryKind> {
    match value.and_then(Value::as_str)? {
        "long_structured" => Some(SummaryKind::LongStructured),
        "long_interleaved" => Some(SummaryKind::LongInterleaved),
        "long_bullets" => Some(SummaryKind::LongBullets),
        "long_editorial_narrative" => Some(SummaryKind::LongEditorialNarrative),
        "short_news" => Some(SummaryKind::ShortNews),
        "longform_artifact" => Some(SummaryKind::LongformArtifact),
        _ => None,
    }
}

const fn summary_kind_str(kind: SummaryKind) -> &'static str {
    match kind {
        SummaryKind::LongStructured => "long_structured",
        SummaryKind::LongInterleaved => "long_interleaved",
        SummaryKind::LongBullets => "long_bullets",
        SummaryKind::LongEditorialNarrative => "long_editorial_narrative",
        SummaryKind::ShortNews => "short_news",
        SummaryKind::LongformArtifact => "longform_artifact",
    }
}

fn parse_summary_version(value: Option<&Value>) -> Option<SummaryVersion> {
    let parsed = value.and_then(|value| value.as_i64().or_else(|| value.as_str()?.parse().ok()))?;
    i32::try_from(parsed)
        .ok()
        .and_then(SummaryVersion::from_i32)
}

fn structured_summary(
    metadata: &Map<String, Value>,
    kind: Option<SummaryKind>,
) -> Option<Map<String, Value>> {
    kind.filter(|kind| DISPLAY_SUMMARY_KINDS.contains(kind))?;
    object_field(metadata, "summary").cloned()
}

fn project_bullet_points(
    metadata: &Map<String, Value>,
    kind: Option<SummaryKind>,
    version: Option<SummaryVersion>,
) -> Vec<ContentSummaryBulletPoint> {
    let Some(summary) = object_field(metadata, "summary") else {
        return Vec::new();
    };
    let raw = match kind {
        Some(SummaryKind::LongStructured) => array_field(summary, "bullet_points"),
        Some(SummaryKind::LongInterleaved) if version == Some(SummaryVersion::V2) => {
            array_field(summary, "key_points")
        }
        Some(SummaryKind::LongInterleaved) => {
            return array_field(summary, "insights")
                .iter()
                .filter_map(Value::as_object)
                .filter_map(|insight| {
                    Some(ContentSummaryBulletPoint {
                        text: value_as_clean_string(insight.get("insight")?)?,
                        category: value_as_clean_string(insight.get("topic")?),
                    })
                })
                .collect();
        }
        Some(SummaryKind::LongBullets) => {
            return array_field(summary, "points")
                .iter()
                .filter_map(Value::as_object)
                .filter_map(|point| {
                    Some(ContentSummaryBulletPoint {
                        text: value_as_clean_string(point.get("text")?)?,
                        category: Some("key_point".to_owned()),
                    })
                })
                .collect();
        }
        Some(SummaryKind::LongEditorialNarrative) => {
            return array_field(summary, "key_points")
                .iter()
                .filter_map(Value::as_object)
                .filter_map(|point| {
                    Some(ContentSummaryBulletPoint {
                        text: value_as_clean_string(point.get("point")?)?,
                        category: Some("key_point".to_owned()),
                    })
                })
                .collect();
        }
        Some(SummaryKind::LongformArtifact) => return artifact_bullet_points(summary),
        _ => return Vec::new(),
    };
    raw.iter()
        .filter_map(Value::as_object)
        .filter_map(|point| {
            Some(ContentSummaryBulletPoint {
                text: value_as_clean_string(point.get("text")?)?,
                category: point.get("category").and_then(value_as_clean_string),
            })
        })
        .collect()
}

fn artifact_bullet_points(summary: &Map<String, Value>) -> Vec<ContentSummaryBulletPoint> {
    let Some(artifact) = object_field(summary, "artifact") else {
        return Vec::new();
    };
    let Some(payload) = object_field(artifact, "payload") else {
        return Vec::new();
    };
    let category = string_field(artifact, "type").unwrap_or_else(|| "key_point".to_owned());
    array_field(payload, "key_points")
        .iter()
        .filter_map(Value::as_object)
        .filter_map(|point| {
            let text = [
                point.get("heading").and_then(value_as_clean_string),
                point.get("content").and_then(value_as_clean_string),
            ]
            .into_iter()
            .flatten()
            .collect::<Vec<_>>()
            .join(" — ");
            (!text.is_empty()).then(|| ContentSummaryBulletPoint {
                text,
                category: Some(category.clone()),
            })
        })
        .collect()
}

fn project_quotes(
    metadata: &Map<String, Value>,
    kind: Option<SummaryKind>,
    version: Option<SummaryVersion>,
) -> Vec<ContentSummaryQuote> {
    let Some(summary) = object_field(metadata, "summary") else {
        return Vec::new();
    };
    let raw = match kind {
        Some(SummaryKind::LongStructured) => array_field(summary, "quotes"),
        Some(SummaryKind::LongInterleaved) if version == Some(SummaryVersion::V2) => {
            array_field(summary, "quotes")
        }
        Some(SummaryKind::LongInterleaved) => {
            return array_field(summary, "insights")
                .iter()
                .filter_map(Value::as_object)
                .filter_map(|insight| {
                    Some(ContentSummaryQuote {
                        text: value_as_clean_string(insight.get("supporting_quote")?)?,
                        context: insight
                            .get("quote_attribution")
                            .or_else(|| insight.get("topic"))
                            .and_then(value_as_clean_string),
                        attribution: None,
                    })
                })
                .collect();
        }
        Some(SummaryKind::LongBullets) => return long_bullets_quotes(summary),
        Some(SummaryKind::LongEditorialNarrative) => {
            return array_field(summary, "quotes")
                .iter()
                .filter_map(Value::as_object)
                .filter_map(|quote| {
                    Some(ContentSummaryQuote {
                        text: value_as_clean_string(quote.get("text")?)?,
                        context: quote.get("attribution").and_then(value_as_clean_string),
                        attribution: None,
                    })
                })
                .collect();
        }
        Some(SummaryKind::LongformArtifact) => {
            let Some(artifact) = object_field(summary, "artifact") else {
                return Vec::new();
            };
            let Some(payload) = object_field(artifact, "payload") else {
                return Vec::new();
            };
            return array_field(payload, "quotes")
                .iter()
                .filter_map(Value::as_object)
                .filter_map(|quote| {
                    Some(ContentSummaryQuote {
                        text: value_as_clean_string(quote.get("text")?)?,
                        context: quote.get("attribution").and_then(value_as_clean_string),
                        attribution: None,
                    })
                })
                .collect();
        }
        _ => return Vec::new(),
    };
    raw.iter()
        .filter_map(Value::as_object)
        .filter_map(|quote| {
            Some(ContentSummaryQuote {
                text: value_as_clean_string(quote.get("text")?)?,
                context: quote.get("context").and_then(value_as_clean_string),
                attribution: quote.get("attribution").and_then(value_as_clean_string),
            })
        })
        .collect()
}

fn long_bullets_quotes(summary: &Map<String, Value>) -> Vec<ContentSummaryQuote> {
    array_field(summary, "points")
        .iter()
        .filter_map(Value::as_object)
        .flat_map(|point| array_field(point, "quotes"))
        .filter_map(Value::as_object)
        .filter_map(|quote| {
            Some(ContentSummaryQuote {
                text: value_as_clean_string(quote.get("text")?)?,
                context: quote
                    .get("context")
                    .or_else(|| quote.get("attribution"))
                    .and_then(value_as_clean_string),
                attribution: None,
            })
        })
        .collect()
}

fn project_topics(
    metadata: &Map<String, Value>,
    kind: Option<SummaryKind>,
    version: Option<SummaryVersion>,
) -> Vec<String> {
    let Some(summary) = object_field(metadata, "summary") else {
        return string_array(metadata.get("topics").unwrap_or(&Value::Null));
    };
    match kind {
        Some(SummaryKind::LongStructured) => {
            string_array(summary.get("topics").unwrap_or(&Value::Null))
        }
        Some(SummaryKind::LongInterleaved) if version == Some(SummaryVersion::V2) => {
            array_field(summary, "topics")
                .iter()
                .filter_map(Value::as_object)
                .filter_map(|topic| topic.get("topic").and_then(value_as_clean_string))
                .collect()
        }
        Some(SummaryKind::LongInterleaved) => {
            let mut seen = HashSet::new();
            array_field(summary, "insights")
                .iter()
                .filter_map(Value::as_object)
                .filter_map(|insight| insight.get("topic").and_then(value_as_clean_string))
                .filter(|topic| seen.insert(topic.clone()))
                .collect()
        }
        Some(SummaryKind::LongformArtifact) => object_field(summary, "artifact")
            .and_then(|artifact| string_field(artifact, "type"))
            .into_iter()
            .collect(),
        Some(SummaryKind::LongBullets | SummaryKind::LongEditorialNarrative) => Vec::new(),
        _ => string_array(metadata.get("topics").unwrap_or(&Value::Null)),
    }
}

struct ArtifactFields {
    longform_artifact: Option<Map<String, Value>>,
    feed_preview: Option<Map<String, Value>>,
    artifact_type: Option<String>,
    preview_bullets: Option<Vec<String>>,
    reason_to_read: Option<String>,
}

fn longform_artifact_fields(metadata: &Map<String, Value>) -> ArtifactFields {
    let Some(summary) = object_field(metadata, "summary") else {
        return ArtifactFields::empty();
    };
    let artifact = object_field(summary, "artifact");
    let feed_preview = object_field(summary, "feed_preview")
        .or_else(|| object_field(metadata, "feed_preview"))
        .cloned();
    let artifact_type = artifact
        .and_then(|value| string_field(value, "type"))
        .or_else(|| {
            feed_preview
                .as_ref()
                .and_then(|value| string_field(value, "artifact_type"))
        });
    let preview_bullets = feed_preview
        .as_ref()
        .map(|value| string_array(value.get("preview_bullets").unwrap_or(&Value::Null)))
        .filter(|value| !value.is_empty());
    let reason_to_read = feed_preview
        .as_ref()
        .and_then(|value| string_field(value, "reason_to_read"));
    ArtifactFields {
        longform_artifact: artifact_type.as_ref().map(|_| summary.clone()),
        feed_preview,
        artifact_type,
        preview_bullets,
        reason_to_read,
    }
}

impl ArtifactFields {
    const fn empty() -> Self {
        Self {
            longform_artifact: None,
            feed_preview: None,
            artifact_type: None,
            preview_bullets: None,
            reason_to_read: None,
        }
    }
}

struct LegacyNewsFields {
    article_url: Option<String>,
    discussion_url: Option<String>,
    key_points: Option<Vec<String>>,
}

fn legacy_content_news_fields(metadata: &Map<String, Value>, url: &str) -> LegacyNewsFields {
    let summary = object_field(metadata, "summary");
    let key_points = summary
        .and_then(|value| value.get("key_points"))
        .map(string_array)
        .filter(|values| !values.is_empty())
        .or_else(|| {
            metadata
                .get("summary_key_points")
                .map(string_array)
                .filter(|values| !values.is_empty())
        });
    LegacyNewsFields {
        article_url: Some(url.to_owned()),
        discussion_url: string_field(metadata, "discussion_url").or_else(|| {
            object_field(metadata, "aggregator").and_then(|value| string_field(value, "url"))
        }),
        key_points,
    }
}

fn resolve_content_url(
    stored_url: &str,
    metadata: &Map<String, Value>,
    content_type: ContentType,
) -> Option<String> {
    let mut candidates = Vec::new();
    if content_type == ContentType::News {
        candidates.push(
            object_field(metadata, "article").and_then(|article| string_field(article, "url")),
        );
    }
    candidates.push(Some(stored_url.to_owned()));
    candidates.push(string_field(metadata, "final_url_after_redirects"));
    candidates.push(string_field(metadata, "final_url"));
    candidates.push(string_field(metadata, "url"));
    candidates
        .into_iter()
        .flatten()
        .find_map(|value| valid_http_url(&value))
}

fn resolve_content_display_title(title: Option<&str>, metadata: &Map<String, Value>) -> String {
    nested_clean_title(metadata, "summary")
        .or_else(|| {
            object_field(metadata, "summary")
                .and_then(|summary| object_field(summary, "feed_preview"))
                .and_then(|preview| preview.get("title"))
                .and_then(value_as_clean_string)
        })
        .or_else(|| title.and_then(clean_title))
        .or_else(|| {
            object_field(metadata, "summary")
                .and_then(|summary| extract_short_summary(Some(Value::Object(summary.clone()))))
                .and_then(|value| summarize_text_as_title(&value))
        })
        .unwrap_or_else(|| "Untitled".to_owned())
}

fn resolve_content_image_urls(
    content_id: i64,
    content_type: ContentType,
    metadata: &Map<String, Value>,
) -> (Option<String>, Option<String>) {
    if content_type == ContentType::News {
        return (None, None);
    }
    let provider_thumbnail = (content_type == ContentType::Podcast)
        .then(|| string_field(metadata, "thumbnail_url"))
        .flatten()
        .filter(|url| valid_http_url(url).is_some());
    let image_version = metadata
        .get("image_generated_at")
        .filter(|value| !value.is_null());
    let has_generated_image = image_version.is_some_and(is_truthy_value);
    let mut image_url = string_field(metadata, "image_url");
    let mut thumbnail_url = string_field(metadata, "thumbnail_url");
    if content_type == ContentType::Podcast && has_generated_image {
        if image_url == provider_thumbnail {
            image_url = None;
        }
        if thumbnail_url == provider_thumbnail {
            thumbnail_url = None;
        }
    }
    if image_url.is_none() && has_generated_image {
        image_url = Some(format!("/static/images/content/{content_id}.png"));
    }
    if thumbnail_url.is_none() && has_generated_image {
        thumbnail_url = Some(format!("/static/images/thumbnails/{content_id}.png"));
    }
    if let Some(version) = image_version.and_then(value_to_string) {
        image_url = image_url.map(|url| append_relative_url_version(&url, &version));
        thumbnail_url = thumbnail_url.map(|url| append_relative_url_version(&url, &version));
    }
    if content_type == ContentType::Podcast && image_url.is_none() {
        return (provider_thumbnail, None);
    }
    (image_url, thumbnail_url)
}

fn append_relative_url_version(url: &str, version: &str) -> String {
    if !url.starts_with("/static/images/") || url.contains("?v=") || url.contains("&v=") {
        return url.to_owned();
    }
    let Ok(base) = reqwest::Url::parse("https://newsly.invalid") else {
        return url.to_owned();
    };
    let Ok(mut parsed) = base.join(url) else {
        return url.to_owned();
    };
    parsed.query_pairs_mut().append_pair("v", version);
    parsed.query().map_or_else(
        || parsed.path().to_owned(),
        |query| format!("{}?{query}", parsed.path()),
    )
}

pub(super) fn detected_feed(metadata: &Map<String, Value>) -> Option<DetectedFeed> {
    let value = object_field(metadata, "detected_feed")?;
    Some(DetectedFeed {
        url: string_field(value, "url")?,
        feed_type: string_field(value, "type")?,
        title: string_field(value, "title"),
        format: string_field(value, "format").unwrap_or_else(|| "rss".to_owned()),
    })
}

fn sanitize_metadata_for_api(mut metadata: Map<String, Value>) -> Map<String, Value> {
    for key in API_METADATA_REDACT_KEYS
        .iter()
        .chain(API_METADATA_INTERNAL_KEYS.iter())
    {
        metadata.remove(*key);
    }
    if let Some(Value::Object(summary)) = metadata.get_mut("summary") {
        summary.remove("full_markdown");
    }
    metadata.retain(|key, value| {
        API_METADATA_LARGE_VALUE_ALLOWLIST.contains(&key.as_str())
            || serde_json::to_string(value).map_or_else(
                |_| value.to_string().chars().count(),
                |text| text.chars().count(),
            ) <= API_METADATA_MAX_VALUE_CHARS
    });
    metadata
}

fn resolve_news_item_url(item: &NewsItemProjection) -> String {
    [
        item.article_url.as_deref(),
        item.canonical_story_url.as_deref(),
        item.discussion_url.as_deref(),
        item.canonical_item_url.as_deref(),
    ]
    .into_iter()
    .flatten()
    .find_map(normalize_http_url)
    .unwrap_or_else(|| format!("https://newsly.invalid/news-items/{}", item.id))
}

fn resolve_news_display_title(
    metadata: &Map<String, Value>,
    summary_text: Option<&str>,
    item_id: i64,
) -> String {
    resolve_news_summary_title(metadata, summary_text)
        .unwrap_or_else(|| format!("News item {item_id}"))
}

fn resolve_news_summary_title(
    metadata: &Map<String, Value>,
    summary_text: Option<&str>,
) -> Option<String> {
    nested_clean_title(metadata, "summary")
        .or_else(|| {
            object_field(metadata, "cluster")
                .and_then(|cluster| cluster.get("related_titles"))
                .and_then(Value::as_array)
                .and_then(|titles| titles.iter().find_map(value_as_clean_string))
                .and_then(|title| clean_title(&title))
        })
        .or_else(|| nested_clean_title(metadata, "article"))
        .or_else(|| summary_text.and_then(summarize_text_as_title))
}

fn news_content_status(status: &str) -> ContentStatus {
    match status {
        "failed" => ContentStatus::Failed,
        "processing" => ContentStatus::Processing,
        "new" => ContentStatus::New,
        _ => ContentStatus::Completed,
    }
}

fn news_classification(metadata: &Map<String, Value>) -> Option<ContentClassification> {
    match object_field(metadata, "summary")
        .and_then(|summary| summary.get("classification"))
        .and_then(Value::as_str)
    {
        Some("to_read") => Some(ContentClassification::ToRead),
        Some("skip") => Some(ContentClassification::Skip),
        _ => None,
    }
}

fn news_top_comment(
    item: &NewsItemProjection,
    metadata: &Map<String, Value>,
) -> Option<BTreeMap<String, String>> {
    if item
        .platform
        .as_deref()
        .is_some_and(|value| value.eq_ignore_ascii_case("techmeme"))
        || item
            .discussion_url
            .as_deref()
            .is_some_and(|value| value.to_ascii_lowercase().contains("techmeme.com"))
    {
        return None;
    }
    let top_comment = object_field(metadata, "top_comment")?;
    let text = top_comment.get("text").and_then(value_as_clean_string)?;
    let author = top_comment
        .get("author")
        .and_then(value_as_clean_string)
        .unwrap_or_else(|| "unknown".to_owned());
    Some(BTreeMap::from([
        ("author".to_owned(), author),
        ("text".to_owned(), text),
    ]))
}

fn news_comment_count(item: &NewsItemProjection, metadata: &Map<String, Value>) -> Option<i64> {
    let aggregator_count = object_field(metadata, "aggregator")
        .and_then(|aggregator| object_field(aggregator, "metadata"))
        .and_then(|metadata| metadata.get("comments_count"));
    [metadata.get("comment_count"), aggregator_count]
        .into_iter()
        .flatten()
        .find_map(value_as_i64)
        .map(|value| value.max(0))
        .or_else(|| (item.cluster_size > 1).then(|| i64::from(item.cluster_size - 1)))
}

fn news_relevant_links(
    metadata: &Map<String, Value>,
    article_url: Option<&str>,
    discussion_url: Option<&str>,
    discussion_summary: Option<&Value>,
) -> Vec<Value> {
    let excluded = [article_url, discussion_url]
        .into_iter()
        .flatten()
        .filter_map(normalize_http_url)
        .collect::<HashSet<_>>();
    let mut seen = HashSet::new();
    let mut links = Vec::new();
    let mut add = |raw: &Map<String, Value>, source: &str, fallback_reason: &str| {
        if links.len() >= 6 {
            return;
        }
        let Some(url) = raw
            .get("url")
            .and_then(value_as_clean_string)
            .as_deref()
            .and_then(normalize_http_url)
        else {
            return;
        };
        if excluded.contains(&url) || !seen.insert(url.clone()) {
            return;
        }
        let title = raw.get("title").and_then(value_as_clean_string);
        let reason = raw
            .get("reason")
            .and_then(value_as_clean_string)
            .unwrap_or_else(|| fallback_reason.to_owned());
        links.push(Value::Object(Map::from_iter([
            ("url".to_owned(), Value::String(url)),
            ("title".to_owned(), title.map_or(Value::Null, Value::String)),
            ("reason".to_owned(), Value::String(reason)),
            ("source".to_owned(), Value::String(source.to_owned())),
        ])));
    };
    for link in array_field(metadata, "article_relevant_links")
        .iter()
        .filter_map(Value::as_object)
    {
        add(
            link,
            "article",
            "Useful supporting context from the article.",
        );
    }
    if let Some(summary) = discussion_summary.and_then(Value::as_object) {
        for link in array_field(summary, "notable_links")
            .iter()
            .filter_map(Value::as_object)
        {
            add(link, "community", "Mentioned in the discussion.");
        }
    }
    links
}

fn nested_clean_title(metadata: &Map<String, Value>, section: &str) -> Option<String> {
    object_field(metadata, section)
        .and_then(|value| value.get("title"))
        .and_then(value_as_clean_string)
        .and_then(|value| clean_title(&value))
}

fn clean_title(value: &str) -> Option<String> {
    let mut without_tags = String::with_capacity(value.len());
    let mut inside_tag = false;
    for character in value.chars() {
        match character {
            '<' => inside_tag = true,
            '>' => inside_tag = false,
            _ if !inside_tag => without_tags.push(character),
            _ => {}
        }
    }
    let normalized = without_tags
        .replace("&amp;", "&")
        .replace("&quot;", "\"")
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ");
    if normalized.is_empty() {
        return None;
    }
    let folded = normalized.to_ascii_lowercase();
    if matches!(
        folded.as_str(),
        "na" | "n/a" | "none" | "unknown" | "untitled" | "void" | "access denied"
    ) || folded.starts_with("just a moment")
        || folded.starts_with("verification required")
    {
        return None;
    }
    let mut title = normalized;
    if title.chars().count() > 500 {
        let truncated = title.chars().take(500).collect::<String>();
        title = String::from(truncated.trim_end());
    }
    Some(title)
}

fn summarize_text_as_title(value: &str) -> Option<String> {
    let text = value.split_whitespace().collect::<Vec<_>>().join(" ");
    if text.is_empty() {
        return None;
    }
    if text.chars().count() <= 120 {
        return Some(text);
    }
    let excerpt = text.chars().take(120).collect::<String>();
    Some(format!("{}…", excerpt.trim_end()))
}

fn extract_short_summary(summary: Option<Value>) -> Option<String> {
    let value = summary?;
    if let Some(text) = value.as_str() {
        return (!text.is_empty()).then(|| text.to_owned());
    }
    let summary = value.as_object()?;
    for value in [
        summary.get("one_line"),
        object_field(summary, "artifact")
            .and_then(|artifact| object_field(artifact, "payload"))
            .and_then(|payload| payload.get("overview")),
        summary.get("overview"),
    ]
    .into_iter()
    .flatten()
    {
        if let Some(text) = value_as_clean_string(value) {
            return Some(text);
        }
    }
    if summary.get("summary_type").and_then(Value::as_str) == Some("interleaved")
        && let Some(text) = summary
            .get("hook")
            .or_else(|| summary.get("takeaway"))
            .and_then(value_as_clean_string)
    {
        return Some(text);
    }
    if let Some(narrative) = summary.get("editorial_narrative").and_then(Value::as_str) {
        let first = narrative.split("\n\n").next().unwrap_or(narrative).trim();
        if !first.is_empty() {
            return Some(first.to_owned());
        }
    }
    if let Some(text) = array_field(summary, "points")
        .first()
        .and_then(Value::as_object)
        .and_then(|point| point.get("text"))
        .and_then(value_as_clean_string)
    {
        return Some(text);
    }
    ["summary", "hook", "takeaway"]
        .into_iter()
        .find_map(|key| summary.get(key).and_then(value_as_clean_string))
}

fn detected_feed_subscription_allowed(content: &ContentDetailResponse) -> bool {
    content.detected_feed.is_some()
        && (content.content_type == ContentType::News
            || content.source.as_deref() == Some("self submission"))
}

pub(super) fn subscription_candidate(content: &ContentDetailResponse) -> Option<(&str, &str)> {
    if !detected_feed_subscription_allowed(content) {
        return None;
    }
    let feed = content.detected_feed.as_ref()?;
    matches!(
        feed.feed_type.as_str(),
        "substack" | "atom" | "podcast_rss" | "youtube" | "reddit" | "aggregator"
    )
    .then_some((feed.feed_type.as_str(), feed.url.as_str()))
}

pub(super) fn canonicalize_feed_url(value: &str) -> String {
    let trimmed = value.trim();
    let Ok(mut parsed) = reqwest::Url::parse(trimmed) else {
        return trimmed.trim_end_matches('/').to_owned();
    };
    parsed.set_fragment(None);
    let mut result = parsed.to_string();
    let query_suffix = parsed
        .query()
        .map(|query| format!("?{query}"))
        .unwrap_or_default();
    let without_query = result.strip_suffix(&query_suffix).unwrap_or(&result);
    result = format!("{}{}", without_query.trim_end_matches('/'), query_suffix);
    result
}

fn valid_http_url(value: &str) -> Option<String> {
    let parsed = reqwest::Url::parse(value.trim()).ok()?;
    matches!(parsed.scheme(), "http" | "https").then(|| parsed.to_string())
}

fn normalize_http_url(value: &str) -> Option<String> {
    let cleaned = value.trim();
    if cleaned.is_empty()
        || cleaned.starts_with('/')
        || cleaned.starts_with('?')
        || cleaned.starts_with('#')
        || cleaned.starts_with("./")
        || cleaned.starts_with("../")
    {
        return None;
    }
    let candidate = if cleaned.starts_with("//") {
        format!("https:{cleaned}")
    } else if cleaned.contains("://") {
        cleaned.to_owned()
    } else {
        format!("https://{cleaned}")
    };
    let mut parsed = reqwest::Url::parse(&candidate).ok()?;
    if !matches!(parsed.scheme(), "http" | "https") || parsed.host_str().is_none() {
        return None;
    }
    parsed.set_scheme("https").ok()?;
    Some(parsed.to_string())
}

fn value_object(value: &Value) -> Map<String, Value> {
    value.as_object().cloned().unwrap_or_default()
}

fn object_field<'a>(value: &'a Map<String, Value>, key: &str) -> Option<&'a Map<String, Value>> {
    value.get(key).and_then(Value::as_object)
}

fn array_field<'a>(value: &'a Map<String, Value>, key: &str) -> &'a [Value] {
    value
        .get(key)
        .and_then(Value::as_array)
        .map_or(&[], Vec::as_slice)
}

fn string_field(value: &Map<String, Value>, key: &str) -> Option<String> {
    value.get(key).and_then(value_as_clean_string)
}

fn string_array(value: &Value) -> Vec<String> {
    value
        .as_array()
        .map(|values| values.iter().filter_map(value_as_clean_string).collect())
        .unwrap_or_default()
}

fn value_as_clean_string(value: &Value) -> Option<String> {
    let text = match value {
        Value::String(value) => value.clone(),
        Value::Number(value) => value.to_string(),
        Value::Bool(value) => value.to_string(),
        _ => return None,
    };
    let cleaned = text.trim();
    (!cleaned.is_empty()).then(|| cleaned.to_owned())
}

fn value_to_string(value: &Value) -> Option<String> {
    match value {
        Value::Null => None,
        Value::String(value) => Some(value.clone()),
        _ => Some(value.to_string()),
    }
}

fn value_as_i64(value: &Value) -> Option<i64> {
    value.as_i64().or_else(|| value.as_str()?.parse().ok())
}

fn insert_string_if_missing(metadata: &mut Map<String, Value>, key: &str, value: Option<&str>) {
    if !metadata.contains_key(key)
        && let Some(value) = value.filter(|value| !value.trim().is_empty())
    {
        metadata.insert(key.to_owned(), Value::String(value.trim().to_owned()));
    }
}

fn is_truthy(value: Option<&Value>) -> bool {
    value.is_some_and(is_truthy_value)
}

fn is_truthy_value(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(value) => *value,
        Value::Number(value) => value.as_f64().is_some_and(|value| value != 0.0),
        Value::String(value) => !value.is_empty(),
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}

#[derive(Debug, Error)]
pub(crate) enum PresentationError {
    #[error("unknown Content type {0:?}")]
    UnknownContentType(String),
    #[error("unknown Content status {0:?}")]
    UnknownContentStatus(String),
    #[error("Content URL is invalid: {0:?}")]
    InvalidContentUrl(String),
}

#[cfg(test)]
mod tests {
    use chrono::{TimeZone as _, Utc};
    use serde_json::json;

    use super::*;

    fn news_item() -> NewsItemProjection {
        let ingested_at = Utc
            .with_ymd_and_hms(2026, 8, 31, 12, 0, 0)
            .single()
            .expect("valid timestamp");
        NewsItemProjection {
            id: 42,
            platform: Some("hackernews".to_owned()),
            source_label: Some("Example".to_owned()),
            canonical_item_url: Some("https://news.ycombinator.com/item?id=42".to_owned()),
            canonical_story_url: Some("https://example.com/story".to_owned()),
            article_url: Some("https://example.com/story".to_owned()),
            article_domain: Some("example.com".to_owned()),
            discussion_url: Some("https://news.ycombinator.com/item?id=42".to_owned()),
            summary_key_points: json!(["First point", "Second point"]),
            summary_text: Some("A concise News summary.".to_owned()),
            raw_metadata: json!({
                "summary": {
                    "title": "Canonical News title",
                    "classification": "to_read"
                },
                "cluster": {
                    "discussion_snippets": ["A related discussion snippet"]
                },
                "comment_count": "12",
                "article_relevant_links": [{
                    "url": "https://example.com/context",
                    "title": "Context",
                    "reason": "Supporting context"
                }],
                "full_text": "must not reach the API"
            }),
            status: "ready".to_owned(),
            cluster_size: 1,
            published_at: Some(ingested_at),
            ingested_at,
            processed_at: Some(ingested_at),
            created_at: ingested_at,
            updated_at: Some(ingested_at),
            sort_timestamp: ingested_at,
            is_read: true,
            discussion_summary: None,
            body_format: Some("markdown".to_owned()),
        }
    }

    #[test]
    fn news_presenters_emit_news_owned_fields_and_preserve_client_values() {
        let item = news_item();

        let summary = present_news_summary(&item);
        assert_eq!(summary.content_type, ContentType::News);
        assert_eq!(summary.title, "Canonical News title");
        assert_eq!(summary.status, ContentStatus::Completed);
        assert_eq!(summary.classification, Some(ContentClassification::ToRead));
        assert_eq!(summary.comment_count, Some(12));
        assert_eq!(
            summary
                .top_comment
                .as_ref()
                .and_then(|comment| comment.get("text"))
                .map(String::as_str),
            Some("A related discussion snippet")
        );

        let detail = present_news_detail(item);
        assert_eq!(detail.content_type, ContentType::News);
        assert_eq!(detail.title, "Canonical News title");
        assert_eq!(
            detail.news_key_points.as_deref(),
            Some(&["First point".to_owned(), "Second point".to_owned()][..])
        );
        assert!(detail.body_available);
        assert_eq!(detail.body_kind.as_deref(), Some("article"));
        assert_eq!(detail.body_format.as_deref(), Some("markdown"));
        assert_eq!(
            detail.metadata["article"]["url"],
            "https://example.com/story"
        );
        assert_eq!(
            detail.metadata["relevant_links"][0]["url"],
            "https://example.com/context"
        );
        assert!(detail.metadata.get("full_text").is_none());
    }

    #[test]
    fn summary_version_parser_and_metadata_remain_numeric() {
        assert_eq!(
            parse_summary_version(Some(&json!(1))),
            Some(SummaryVersion::V1)
        );
        assert_eq!(
            parse_summary_version(Some(&json!("2"))),
            Some(SummaryVersion::V2)
        );
        assert_eq!(parse_summary_version(Some(&json!(3))), None);

        let mut metadata = json!({
            "summary": {
                "summary_type": "interleaved",
                "key_points": [{"text": "Point"}]
            }
        })
        .as_object()
        .expect("metadata object")
        .clone();
        normalize_summary_contract(&mut metadata, ContentType::Article);
        assert_eq!(metadata["summary_version"], json!(2));
        assert_eq!(
            parse_summary_version(metadata.get("summary_version")),
            Some(SummaryVersion::V2)
        );
    }
}
