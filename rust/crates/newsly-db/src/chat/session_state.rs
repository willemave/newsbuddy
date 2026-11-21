//! Page-level chat session facts loaded in one round trip instead of per-session queries.

use std::collections::HashMap;

use sqlx::{FromRow, PgPool};

use super::{ChatRepositoryError, ChatSessionRow};

/// Per-session message and knowledge-save facts, loaded for a whole page in one round trip.
#[derive(Debug, Default, FromRow)]
pub(super) struct SessionMessageState {
    pub(super) session_id: i64,
    pub(super) has_pending_message: bool,
    pub(super) has_messages: bool,
    pub(super) last_message_list: Option<String>,
    pub(super) is_saved_to_knowledge: bool,
}

pub(super) async fn load_session_message_states(
    pool: &PgPool,
    user_id: i64,
    rows: &[ChatSessionRow],
) -> Result<HashMap<i64, SessionMessageState>, ChatRepositoryError> {
    if rows.is_empty() {
        return Ok(HashMap::new());
    }
    let session_ids: Vec<i64> = rows.iter().map(|row| row.id).collect();
    let content_ids: Vec<Option<i64>> = rows.iter().map(|row| row.content_id).collect();
    let states = sqlx::query_as::<_, SessionMessageState>(
        r"
        SELECT
            requested.session_id,
            EXISTS(
                SELECT 1 FROM chat_messages pending
                WHERE pending.session_id = requested.session_id AND pending.status = 'processing'
            ) AS has_pending_message,
            latest.message_list IS NOT NULL AS has_messages,
            latest.message_list AS last_message_list,
            (
                requested.content_id IS NOT NULL
                AND EXISTS(
                    SELECT 1 FROM content_knowledge_saves saves
                    WHERE saves.user_id = $3::bigint AND saves.content_id = requested.content_id
                )
            ) AS is_saved_to_knowledge
        FROM unnest($1::bigint[], $2::bigint[]) AS requested(session_id, content_id)
        LEFT JOIN LATERAL (
            SELECT message_list FROM chat_messages
            WHERE chat_messages.session_id = requested.session_id
            ORDER BY id DESC
            LIMIT 1
        ) AS latest ON TRUE
        ",
    )
    .bind(&session_ids)
    .bind(&content_ids)
    .bind(user_id)
    .fetch_all(pool)
    .await?;
    Ok(states
        .into_iter()
        .map(|state| (state.session_id, state))
        .collect())
}
