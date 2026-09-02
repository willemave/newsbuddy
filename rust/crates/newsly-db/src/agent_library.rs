use chrono::{DateTime, NaiveDateTime, Utc};
use serde_json::Value;
use sqlx::{FromRow, PgPool};
use thiserror::Error;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgentKnowledgeItem {
    pub content_id: i64,
    pub title: String,
    pub source: Option<String>,
    pub url: String,
    pub storage_key: Option<String>,
    pub fallback_text: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgentLibraryBodyPointer {
    pub storage_key: String,
    pub updated_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct AgentLibraryContentProjection {
    pub content_id: i64,
    pub content_type: String,
    pub url: String,
    pub title: Option<String>,
    pub source: Option<String>,
    pub publication_date: Option<DateTime<Utc>>,
    pub created_at: DateTime<Utc>,
    pub updated_at: Option<DateTime<Utc>>,
    pub content_metadata: Value,
    pub saved_at: Option<DateTime<Utc>>,
    pub saved_to_knowledge: bool,
    pub chat_session_ids: Vec<i64>,
    pub source_body: Option<AgentLibraryBodyPointer>,
    pub rendered_body: Option<AgentLibraryBodyPointer>,
}

#[derive(Debug, FromRow)]
struct AgentLibraryContentRow {
    content_id: i64,
    content_type: String,
    url: String,
    title: Option<String>,
    source: Option<String>,
    publication_date: Option<NaiveDateTime>,
    created_at: NaiveDateTime,
    updated_at: Option<NaiveDateTime>,
    content_metadata: Value,
    saved_at: Option<NaiveDateTime>,
    saved_to_knowledge: bool,
    chat_session_ids: Vec<i64>,
    source_storage_key: Option<String>,
    source_updated_at: Option<NaiveDateTime>,
    rendered_storage_key: Option<String>,
    rendered_updated_at: Option<NaiveDateTime>,
}

impl From<AgentLibraryContentRow> for AgentLibraryContentProjection {
    fn from(row: AgentLibraryContentRow) -> Self {
        Self {
            content_id: row.content_id,
            content_type: row.content_type,
            url: row.url,
            title: row.title,
            source: row.source,
            publication_date: row.publication_date.map(|value| value.and_utc()),
            created_at: row.created_at.and_utc(),
            updated_at: row.updated_at.map(|value| value.and_utc()),
            content_metadata: row.content_metadata,
            saved_at: row.saved_at.map(|value| value.and_utc()),
            saved_to_knowledge: row.saved_to_knowledge,
            chat_session_ids: row.chat_session_ids,
            source_body: row
                .source_storage_key
                .map(|storage_key| AgentLibraryBodyPointer {
                    storage_key,
                    updated_at: row.source_updated_at.map(|value| value.and_utc()),
                }),
            rendered_body: row
                .rendered_storage_key
                .map(|storage_key| AgentLibraryBodyPointer {
                    storage_key,
                    updated_at: row.rendered_updated_at.map(|value| value.and_utc()),
                }),
        }
    }
}

/// Loads the complete exportable content set for one active user without touching the filesystem
/// or holding a connection while object bodies are read.
pub async fn list_agent_library_content(
    pool: &PgPool,
    user_id: i64,
) -> Result<Vec<AgentLibraryContentProjection>, AgentLibraryRepositoryError> {
    if user_id <= 0 {
        return Err(AgentLibraryRepositoryError::InvalidUserId);
    }
    let rows = sqlx::query_as::<_, AgentLibraryContentRow>(
        r#"
        WITH knowledge AS (
            SELECT
                save.content_id::bigint AS content_id,
                min(save.saved_at) AS saved_at
            FROM content_knowledge_saves AS save
            WHERE save.user_id::bigint = $1
            GROUP BY save.content_id
        ),
        chats AS (
            SELECT
                session.content_id::bigint AS content_id,
                array_agg(session.id::bigint ORDER BY session.id) AS chat_session_ids,
                min(session.created_at) AS saved_at
            FROM chat_sessions AS session
            WHERE session.user_id::bigint = $1
              AND session.content_id IS NOT NULL
              AND session.is_archived = false
            GROUP BY session.content_id
        ),
        reasons AS (
            SELECT
                coalesce(knowledge.content_id, chats.content_id)::bigint AS content_id,
                CASE
                    WHEN knowledge.saved_at IS NULL THEN chats.saved_at
                    WHEN chats.saved_at IS NULL THEN knowledge.saved_at
                    ELSE least(knowledge.saved_at, chats.saved_at)
                END AS saved_at,
                knowledge.content_id IS NOT NULL AS saved_to_knowledge,
                coalesce(chats.chat_session_ids, ARRAY[]::bigint[]) AS chat_session_ids
            FROM knowledge
            FULL OUTER JOIN chats ON chats.content_id = knowledge.content_id
        )
        SELECT
            content.id::bigint AS content_id,
            content.content_type,
            content.url,
            content.title,
            content.source,
            content.publication_date,
            content.created_at,
            content.updated_at,
            content.content_metadata::jsonb AS content_metadata,
            reasons.saved_at,
            reasons.saved_to_knowledge,
            reasons.chat_session_ids,
            source_body.storage_key AS source_storage_key,
            source_body.updated_at AS source_updated_at,
            rendered_body.storage_key AS rendered_storage_key,
            rendered_body.updated_at AS rendered_updated_at
        FROM reasons
        JOIN users AS account
          ON account.id::bigint = $1
         AND account.is_active = true
        JOIN contents AS content ON content.id = reasons.content_id
        LEFT JOIN content_bodies AS source_body
          ON source_body.content_id = content.id
         AND source_body.variant = 'source'
        LEFT JOIN content_bodies AS rendered_body
          ON rendered_body.content_id = content.id
         AND rendered_body.variant = 'rendered'
        ORDER BY content.id
        "#,
    )
    .bind(user_id)
    .fetch_all(pool)
    .await?;
    Ok(rows.into_iter().map(Into::into).collect())
}

/// Reauthorizes and resolves a bounded set of canonical Knowledge items.
///
/// The returned values contain only copied scalars and immutable object keys. Callers must release
/// PostgreSQL before reading the referenced objects.
pub async fn find_agent_knowledge_items(
    pool: &PgPool,
    user_id: i64,
    content_ids: &[i64],
) -> Result<Vec<AgentKnowledgeItem>, AgentLibraryRepositoryError> {
    if user_id <= 0
        || content_ids.is_empty()
        || content_ids.len() > 20
        || content_ids.iter().any(|value| *value <= 0)
    {
        return Err(AgentLibraryRepositoryError::InvalidInput);
    }
    let rows = sqlx::query_as::<
        _,
        (
            i64,
            String,
            Option<String>,
            String,
            Option<String>,
            Option<String>,
        ),
    >(
        r#"
        SELECT
            content.id::bigint,
            COALESCE(
                NULLIF(BTRIM(content.content_metadata->'summary'->>'title'), ''),
                NULLIF(BTRIM(content.title), ''),
                'Untitled'
            ),
            NULLIF(BTRIM(content.source), ''),
            content.url,
            COALESCE(rendered.storage_key, source.storage_key),
            NULLIF(BTRIM(content.search_text), '')
        FROM users AS account
        JOIN content_knowledge_saves AS saved
          ON saved.user_id = account.id
        JOIN contents AS content
          ON content.id = saved.content_id
        LEFT JOIN content_bodies AS rendered
          ON rendered.content_id = content.id AND rendered.variant = 'rendered'
        LEFT JOIN content_bodies AS source
          ON source.content_id = content.id AND source.variant = 'source'
        WHERE account.id::bigint = $1
          AND account.is_active = TRUE
          AND content.id::bigint = ANY($2)
        ORDER BY array_position($2::bigint[], content.id::bigint)
        "#,
    )
    .bind(user_id)
    .bind(content_ids)
    .fetch_all(pool)
    .await?;
    Ok(rows
        .into_iter()
        .map(
            |(content_id, title, source, url, storage_key, fallback_text)| AgentKnowledgeItem {
                content_id,
                title,
                source,
                url,
                storage_key,
                fallback_text,
            },
        )
        .collect())
}

#[derive(Debug, Error)]
pub enum AgentLibraryRepositoryError {
    #[error("user id must be positive")]
    InvalidUserId,
    #[error("Knowledge item input is invalid")]
    InvalidInput,
    #[error("PostgreSQL agent-library query failed")]
    Sqlx(#[from] sqlx::Error),
}
