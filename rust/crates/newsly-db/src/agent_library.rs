use chrono::{DateTime, NaiveDateTime, Utc};
use serde_json::Value;
use sqlx::{FromRow, PgPool};
use thiserror::Error;

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

#[derive(Debug, Error)]
pub enum AgentLibraryRepositoryError {
    #[error("user id must be positive")]
    InvalidUserId,
    #[error("PostgreSQL agent-library query failed")]
    Sqlx(#[from] sqlx::Error),
}
