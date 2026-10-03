//! Read-only preflight for the maintenance-only historical chat conversion.

use serde::Serialize;
use sqlx::PgPool;

#[derive(Debug, Serialize)]
pub struct CouncilRetirementPreflight {
    pub processing_message_count: i64,
    pub processing_message_ids: Vec<i64>,
    pub malformed_session_count: i64,
    pub malformed_session_ids: Vec<i64>,
}

impl CouncilRetirementPreflight {
    pub const fn is_ready(&self) -> bool {
        self.processing_message_count == 0 && self.malformed_session_count == 0
    }
}

/// Report blockers without reading transcript text, mutating records, or acquiring writer locks.
/// IDs are bounded to 100 per category; counts describe the complete set. A ready preflight is
/// advisory: the migration checks again after all old writers have stopped.
pub async fn verify_council_retirement(
    pool: &PgPool,
) -> Result<CouncilRetirementPreflight, sqlx::Error> {
    let processing = sqlx::query_as::<_, (i64, i64)>(
        r#"
        SELECT message.id::bigint, count(*) OVER ()
        FROM chat_messages AS message
        LEFT JOIN chat_sessions AS session ON session.id = message.session_id
        WHERE message.status = 'processing'
          AND (message.processing_context ->> 'kind' = 'council'
               OR COALESCE(message.processing_context::jsonb -> 'council_run', 'null'::jsonb) <> 'null'::jsonb
               OR session.council_mode OR session.is_hidden_from_history)
        ORDER BY message.id LIMIT 100
        "#,
    )
    .fetch_all(pool)
    .await?;
    let malformed = sqlx::query_as::<_, (i64, i64)>(
        r#"
        SELECT session.id::bigint, count(*) OVER ()
        FROM chat_sessions AS session
        LEFT JOIN chat_sessions AS owner ON owner.id = session.parent_session_id
        LEFT JOIN chat_sessions AS child ON child.id = session.active_child_session_id
        WHERE (session.is_hidden_from_history AND (owner.id IS NULL OR owner.user_id <> session.user_id))
           OR (NOT session.council_mode AND (session.active_child_session_id IS NOT NULL OR session.council_message_id IS NOT NULL))
           OR (session.council_mode AND (
                child.id IS NULL OR child.parent_session_id IS DISTINCT FROM session.id
                OR child.user_id IS DISTINCT FROM session.user_id OR NOT child.is_hidden_from_history
                OR NOT EXISTS (SELECT 1 FROM chat_messages WHERE id = session.council_message_id AND session_id = session.id)
                OR NOT EXISTS (
                    SELECT 1 FROM chat_messages AS boundary
                    WHERE boundary.session_id = child.id
                      AND boundary.id = COALESCE(child.branch_start_message_id, (
                          SELECT max(id) FROM chat_messages WHERE session_id = child.id
                            AND COALESCE(processing_context::jsonb -> 'council_run', 'null'::jsonb) <> 'null'::jsonb
                      ))
                )
           ))
        ORDER BY session.id LIMIT 100
        "#,
    )
    .fetch_all(pool)
    .await?;
    Ok(CouncilRetirementPreflight {
        processing_message_count: processing.first().map_or(0, |row| row.1),
        processing_message_ids: processing.into_iter().map(|row| row.0).collect(),
        malformed_session_count: malformed.first().map_or(0, |row| row.1),
        malformed_session_ids: malformed.into_iter().map(|row| row.0).collect(),
    })
}
