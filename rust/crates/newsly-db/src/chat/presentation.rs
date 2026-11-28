use std::collections::{BTreeSet, HashMap};

use newsly_domain::truncate_chars;
use serde_json::Value;
use sqlx::{FromRow, PgPool, Postgres, Transaction};

use super::{
    ArticlePresentation, AssistantScreenContext, ContentPresentationRow, NewsPresentationRow,
    load_visible_content_in_transaction, load_visible_news_in_transaction,
};

#[derive(Debug, Clone, FromRow)]
struct BatchContentPresentationRow {
    id: i64,
    url: String,
    title: Option<String>,
    source: Option<String>,
    content_metadata: Value,
}

#[derive(Debug, Clone, FromRow)]
struct BatchNewsPresentationRow {
    id: i64,
    platform: Option<String>,
    source_type: Option<String>,
    source_label: Option<String>,
    canonical_story_url: Option<String>,
    article_url: Option<String>,
    article_domain: Option<String>,
    discussion_url: Option<String>,
    summary_key_points: Value,
    summary_text: Option<String>,
    raw_metadata: Value,
}

pub(super) async fn load_contents_by_ids(
    pool: &PgPool,
    content_ids: &[i64],
) -> Result<HashMap<i64, ContentPresentationRow>, sqlx::Error> {
    if content_ids.is_empty() {
        return Ok(HashMap::new());
    }
    let rows = sqlx::query_as::<_, BatchContentPresentationRow>(
        r"
        SELECT
            id::bigint AS id,
            url,
            title,
            source,
            content_metadata
        FROM contents
        WHERE id = ANY($1::bigint[])
        ",
    )
    .bind(content_ids)
    .fetch_all(pool)
    .await?;
    Ok(rows
        .into_iter()
        .map(|row| {
            (
                row.id,
                ContentPresentationRow {
                    url: row.url,
                    title: row.title,
                    source: row.source,
                    content_metadata: row.content_metadata,
                },
            )
        })
        .collect())
}

pub(super) async fn load_news_by_ids(
    pool: &PgPool,
    news_ids: &[i64],
) -> Result<HashMap<i64, NewsPresentationRow>, sqlx::Error> {
    if news_ids.is_empty() {
        return Ok(HashMap::new());
    }
    let rows = sqlx::query_as::<_, BatchNewsPresentationRow>(
        r"
        SELECT
            id::bigint AS id,
            platform,
            source_type,
            source_label,
            canonical_story_url,
            article_url,
            article_domain,
            discussion_url,
            summary_key_points,
            summary_text,
            raw_metadata
        FROM news_items
        WHERE id = ANY($1::bigint[])
        ",
    )
    .bind(news_ids)
    .fetch_all(pool)
    .await?;
    Ok(rows
        .into_iter()
        .map(|row| {
            (
                row.id,
                NewsPresentationRow {
                    platform: row.platform,
                    source_type: row.source_type,
                    source_label: row.source_label,
                    canonical_story_url: row.canonical_story_url,
                    article_url: row.article_url,
                    article_domain: row.article_domain,
                    discussion_url: row.discussion_url,
                    summary_key_points: row.summary_key_points,
                    summary_text: row.summary_text,
                    raw_metadata: row.raw_metadata,
                },
            )
        })
        .collect())
}

pub(super) async fn load_waiting_content_ids(
    pool: &PgPool,
    user_id: i64,
    content_ids: &[i64],
) -> Result<BTreeSet<i64>, sqlx::Error> {
    if content_ids.is_empty() {
        return Ok(BTreeSet::new());
    }
    let ids = sqlx::query_scalar::<_, i64>(
        r"
        SELECT candidate.content_id
        FROM unnest($1::bigint[]) AS candidate(content_id)
        WHERE EXISTS(
            SELECT 1
            FROM contents
            WHERE id = candidate.content_id
              AND (
                    COALESCE(
                        content_metadata::jsonb->'processing'->'share_and_chat_requests',
                        '[]'::jsonb
                    ) @> jsonb_build_array(jsonb_build_object('user_id', $2::bigint))
                    OR COALESCE(
                        content_metadata::jsonb->'share_and_chat_requests',
                        '[]'::jsonb
                    ) @> jsonb_build_array(jsonb_build_object('user_id', $2::bigint))
              )
        ) OR EXISTS(
            SELECT 1
            FROM processing_tasks
            WHERE content_id = candidate.content_id
              AND task_type = 'dig_deeper'
              AND status IN ('pending', 'processing')
              AND payload::jsonb @> jsonb_build_object('user_id', $2::bigint)
        )
        ",
    )
    .bind(content_ids)
    .bind(user_id)
    .fetch_all(pool)
    .await?;
    Ok(ids.into_iter().collect())
}

pub(super) fn content_presentation(content: &ContentPresentationRow) -> ArticlePresentation {
    let metadata = content
        .content_metadata
        .as_object()
        .cloned()
        .unwrap_or_default();
    ArticlePresentation {
        title: Some(content_title(content)),
        url: Some(content.url.clone()),
        summary: extract_short_summary(metadata.get("summary")),
        source: content.source.clone(),
        image_url: metadata
            .get("image_url")
            .and_then(Value::as_str)
            .map(str::to_owned),
        thumbnail_url: metadata
            .get("thumbnail_url")
            .and_then(Value::as_str)
            .map(str::to_owned),
    }
}

pub(super) fn news_presentation(news: &NewsPresentationRow) -> ArticlePresentation {
    ArticlePresentation {
        title: Some(news_title(news)),
        url: news
            .article_url
            .clone()
            .or_else(|| news.canonical_story_url.clone()),
        summary: news.summary_text.clone(),
        source: first_non_empty([
            news.source_label.as_deref(),
            news.article_domain.as_deref(),
            news.platform.as_deref(),
            news.source_type.as_deref(),
        ]),
        image_url: None,
        thumbnail_url: None,
    }
}

pub(super) fn content_title(content: &ContentPresentationRow) -> String {
    content_display_title(content.title.as_deref(), &content.content_metadata)
        .unwrap_or_else(|| "Untitled".to_owned())
}

pub(crate) fn content_display_title(title: Option<&str>, metadata: &Value) -> Option<String> {
    let metadata = metadata
        .as_object()
        .map(crate::content_metadata::runtime_metadata)
        .unwrap_or_default();
    crate::content_metadata::source_presentation::content_display_title(title, &metadata)
        .or_else(|| extract_short_summary(metadata.get("summary")))
        .map(|value| summarize_as_title(&value))
}

pub(super) fn news_title(news: &NewsPresentationRow) -> String {
    let metadata = news.raw_metadata.as_object();
    let summary_title = metadata
        .and_then(|value| value.get("summary"))
        .and_then(Value::as_object)
        .and_then(|value| value.get("title"))
        .and_then(Value::as_str);
    let related_title = metadata
        .and_then(|value| value.get("cluster"))
        .and_then(Value::as_object)
        .and_then(|value| value.get("related_titles"))
        .and_then(Value::as_array)
        .and_then(|values| values.first())
        .and_then(Value::as_str);
    let article_title = metadata
        .and_then(|value| value.get("article"))
        .and_then(Value::as_object)
        .and_then(|value| value.get("title"))
        .and_then(Value::as_str);
    [summary_title, related_title, article_title]
        .into_iter()
        .flatten()
        .find(|value| !value.trim().is_empty())
        .map(|value| truncate_chars(value.trim(), 500))
        .or_else(|| news.summary_text.as_deref().map(summarize_as_title))
        .unwrap_or_else(|| "Untitled News Item".to_owned())
}

pub(crate) fn extract_short_summary(value: Option<&Value>) -> Option<String> {
    match value {
        Some(Value::String(value)) if !value.trim().is_empty() => Some(value.clone()),
        Some(Value::Object(summary)) => {
            for field in ["one_line", "overview", "summary", "hook", "takeaway"] {
                if let Some(value) = summary.get(field).and_then(Value::as_str)
                    && !value.trim().is_empty()
                {
                    return Some(value.to_owned());
                }
            }
            summary
                .get("artifact")
                .and_then(Value::as_object)
                .and_then(|value| value.get("payload"))
                .and_then(Value::as_object)
                .and_then(|value| value.get("overview"))
                .and_then(Value::as_str)
                .filter(|value| !value.trim().is_empty())
                .map(str::to_owned)
        }
        _ => None,
    }
}

pub(crate) async fn build_screen_context_snapshot(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    context: &AssistantScreenContext,
) -> Result<String, sqlx::Error> {
    let screen_type =
        clean_context_text(Some(&context.screen_type)).unwrap_or_else(|| "unknown".to_owned());
    let mut lines = vec![format!("Screen Type: {screen_type}")];
    append_context_line(
        &mut lines,
        "Screen Title",
        clean_context_text(context.screen_title.as_deref()),
    );
    append_context_line(
        &mut lines,
        "Selected Topic",
        clean_context_text(context.selected_topic.as_deref()),
    );
    append_context_line(
        &mut lines,
        "Query",
        clean_context_text(context.query.as_deref()),
    );
    append_context_line(
        &mut lines,
        "Client Note",
        clean_context_text(context.note.as_deref()),
    );
    append_context_line(
        &mut lines,
        "Assistant Action",
        clean_context_text(context.assistant_action.as_deref()),
    );

    let mut content_ids = Vec::new();
    push_unique_positive(&mut content_ids, context.content_id);
    for id in context.visible_content_ids.iter().copied() {
        push_unique_positive(&mut content_ids, Some(id));
    }
    if !content_ids.is_empty() {
        let mut content_lines = Vec::new();
        for content_id in content_ids {
            let Some(content) =
                load_visible_content_in_transaction(transaction, user_id, content_id).await?
            else {
                continue;
            };
            let source = content
                .source
                .as_deref()
                .filter(|value| !value.trim().is_empty())
                .map_or_else(String::new, |value| format!(" ({value})"));
            content_lines.push(format!(
                "- [{content_id}] {}{source} — {}",
                content_title(&content),
                content.url
            ));
            if let Some(summary) = extract_short_summary(
                content
                    .content_metadata
                    .as_object()
                    .and_then(|value| value.get("summary")),
            ) {
                content_lines.push(format!("  Short Summary: {summary}"));
            }
            let excerpt_limit =
                if screen_type == "learning_deck" && context.content_id == Some(content_id) {
                    1_800
                } else {
                    420
                };
            if let Some(excerpt) = content_excerpt(&content.content_metadata, excerpt_limit) {
                content_lines.push(format!("  Transcript Excerpt: {excerpt}"));
            }
        }
        if !content_lines.is_empty() {
            lines.push("Visible Content:".to_owned());
            lines.extend(content_lines);
        }
    }

    let mut news_item_ids = Vec::new();
    push_unique_positive(&mut news_item_ids, context.news_item_id);
    for id in context.visible_news_item_ids.iter().copied() {
        push_unique_positive(&mut news_item_ids, Some(id));
    }
    if !news_item_ids.is_empty() {
        let mut news_lines = Vec::new();
        for news_item_id in news_item_ids {
            let Some(news) =
                load_visible_news_in_transaction(transaction, user_id, news_item_id).await?
            else {
                continue;
            };
            let source = first_non_empty([
                news.source_label.as_deref(),
                news.article_domain.as_deref(),
                news.platform.as_deref(),
                news.source_type.as_deref(),
            ])
            .map_or_else(String::new, |value| format!(" ({value})"));
            news_lines.push(format!(
                "- [news:{news_item_id}] {}{source}",
                news_title(&news)
            ));
            if let Some(url) = &news.article_url {
                news_lines.push(format!("  Article URL: {url}"));
            }
            if news.canonical_story_url != news.article_url
                && let Some(url) = &news.canonical_story_url
            {
                news_lines.push(format!("  Story URL: {url}"));
            }
            if let Some(url) = &news.discussion_url {
                news_lines.push(format!("  Discussion URL: {url}"));
            }
            if let Some(summary) = &news.summary_text {
                news_lines.push(format!("  Summary: {summary}"));
            }
            if let Some(points) = news.summary_key_points.as_array()
                && !points.is_empty()
            {
                let rendered = points
                    .iter()
                    .take(5)
                    .map(value_as_display_text)
                    .collect::<Vec<_>>()
                    .join("; ");
                news_lines.push(format!("  Key Points: {rendered}"));
            }
        }
        if !news_lines.is_empty() {
            lines.push("Visible News Items:".to_owned());
            lines.extend(news_lines);
        }
    }
    Ok(lines.join("\n"))
}

fn clean_context_text(value: Option<&str>) -> Option<String> {
    value
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
}

fn push_unique_positive(values: &mut Vec<i64>, value: Option<i64>) {
    if let Some(value) = value.filter(|value| *value > 0)
        && !values.contains(&value)
    {
        values.push(value);
    }
}

fn append_context_line(lines: &mut Vec<String>, label: &str, value: Option<String>) {
    if let Some(value) = value {
        lines.push(format!("{label}: {value}"));
    }
}

fn content_excerpt(metadata: &Value, max_chars: usize) -> Option<String> {
    let metadata = metadata.as_object()?;
    let candidates = [
        metadata.get("excerpt"),
        metadata.get("transcript"),
        metadata.get("content"),
        metadata
            .get("summary")
            .and_then(Value::as_object)
            .and_then(|summary| summary.get("full_markdown")),
    ];
    for candidate in candidates.into_iter().flatten() {
        let Some(text) = candidate.as_str() else {
            continue;
        };
        let compact = text.split_whitespace().collect::<Vec<_>>().join(" ");
        if compact.is_empty() {
            continue;
        }
        if compact.chars().count() <= max_chars {
            return Some(compact);
        }
        let mut excerpt = compact
            .chars()
            .take(max_chars.saturating_sub(3))
            .collect::<String>();
        excerpt = excerpt.trim_end().to_owned();
        excerpt.push_str("...");
        return Some(excerpt);
    }
    None
}

fn summarize_as_title(value: &str) -> String {
    let compact = value.split_whitespace().collect::<Vec<_>>().join(" ");
    if compact.chars().count() <= 120 {
        return compact;
    }
    let excerpt = truncate_chars(&compact, 120);
    format!("{}…", excerpt.trim_end())
}

fn first_non_empty<'a>(values: impl IntoIterator<Item = Option<&'a str>>) -> Option<String> {
    values
        .into_iter()
        .flatten()
        .map(str::trim)
        .find(|value| !value.is_empty())
        .map(str::to_owned)
}

fn value_as_display_text(value: &Value) -> String {
    match value {
        Value::String(value) => value.clone(),
        _ => value.to_string(),
    }
}
