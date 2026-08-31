//! Bounded host-side data access for native chat tools.
//!
//! Each function owns one short pool checkout or caller transaction. Tool executors never retain
//! an ORM-shaped row or SQLx connection while Rig performs another model request.

use std::collections::BTreeSet;

use chrono::NaiveDateTime;
use serde::Serialize;
use serde_json::Value;
use sqlx::{AssertSqlSafe, FromRow, PgPool, Postgres, Transaction};
use thiserror::Error;

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct ChatContentHit {
    pub content_id: i64,
    pub content_type: String,
    pub title: String,
    pub source: Option<String>,
    pub url: String,
    pub snippet: Option<String>,
    pub is_read: bool,
    pub is_saved_to_knowledge: bool,
    pub corpus_path: Option<String>,
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

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ChatArticleConversionSource {
    pub url: String,
    pub title: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct ChatUnreadNewsPage {
    pub items: Vec<ChatNewsHit>,
    pub total_count: i64,
}

#[derive(Debug, FromRow)]
struct ContentHitRow {
    content_id: i64,
    content_type: String,
    title: String,
    source: Option<String>,
    url: String,
    snippet: Option<String>,
    is_read: bool,
    is_saved_to_knowledge: bool,
    corpus_path: Option<String>,
}

impl From<ContentHitRow> for ChatContentHit {
    fn from(row: ContentHitRow) -> Self {
        Self {
            content_id: row.content_id,
            content_type: row.content_type,
            title: row.title,
            source: row.source,
            url: row.url,
            snippet: row.snippet,
            is_read: row.is_read,
            is_saved_to_knowledge: row.is_saved_to_knowledge,
            corpus_path: row
                .corpus_path
                .map(|path| format!("/data/{}", path.trim_start_matches('/'))),
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

#[derive(Debug, FromRow)]
struct SubscriptionConfigRow {
    display_name: Option<String>,
    configured_name: Option<String>,
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

pub async fn search_chat_knowledge(
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

pub async fn search_chat_content(
    pool: &PgPool,
    user_id: i64,
    query: &str,
    limit: i64,
) -> Result<Vec<ChatContentHit>, ChatToolRepositoryError> {
    validate_search(user_id, query, limit)?;
    let statement = format!(
        "{}\nJOIN content_status AS inbox ON inbox.content_id = content.id AND inbox.user_id::bigint = $1 AND inbox.status = 'inbox'\n{}",
        content_select(),
        content_search_tail("content.created_at", "", true)
    );
    let normalized = query.trim();
    let mut rows = sqlx::query_as::<_, ContentHitRow>(AssertSqlSafe(statement.clone()))
        .bind(user_id)
        .bind(normalized)
        .bind(limit)
        .fetch_all(pool)
        .await?;
    if rows.is_empty() && !normalized.is_empty() {
        rows = sqlx::query_as::<_, ContentHitRow>(AssertSqlSafe(statement))
            .bind(user_id)
            .bind("")
            .bind(limit)
            .fetch_all(pool)
            .await?;
    }
    Ok(rows.into_iter().map(Into::into).collect())
}

pub async fn search_chat_subscription_content(
    pool: &PgPool,
    user_id: i64,
    query: &str,
    limit: i64,
) -> Result<Vec<ChatContentHit>, ChatToolRepositoryError> {
    validate_search(user_id, query, limit)?;
    let raw_tokens = subscription_tokens(query);
    let significant_query_tokens = significant_subscription_tokens(query);
    if significant_query_tokens.is_empty() {
        return Ok(Vec::new());
    }
    let query_has_subscription_hint = raw_tokens
        .iter()
        .any(|token| SUBSCRIPTION_QUERY_HINTS.contains(&token.as_str()));
    let normalized_query = query.trim().to_ascii_lowercase();
    let configs = sqlx::query_as::<_, SubscriptionConfigRow>(
        r#"
        SELECT
            NULLIF(BTRIM(subscription.display_name), '') AS display_name,
            NULLIF(BTRIM(subscription.config->>'name'), '') AS configured_name
        FROM user_scraper_configs AS subscription
        JOIN users AS account ON account.id = subscription.user_id AND account.is_active = TRUE
        WHERE subscription.user_id::bigint = $1 AND subscription.is_active = TRUE
        "#,
    )
    .bind(user_id)
    .fetch_all(pool)
    .await?;
    let mut matchers = Vec::new();
    for config in configs {
        let names = [config.display_name, config.configured_name]
            .into_iter()
            .flatten()
            .map(|name| name.trim().to_owned())
            .filter(|name| !name.is_empty())
            .collect::<BTreeSet<_>>();
        if names.is_empty() {
            continue;
        }
        let candidate_tokens = names
            .iter()
            .flat_map(|name| significant_subscription_tokens(name))
            .collect::<BTreeSet<_>>();
        if candidate_tokens.is_empty() {
            continue;
        }
        let name_overlap = significant_query_tokens
            .iter()
            .any(|token| candidate_tokens.contains(token));
        let substring_match = names.iter().any(|name| {
            let name = name.to_ascii_lowercase();
            !normalized_query.is_empty()
                && (normalized_query.contains(&name) || name.contains(&normalized_query))
        });
        if !name_overlap && !substring_match {
            continue;
        }
        if !query_has_subscription_hint && !name_overlap {
            continue;
        }
        matchers.push(serde_json::json!({
            "names": names,
            "tokens": candidate_tokens,
        }));
    }
    if matchers.is_empty() {
        return Ok(Vec::new());
    }
    let rows = sqlx::query_as::<_, ContentHitRow>(AssertSqlSafe(format!(
        "{}\nJOIN content_status AS inbox ON inbox.content_id = content.id AND inbox.user_id::bigint = $1 AND inbox.status = 'inbox'\n{}",
        content_select(),
        subscription_search_tail()
    )))
    .bind(user_id)
    .bind(Value::Array(matchers))
    .bind(limit)
    .fetch_all(pool)
    .await?;
    Ok(rows.into_iter().map(Into::into).collect())
}

pub async fn search_chat_news(
    pool: &PgPool,
    user_id: i64,
    query: &str,
    limit: i64,
) -> Result<Vec<ChatNewsHit>, ChatToolRepositoryError> {
    validate_search(user_id, query, limit)?;
    let normalized = query.trim();
    let statement = news_search_statement();
    let mut rows = sqlx::query_as::<_, NewsHitRow>(statement)
        .bind(user_id)
        .bind(normalized)
        .bind(limit)
        .bind(false)
        .fetch_all(pool)
        .await?;
    if rows.is_empty() && !normalized.is_empty() {
        rows = sqlx::query_as::<_, NewsHitRow>(statement)
            .bind(user_id)
            .bind("")
            .bind(limit)
            .bind(false)
            .fetch_all(pool)
            .await?;
    }
    Ok(rows.into_iter().map(Into::into).collect())
}

pub async fn list_unread_chat_news(
    pool: &PgPool,
    user_id: i64,
    limit: i64,
) -> Result<ChatUnreadNewsPage, ChatToolRepositoryError> {
    if user_id <= 0 || !(1..=200).contains(&limit) {
        return Err(ChatToolRepositoryError::InvalidInput);
    }
    let mut transaction = pool.begin().await?;
    let total_count = sqlx::query_scalar::<_, i64>(
        r#"
        SELECT COUNT(item.id)::bigint
        FROM news_items AS item
        JOIN users AS account ON account.id::bigint = $1 AND account.is_active = TRUE
        LEFT JOIN news_item_read_status AS read
          ON read.news_item_id = item.id AND read.user_id::bigint = $1
        WHERE item.status = 'ready'
          AND item.representative_news_item_id IS NULL
          AND read.id IS NULL
          AND (
              item.visibility_scope = 'global'
              OR (item.visibility_scope = 'user' AND item.owner_user_id::bigint = $1)
          )
        "#,
    )
    .bind(user_id)
    .fetch_one(&mut *transaction)
    .await?;
    let rows = sqlx::query_as::<_, NewsHitRow>(news_search_statement())
        .bind(user_id)
        .bind("")
        .bind(limit)
        .bind(true)
        .fetch_all(&mut *transaction)
        .await?;
    transaction.commit().await?;
    Ok(ChatUnreadNewsPage {
        items: rows.into_iter().map(Into::into).collect(),
        total_count,
    })
}

fn news_search_statement() -> &'static str {
    r#"
        SELECT
            item.id::bigint AS news_item_id,
            COALESCE(
                NULLIF(BTRIM(item.raw_metadata->'summary'->>'title'), ''),
                NULLIF(BTRIM(item.raw_metadata->'article'->>'title'), ''),
                NULLIF(BTRIM(item.summary_text), ''),
                'Untitled'
            ) AS title,
            NULLIF(BTRIM(item.source_label), '') AS source,
            item.article_url,
            item.canonical_story_url AS story_url,
            item.discussion_url,
            NULLIF(BTRIM(item.summary_text), '') AS summary,
            item.summary_key_points::jsonb AS key_points,
            COALESCE(item.published_at, item.processed_at, item.ingested_at, item.created_at) AS published_at,
            read.id IS NOT NULL AS is_read
        FROM news_items AS item
        JOIN users AS account ON account.id::bigint = $1 AND account.is_active = TRUE
        LEFT JOIN news_item_read_status AS read
          ON read.news_item_id = item.id AND read.user_id::bigint = $1
        WHERE item.status = 'ready'
          AND item.representative_news_item_id IS NULL
          AND (
              item.visibility_scope = 'global'
              OR (item.visibility_scope = 'user' AND item.owner_user_id::bigint = $1)
          )
          AND ($4::boolean IS FALSE OR read.id IS NULL)
          AND (
              BTRIM($2) = ''
              OR (
                  setweight(to_tsvector('english', COALESCE(item.raw_metadata->'summary'->>'title', '')), 'A')
                  || setweight(to_tsvector('english', COALESCE(item.raw_metadata->'article'->>'title', '')), 'B')
                  || setweight(to_tsvector('english', COALESCE(item.summary_text, '')), 'C')
                  || setweight(to_tsvector('english', COALESCE(item.source_label, '') || ' ' || COALESCE(item.article_domain, '') || ' ' || COALESCE(item.raw_metadata->'cluster'->>'related_titles', '')), 'D')
              ) @@ websearch_to_tsquery('english', $2)
              OR COALESCE(item.raw_metadata->'summary'->>'title', '') OPERATOR(public.%) $2
              OR COALESCE(item.raw_metadata->'article'->>'title', '') OPERATOR(public.%) $2
              OR GREATEST(
                  public.word_similarity($2, COALESCE(item.raw_metadata->'summary'->>'title', '')),
                  public.word_similarity($2, COALESCE(item.raw_metadata->'article'->>'title', '')),
                  public.word_similarity($2, COALESCE(item.source_label, '')),
                  public.word_similarity($2, COALESCE(item.article_domain, '')),
                  public.word_similarity($2, COALESCE(item.raw_metadata->'cluster'->>'related_titles', ''))
              ) >= 0.5
          )
        ORDER BY
          CASE WHEN BTRIM($2) = '' THEN 0 ELSE GREATEST(
              ts_rank_cd(
                  setweight(to_tsvector('english', COALESCE(item.raw_metadata->'summary'->>'title', '')), 'A')
                  || setweight(to_tsvector('english', COALESCE(item.raw_metadata->'article'->>'title', '')), 'B')
                  || setweight(to_tsvector('english', COALESCE(item.summary_text, '')), 'C')
                  || setweight(to_tsvector('english', COALESCE(item.source_label, '') || ' ' || COALESCE(item.article_domain, '') || ' ' || COALESCE(item.raw_metadata->'cluster'->>'related_titles', '')), 'D'),
                  websearch_to_tsquery('english', $2)
              ),
              GREATEST(
                  public.word_similarity($2, COALESCE(item.raw_metadata->'summary'->>'title', '')),
                  public.word_similarity($2, COALESCE(item.raw_metadata->'article'->>'title', '')),
                  public.word_similarity($2, COALESCE(item.source_label, '')),
                  public.word_similarity($2, COALESCE(item.article_domain, '')),
                  public.word_similarity($2, COALESCE(item.raw_metadata->'cluster'->>'related_titles', ''))
              ) * 0.25
          ) END DESC,
          COALESCE(item.published_at, item.processed_at, item.ingested_at, item.created_at) DESC,
          item.id DESC
        LIMIT $3
        "#
}

pub async fn prepare_chat_article_conversion(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    content_id: i64,
) -> Result<Option<ChatArticleConversionSource>, ChatToolRepositoryError> {
    if user_id <= 0 || content_id <= 0 {
        return Err(ChatToolRepositoryError::InvalidInput);
    }
    let row = sqlx::query_as::<_, (String, Option<String>)>(
        r#"
        SELECT
            COALESCE(NULLIF(BTRIM(content.content_metadata->'article'->>'url'), ''), content.url),
            NULLIF(BTRIM(content.content_metadata->'article'->>'title'), '')
        FROM contents AS content
        JOIN users AS account ON account.id::bigint = $1 AND account.is_active = TRUE
        WHERE content.id::bigint = $2 AND content.content_type = 'news'
        FOR SHARE OF content
        "#,
    )
    .bind(user_id)
    .bind(content_id)
    .fetch_optional(&mut **transaction)
    .await?;
    Ok(row.map(|(url, title)| ChatArticleConversionSource { url, title }))
}

pub async fn create_deep_research_handoff(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    content_id: Option<i64>,
    question: &str,
    model: &str,
) -> Result<Option<i64>, ChatToolRepositoryError> {
    if user_id <= 0
        || content_id.is_some_and(|value| value <= 0)
        || question.trim().is_empty()
        || question.chars().count() > 10_000
        || model.trim().is_empty()
    {
        return Err(ChatToolRepositoryError::InvalidInput);
    }
    sqlx::query_scalar::<_, i64>(
        r#"
        INSERT INTO chat_sessions (
            user_id, content_id, title, session_type, topic, llm_provider, llm_model,
            created_at, updated_at, council_mode, is_hidden_from_history, is_archived
        )
        SELECT
            account.id, $2::bigint::integer, 'Deep Research', 'deep_research', $3,
            'deep_research', $4, timezone('UTC', clock_timestamp()),
            timezone('UTC', clock_timestamp()), FALSE, FALSE, FALSE
        FROM users AS account
        WHERE account.id::bigint = $1 AND account.is_active = TRUE
        RETURNING id::bigint
        "#,
    )
    .bind(user_id)
    .bind(content_id)
    .bind(question.trim().chars().take(500).collect::<String>())
    .bind(model)
    .fetch_optional(&mut **transaction)
    .await
    .map_err(Into::into)
}

fn content_select() -> &'static str {
    r#"
        SELECT
            content.id::bigint AS content_id,
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
            saved.id IS NOT NULL AS is_saved_to_knowledge,
            agent_file.path AS corpus_path
        FROM contents AS content
        JOIN users AS account ON account.id::bigint = $1 AND account.is_active = TRUE
        LEFT JOIN content_read_status AS read
          ON read.content_id = content.id AND read.user_id::bigint = $1
        LEFT JOIN content_knowledge_saves AS saved
          ON saved.content_id = content.id AND saved.user_id::bigint = $1
        LEFT JOIN agent_data_files AS agent_file
          ON agent_file.user_id::bigint = $1
         AND agent_file.document_kind = 'content'
         AND agent_file.document_key = content.id::text
         AND agent_file.deleted_at IS NULL
    "#
}

fn content_search_tail(
    sort_expression: &str,
    additional_predicate: &str,
    require_completed: bool,
) -> String {
    let lifecycle_predicate = if require_completed {
        "AND content.status = 'completed'\n          AND (content.classification IS NULL OR content.classification <> 'skip')"
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

fn subscription_search_tail() -> &'static str {
    r#"
        WHERE content.status = 'completed'
          AND (content.classification IS NULL OR content.classification <> 'skip')
          AND EXISTS (
              SELECT 1
              FROM jsonb_array_elements($2::jsonb) AS matcher
              WHERE (
                  EXISTS (
                      SELECT 1
                      FROM jsonb_array_elements_text(matcher->'names') AS candidate(name)
                      WHERE LOWER(COALESCE(content.source, '')) LIKE '%' || LOWER(candidate.name) || '%'
                         OR LOWER(COALESCE(content.title, '')) LIKE '%' || LOWER(candidate.name) || '%'
                         OR LOWER(COALESCE(content.search_text, '')) LIKE '%' || LOWER(candidate.name) || '%'
                  )
                  OR NOT EXISTS (
                      SELECT 1
                      FROM jsonb_array_elements_text(matcher->'tokens') AS candidate(token)
                      WHERE NOT (
                          LOWER(COALESCE(content.title, '')) LIKE '%' || LOWER(candidate.token) || '%'
                          OR LOWER(COALESCE(content.search_text, '')) LIKE '%' || LOWER(candidate.token) || '%'
                          OR LOWER(COALESCE(content.source, '')) LIKE '%' || LOWER(candidate.token) || '%'
                      )
                  )
                )
          )
        ORDER BY content.created_at DESC, content.id DESC
        LIMIT $3
    "#
}

const SUBSCRIPTION_QUERY_HINTS: [&str; 10] = [
    "episode", "episodes", "feed", "feeds", "pod", "pods", "podcast", "podcasts", "series", "show",
];

fn subscription_tokens(value: &str) -> Vec<String> {
    value
        .to_ascii_lowercase()
        .split(|character: char| !character.is_ascii_alphanumeric())
        .filter(|token| !token.is_empty())
        .map(|token| {
            if token.len() > 4 && token.ends_with("ies") {
                format!("{}y", &token[..token.len() - 3])
            } else if token.len() > 3 && token.ends_with('s') {
                token[..token.len() - 1].to_owned()
            } else {
                token.to_owned()
            }
        })
        .collect()
}

fn significant_subscription_tokens(value: &str) -> BTreeSet<String> {
    subscription_tokens(value)
        .into_iter()
        .filter(|token| !is_subscription_stopword(token))
        .collect()
}

fn is_subscription_stopword(value: &str) -> bool {
    matches!(
        value,
        "a" | "an"
            | "and"
            | "article"
            | "articles"
            | "episode"
            | "episodes"
            | "feed"
            | "feeds"
            | "have"
            | "in"
            | "inbox"
            | "my"
            | "newsletter"
            | "newsletters"
            | "of"
            | "pod"
            | "pods"
            | "podcast"
            | "podcasts"
            | "read"
            | "series"
            | "show"
            | "shows"
            | "the"
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
}
