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
        WHERE id <> $1::bigint AND content_type = $2 AND url = $3
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
        "SELECT status IN ('new', 'pending', 'processing', 'awaiting_image', 'completed') FROM contents WHERE id = $1::bigint",
    )
    .bind(content_id)
    .fetch_one(&mut **transaction)
    .await
}

/// Move all unique-per-user overlays from a classification loser to its canonical winner.
///
/// Delegates to [`newsly_db::relink_canonical_user_state`] so content classification and
/// Learning Deck canonical recovery stay on one transactional implementation.
pub(super) async fn relink_canonical_user_state(
    transaction: &mut Transaction<'static, Postgres>,
    loser_content_id: i64,
    winner_content_id: i64,
) -> Result<(), sqlx::Error> {
    newsly_db::relink_canonical_user_state(transaction, loser_content_id, winner_content_id).await
}
