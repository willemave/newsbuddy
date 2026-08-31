use chrono::{DateTime, Utc};
use sqlx::{FromRow, Postgres, Transaction};
use thiserror::Error;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RefreshRotationClaim {
    New,
    Replay(String),
    Rejected,
    InactiveUser,
}

#[derive(Debug, FromRow)]
struct ConsumedRefreshTokenRow {
    attempt_id: Option<String>,
    replay_payload_encrypted: Option<String>,
    replay_expires_at: Option<DateTime<Utc>>,
}

/// Atomically consume a refresh token or retrieve the bounded result for the
/// same idempotency attempt. The advisory-key algorithm matches Python exactly.
///
/// # Errors
///
/// Returns [`RefreshTokenRepositoryError`] when PostgreSQL cannot complete the
/// user lock, cleanup, advisory lock, lookup, or insert.
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

    sqlx::query("DELETE FROM consumed_refresh_tokens WHERE expires_at < $1")
        .bind(now)
        .execute(&mut **transaction)
        .await?;
    sqlx::query(
        r#"
        UPDATE consumed_refresh_tokens
        SET attempt_id = NULL,
            replay_payload_encrypted = NULL,
            replay_expires_at = NULL
        WHERE replay_expires_at IS NOT NULL
          AND replay_expires_at <= $1
        "#,
    )
    .bind(now)
    .execute(&mut **transaction)
    .await?;
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
