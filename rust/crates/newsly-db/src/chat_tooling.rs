//! Bounded host-side data access for native chat tools.
//!
//! Each function owns one short pool checkout or caller transaction. Tool executors never retain
//! an ORM-shaped row or SQLx connection while Rig performs another model request.
use chrono::NaiveDateTime;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sqlx::{AssertSqlSafe, FromRow, PgPool};
use thiserror::Error;

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct ChatContentHit {
    pub reference: ChatKnowledgeReference,
    pub content_id: i64,
    pub content_type: String,
    pub title: String,
    pub source: Option<String>,
    pub url: String,
    pub snippet: Option<String>,
    pub published_at: Option<NaiveDateTime>,
    pub is_read: bool,
    pub is_saved_to_knowledge: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ChatKnowledgeReference {
    pub kind: &'static str,
    pub id: i64,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct ChatNewsHit {
    pub news_item_id: i64,
    pub title: String,
    pub source: Option<String>,
    pub article_url: Option<String>,
    pub story_url: Option<String>,
    pub discussion_url: Option<String>,
    pub summary: Option<String>,
    pub key_points: Value,
    pub published_at: Option<NaiveDateTime>,
    pub is_read: bool,
}

#[derive(Debug, Deserialize, FromRow)]
struct ContentHitRow {
    content_id: i64,
    content_type: String,
    title: String,
    source: Option<String>,
    url: String,
    snippet: Option<String>,
    published_at: Option<NaiveDateTime>,
    is_read: bool,
    is_saved_to_knowledge: bool,
}

impl From<ContentHitRow> for ChatContentHit {
    fn from(row: ContentHitRow) -> Self {
        Self {
            reference: ChatKnowledgeReference {
                kind: "content",
                id: row.content_id,
            },
            content_id: row.content_id,
            content_type: row.content_type,
            title: row.title,
            source: row.source,
            url: row.url,
            snippet: row.snippet,
            published_at: row.published_at,
            is_read: row.is_read,
            is_saved_to_knowledge: row.is_saved_to_knowledge,
        }
    }
}

#[derive(Debug, FromRow)]
struct NewsHitRow {
    news_item_id: i64,
    title: String,
    source: Option<String>,
    article_url: Option<String>,
    story_url: Option<String>,
    discussion_url: Option<String>,
    summary: Option<String>,
    key_points: Value,
    published_at: Option<NaiveDateTime>,
    is_read: bool,
}

impl From<NewsHitRow> for ChatNewsHit {
    fn from(row: NewsHitRow) -> Self {
        Self {
            news_item_id: row.news_item_id,
            title: row.title,
            source: row.source,
            article_url: row.article_url,
            story_url: row.story_url,
            discussion_url: row.discussion_url,
            summary: row.summary,
            key_points: row.key_points,
            published_at: row.published_at,
            is_read: row.is_read,
        }
    }
}

pub async fn search_agent_knowledge(
    pool: &PgPool,
    user_id: i64,
    query: &str,
    limit: i64,
) -> Result<Vec<ChatContentHit>, ChatToolRepositoryError> {
    validate_search(user_id, query, limit)?;
    let rows = sqlx::query_as::<_, ContentHitRow>(AssertSqlSafe(format!(
        "{}\n{}",
        content_select(),
        content_search_tail("saved.saved_at", "AND saved.id IS NOT NULL", false)
    )))
    .bind(user_id)
    .bind(query.trim())
    .bind(limit)
    .fetch_all(pool)
    .await?;
    Ok(rows.into_iter().map(Into::into).collect())
}

fn content_select() -> &'static str {
    r#"
        SELECT
            content.id::bigint AS content_id,
            COALESCE(content.publication_date, content.created_at) AS published_at,
            content.content_type,
            COALESCE(
                NULLIF(BTRIM(content.content_metadata->'summary'->>'title'), ''),
                NULLIF(BTRIM(content.title), ''),
                'Untitled'
            ) AS title,
            NULLIF(BTRIM(content.source), '') AS source,
            content.url,
            COALESCE(
                NULLIF(BTRIM(content.content_metadata->'summary'->>'one_line'), ''),
                NULLIF(BTRIM(content.content_metadata->'summary'->>'overview'), ''),
                NULLIF(BTRIM(content.content_metadata->'summary'->>'summary'), ''),
                NULLIF(BTRIM(content.search_text), '')
            ) AS snippet,
            read.id IS NOT NULL AS is_read,
            saved.id IS NOT NULL AS is_saved_to_knowledge
        FROM contents AS content
        JOIN users AS account ON account.id::bigint = $1 AND account.is_active = TRUE
        LEFT JOIN content_read_status AS read
          ON read.content_id = content.id AND read.user_id::bigint = $1
        LEFT JOIN content_knowledge_saves AS saved
          ON saved.content_id = content.id AND saved.user_id::bigint = $1
    "#
}

fn content_search_tail(
    sort_expression: &str,
    additional_predicate: &str,
    require_completed: bool,
) -> String {
    let lifecycle_predicate = if require_completed {
        "AND content.status = 'completed'"
    } else {
        ""
    };
    format!(
        r#"
        WHERE TRUE
          {lifecycle_predicate}
          {additional_predicate}
          AND (
              BTRIM($2) = ''
              OR (
                  setweight(to_tsvector('english', COALESCE(content.content_metadata->'summary'->>'title', '')), 'A')
                  || setweight(to_tsvector('english', COALESCE(content.title, '')), 'B')
                  || setweight(to_tsvector('english', COALESCE(content.source, '')), 'C')
                  || setweight(to_tsvector('english', COALESCE(content.search_text, '')), 'D')
              ) @@ websearch_to_tsquery('english', $2)
              OR COALESCE(content.content_metadata->'summary'->>'title', '') OPERATOR(public.%>>) $2
              OR COALESCE(content.title, '') OPERATOR(public.%>>) $2
              OR COALESCE(content.source, '') OPERATOR(public.%>>) $2
          )
        ORDER BY CASE WHEN BTRIM($2) = '' THEN 0 ELSE GREATEST(
              ts_rank_cd(
                  setweight(to_tsvector('english', COALESCE(content.content_metadata->'summary'->>'title', '')), 'A')
                  || setweight(to_tsvector('english', COALESCE(content.title, '')), 'B')
                  || setweight(to_tsvector('english', COALESCE(content.source, '')), 'C')
                  || setweight(to_tsvector('english', COALESCE(content.search_text, '')), 'D'),
                  websearch_to_tsquery('english', $2)
              ),
              GREATEST(
                  public.word_similarity($2, COALESCE(content.content_metadata->'summary'->>'title', '')),
                  public.word_similarity($2, COALESCE(content.title, '')),
                  public.word_similarity($2, COALESCE(content.source, ''))
              ) * 0.25
          ) END DESC,
          {sort_expression} DESC,
          content.id DESC
        LIMIT $3
        "#
    )
}

fn validate_search(user_id: i64, query: &str, limit: i64) -> Result<(), ChatToolRepositoryError> {
    if user_id <= 0 || query.chars().count() > 2_000 || !(1..=100).contains(&limit) {
        Err(ChatToolRepositoryError::InvalidInput)
    } else {
        Ok(())
    }
}

#[derive(Debug, Error)]
pub enum ChatToolRepositoryError {
    #[error("chat tool input is invalid")]
    InvalidInput,
    #[error("chat tool database operation failed")]
    Sqlx(#[from] sqlx::Error),
    #[error("chat tool result serialization failed")]
    Serialization(#[from] serde_json::Error),
}

#[path = "chat_tooling/search.rs"]
mod search;
pub use search::{ChatContentFilters, ChatContentPage, search_accessible_content};

#[path = "chat_tooling/assistant.rs"]
mod assistant;
pub use assistant::{
    ChatSource, find_chat_source, list_chat_sources, read_chat_news, search_chat_news_page,
    set_chat_news_read,
};

#[path = "chat_tooling/news_queries.rs"]
mod news_queries;
