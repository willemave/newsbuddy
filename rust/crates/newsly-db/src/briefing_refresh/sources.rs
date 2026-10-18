//! Eligible source loading and conversion into provider-independent Briefing projections.
use crate::news_visibility::{SUBSCRIBED_AGGREGATORS_CTE, visible_news_predicate};

use super::{
    AssertSqlSafe, BriefingRefreshSource, HashMap, HashSet, Map, NaiveDateTime, Postgres,
    SourceContentRow, SourceNewsRow, Transaction, Value,
};
use crate::briefing::parse_source_ids;

const BRIEFING_CONTEXT_MAX_CHARS: usize = 2_400;
const BRIEFING_SOURCE_EXCERPT_MAX_CHARS: usize = 900;
const BRIEFING_CONTEXT_LIST_MAX_ITEMS: usize = 6;
const BRIEFING_CONTEXT_ITEM_MAX_CHARS: usize = 700;

pub(crate) async fn load_eligible_sources_for_keys(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    keys: &[String],
) -> Result<HashMap<String, BriefingRefreshSource>, sqlx::Error> {
    let (content_ids, news_ids) = parse_source_ids(keys);
    let mut sources = HashMap::new();
    if !content_ids.is_empty() {
        let rows = sqlx::query_as::<_, SourceContentRow>(
            r#"
            SELECT content.id::bigint AS id, content.content_type, content.url,
                   content.source_url, content.title, content.source,
                   content.content_metadata::jsonb AS metadata, content.created_at,
                   content.publication_date
            FROM contents AS content
            JOIN content_status AS membership ON membership.content_id = content.id
            WHERE content.id = ANY($2::bigint[])
              AND membership.user_id = $1::bigint AND membership.status = 'inbox'
              AND content.status = 'completed'
              AND content.content_type IN ('article', 'podcast')
              AND NULLIF(COALESCE(
                  content.content_metadata::jsonb #> '{domain,image_generated_at}',
                  content.content_metadata::jsonb -> 'image_generated_at'
              ), 'null'::jsonb) IS NOT NULL
              AND NULLIF(COALESCE(
                  content.content_metadata::jsonb #> '{domain,image_url}',
                  content.content_metadata::jsonb -> 'image_url',
                  content.content_metadata::jsonb #> '{domain,thumbnail_url}',
                  content.content_metadata::jsonb -> 'thumbnail_url'
              ), 'null'::jsonb) IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM content_read_status AS read_status
                  WHERE read_status.user_id = $1::bigint
                    AND read_status.content_id = content.id
              )
            "#,
        )
        .bind(user_id)
        .bind(&content_ids)
        .fetch_all(&mut **transaction)
        .await?;
        for row in rows {
            let source = source_from_content(
                row.id,
                &row.content_type,
                &row.url,
                row.source_url.as_deref(),
                row.title.as_deref(),
                row.source.as_deref(),
                &row.metadata,
                row.created_at,
                row.publication_date,
            );
            sources.insert(source.source_key.clone(), source);
        }
    }
    if !news_ids.is_empty() {
        let rows = sqlx::query_as::<_, SourceNewsRow>(AssertSqlSafe(format!(
            r#"
            WITH visible_news AS ({visible_news})
            SELECT news.id::bigint AS id, news.summary_text,
                   news.summary_key_points::jsonb AS summary_key_points,
                   news.raw_metadata::jsonb AS raw_metadata, news.article_url,
                   news.canonical_story_url, news.canonical_item_url,
                   news.published_at, news.processed_at, news.ingested_at, news.created_at
            FROM visible_news AS news
            WHERE news.id = ANY($2::bigint[])
              AND NOT EXISTS (
                  SELECT 1 FROM news_items AS member
                  JOIN news_item_read_status AS read_status ON read_status.news_item_id = member.id
                  WHERE read_status.user_id = $1::bigint
                    AND coalesce(member.representative_news_item_id, member.id) = news.id
              )
            "#,
            visible_news = visible_news_sql()
        )))
        .bind(user_id)
        .bind(&news_ids)
        .fetch_all(&mut **transaction)
        .await?;
        for row in rows {
            let source = source_from_news(&row);
            sources.insert(source.source_key.clone(), source);
        }
    }
    Ok(sources)
}

pub(super) async fn load_read_source_keys(
    transaction: &mut Transaction<'static, Postgres>,
    user_id: i64,
    keys: &[String],
) -> Result<HashSet<String>, sqlx::Error> {
    let (content_ids, news_ids) = parse_source_ids(keys);
    let mut read = HashSet::new();
    if !content_ids.is_empty() {
        for id in sqlx::query_scalar::<_, i64>(
            "SELECT DISTINCT content_id::bigint FROM content_read_status WHERE user_id = $1::bigint AND content_id = ANY($2::bigint[])",
        )
        .bind(user_id)
        .bind(&content_ids)
        .fetch_all(&mut **transaction)
        .await?
        {
            read.insert(format!("content:{id}"));
        }
    }
    if !news_ids.is_empty() {
        for id in sqlx::query_scalar::<_, i64>(
            r#"
            SELECT requested.id::bigint
            FROM unnest($2::bigint[]) AS requested(id)
            JOIN news_items AS requested_news ON requested_news.id = requested.id
            WHERE EXISTS (
                SELECT 1 FROM news_items AS member
                JOIN news_item_read_status AS read_status ON read_status.news_item_id = member.id
                WHERE read_status.user_id = $1::bigint
                  AND coalesce(member.representative_news_item_id, member.id) =
                      coalesce(requested_news.representative_news_item_id, requested_news.id)
            )
            "#,
        )
        .bind(user_id)
        .bind(&news_ids)
        .fetch_all(&mut **transaction)
        .await?
        {
            read.insert(format!("news:{id}"));
        }
    }
    Ok(read)
}

pub(super) fn visible_news_sql() -> String {
    format!(
        r#"
    {SUBSCRIBED_AGGREGATORS_CTE}
    SELECT news.*
    FROM news_items AS news
    WHERE news.status = 'ready' AND news.representative_news_item_id IS NULL
      AND {visibility}
    "#,
        visibility = visible_news_predicate("news")
    )
}

// Restrict visibility checks to the exact pending news identities for this user.
pub(super) fn visible_pending_news_sql() -> String {
    format!(
        r#"
        {SUBSCRIBED_AGGREGATORS_CTE}
        SELECT news.id
        FROM briefing_pending_sources AS pending
        JOIN news_items AS news ON news.id = pending.source_id
        WHERE pending.user_id = $1::bigint AND pending.source_kind = 'news'
          AND news.status = 'ready' AND news.representative_news_item_id IS NULL
          AND {visibility}
        "#,
        visibility = visible_news_predicate("news")
    )
}

// The run parameter is a static SQL placeholder supplied by our query builders.
// Resolve the frozen cohort by primary key before evaluating subscription visibility.
pub(super) fn visible_cohort_news_sql(run_parameter: &str) -> String {
    format!(
        r#"
        {SUBSCRIBED_AGGREGATORS_CTE}
        SELECT news.*
        FROM onboarding_first_edition_items cohort
        JOIN news_items news ON news.id = cohort.source_id
        WHERE cohort.run_id = {run_parameter}::bigint AND cohort.source_kind = 'news'
          AND news.status = 'ready' AND news.representative_news_item_id IS NULL
          AND {visibility}
        "#,
        visibility = visible_news_predicate("news")
    )
}

#[allow(clippy::too_many_arguments)]
pub(super) fn source_from_content(
    id: i64,
    _content_type: &str,
    url: &str,
    source_url: Option<&str>,
    title: Option<&str>,
    source_name: Option<&str>,
    metadata: &Value,
    created_at: NaiveDateTime,
    publication_date: Option<NaiveDateTime>,
) -> BriefingRefreshSource {
    let map = metadata.as_object().cloned().unwrap_or_default();
    let summary_value = map.get("summary");
    let summary = summary_value
        .and_then(short_summary)
        .or_else(|| clean_value(map.get("excerpt")));
    let key_points = metadata_key_points(&map);
    let version = image_version(&map);
    BriefingRefreshSource {
        source_key: format!("content:{id}"),
        kind: "content".to_owned(),
        id,
        title: clean_text(title).unwrap_or_else(|| format!("Content {id}")),
        source_name: clean_text(source_name).or_else(|| clean_value(map.get("source"))),
        summary,
        key_points,
        url: clean_text(source_url).or_else(|| clean_text(Some(url))),
        image_url: clean_value(map.get("image_url"))
            .or_else(|| {
                version
                    .as_ref()
                    .map(|_| format!("/static/images/content/{id}.png"))
            })
            .map(|url| versioned_url(url, version.as_deref())),
        thumbnail_url: clean_value(map.get("thumbnail_url"))
            .or_else(|| {
                version
                    .as_ref()
                    .map(|_| format!("/static/images/thumbnails/{id}.png"))
            })
            .map(|url| versioned_url(url, version.as_deref())),
        published_at: publication_date
            .or(Some(created_at))
            .map(|value| value.and_utc()),
        briefing_context: briefing_context(&map),
    }
}

pub(crate) fn source_from_news(row: &super::SourceNewsRow) -> BriefingRefreshSource {
    let id = row.id;
    let summary_text = row.summary_text.as_deref();
    let article_url = row.article_url.as_deref();
    let canonical_story_url = row.canonical_story_url.as_deref();
    let canonical_item_url = row.canonical_item_url.as_deref();
    let summary_key_points = &row.summary_key_points;
    let raw_metadata = &row.raw_metadata;
    let published_at = row.published_at;
    let processed_at = row.processed_at;
    let ingested_at = row.ingested_at;
    let created_at = row.created_at;
    let map = raw_metadata.as_object().cloned().unwrap_or_default();
    let version = image_version(&map);
    let has_image = map.get("image_generated_at").is_some_and(value_truthy);
    let title = news_title(&map, summary_text, id);
    BriefingRefreshSource {
        source_key: format!("news:{id}"),
        kind: "news".to_owned(),
        id,
        title,
        source_name: None,
        summary: clean_text(summary_text),
        key_points: json_strings(summary_key_points),
        url: clean_text(article_url)
            .or_else(|| clean_text(canonical_story_url))
            .or_else(|| clean_text(canonical_item_url)),
        image_url: None,
        thumbnail_url: has_image.then(|| {
            versioned_url(
                format!("/static/images/news_thumbnails/{id}.png"),
                version.as_deref(),
            )
        }),
        published_at: published_at
            .or(processed_at)
            .or(Some(ingested_at))
            .or(Some(created_at))
            .map(|value| value.and_utc()),
        briefing_context: None,
    }
}

pub(super) fn news_title(
    metadata: &Map<String, Value>,
    summary_text: Option<&str>,
    id: i64,
) -> String {
    for path in [["summary", "title"], ["article", "title"]] {
        if let Some(value) = metadata
            .get(path[0])
            .and_then(Value::as_object)
            .and_then(|object| object.get(path[1]))
            .and_then(Value::as_str)
            .and_then(|value| clean_text(Some(value)))
        {
            return value;
        }
    }
    clean_text(summary_text).map_or_else(
        || format!("News item {id}"),
        |value| value.chars().take(120).collect(),
    )
}

pub(super) fn briefing_context(metadata: &Map<String, Value>) -> Option<String> {
    let mut parts = Vec::new();
    if let Some(summary) = metadata.get("summary") {
        if let Some(object) = summary.as_object() {
            for (label, key) in [
                ("Overview", "overview"),
                ("Summary", "summary"),
                ("Hook", "hook"),
                ("One line", "one_line"),
                ("Narrative", "editorial_narrative"),
            ] {
                append_labeled_text(&mut parts, label, object.get(key));
            }
            append_items(
                &mut parts,
                "Key points",
                ["key_points", "bullet_points", "points"]
                    .into_iter()
                    .find_map(|key| {
                        object
                            .get(key)
                            .filter(|value| value.as_array().is_some_and(|items| !items.is_empty()))
                    }),
                BRIEFING_CONTEXT_LIST_MAX_ITEMS,
            );
            append_items(
                &mut parts,
                "Insights",
                object.get("insights"),
                BRIEFING_CONTEXT_LIST_MAX_ITEMS,
            );
            append_items(
                &mut parts,
                "Topics",
                object.get("topics"),
                BRIEFING_CONTEXT_LIST_MAX_ITEMS,
            );
            append_items(&mut parts, "Quotes", object.get("quotes"), 3);
            append_items(&mut parts, "Questions", object.get("questions"), 3);
            append_items(
                &mut parts,
                "Counterarguments",
                object.get("counter_arguments"),
                3,
            );
            append_labeled_text(&mut parts, "Takeaway", object.get("takeaway"));
            append_source_details(&mut parts, object.get("source_details"));
            append_artifact(&mut parts, object.get("artifact"));
            append_feed_preview(&mut parts, object.get("feed_preview"));
            append_source_excerpt(&mut parts, "Source excerpt", object.get("full_markdown"));
        } else {
            append_labeled_text(&mut parts, "Summary", Some(summary));
        }
    }
    append_labeled_text(&mut parts, "Excerpt", metadata.get("excerpt"));
    append_source_excerpt(
        &mut parts,
        "Source excerpt",
        metadata.get("content_to_summarize"),
    );
    append_source_excerpt(&mut parts, "Source excerpt", metadata.get("content"));
    append_source_excerpt(&mut parts, "Transcript excerpt", metadata.get("transcript"));
    let value = parts.join("\n\n");
    (!value.is_empty()).then(|| truncate_text(&value, BRIEFING_CONTEXT_MAX_CHARS))
}

fn append_labeled_text(parts: &mut Vec<String>, label: &str, value: Option<&Value>) {
    if let Some(text) = text_from_value(value) {
        parts.push(format!("{label}: {text}"));
    }
}

fn append_items(parts: &mut Vec<String>, label: &str, value: Option<&Value>, max_items: usize) {
    let Some(items) = value.and_then(Value::as_array) else {
        return;
    };
    let lines = items
        .iter()
        .take(max_items)
        .filter_map(|item| text_from_value(Some(item)))
        .map(|item| format!("- {item}"))
        .collect::<Vec<_>>();
    if !lines.is_empty() {
        parts.push(format!("{label}:\n{}", lines.join("\n")));
    }
}

fn append_source_details(parts: &mut Vec<String>, value: Option<&Value>) {
    let Some(details) = value.and_then(Value::as_object) else {
        return;
    };
    let mut lines = Vec::new();
    for (key, value) in details {
        if key == "template" {
            continue;
        }
        let label = display_label(key);
        if let Some(items) = value.as_array() {
            let items = items
                .iter()
                .take(BRIEFING_CONTEXT_LIST_MAX_ITEMS)
                .filter_map(|item| text_from_value(Some(item)))
                .collect::<Vec<_>>();
            if !items.is_empty() {
                lines.push(format!("{label}: {}", items.join("; ")));
            }
        } else if let Some(text) = text_from_value(Some(value)) {
            lines.push(format!("{label}: {text}"));
        }
    }
    if !lines.is_empty() {
        parts.push(format!(
            "Source details:\n{}",
            lines
                .into_iter()
                .map(|line| format!("- {line}"))
                .collect::<Vec<_>>()
                .join("\n")
        ));
    }
}

fn append_artifact(parts: &mut Vec<String>, value: Option<&Value>) {
    let Some(payload) = value
        .and_then(Value::as_object)
        .and_then(|artifact| artifact.get("payload"))
        .and_then(Value::as_object)
    else {
        return;
    };
    let mut lines = Vec::new();
    for key in [
        "overview",
        "thesis",
        "primary_claim",
        "counterpoint",
        "takeaway",
        "reason_to_read",
    ] {
        if let Some(text) = text_from_value(payload.get(key)) {
            lines.push(format!("{}: {text}", display_label(key)));
        }
    }
    for key in [
        "key_points",
        "supporting_arguments",
        "evidence",
        "implications",
        "quotes",
    ] {
        let Some(items) = payload.get(key).and_then(Value::as_array) else {
            continue;
        };
        let items = items
            .iter()
            .take(BRIEFING_CONTEXT_LIST_MAX_ITEMS)
            .filter_map(|item| text_from_value(Some(item)))
            .collect::<Vec<_>>();
        if !items.is_empty() {
            lines.push(format!("{}: {}", display_label(key), items.join("; ")));
        }
    }
    if let Some(extras) = payload.get("extras").and_then(Value::as_object) {
        for (key, value) in extras {
            if let Some(text) = text_from_value(Some(value)) {
                lines.push(format!("{}: {text}", display_label(key)));
            }
        }
    }
    if !lines.is_empty() {
        parts.push(format!(
            "Artifact context:\n{}",
            lines
                .into_iter()
                .map(|line| format!("- {line}"))
                .collect::<Vec<_>>()
                .join("\n")
        ));
    }
}

fn append_feed_preview(parts: &mut Vec<String>, value: Option<&Value>) {
    let Some(preview) = value.and_then(Value::as_object) else {
        return;
    };
    let mut lines = Vec::new();
    for key in ["one_line", "reason_to_read"] {
        if let Some(text) = text_from_value(preview.get(key)) {
            lines.push(format!("{}: {text}", display_label(key)));
        }
    }
    if let Some(items) = preview.get("preview_bullets").and_then(Value::as_array) {
        let items = items
            .iter()
            .take(3)
            .filter_map(|item| text_from_value(Some(item)))
            .collect::<Vec<_>>();
        if !items.is_empty() {
            lines.push(format!("Preview bullets: {}", items.join("; ")));
        }
    }
    if !lines.is_empty() {
        parts.push(format!(
            "Feed preview:\n{}",
            lines
                .into_iter()
                .map(|line| format!("- {line}"))
                .collect::<Vec<_>>()
                .join("\n")
        ));
    }
}

fn append_source_excerpt(parts: &mut Vec<String>, label: &str, value: Option<&Value>) {
    if let Some(text) = text_from_value(value) {
        parts.push(format!(
            "{label}: {}",
            truncate_text(&text, BRIEFING_SOURCE_EXCERPT_MAX_CHARS)
        ));
    }
}

fn text_from_value(value: Option<&Value>) -> Option<String> {
    let value = value?;
    if let Some(value) = value.as_str() {
        return clean_text(Some(value));
    }
    let object = value.as_object()?;
    let heading = [
        "heading",
        "topic",
        "title",
        "category",
        "attribution",
        "context",
    ]
    .into_iter()
    .find_map(|key| clean_value(object.get(key)));
    let body = [
        "text",
        "point",
        "detail",
        "content",
        "summary",
        "insight",
        "overview",
        "thesis",
        "primary_claim",
        "takeaway",
    ]
    .into_iter()
    .find_map(|key| clean_value(object.get(key)));
    let nested = object
        .get("bullets")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .take(3)
        .filter_map(|item| text_from_value(Some(item)))
        .collect::<Vec<_>>();
    let mut text = match (heading, body) {
        (Some(heading), Some(body)) if !body.to_lowercase().contains(&heading.to_lowercase()) => {
            Some(format!("{heading}: {body}"))
        }
        (heading, body) => body.or(heading),
    };
    if !nested.is_empty() {
        let nested = nested.join("; ");
        text = Some(text.map_or(nested.clone(), |text| format!("{text} ({nested})")));
    }
    text.map(|text| truncate_text(&text, BRIEFING_CONTEXT_ITEM_MAX_CHARS))
}

fn display_label(key: &str) -> String {
    let value = key.replace('_', " ");
    let mut chars = value.chars();
    chars
        .next()
        .map(|first| first.to_uppercase().collect::<String>() + chars.as_str())
        .unwrap_or_default()
}

fn truncate_text(value: &str, max_chars: usize) -> String {
    let cleaned = value.split_whitespace().collect::<Vec<_>>().join(" ");
    if cleaned.chars().count() <= max_chars {
        return cleaned;
    }
    let available = max_chars.saturating_sub(3);
    let prefix = cleaned.chars().take(available).collect::<String>();
    let truncated = prefix
        .rsplit_once(char::is_whitespace)
        .map_or(prefix.as_str(), |(head, _)| head)
        .trim_end_matches(['.', ',', ';', ':']);
    format!("{truncated}...").chars().take(max_chars).collect()
}

pub(super) fn metadata_key_points(metadata: &Map<String, Value>) -> Vec<String> {
    metadata
        .get("key_points")
        .or_else(|| {
            metadata
                .get("summary")
                .and_then(Value::as_object)
                .and_then(|summary| summary.get("key_points").or_else(|| summary.get("points")))
        })
        .map_or_else(Vec::new, json_strings)
        .into_iter()
        .take(6)
        .collect()
}

pub(super) fn short_summary(value: &Value) -> Option<String> {
    value
        .as_str()
        .and_then(|value| clean_text(Some(value)))
        .or_else(|| {
            value.as_object().and_then(|object| {
                ["one_line", "summary", "overview", "hook"]
                    .into_iter()
                    .find_map(|key| clean_value(object.get(key)))
                    .or_else(|| {
                        object
                            .get("feed_preview")
                            .and_then(Value::as_object)
                            .and_then(|preview| clean_value(preview.get("one_line")))
                    })
            })
        })
}

pub(super) fn clean_value(value: Option<&Value>) -> Option<String> {
    value
        .and_then(Value::as_str)
        .and_then(|value| clean_text(Some(value)))
}

pub(super) fn clean_text(value: Option<&str>) -> Option<String> {
    let compact = value?.split_whitespace().collect::<Vec<_>>().join(" ");
    (!compact.is_empty()).then_some(compact)
}

pub(super) fn json_strings(value: &Value) -> Vec<String> {
    value
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|item| {
            item.as_str()
                .or_else(|| item.get("text").and_then(Value::as_str))
                .and_then(|value| clean_text(Some(value)))
        })
        .collect()
}

pub(super) fn event_group_count(value: &Value) -> usize {
    value.as_array().map_or(0, Vec::len)
}

pub(super) fn image_version(metadata: &Map<String, Value>) -> Option<String> {
    metadata
        .get("image_version")
        .filter(|value| value_truthy(value))
        .or_else(|| {
            metadata
                .get("thumbnail_version")
                .filter(|value| value_truthy(value))
        })
        .and_then(|value| match value {
            Value::String(value) => clean_text(Some(value)),
            Value::Number(value) => Some(value.to_string()),
            Value::Bool(true) => Some("true".to_owned()),
            _ => None,
        })
}

pub(super) fn value_truthy(value: &Value) -> bool {
    match value {
        Value::Null | Value::Bool(false) => false,
        Value::String(value) => !value.trim().is_empty(),
        Value::Number(_) | Value::Bool(true) => true,
        Value::Array(value) => !value.is_empty(),
        Value::Object(value) => !value.is_empty(),
    }
}

pub(super) fn versioned_url(base: String, version: Option<&str>) -> String {
    match version {
        Some(version) => format!("{base}?v={}", percent_encode(version)),
        None => base,
    }
}

pub(super) fn percent_encode(value: &str) -> String {
    value
        .bytes()
        .flat_map(|byte| {
            if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.' | b'~') {
                vec![char::from(byte)]
            } else {
                format!("%{byte:02X}").chars().collect()
            }
        })
        .collect()
}

pub(super) fn u64_to_i32(value: u64) -> i32 {
    i32::try_from(value).unwrap_or(i32::MAX)
}
