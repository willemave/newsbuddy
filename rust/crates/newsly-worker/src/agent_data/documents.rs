use std::collections::{BTreeMap, BTreeSet};

use chrono::{Datelike, NaiveDateTime};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use sqlx::{FromRow, PgPool};

use super::storage::{AgentDataMirrorStore, AgentDataMirrorStoreError};

const TRUNCATION_MARKER: &str = "\n\n[Newsly: document truncated at the per-file byte limit.]\n";

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub(super) struct AgentDataSelection {
    pub(super) content_ids: BTreeSet<i64>,
    pub(super) news_item_ids: BTreeSet<i64>,
    pub(super) chat_session_ids: BTreeSet<i64>,
    pub(super) briefing_dates: BTreeSet<String>,
}

impl AgentDataSelection {
    pub(super) fn identities(&self) -> Vec<(String, String)> {
        self.content_ids
            .iter()
            .map(|value| ("content".to_owned(), value.to_string()))
            .chain(
                self.news_item_ids
                    .iter()
                    .map(|value| ("news".to_owned(), value.to_string())),
            )
            .chain(
                self.chat_session_ids
                    .iter()
                    .map(|value| ("chat".to_owned(), value.to_string())),
            )
            .chain(
                self.briefing_dates
                    .iter()
                    .map(|value| ("briefing".to_owned(), value.clone())),
            )
            .collect()
    }
}

#[derive(Debug, Clone, PartialEq)]
pub(super) struct AgentDataDocument {
    pub(super) document_kind: String,
    pub(super) document_key: String,
    pub(super) path: String,
    pub(super) content_bytes: Vec<u8>,
    pub(super) checksum_sha256: String,
    pub(super) index_record: Value,
}

#[derive(Debug, Clone, PartialEq)]
pub(super) struct AgentDataDocumentSnapshot {
    pub(super) user_id: i64,
    pub(super) expected_revision: i64,
    pub(super) documents: Vec<AgentDataDocument>,
    pub(super) selected_identities: Vec<(String, String)>,
}

#[derive(Debug, FromRow)]
struct ContentRow {
    id: i64,
    content_type: String,
    url: String,
    title: Option<String>,
    source: Option<String>,
    publication_date: Option<NaiveDateTime>,
    processed_at: Option<NaiveDateTime>,
    created_at: NaiveDateTime,
    content_metadata: Value,
    saved: bool,
    source_provider: Option<String>,
    source_key: Option<String>,
    rendered_provider: Option<String>,
    rendered_key: Option<String>,
}

#[derive(Debug, FromRow)]
struct NewsRow {
    id: i64,
    platform: Option<String>,
    source_label: Option<String>,
    canonical_item_url: Option<String>,
    canonical_story_url: Option<String>,
    article_url: Option<String>,
    discussion_url: Option<String>,
    summary_key_points: Value,
    summary_text: Option<String>,
    raw_metadata: Value,
    published_at: Option<NaiveDateTime>,
    processed_at: Option<NaiveDateTime>,
    ingested_at: NaiveDateTime,
    legacy_source_provider: Option<String>,
    legacy_source_key: Option<String>,
    legacy_rendered_provider: Option<String>,
    legacy_rendered_key: Option<String>,
    legacy_content_type: Option<String>,
    legacy_content_metadata: Value,
}

#[derive(Debug, FromRow)]
struct ChatRow {
    id: i64,
    title: Option<String>,
    topic: Option<String>,
    session_type: Option<String>,
    created_at: NaiveDateTime,
    message_lists: Value,
}

#[derive(Debug, FromRow)]
struct BriefingRow {
    id: i64,
    markdown_raw: String,
    narration_text: String,
    created_at: NaiveDateTime,
}

/// Loads a bounded relational snapshot, releases `PostgreSQL`, then resolves immutable body files.
pub(super) async fn collect_agent_data_documents(
    pool: &PgPool,
    store: &AgentDataMirrorStore,
    user_id: i64,
    selection: &AgentDataSelection,
    max_document_bytes: usize,
) -> Result<Option<AgentDataDocumentSnapshot>, AgentDataDocumentError> {
    let mut transaction = pool.begin().await?;
    let revision = sqlx::query_scalar::<_, i64>(
        r"
        SELECT agent_data_revision
        FROM users
        WHERE id::bigint = $1 AND is_active IS TRUE
        FOR SHARE
        ",
    )
    .bind(user_id)
    .fetch_optional(&mut *transaction)
    .await?;
    let Some(expected_revision) = revision else {
        transaction.rollback().await?;
        return Ok(None);
    };

    let content_ids = selection.content_ids.iter().copied().collect::<Vec<_>>();
    let news_ids = selection.news_item_ids.iter().copied().collect::<Vec<_>>();
    let chat_ids = selection
        .chat_session_ids
        .iter()
        .copied()
        .collect::<Vec<_>>();
    let briefing_dates = selection.briefing_dates.iter().cloned().collect::<Vec<_>>();
    let content_rows = load_content_rows(&mut transaction, user_id, &content_ids).await?;
    let news_rows = load_news_rows(&mut transaction, user_id, &news_ids).await?;
    let chat_rows = load_chat_rows(&mut transaction, user_id, &chat_ids).await?;
    let briefing_rows = load_briefing_rows(&mut transaction, user_id, &briefing_dates).await?;
    transaction.commit().await?;

    let mut documents = Vec::new();
    for row in content_rows {
        documents.push(render_content(store, row, max_document_bytes).await?);
    }
    for row in news_rows {
        documents.push(render_news(store, row, max_document_bytes).await?);
    }
    documents.extend(render_chats(chat_rows, max_document_bytes)?);
    documents.extend(render_briefings(briefing_rows, max_document_bytes)?);
    documents.sort_by(|left, right| left.path.cmp(&right.path));
    Ok(Some(AgentDataDocumentSnapshot {
        user_id,
        expected_revision,
        documents,
        selected_identities: selection.identities(),
    }))
}

async fn load_content_rows(
    transaction: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    user_id: i64,
    ids: &[i64],
) -> Result<Vec<ContentRow>, sqlx::Error> {
    if ids.is_empty() {
        return Ok(Vec::new());
    }
    sqlx::query_as::<_, ContentRow>(
        r"
        SELECT content.id::bigint AS id, content.content_type, content.url, content.title,
               content.source, content.publication_date, content.processed_at, content.created_at,
               coalesce(content.content_metadata, '{}'::json)::jsonb AS content_metadata,
               EXISTS (
                   SELECT 1 FROM content_knowledge_saves AS save
                   WHERE save.user_id::bigint = $1 AND save.content_id = content.id
               ) AS saved,
               source_body.storage_provider AS source_provider,
               source_body.storage_key AS source_key,
               rendered_body.storage_provider AS rendered_provider,
               rendered_body.storage_key AS rendered_key
        FROM contents AS content
        LEFT JOIN content_bodies AS source_body
          ON source_body.content_id = content.id AND source_body.variant = 'source'
        LEFT JOIN content_bodies AS rendered_body
          ON rendered_body.content_id = content.id AND rendered_body.variant = 'rendered'
        WHERE content.id::bigint = ANY($2::bigint[])
          AND content.status = 'completed'
          AND (
              EXISTS (SELECT 1 FROM content_status WHERE user_id::bigint = $1 AND content_id = content.id)
              OR EXISTS (SELECT 1 FROM content_knowledge_saves WHERE user_id::bigint = $1 AND content_id = content.id)
              OR EXISTS (SELECT 1 FROM chat_sessions WHERE user_id::bigint = $1 AND content_id = content.id)
          )
        ORDER BY content.id
        ",
    )
    .bind(user_id)
    .bind(ids)
    .fetch_all(&mut **transaction)
    .await
}

async fn load_news_rows(
    transaction: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    user_id: i64,
    ids: &[i64],
) -> Result<Vec<NewsRow>, sqlx::Error> {
    if ids.is_empty() {
        return Ok(Vec::new());
    }
    sqlx::query_as::<_, NewsRow>(
        r"
        SELECT news.id::bigint AS id, news.platform, news.source_label,
               news.canonical_item_url, news.canonical_story_url, news.article_url,
               news.discussion_url, coalesce(news.summary_key_points, '[]'::json)::jsonb AS summary_key_points,
               news.summary_text, coalesce(news.raw_metadata, '{}'::json)::jsonb AS raw_metadata,
               news.published_at, news.processed_at, news.ingested_at,
               legacy_source.storage_provider AS legacy_source_provider,
               legacy_source.storage_key AS legacy_source_key,
               legacy_rendered.storage_provider AS legacy_rendered_provider,
               legacy_rendered.storage_key AS legacy_rendered_key,
               legacy_content.content_type AS legacy_content_type,
               coalesce(legacy_content.content_metadata, '{}'::json)::jsonb AS legacy_content_metadata
        FROM news_items AS news
        LEFT JOIN contents AS legacy_content
          ON legacy_content.id = news.legacy_content_id AND legacy_content.status = 'completed'
        LEFT JOIN content_bodies AS legacy_source
          ON legacy_source.content_id = news.legacy_content_id AND legacy_source.variant = 'source'
        LEFT JOIN content_bodies AS legacy_rendered
          ON legacy_rendered.content_id = news.legacy_content_id AND legacy_rendered.variant = 'rendered'
        WHERE news.id::bigint = ANY($2::bigint[])
          AND news.status = 'ready'
          AND news.representative_news_item_id IS NULL
          AND (
              (news.visibility_scope = 'user' AND news.owner_user_id::bigint = $1)
              OR (
                  news.visibility_scope = 'global'
                  AND EXISTS (
                      SELECT 1 FROM user_scraper_configs AS config
                      WHERE config.user_id::bigint = $1
                        AND config.is_active IS TRUE
                        AND config.scraper_type = 'aggregator'
                        AND lower(config.config::jsonb ->> 'key') = lower(news.platform)
                  )
              )
          )
        ORDER BY news.id
        ",
    )
    .bind(user_id)
    .bind(ids)
    .fetch_all(&mut **transaction)
    .await
}

async fn load_chat_rows(
    transaction: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    user_id: i64,
    ids: &[i64],
) -> Result<Vec<ChatRow>, sqlx::Error> {
    if ids.is_empty() {
        return Ok(Vec::new());
    }
    sqlx::query_as::<_, ChatRow>(
        r"
        SELECT session.id::bigint AS id, session.title, session.topic, session.session_type,
               session.created_at,
               coalesce(
                   jsonb_agg(message.message_list ORDER BY message.created_at, message.id)
                       FILTER (WHERE message.id IS NOT NULL),
                   '[]'::jsonb
               ) AS message_lists
        FROM chat_sessions AS session
        LEFT JOIN chat_messages AS message
          ON message.session_id = session.id AND message.status = 'completed'
        WHERE session.user_id::bigint = $1 AND session.id::bigint = ANY($2::bigint[])
        GROUP BY session.id
        ORDER BY session.id
        ",
    )
    .bind(user_id)
    .bind(ids)
    .fetch_all(&mut **transaction)
    .await
}

async fn load_briefing_rows(
    transaction: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    user_id: i64,
    dates: &[String],
) -> Result<Vec<BriefingRow>, sqlx::Error> {
    if dates.is_empty() {
        return Ok(Vec::new());
    }
    sqlx::query_as::<_, BriefingRow>(
        r"
        SELECT id::bigint AS id, markdown_raw, narration_text, created_at
        FROM briefing_segments
        WHERE user_id::bigint = $1
          AND status IN ('active', 'degraded')
          AND to_char(created_at, 'YYYY-MM-DD') = ANY($2::text[])
        ORDER BY created_at, id
        ",
    )
    .bind(user_id)
    .bind(dates)
    .fetch_all(&mut **transaction)
    .await
}

async fn render_content(
    store: &AgentDataMirrorStore,
    row: ContentRow,
    max_bytes: usize,
) -> Result<AgentDataDocument, AgentDataDocumentError> {
    let title = content_title(&row);
    let rendered = read_optional_body(store, row.rendered_provider, row.rendered_key)
        .await?
        .or_else(|| extract_rendered_body_text(&row.content_metadata));
    let source = read_optional_body(store, row.source_provider, row.source_key)
        .await?
        .or_else(|| extract_source_body_text(&row.content_type, &row.content_metadata));
    let summary = rendered
        .or_else(|| extract_summary_text(row.content_metadata.get("summary")))
        .unwrap_or_default();
    let source = source.unwrap_or_default();
    let mut body = Vec::new();
    if !summary.trim().is_empty() {
        body.push(format!("## Summary\n\n{summary}"));
    }
    if !source.trim().is_empty() && source.trim() != summary.trim() {
        body.push(format!("## Content\n\n{source}"));
    }
    let timestamp = row
        .publication_date
        .or(row.processed_at)
        .unwrap_or(row.created_at);
    let path = if row.saved {
        format!("knowledge/{}--content-{}.md", slug(&title), row.id)
    } else {
        format!(
            "content/{:04}/{:02}/{}--content-{}.md",
            timestamp.year(),
            timestamp.month(),
            slug(&title),
            row.id
        )
    };
    let metadata = json!({
        "id": row.id,
        "kind": row.content_type,
        "title": title,
        "url": row.url,
        "published_at": timestamp.and_utc().to_rfc3339(),
        "source": row.source,
        "tags": if row.saved { vec![row.content_type.clone(), "saved".to_owned()] } else { vec![row.content_type.clone()] },
        "saved": row.saved,
    });
    Ok(document(
        "content",
        row.id.to_string(),
        path,
        metadata,
        &body.join("\n\n"),
        max_bytes,
    )?)
}

async fn render_news(
    store: &AgentDataMirrorStore,
    row: NewsRow,
    max_bytes: usize,
) -> Result<AgentDataDocument, AgentDataDocumentError> {
    let title = news_title(&row);
    let rendered =
        read_optional_body(store, row.legacy_rendered_provider, row.legacy_rendered_key).await?;
    let source =
        read_optional_body(store, row.legacy_source_provider, row.legacy_source_key).await?;
    let rendered = rendered.or_else(|| extract_rendered_body_text(&row.legacy_content_metadata));
    let source = source.or_else(|| {
        row.legacy_content_type
            .as_deref()
            .and_then(|kind| extract_source_body_text(kind, &row.legacy_content_metadata))
    });
    let timestamp = row
        .published_at
        .or(row.processed_at)
        .unwrap_or(row.ingested_at);
    let points = row
        .summary_key_points
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(value_clean_string)
        .map(|value| format!("- {value}"))
        .collect::<Vec<_>>()
        .join("\n");
    let mut parts = [row.summary_text.clone().unwrap_or_default(), points]
        .into_iter()
        .filter(|value| !value.trim().is_empty())
        .collect::<Vec<_>>();
    if let Some(body) = rendered.or(source).filter(|value| !value.trim().is_empty()) {
        parts.push(format!("## Enriched article body\n\n{body}"));
    }
    let url = row
        .article_url
        .or(row.canonical_story_url)
        .or(row.discussion_url)
        .or(row.canonical_item_url);
    let source_label = row.source_label.or_else(|| row.platform.clone());
    let metadata = json!({
        "id": row.id,
        "kind": "news",
        "title": title,
        "url": url,
        "published_at": timestamp.and_utc().to_rfc3339(),
        "source": source_label,
        "tags": ["news", row.platform.as_deref().unwrap_or("unknown")],
        "saved": false,
    });
    let path = format!(
        "news/{:04}-{:02}-{:02}/{}--news-{}.md",
        timestamp.year(),
        timestamp.month(),
        timestamp.day(),
        slug(&title),
        row.id
    );
    Ok(document(
        "news",
        row.id.to_string(),
        path,
        metadata,
        &parts.join("\n\n"),
        max_bytes,
    )?)
}

fn render_chats(
    rows: Vec<ChatRow>,
    max_bytes: usize,
) -> Result<Vec<AgentDataDocument>, AgentDataDocumentError> {
    let mut documents = Vec::new();
    for row in rows {
        let transcript = row
            .message_lists
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .filter_map(|value| serde_json::from_str::<Value>(value).ok())
            .map(|value| render_message_list(&value))
            .filter(|value| !value.trim().is_empty())
            .collect::<Vec<_>>()
            .join("\n\n");
        if transcript.is_empty() {
            continue;
        }
        let title = row
            .title
            .or(row.topic)
            .unwrap_or_else(|| format!("Chat {}", row.id));
        let metadata = json!({
            "id": row.id,
            "kind": "chat",
            "title": title,
            "url": Value::Null,
            "published_at": row.created_at.and_utc().to_rfc3339(),
            "source": "Newsly chat",
            "tags": ["chat", row.session_type.as_deref().unwrap_or("general")],
            "saved": false,
        });
        documents.push(document(
            "chat",
            row.id.to_string(),
            format!("chats/{}-{}.md", row.id, slug(&title)),
            metadata,
            &transcript,
            max_bytes,
        )?);
    }
    Ok(documents)
}

fn render_briefings(
    rows: Vec<BriefingRow>,
    max_bytes: usize,
) -> Result<Vec<AgentDataDocument>, AgentDataDocumentError> {
    let mut grouped = BTreeMap::<String, Vec<BriefingRow>>::new();
    for row in rows {
        grouped
            .entry(row.created_at.format("%Y-%m-%d").to_string())
            .or_default()
            .push(row);
    }
    grouped
        .into_iter()
        .filter_map(|(date, rows)| {
            let body = rows
                .iter()
                .map(|row| {
                    if row.markdown_raw.trim().is_empty() {
                        row.narration_text.trim()
                    } else {
                        row.markdown_raw.trim()
                    }
                })
                .filter(|value| !value.is_empty())
                .collect::<Vec<_>>()
                .join("\n\n---\n\n");
            (!body.is_empty()).then(|| {
                let metadata = json!({
                    "id": date,
                    "kind": "briefing",
                    "title": format!("Newsly Briefing — {date}"),
                    "url": Value::Null,
                    "published_at": date,
                    "source": "Newsly Briefing",
                    "tags": ["briefing"],
                    "saved": false,
                    "segment_ids": rows.iter().map(|row| row.id).collect::<Vec<_>>(),
                });
                document(
                    "briefing",
                    date.clone(),
                    format!("briefings/{date}.md"),
                    metadata,
                    &body,
                    max_bytes,
                )
                .map_err(AgentDataDocumentError::from)
            })
        })
        .collect()
}

async fn read_optional_body(
    store: &AgentDataMirrorStore,
    provider: Option<String>,
    key: Option<String>,
) -> Result<Option<String>, AgentDataMirrorStoreError> {
    match (provider, key) {
        (Some(provider), Some(key)) => store.read_content_body(&provider, &key).await,
        _ => Ok(None),
    }
}

fn extract_rendered_body_text(metadata: &Value) -> Option<String> {
    metadata
        .pointer("/summary/full_markdown")
        .and_then(value_clean_string_preserving_lines)
}

fn extract_source_body_text(content_type: &str, metadata: &Value) -> Option<String> {
    let keys: &[&str] = match content_type {
        "podcast" => &["transcript", "content_to_summarize"],
        "article" | "news" => &["content_to_summarize", "content"],
        _ => &[],
    };
    keys.iter().find_map(|key| {
        metadata
            .get(*key)
            .and_then(value_clean_string_preserving_lines)
    })
}

fn value_clean_string_preserving_lines(value: &Value) -> Option<String> {
    value
        .as_str()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(ToOwned::to_owned)
}

fn document(
    kind: &str,
    key: String,
    path: String,
    metadata: Value,
    body: &str,
    max_bytes: usize,
) -> Result<AgentDataDocument, serde_json::Error> {
    let metadata_json = serde_json::to_string_pretty(&metadata)?;
    let mut bytes = format!("---\n{metadata_json}\n---\n\n{}\n", body.trim()).into_bytes();
    if bytes.len() > max_bytes {
        let marker = TRUNCATION_MARKER.as_bytes();
        let keep = max_bytes.saturating_sub(marker.len());
        bytes.truncate(keep);
        while std::str::from_utf8(&bytes).is_err() {
            bytes.pop();
        }
        bytes.extend_from_slice(marker);
    }
    let checksum_sha256 = hex_sha256(&bytes);
    let byte_size = bytes.len();
    let index_record = match metadata {
        Value::Object(mut value) => {
            value.insert("path".to_owned(), Value::String(path.clone()));
            value.insert(
                "checksum_sha256".to_owned(),
                Value::String(checksum_sha256.clone()),
            );
            value.insert("byte_size".to_owned(), Value::from(byte_size));
            Value::Object(value)
        }
        _ => unreachable!("agent-data metadata is always an object"),
    };
    Ok(AgentDataDocument {
        document_kind: kind.to_owned(),
        document_key: key,
        path,
        content_bytes: bytes,
        checksum_sha256,
        index_record,
    })
}

fn content_title(row: &ContentRow) -> String {
    row.title
        .clone()
        .or_else(|| value_clean_string(row.content_metadata.get("title")?))
        .or_else(|| {
            row.content_metadata
                .pointer("/summary/title")
                .and_then(value_clean_string)
        })
        .unwrap_or_else(|| format!("Content {}", row.id))
}

fn news_title(row: &NewsRow) -> String {
    [
        "/summary_title",
        "/article_title",
        "/title",
        "/aggregator/title",
        "/aggregator/metadata/title",
    ]
    .into_iter()
    .find_map(|pointer| {
        row.raw_metadata
            .pointer(pointer)
            .and_then(value_clean_string)
    })
    .or_else(|| {
        row.summary_text
            .as_ref()
            .and_then(|value| first_sentence(value))
    })
    .unwrap_or_else(|| format!("News item {}", row.id))
}

fn extract_summary_text(value: Option<&Value>) -> Option<String> {
    let value = value?;
    if let Some(value) = value_clean_string(value) {
        return Some(value);
    }
    [
        "/artifact/payload/overview",
        "/overview",
        "/editorial_narrative",
        "/summary",
        "/hook",
        "/one_line",
    ]
    .into_iter()
    .find_map(|pointer| value.pointer(pointer).and_then(value_clean_string))
}

fn render_message_list(value: &Value) -> String {
    value
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_object)
        .filter_map(|message| {
            let role = if message.get("kind").and_then(Value::as_str) == Some("response") {
                "Assistant"
            } else {
                "User"
            };
            let contents = message
                .get("parts")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .filter_map(Value::as_object)
                .filter(|part| {
                    matches!(
                        part.get("part_kind").and_then(Value::as_str),
                        Some("text" | "user-prompt")
                    )
                })
                .filter_map(|part| part.get("content").and_then(value_clean_string))
                .collect::<Vec<_>>();
            (!contents.is_empty()).then(|| format!("## {role}\n\n{}", contents.join("\n\n")))
        })
        .collect::<Vec<_>>()
        .join("\n\n")
}

fn value_clean_string(value: &Value) -> Option<String> {
    value
        .as_str()
        .map(|value| value.split_whitespace().collect::<Vec<_>>().join(" "))
        .filter(|value| !value.is_empty())
}

fn first_sentence(value: &str) -> Option<String> {
    value
        .split(['.', '\n'])
        .map(str::trim)
        .find(|value| !value.is_empty())
        .map(|value| value.chars().take(120).collect())
}

fn slug(value: &str) -> String {
    let mut output = String::new();
    let mut separator = false;
    for character in value.to_ascii_lowercase().chars() {
        if character.is_ascii_alphanumeric() {
            if separator && !output.is_empty() {
                output.push('-');
            }
            output.push(character);
            separator = false;
        } else {
            separator = true;
        }
        if output.len() >= 80 {
            break;
        }
    }
    let value = output
        .trim_matches('-')
        .to_owned()
        .chars()
        .take(80)
        .collect::<String>();
    if value.is_empty() {
        "untitled".to_owned()
    } else {
        value
    }
}

fn hex_sha256(value: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let digest = Sha256::digest(value);
    let mut encoded = String::with_capacity(digest.len() * 2);
    for byte in digest {
        encoded.push(char::from(HEX[usize::from(byte >> 4)]));
        encoded.push(char::from(HEX[usize::from(byte & 0x0f)]));
    }
    encoded
}

#[derive(Debug, thiserror::Error)]
pub(super) enum AgentDataDocumentError {
    #[error("agent-data relational snapshot failed")]
    Sqlx(#[from] sqlx::Error),
    #[error("agent-data body read failed")]
    Storage(#[from] AgentDataMirrorStoreError),
    #[error("agent-data document serialization failed")]
    Json(#[from] serde_json::Error),
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn slug_is_bounded_and_safe() {
        assert_eq!(slug("Hello, Rust World!"), "hello-rust-world");
        assert_eq!(slug("---"), "untitled");
        assert!(slug(&"a".repeat(200)).len() <= 80);
    }

    #[test]
    fn document_truncation_preserves_utf8() {
        let document = document(
            "content",
            "1".to_owned(),
            "content/one.md".to_owned(),
            json!({"id": 1}),
            &"é".repeat(500),
            200,
        )
        .unwrap();
        assert!(std::str::from_utf8(&document.content_bytes).is_ok());
        assert!(document.content_bytes.len() <= 200);
    }
}
