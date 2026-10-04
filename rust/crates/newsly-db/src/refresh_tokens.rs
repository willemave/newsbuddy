use chrono::{DateTime, Utc};
use sqlx::{FromRow, PgPool, Postgres, Transaction};
use thiserror::Error;

const REFRESH_TOKEN_SWEEP_LIMIT: i64 = 1_000;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RefreshRotationClaim {
    New,
    Replay(String),
    Rejected,
    InactiveUser,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct RefreshTokenSweepReport {
    pub expired_tokens_deleted: usize,
    pub expired_replays_cleared: usize,
}

#[derive(Debug, FromRow)]
struct ConsumedRefreshTokenRow {
    attempt_id: Option<String>,
    replay_payload_encrypted: Option<String>,
    replay_expires_at: Option<DateTime<Utc>>,
}

/// Atomically consumes a refresh token or retrieves the bounded result for the
/// same idempotency attempt. The caller supplies a deterministic advisory key so
/// concurrent rotations of one token serialize consistently.
///
/// # Errors
///
/// Returns [`RefreshTokenRepositoryError`] when PostgreSQL cannot complete the
/// user lock, advisory lock, lookup, or insert.
pub async fn begin_refresh_rotation(
    transaction: &mut Transaction<'_, Postgres>,
    token_hash: &str,
    advisory_lock_key: i64,
    user_id: i64,
    token_expires_at: DateTime<Utc>,
    attempt_id: Option<&str>,
    now: DateTime<Utc>,
) -> Result<RefreshRotationClaim, RefreshTokenRepositoryError> {
    let active_user = sqlx::query_scalar::<_, i64>(
        r#"
        SELECT id::bigint
        FROM users
        WHERE id = $1::bigint::integer
          AND is_active = TRUE
        FOR SHARE
        "#,
    )
    .bind(user_id)
    .fetch_optional(&mut **transaction)
    .await?;
    if active_user.is_none() {
        return Ok(RefreshRotationClaim::InactiveUser);
    }

    sqlx::query("SELECT pg_advisory_xact_lock($1)")
        .bind(advisory_lock_key)
        .execute(&mut **transaction)
        .await?;

    if let Some(existing) = consumed_token(transaction, token_hash).await? {
        return Ok(replay_or_reject(existing, attempt_id, now));
    }

    let inserted = sqlx::query_scalar::<_, String>(
        r#"
        INSERT INTO consumed_refresh_tokens (
            token_hash,
            user_id,
            expires_at,
            attempt_id
        )
        VALUES ($1, $2::bigint::integer, $3, $4)
        ON CONFLICT (token_hash) DO NOTHING
        RETURNING token_hash
        "#,
    )
    .bind(token_hash)
    .bind(user_id)
    .bind(token_expires_at)
    .bind(attempt_id)
    .fetch_optional(&mut **transaction)
    .await?;
    if inserted.is_some() {
        return Ok(RefreshRotationClaim::New);
    }
    Ok(match consumed_token(transaction, token_hash).await? {
        Some(existing) => replay_or_reject(existing, attempt_id, now),
        None => RefreshRotationClaim::Rejected,
    })
}

/// Delete expired token rows and clear expired replay payloads in independent bounded
/// transactions. Locked rows are left for a later maintenance sweep.
///
/// The one-minute deletion margin preserves tokens whose JWT expiry was rounded to a whole
/// second while the stored timestamp retained sub-second precision.
pub async fn sweep_consumed_refresh_tokens(
    pool: &PgPool,
    now: DateTime<Utc>,
) -> Result<RefreshTokenSweepReport, sqlx::Error> {
    sweep_consumed_refresh_tokens_with_limit(pool, now, REFRESH_TOKEN_SWEEP_LIMIT).await
}

async fn sweep_consumed_refresh_tokens_with_limit(
    pool: &PgPool,
    now: DateTime<Utc>,
    limit: i64,
) -> Result<RefreshTokenSweepReport, sqlx::Error> {
    let mut delete_transaction = pool.begin().await?;
    let expired_tokens_deleted = sqlx::query_scalar::<_, i64>(
        r#"
        WITH candidates AS (
            SELECT token_hash
            FROM consumed_refresh_tokens
            WHERE expires_at < $1 - interval '1 minute'
            ORDER BY expires_at, token_hash
            LIMIT $2
            FOR UPDATE SKIP LOCKED
        ), deleted AS (
            DELETE FROM consumed_refresh_tokens AS token
            USING candidates
            WHERE token.token_hash = candidates.token_hash
            RETURNING token.token_hash
        )
        SELECT count(*)::bigint FROM deleted
        "#,
    )
    .bind(now)
    .bind(limit)
    .fetch_one(&mut *delete_transaction)
    .await?;
    delete_transaction.commit().await?;

    let mut replay_transaction = pool.begin().await?;
    let expired_replays_cleared = sqlx::query_scalar::<_, i64>(
        r#"
        WITH candidates AS (
            SELECT token_hash
            FROM consumed_refresh_tokens
            WHERE replay_expires_at <= $1
            ORDER BY replay_expires_at, token_hash
            LIMIT $2
            FOR UPDATE SKIP LOCKED
        ), cleared AS (
            UPDATE consumed_refresh_tokens AS token
            SET attempt_id = NULL,
                replay_payload_encrypted = NULL,
                replay_expires_at = NULL
            FROM candidates
            WHERE token.token_hash = candidates.token_hash
            RETURNING token.token_hash
        )
        SELECT count(*)::bigint FROM cleared
        "#,
    )
    .bind(now)
    .bind(limit)
    .fetch_one(&mut *replay_transaction)
    .await?;
    replay_transaction.commit().await?;

    Ok(RefreshTokenSweepReport {
        expired_tokens_deleted: usize::try_from(expired_tokens_deleted).unwrap_or(usize::MAX),
        expired_replays_cleared: usize::try_from(expired_replays_cleared).unwrap_or(usize::MAX),
    })
}

/// Persist the encrypted response for exactly the attempt that won consumption.
///
/// # Errors
///
/// Returns [`RefreshTokenRepositoryError`] when PostgreSQL cannot update the
/// consumed-token row.
pub async fn store_refresh_replay(
    transaction: &mut Transaction<'_, Postgres>,
    token_hash: &str,
    attempt_id: &str,
    encrypted_payload: &str,
    replay_expires_at: DateTime<Utc>,
) -> Result<(), RefreshTokenRepositoryError> {
    let result = sqlx::query(
        r#"
        UPDATE consumed_refresh_tokens
        SET replay_payload_encrypted = $3,
            replay_expires_at = $4
        WHERE token_hash = $1
          AND attempt_id = $2
        "#,
    )
    .bind(token_hash)
    .bind(attempt_id)
    .bind(encrypted_payload)
    .bind(replay_expires_at)
    .execute(&mut **transaction)
    .await?;
    if result.rows_affected() != 1 {
        return Err(RefreshTokenRepositoryError::LostConsumption);
    }
    Ok(())
}

async fn consumed_token(
    transaction: &mut Transaction<'_, Postgres>,
    token_hash: &str,
) -> Result<Option<ConsumedRefreshTokenRow>, sqlx::Error> {
    sqlx::query_as::<_, ConsumedRefreshTokenRow>(
        r#"
        SELECT attempt_id, replay_payload_encrypted, replay_expires_at
        FROM consumed_refresh_tokens
        WHERE token_hash = $1
        "#,
    )
    .bind(token_hash)
    .fetch_optional(&mut **transaction)
    .await
}

fn replay_or_reject(
    existing: ConsumedRefreshTokenRow,
    attempt_id: Option<&str>,
    now: DateTime<Utc>,
) -> RefreshRotationClaim {
    if attempt_id.is_some()
        && existing.attempt_id.as_deref() == attempt_id
        && existing
            .replay_expires_at
            .is_some_and(|expiry| expiry > now)
        && let Some(payload) = existing.replay_payload_encrypted
    {
        return RefreshRotationClaim::Replay(payload);
    }
    RefreshRotationClaim::Rejected
}

#[derive(Debug, Error)]
pub enum RefreshTokenRepositoryError {
    #[error("refresh-token rotation database operation failed")]
    Sqlx(#[from] sqlx::Error),
    #[error("refresh-token consumption row was lost before replay storage")]
    LostConsumption,
}

#[cfg(test)]
mod tests {
    use std::time::Duration as StdDuration;

    use chrono::Duration;
    use sqlx::PgPool;

    use super::*;

    async fn active_user(pool: &PgPool, suffix: &str) -> i64 {
        sqlx::query_scalar(
            "INSERT INTO users(apple_id,email,is_active,is_admin) VALUES($1,$2,true,false) RETURNING id::bigint",
        )
        .bind(format!("refresh-{suffix}"))
        .bind(format!("refresh-{suffix}@example.com"))
        .fetch_one(pool)
        .await
        .unwrap()
    }

    async fn insert_consumed(
        pool: &PgPool,
        user_id: i64,
        token_hash: &str,
        expires_at: DateTime<Utc>,
        replay_expires_at: Option<DateTime<Utc>>,
    ) {
        sqlx::query(
            r#"
            INSERT INTO consumed_refresh_tokens(
                token_hash, user_id, expires_at, attempt_id,
                replay_payload_encrypted, replay_expires_at
            ) VALUES ($1, $2::bigint::integer, $3, 'attempt', 'payload', $4)
            "#,
        )
        .bind(token_hash)
        .bind(user_id)
        .bind(expires_at)
        .bind(replay_expires_at)
        .execute(pool)
        .await
        .unwrap();
    }

    async fn assert_concurrent_rotation(pool: PgPool, second_attempt: &'static str) {
        let now = Utc::now();
        let user_id = active_user(&pool, second_attempt).await;
        let mut first = pool.begin().await.unwrap();
        assert_eq!(
            begin_refresh_rotation(
                &mut first,
                "shared-token",
                41,
                user_id,
                now + Duration::days(1),
                Some("same-attempt"),
                now,
            )
            .await
            .unwrap(),
            RefreshRotationClaim::New
        );
        store_refresh_replay(
            &mut first,
            "shared-token",
            "same-attempt",
            "encrypted-response",
            now + Duration::minutes(10),
        )
        .await
        .unwrap();

        let second_pool = pool.clone();
        let second = tokio::spawn(async move {
            let mut transaction = second_pool.begin().await.unwrap();
            let claim = begin_refresh_rotation(
                &mut transaction,
                "shared-token",
                41,
                user_id,
                now + Duration::days(1),
                Some(second_attempt),
                now,
            )
            .await
            .unwrap();
            transaction.commit().await.unwrap();
            claim
        });
        tokio::time::sleep(StdDuration::from_millis(25)).await;
        assert!(!second.is_finished());
        first.commit().await.unwrap();
        let claim = second.await.unwrap();
        if second_attempt == "same-attempt" {
            assert_eq!(
                claim,
                RefreshRotationClaim::Replay("encrypted-response".to_owned())
            );
        } else {
            assert_eq!(claim, RefreshRotationClaim::Rejected);
        }
    }

    #[sqlx::test]
    async fn concurrent_same_attempt_replays_committed_result(pool: PgPool) {
        assert_concurrent_rotation(pool, "same-attempt").await;
    }

    #[sqlx::test]
    async fn concurrent_different_attempt_is_rejected(pool: PgPool) {
        assert_concurrent_rotation(pool, "different-attempt").await;
    }

    #[sqlx::test]
    async fn replay_is_rejected_at_exact_expiry(pool: PgPool) {
        let now = Utc::now();
        let user_id = active_user(&pool, "deadline").await;
        insert_consumed(
            &pool,
            user_id,
            "deadline-token",
            now + Duration::days(1),
            Some(now),
        )
        .await;
        let mut transaction = pool.begin().await.unwrap();
        let claim = begin_refresh_rotation(
            &mut transaction,
            "deadline-token",
            42,
            user_id,
            now + Duration::days(1),
            Some("attempt"),
            now,
        )
        .await
        .unwrap();
        assert_eq!(claim, RefreshRotationClaim::Rejected);
    }

    #[sqlx::test]
    async fn sweep_honors_expiry_margin_and_independent_bounds(pool: PgPool) {
        let now = Utc::now();
        let user_id = active_user(&pool, "sweep-bounds").await;
        insert_consumed(
            &pool,
            user_id,
            "inside-margin",
            now - Duration::seconds(59),
            None,
        )
        .await;
        for index in 0..3 {
            insert_consumed(
                &pool,
                user_id,
                &format!("expired-{index}"),
                now - Duration::minutes(2) - Duration::seconds(index),
                None,
            )
            .await;
            insert_consumed(
                &pool,
                user_id,
                &format!("replay-{index}"),
                now + Duration::days(1),
                Some(now),
            )
            .await;
        }

        let report = sweep_consumed_refresh_tokens_with_limit(&pool, now, 2)
            .await
            .unwrap();
        assert_eq!(report.expired_tokens_deleted, 2);
        assert_eq!(report.expired_replays_cleared, 2);
        let inside_margin_exists: bool = sqlx::query_scalar(
            "SELECT EXISTS(SELECT 1 FROM consumed_refresh_tokens WHERE token_hash='inside-margin')",
        )
        .fetch_one(&pool)
        .await
        .unwrap();
        assert!(inside_margin_exists);
        let remaining_expired: i64 = sqlx::query_scalar(
            "SELECT count(*)::bigint FROM consumed_refresh_tokens WHERE token_hash LIKE 'expired-%'",
        )
        .fetch_one(&pool)
        .await
        .unwrap();
        let remaining_replays: i64 = sqlx::query_scalar(
            "SELECT count(*)::bigint FROM consumed_refresh_tokens WHERE replay_expires_at IS NOT NULL AND token_hash LIKE 'replay-%'",
        )
        .fetch_one(&pool)
        .await
        .unwrap();
        assert_eq!((remaining_expired, remaining_replays), (1, 1));
    }

    #[sqlx::test]
    async fn sweep_skips_locked_rows(pool: PgPool) {
        let now = Utc::now();
        let user_id = active_user(&pool, "sweep-lock").await;
        insert_consumed(
            &pool,
            user_id,
            "locked-token",
            now - Duration::minutes(2),
            Some(now),
        )
        .await;
        let mut locked = pool.begin().await.unwrap();
        sqlx::query("SELECT token_hash FROM consumed_refresh_tokens WHERE token_hash='locked-token' FOR UPDATE")
            .fetch_one(&mut *locked)
            .await
            .unwrap();

        let report = sweep_consumed_refresh_tokens_with_limit(&pool, now, 1)
            .await
            .unwrap();
        assert_eq!(report, RefreshTokenSweepReport::default());
        locked.rollback().await.unwrap();
        let report = sweep_consumed_refresh_tokens_with_limit(&pool, now, 1)
            .await
            .unwrap();
        assert_eq!(report.expired_tokens_deleted, 1);
    }
}
