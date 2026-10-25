use chrono::{DateTime, NaiveDateTime, Utc};
use serde_json::Value;
use sqlx::{AssertSqlSafe, FromRow, PgPool, Postgres, Transaction};
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
    saved_at: Option<DateTime<Utc>>,
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
            saved_at: row.saved_at,
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
    load_library_content(pool, user_id, None).await
}

/// Load one authorized exportable item without scanning or rendering its sibling documents.
pub async fn find_agent_library_content(
    pool: &PgPool,
    user_id: i64,
    content_id: i64,
) -> Result<Option<AgentLibraryContentProjection>, AgentLibraryRepositoryError> {
    if content_id <= 0 {
        return Err(AgentLibraryRepositoryError::InvalidInput);
    }
    Ok(load_library_content(pool, user_id, Some(content_id))
        .await?
        .pop())
}

async fn load_library_content(
    pool: &PgPool,
    user_id: i64,
    content_id: Option<i64>,
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
              AND ($2::bigint IS NULL OR save.content_id::bigint = $2)
            GROUP BY save.content_id
        ),
        chats AS (
            SELECT
                session.content_id::bigint AS content_id,
                array_agg(session.id::bigint ORDER BY session.id) AS chat_session_ids,
                min(session.created_at) AT TIME ZONE 'UTC' AS saved_at
            FROM chat_sessions AS session
            WHERE session.user_id::bigint = $1
              AND ($2::bigint IS NULL OR session.content_id::bigint = $2)
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
    .bind(content_id)
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
    find_agent_readable_items(pool, user_id, content_ids, true).await
}

const READABLE_CONTENT_PREDICATE: &str = r#"(saved.id IS NOT NULL OR (NOT $3::boolean AND EXISTS (
            SELECT 1 FROM content_status inbox WHERE inbox.user_id = account.id
              AND inbox.content_id = content.id AND inbox.status = 'inbox'
          )))"#;

/// Reauthorizes a mutation batch inside the caller's transaction and locks its content rows.
///
/// Missing or unauthorized ids are omitted. Callers must verify that every requested id was
/// returned before mutating any item. Rows are locked and returned in ascending content-id order.
/// Acquire queue and message fences before calling this.
pub async fn lock_authorized_content(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    content_ids: &[i64],
) -> Result<Vec<i64>, AgentLibraryRepositoryError> {
    if user_id <= 0 || content_ids.len() > 20 || content_ids.iter().any(|id| *id <= 0) {
        return Err(AgentLibraryRepositoryError::InvalidInput);
    }
    if content_ids.is_empty() {
        return Ok(Vec::new());
    }
    Ok(sqlx::query_scalar::<_, i64>(AssertSqlSafe(format!(
        r#"
        SELECT content.id::bigint
        FROM users AS account
        JOIN contents AS content ON TRUE
        LEFT JOIN content_knowledge_saves AS saved
          ON saved.user_id = account.id AND saved.content_id = content.id
        WHERE account.id::bigint = $1::bigint AND account.is_active
          AND content.id::bigint = ANY($2::bigint[])
          AND {READABLE_CONTENT_PREDICATE}
        ORDER BY content.id
        FOR SHARE OF content
        "#
    )))
    .bind(user_id)
    .bind(content_ids)
    .bind(false)
    .fetch_all(&mut **transaction)
    .await?)
}

/// Reauthorizes each content body against the current inbox or saved overlay.
pub async fn find_agent_readable_items(
    pool: &PgPool,
    user_id: i64,
    content_ids: &[i64],
    saved_only: bool,
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
    >(AssertSqlSafe(format!(
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
        JOIN contents AS content ON TRUE
        LEFT JOIN content_knowledge_saves AS saved
          ON saved.user_id = account.id AND saved.content_id = content.id
        LEFT JOIN content_bodies AS rendered
          ON rendered.content_id = content.id AND rendered.variant = 'rendered'
        LEFT JOIN content_bodies AS source
          ON source.content_id = content.id AND source.variant = 'source'
        WHERE account.id::bigint = $1
          AND account.is_active = TRUE
          AND content.id::bigint = ANY($2)
          AND {READABLE_CONTENT_PREDICATE}
        ORDER BY array_position($2::bigint[], content.id::bigint)
        "#
    )))
    .bind(user_id)
    .bind(content_ids)
    .bind(saved_only)
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

#[cfg(test)]
mod tests {
    use chrono::{TimeZone, Utc};
    use sqlx::PgPool;

    use super::{find_agent_library_content, list_agent_library_content};

    #[sqlx::test]
    async fn library_content_decodes_knowledge_and_chat_saved_timestamps(pool: PgPool) {
        let user_id = sqlx::query_scalar::<_, i64>(
            r#"
            INSERT INTO users (apple_id, email, is_admin, is_active)
            VALUES ('agent-library-test', 'agent-library@example.com', FALSE, TRUE)
            RETURNING id::bigint
            "#,
        )
        .fetch_one(&pool)
        .await
        .expect("test user should insert");
        let knowledge_content_id = sqlx::query_scalar::<_, i64>(
            r#"
            INSERT INTO contents (content_type, url, status, is_aggregate, content_metadata)
            VALUES ('article', 'https://example.com/knowledge', 'completed', FALSE, '{}')
            RETURNING id::bigint
            "#,
        )
        .fetch_one(&pool)
        .await
        .expect("Knowledge content should insert");
        let chat_content_id = sqlx::query_scalar::<_, i64>(
            r#"
            INSERT INTO contents (content_type, url, status, is_aggregate, content_metadata)
            VALUES ('article', 'https://example.com/chat', 'completed', FALSE, '{}')
            RETURNING id::bigint
            "#,
        )
        .fetch_one(&pool)
        .await
        .expect("chat content should insert");

        sqlx::query(
            r#"
            INSERT INTO content_knowledge_saves (user_id, content_id, saved_at, created_at)
            VALUES ($1::bigint::integer, $2::bigint::integer, '2026-09-15 12:34:56+00', '2026-09-15 12:34:56+00')
            "#,
        )
        .bind(user_id)
        .bind(knowledge_content_id)
        .execute(&pool)
        .await
        .expect("Knowledge save should insert");
        let chat_session_id = sqlx::query_scalar::<_, i64>(
            r#"
            INSERT INTO chat_sessions (
                user_id, content_id, title, session_type, created_at,
                council_mode, is_hidden_from_history
            )
            VALUES (
                $1::bigint::integer, $2::bigint::integer, 'Library chat', 'content',
                '2026-09-15 13:45:00', FALSE, FALSE
            )
            RETURNING id::bigint
            "#,
        )
        .bind(user_id)
        .bind(chat_content_id)
        .fetch_one(&pool)
        .await
        .expect("chat session should insert");

        let rows = list_agent_library_content(&pool, user_id)
            .await
            .expect("library timestamps should decode");

        assert_eq!(rows.len(), 2);
        assert_eq!(
            find_agent_library_content(&pool, user_id, knowledge_content_id)
                .await
                .unwrap()
                .unwrap()
                .content_id,
            knowledge_content_id
        );
        assert!(
            find_agent_library_content(&pool, user_id + 1, knowledge_content_id)
                .await
                .unwrap()
                .is_none()
        );
        assert!(
            find_agent_library_content(&pool, user_id, chat_content_id + 1)
                .await
                .unwrap()
                .is_none()
        );
        let knowledge = rows
            .iter()
            .find(|row| row.content_id == knowledge_content_id)
            .expect("Knowledge row should be present");
        assert!(knowledge.saved_to_knowledge);
        assert_eq!(
            knowledge.saved_at,
            Utc.with_ymd_and_hms(2026, 9, 15, 12, 34, 56).single()
        );
        let chat = rows
            .iter()
            .find(|row| row.content_id == chat_content_id)
            .expect("chat row should be present");
        assert!(!chat.saved_to_knowledge);
        assert_eq!(chat.chat_session_ids, vec![chat_session_id]);
        assert_eq!(
            chat.saved_at,
            Utc.with_ymd_and_hms(2026, 9, 15, 13, 45, 0).single()
        );
    }
}

#[cfg(test)]
mod content_authorization_tests {
    use super::*;

    #[sqlx::test(migrations = false)]
    async fn mutation_batch_and_body_reads_share_current_authorization(pool: PgPool) {
        crate::run_migrations(&pool).await.unwrap();
        sqlx::raw_sql(
            r#"
            INSERT INTO users (id, apple_id, email, is_active, is_admin) VALUES
                (9401, 'content-auth-one', 'one@content-auth.test', true, false),
                (9402, 'content-auth-two', 'two@content-auth.test', true, false);
            INSERT INTO contents (id, content_type, url, status, is_aggregate, content_metadata) VALUES
                (9401, 'article', 'https://content-auth.test/inbox', 'completed', false, '{}'),
                (9402, 'article', 'https://content-auth.test/saved', 'completed', false, '{}'),
                (9403, 'article', 'https://content-auth.test/private', 'completed', false, '{}');
            INSERT INTO content_status(user_id, content_id, status) VALUES
                (9401, 9401, 'inbox'), (9401, 9402, 'archived'), (9402, 9403, 'inbox');
            INSERT INTO content_knowledge_saves(user_id, content_id) VALUES (9401, 9402);
            "#,
        )
        .execute(&pool)
        .await
        .unwrap();
        let ids = [9403, 9402, 9401, 9499];
        let readable = find_agent_readable_items(&pool, 9401, &ids, false)
            .await
            .unwrap();
        assert_eq!(
            readable
                .iter()
                .map(|item| item.content_id)
                .collect::<Vec<_>>(),
            vec![9402, 9401]
        );
        let mut tx = pool.begin().await.unwrap();
        assert_eq!(
            lock_authorized_content(&mut tx, 9401, &ids).await.unwrap(),
            vec![9401, 9402]
        );
        assert!(
            lock_authorized_content(&mut tx, 9401, &[])
                .await
                .unwrap()
                .is_empty()
        );
        tx.rollback().await.unwrap();
        sqlx::query("UPDATE users SET is_active = false WHERE id = 9401")
            .execute(&pool)
            .await
            .unwrap();
        let mut tx = pool.begin().await.unwrap();
        assert!(
            lock_authorized_content(&mut tx, 9401, &ids)
                .await
                .unwrap()
                .is_empty()
        );
        tx.rollback().await.unwrap();
        assert!(
            find_agent_readable_items(&pool, 9401, &ids, false)
                .await
                .unwrap()
                .is_empty()
        );
    }
}
