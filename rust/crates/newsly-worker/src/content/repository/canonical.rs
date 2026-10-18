//! Canonical-content lookup and transactional transfer of user overlays.
use sqlx::{Postgres, Transaction};

pub(super) async fn find_duplicate_content(
    transaction: &mut Transaction<'static, Postgres>,
    content_id: i64,
    content_type: &str,
    url: &str,
) -> Result<Option<i64>, sqlx::Error> {
    sqlx::query_scalar::<_, i64>(
        r"
        SELECT id::bigint
        FROM contents
        WHERE id::bigint <> $1 AND content_type = $2 AND url = $3
        ORDER BY id
        LIMIT 1
        FOR SHARE
        ",
    )
    .bind(content_id)
    .bind(content_type)
    .bind(url)
    .fetch_optional(&mut **transaction)
    .await
}

pub(super) async fn duplicate_can_prepare_or_read(
    transaction: &mut Transaction<'static, Postgres>,
    content_id: i64,
) -> Result<bool, sqlx::Error> {
    sqlx::query_scalar(
        "SELECT status IN ('new', 'pending', 'processing', 'awaiting_image', 'completed') FROM contents WHERE id::bigint = $1",
    )
    .bind(content_id)
    .fetch_one(&mut **transaction)
    .await
}

/// Move all unique-per-user overlays from a classification loser to its canonical winner.
///
/// Each insert absorbs a concurrent or pre-existing winner row before the loser is deleted. The
/// timestamped overlays retain the newer interaction, while independent chat histories only
/// change their content destination. The relink runs inside the queue/product finalize fence.
pub(super) async fn relink_canonical_user_state(
    transaction: &mut Transaction<'static, Postgres>,
    loser_content_id: i64,
    winner_content_id: i64,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        r"
        INSERT INTO content_status (user_id, content_id, status, created_at, updated_at)
        SELECT user_id, $2, status, created_at, updated_at
        FROM content_status
        WHERE content_id::bigint = $1
        ON CONFLICT (user_id, content_id) DO NOTHING
        ",
    )
    .bind(loser_content_id)
    .bind(winner_content_id)
    .execute(&mut **transaction)
    .await?;
    sqlx::query("DELETE FROM content_status WHERE content_id::bigint = $1")
        .bind(loser_content_id)
        .execute(&mut **transaction)
        .await?;

    sqlx::query(
        r"
        INSERT INTO content_read_status (user_id, content_id, read_at, created_at)
        SELECT user_id, $2, read_at, created_at
        FROM content_read_status
        WHERE content_id::bigint = $1
        ON CONFLICT (user_id, content_id) DO UPDATE
        SET read_at = GREATEST(content_read_status.read_at, EXCLUDED.read_at)
        ",
    )
    .bind(loser_content_id)
    .bind(winner_content_id)
    .execute(&mut **transaction)
    .await?;
    sqlx::query("DELETE FROM content_read_status WHERE content_id::bigint = $1")
        .bind(loser_content_id)
        .execute(&mut **transaction)
        .await?;

    sqlx::query(
        r"
        INSERT INTO content_knowledge_saves (user_id, content_id, saved_at, created_at)
        SELECT user_id, $2, saved_at, created_at
        FROM content_knowledge_saves
        WHERE content_id::bigint = $1
        ON CONFLICT (user_id, content_id) DO UPDATE
        SET saved_at = GREATEST(content_knowledge_saves.saved_at, EXCLUDED.saved_at)
        ",
    )
    .bind(loser_content_id)
    .bind(winner_content_id)
    .execute(&mut **transaction)
    .await?;
    sqlx::query("DELETE FROM content_knowledge_saves WHERE content_id::bigint = $1")
        .bind(loser_content_id)
        .execute(&mut **transaction)
        .await?;

    sqlx::query(
        r"
        INSERT INTO content_unlikes (user_id, content_id, unliked_at, created_at)
        SELECT user_id, $2, unliked_at, created_at
        FROM content_unlikes
        WHERE content_id::bigint = $1
        ON CONFLICT (user_id, content_id) DO UPDATE
        SET unliked_at = GREATEST(content_unlikes.unliked_at, EXCLUDED.unliked_at)
        ",
    )
    .bind(loser_content_id)
    .bind(winner_content_id)
    .execute(&mut **transaction)
    .await?;
    sqlx::query("DELETE FROM content_unlikes WHERE content_id::bigint = $1")
        .bind(loser_content_id)
        .execute(&mut **transaction)
        .await?;

    sqlx::query(
        r"
        UPDATE user_integration_synced_items
        SET content_id = $2, updated_at = timezone('UTC', now())
        WHERE content_id::bigint = $1
        ",
    )
    .bind(loser_content_id)
    .bind(winner_content_id)
    .execute(&mut **transaction)
    .await?;

    sqlx::query(
        r"
        UPDATE chat_sessions
        SET content_id = $2, updated_at = timezone('UTC', now())
        WHERE content_id::bigint = $1
        ",
    )
    .bind(loser_content_id)
    .bind(winner_content_id)
    .execute(&mut **transaction)
    .await?;
    Ok(())
}
