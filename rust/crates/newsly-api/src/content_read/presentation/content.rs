use super::{
    ArtifactFields, ContentClassification, ContentDetailProjection, ContentDetailResponse,
    ContentStatus, ContentSummaryResponse, ContentType, DISPLAY_SUMMARY_KINDS, FromStr,
    LegacyNewsFields, Map, PresentationError, SavedSource, Value, backfill_legacy_news_article,
    clean_optional_str, content_top_comment, detected_feed, extract_key_takeaway,
    extract_short_summary, insert_string_if_missing, is_typed_artifact_detail,
    legacy_content_news_fields, longform_artifact_fields, normalize_summary_contract, object_field,
    parse_summary_kind, parse_summary_version, project_bullet_points, project_quotes,
    project_topics, resolve_content_display_title, resolve_content_image_urls, resolve_content_url,
    runtime_metadata, sanitize_metadata_for_api, string_field, structured_summary,
};

// Normalized source values shared by the two independent wire projections.
struct ContentPresentation {
    row: ContentDetailProjection,
    content_type: ContentType,
    status: ContentStatus,
    metadata: Map<String, Value>,
    url: String,
    display_title: String,
    summary_text: Option<String>,
    news_fields: Option<LegacyNewsFields>,
    discussion_url: Option<String>,
    image_url: Option<String>,
    thumbnail_url: Option<String>,
}

impl ContentPresentation {
    fn new(row: ContentDetailProjection) -> Result<Self, PresentationError> {
        let content_type = ContentType::from_str(&row.content_type)
            .map_err(|_| PresentationError::UnknownContentType(row.content_type.clone()))?;
        let status = ContentStatus::from_str(&row.status)
            .map_err(|_| PresentationError::UnknownContentStatus(row.status.clone()))?;
        let mut metadata = runtime_metadata(&row.content_metadata);
        insert_string_if_missing(&mut metadata, "platform", row.platform.as_deref());
        insert_string_if_missing(&mut metadata, "source", row.source.as_deref());
        backfill_legacy_news_article(&mut metadata, &row, content_type);
        normalize_summary_contract(&mut metadata, content_type);
        let url = resolve_content_url(&row.url, &metadata, content_type)
            .ok_or_else(|| PresentationError::InvalidContentUrl(row.url.clone()))?;
        let display_title = resolve_content_display_title(row.title.as_deref(), &metadata);
        let summary_text = extract_short_summary(object_field(&metadata, "summary"));
        let news_fields = (content_type == ContentType::News
            && longform_artifact_fields(&metadata)
                .longform_artifact
                .is_none())
        .then(|| legacy_content_news_fields(&metadata, &url));
        let discussion_url = news_fields.as_ref().map_or_else(
            || string_field(&metadata, "discussion_url"),
            |fields| fields.discussion_url.clone(),
        );
        let (image_url, thumbnail_url) =
            resolve_content_image_urls(row.id, content_type, &metadata);
        Ok(Self {
            row,
            content_type,
            status,
            metadata,
            url,
            display_title,
            summary_text,
            news_fields,
            discussion_url,
            image_url,
            thumbnail_url,
        })
    }
}

pub(crate) fn present_content_detail(
    row: ContentDetailProjection,
) -> Result<ContentDetailResponse, PresentationError> {
    let content = ContentPresentation::new(row)?;
    let row = content.row;
    let mut metadata = content.metadata;
    let summary_kind = parse_summary_kind(metadata.get("summary_kind"));
    let summary_version = parse_summary_version(metadata.get("summary_version"));
    let artifact = longform_artifact_fields(&metadata);
    let canonical_artifact_detail =
        is_typed_artifact_detail(content.content_type, summary_kind, &artifact);
    let is_legacy_news = content.news_fields.is_some();

    let (structured_summary, bullet_points, quotes, topics) =
        if is_legacy_news || canonical_artifact_detail {
            (None, Vec::new(), Vec::new(), Vec::new())
        } else {
            (
                structured_summary(&metadata, summary_kind),
                project_bullet_points(&metadata, summary_kind, summary_version),
                project_quotes(&metadata, summary_kind, summary_version),
                project_topics(&metadata, summary_kind, summary_version),
            )
        };
    let detected_feed = detected_feed(&metadata);

    let longform_artifact = artifact.longform_artifact.cloned();
    let ArtifactFields {
        mut feed_preview,
        mut artifact_type,
        mut preview_bullets,
        mut reason_to_read,
        ..
    } = artifact;
    if canonical_artifact_detail {
        // The complete envelope owns these values. Keep nullable wire keys for installed clients.
        for key in ["summary", "feed_preview", "selection_trace"] {
            metadata.remove(key);
        }
        feed_preview = None;
        artifact_type = None;
        preview_bullets = None;
        reason_to_read = None;
    }

    Ok(ContentDetailResponse {
        id: row.id,
        content_type: content.content_type,
        url: content.url,
        source_url: row.source_url.or(Some(row.url)),
        discussion_url: content.discussion_url,
        title: row.title,
        display_title: content.display_title,
        source: row.source,
        status: content.status,
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
        summary: content.summary_text.clone(),
        short_summary: content.summary_text.clone(),
        summary_kind,
        summary_version,
        structured_summary,
        longform_artifact,
        feed_preview,
        artifact_type,
        preview_bullets,
        reason_to_read,
        bullet_points,
        quotes,
        topics,
        full_markdown: None,
        body_available: row.body_available,
        body_kind: row.body_available.then(|| {
            if content.content_type == ContentType::Podcast {
                "transcript".to_owned()
            } else {
                "article".to_owned()
            }
        }),
        body_format: row.body_format,
        news_article_url: content
            .news_fields
            .as_ref()
            .and_then(|fields| fields.article_url.clone()),
        news_discussion_url: content
            .news_fields
            .as_ref()
            .and_then(|fields| fields.discussion_url.clone()),
        news_key_points: content
            .news_fields
            .as_ref()
            .and_then(|fields| fields.key_points.clone()),
        news_summary: content.summary_text,
        image_url: content.image_url,
        thumbnail_url: content.thumbnail_url,
        detected_feed,
        can_subscribe: false,
    })
}

pub(crate) fn present_content_summary(
    row: ContentDetailProjection,
    knowledge_saved_at: Option<chrono::DateTime<chrono::Utc>>,
    saved_source_override: Option<SavedSource>,
) -> Result<ContentSummaryResponse, PresentationError> {
    let content = ContentPresentation::new(row)?;
    let row = content.row;
    let metadata = content.metadata;
    let platform = row.platform.clone();
    let kind = parse_summary_kind(metadata.get("summary_kind"));
    let version = parse_summary_version(metadata.get("summary_version"));
    let ArtifactFields {
        feed_preview,
        artifact_type,
        preview_bullets,
        reason_to_read,
        ..
    } = longform_artifact_fields(&metadata);
    let is_legacy_news = content.news_fields.is_some();
    let classification = kind
        .filter(|kind| !is_legacy_news && DISPLAY_SUMMARY_KINDS.contains(kind))
        .and_then(|_| object_field(&metadata, "summary"))
        .and_then(|summary| summary.get("classification"))
        .and_then(Value::as_str)
        .and_then(|value| match value {
            "to_read" => Some(ContentClassification::ToRead),
            "skip" => Some(ContentClassification::Skip),
            _ => None,
        });
    let primary_topic = (!is_legacy_news)
        .then(|| project_topics(&metadata, kind, version))
        .and_then(|topics| {
            topics
                .first()
                .and_then(|value| clean_optional_str(Some(value)))
        })
        .or_else(|| {
            (content.content_type == ContentType::News)
                .then(|| platform.clone())
                .flatten()
                .and_then(|value| clean_optional_str(Some(&value)))
        });
    let key_takeaway = extract_key_takeaway(&metadata);
    let metadata = sanitize_metadata_for_api(metadata);
    let top_comment = content_top_comment(
        platform.as_deref(),
        content.discussion_url.as_deref(),
        &metadata,
    );
    let comment_count = metadata.get("comment_count").and_then(Value::as_i64);
    let saved_source =
        saved_source_override.or_else(|| infer_saved_source(&metadata, row.is_saved_to_knowledge));
    let user_status = matches!(
        content.content_type,
        ContentType::Article | ContentType::Podcast
    )
    .then(|| "inbox".to_owned());
    Ok(ContentSummaryResponse {
        id: row.id,
        content_type: content.content_type,
        url: content.url,
        source_url: row.source_url.or(Some(row.url)),
        discussion_url: content.discussion_url,
        title: Some(content.display_title),
        source: row.source,
        platform,
        status: content.status,
        short_summary: content.summary_text.clone(),
        created_at: row.created_at,
        processed_at: row.processed_at,
        classification,
        publication_date: row.publication_date,
        is_read: row.is_read,
        is_saved_to_knowledge: row.is_saved_to_knowledge,
        knowledge_saved_at,
        news_article_url: content
            .news_fields
            .as_ref()
            .and_then(|fields| fields.article_url.clone()),
        news_discussion_url: content
            .news_fields
            .as_ref()
            .and_then(|fields| fields.discussion_url.clone()),
        news_key_points: content
            .news_fields
            .as_ref()
            .and_then(|fields| fields.key_points.clone()),
        news_summary: content.summary_text,
        user_status,
        image_url: content.image_url,
        thumbnail_url: content.thumbnail_url,
        primary_topic,
        top_comment,
        comment_count,
        feed_preview,
        artifact_type,
        preview_bullets,
        reason_to_read,
        key_takeaway,
        saved_source,
    })
}

fn infer_saved_source(metadata: &Map<String, Value>, is_saved: bool) -> Option<SavedSource> {
    if !is_saved {
        return None;
    }
    let is_x_bookmark = [
        ("submitted_via", "x_bookmarks"),
        ("tweet_snapshot_source", "x_bookmarks_sync"),
    ]
    .into_iter()
    .any(|(key, expected)| {
        string_field(metadata, key).is_some_and(|value| value.to_lowercase() == expected)
    });
    Some(if is_x_bookmark {
        SavedSource::XBookmark
    } else {
        SavedSource::Knowledge
    })
}
