use chrono::{DateTime, Utc};
use secrecy::ExposeSecret;
use sqlx::{FromRow, Postgres, Transaction};
use thiserror::Error;

use crate::api_keys::verify_api_key_hash;
use crate::create_api_key;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StartedCliLink {
    pub session_id: String,
    pub expires_at: DateTime<Utc>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ApprovedCliLink {
    pub session_id: String,
    pub key_prefix: String,
    pub expires_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CliLinkPollStatus {
    Pending,
    Approved,
    Claimed,
    Expired,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PolledCliLink {
    pub session_id: String,
    pub status: CliLinkPollStatus,
    pub expires_at: DateTime<Utc>,
    pub api_key: Option<String>,
    pub key_prefix: Option<String>,
}

/// Insert a short-lived unauthenticated CLI-link session.
///
/// # Errors
///
/// Returns [`CliLinkRepositoryError`] when PostgreSQL cannot persist it.
pub async fn start_cli_link(
    transaction: &mut Transaction<'_, Postgres>,
    session_id: &str,
    approve_token_hash: &str,
    poll_token_hash: &str,
    requested_device_name: Option<&str>,
    expires_at: DateTime<Utc>,
) -> Result<StartedCliLink, CliLinkRepositoryError> {
    let session = sqlx::query_as::<_, StartedCliLinkRow>(
        r"
        INSERT INTO cli_link_sessions (
            session_id,
            approve_token_hash,
            poll_token_hash,
            requested_device_name,
            status,
            expires_at,
            created_at
        )
        VALUES ($1, $2, $3, $4, 'pending', $5, NOW())
        RETURNING session_id, timezone('UTC', expires_at) AS expires_at
        ",
    )
    .bind(session_id)
    .bind(approve_token_hash)
    .bind(poll_token_hash)
    .bind(requested_device_name)
    .bind(expires_at.naive_utc())
    .fetch_one(&mut **transaction)
    .await?;
    Ok(StartedCliLink {
        session_id: session.session_id,
        expires_at: session.expires_at,
    })
}

/// Approve one pending session and atomically mint its display-once API key.
///
/// # Errors
///
/// Returns a stable session error for invalid state/token/expiry, or a database
/// error when the update cannot complete.
pub async fn approve_cli_link(
    transaction: &mut Transaction<'_, Postgres>,
    session_id: &str,
    approve_token: &str,
    user_id: i64,
    requested_device_name: Option<&str>,
    now: DateTime<Utc>,
) -> Result<ApprovedCliLink, CliLinkRepositoryError> {
    let mut session = load_locked_session(transaction, session_id)
        .await?
        .ok_or(CliLinkRepositoryError::NotFound)?;
    verify_token(
        approve_token,
        &session.approve_token_hash,
        CliLinkRepositoryError::InvalidApprovalToken,
    )?;
    if session.status == "claimed" {
        return Err(CliLinkRepositoryError::AlreadyClaimed);
    }
    if expire_session(transaction, &mut session, now).await? {
        return Err(CliLinkRepositoryError::Expired);
    }
    if session.status == "approved" {
        return Ok(ApprovedCliLink {
            session_id: session.session_id,
            key_prefix: session
                .key_prefix
                .ok_or(CliLinkRepositoryError::MissingApiKey)?,
            expires_at: session.expires_at,
        });
    }
    let created = create_api_key(transaction, user_id, None).await?;
    sqlx::query(
        r"
        UPDATE cli_link_sessions
        SET
            status = 'approved',
            approved_by_user_id = $2,
            user_api_key_id = $3,
            issued_api_key_plaintext = $4,
            requested_device_name = COALESCE($5, requested_device_name),
            approved_at = $6
        WHERE id = $1
        ",
    )
    .bind(session.id)
    .bind(user_id)
    .bind(created.record.id)
    .bind(created.raw_key.expose_secret())
    .bind(requested_device_name)
    .bind(now.naive_utc())
    .execute(&mut **transaction)
    .await?;
    Ok(ApprovedCliLink {
        session_id: session.session_id,
        key_prefix: created.record.key_prefix,
        expires_at: session.expires_at,
    })
}

/// Poll and, at most once, claim the plaintext API key.
///
/// # Errors
///
/// Returns a stable session error for invalid lookup/token state, or a database
/// error when PostgreSQL cannot lock/update the session.
pub async fn poll_cli_link(
    transaction: &mut Transaction<'_, Postgres>,
    session_id: &str,
    poll_token: &str,
    now: DateTime<Utc>,
) -> Result<PolledCliLink, CliLinkRepositoryError> {
    let mut session = load_locked_session(transaction, session_id)
        .await?
        .ok_or(CliLinkRepositoryError::NotFound)?;
    verify_token(
        poll_token,
        &session.poll_token_hash,
        CliLinkRepositoryError::InvalidPollingToken,
    )?;
    expire_session(transaction, &mut session, now).await?;
    if session.status == "approved"
        && let Some(api_key) = session.issued_api_key_plaintext.take()
    {
        sqlx::query(
            r"
            UPDATE cli_link_sessions
            SET issued_api_key_plaintext = NULL, status = 'claimed', claimed_at = $2
            WHERE id = $1
            ",
        )
        .bind(session.id)
        .bind(now.naive_utc())
        .execute(&mut **transaction)
        .await?;
        return Ok(PolledCliLink {
            session_id: session.session_id,
            status: CliLinkPollStatus::Approved,
            expires_at: session.expires_at,
            api_key: Some(api_key),
            key_prefix: session.key_prefix,
        });
    }
    let status = match session.status.as_str() {
        "approved" => CliLinkPollStatus::Approved,
        "claimed" => CliLinkPollStatus::Claimed,
        "expired" => CliLinkPollStatus::Expired,
        _ => CliLinkPollStatus::Pending,
    };
    Ok(PolledCliLink {
        session_id: session.session_id,
        status,
        expires_at: session.expires_at,
        api_key: None,
        key_prefix: session.key_prefix,
    })
}

// Caller holds the session lock and must commit an Expired outcome to persist retirement.
async fn expire_session(
    transaction: &mut Transaction<'_, Postgres>,
    session: &mut CliLinkSessionRow,
    now: DateTime<Utc>,
) -> Result<bool, sqlx::Error> {
    if session.status == "claimed" || (session.status != "expired" && session.expires_at > now) {
        return Ok(false);
    }
    sqlx::query(
        "UPDATE user_api_keys SET revoked_at = COALESCE(revoked_at, $2)
         WHERE id = (SELECT user_api_key_id FROM cli_link_sessions
                     WHERE id = $1 AND issued_api_key_plaintext IS NOT NULL AND claimed_at IS NULL)",
    )
    .bind(session.id)
    .bind(now.naive_utc())
    .execute(&mut **transaction)
    .await?;
    sqlx::query("UPDATE cli_link_sessions SET status = 'expired', issued_api_key_plaintext = NULL WHERE id = $1")
        .bind(session.id)
        .execute(&mut **transaction)
        .await?;
    "expired".clone_into(&mut session.status);
    session.issued_api_key_plaintext = None;
    Ok(true)
}

/// Retire abandoned secrets in a bounded batch and delete sessions expired for over a day.
/// Claimed keys survive even if historical code subsequently marked their sessions expired.
/// The caller owns a short transaction independent of queue maintenance.
///
/// # Errors
/// Returns a database error without committing any partial retirement.
pub async fn sweep_expired_cli_links(
    transaction: &mut Transaction<'_, Postgres>,
    now: DateTime<Utc>,
    limit: i64,
) -> Result<u64, sqlx::Error> {
    let retired = sqlx::query_as::<_, (i64, bool)>(
        r"
        WITH candidates AS MATERIALIZED (
            SELECT id, user_api_key_id, issued_api_key_plaintext IS NOT NULL AS unclaimed_secret,
                   claimed_at
            FROM cli_link_sessions
            WHERE expires_at <= $1
              AND (status IN ('pending', 'approved') OR issued_api_key_plaintext IS NOT NULL
                   OR expires_at < $1 - interval '1 day')
            ORDER BY expires_at, id
            LIMIT $2
            FOR UPDATE SKIP LOCKED
        ), revoked AS (
            UPDATE user_api_keys SET revoked_at = COALESCE(revoked_at, $1)
            FROM candidates
            WHERE user_api_keys.id = candidates.user_api_key_id
              AND candidates.unclaimed_secret AND candidates.claimed_at IS NULL
        )
        UPDATE cli_link_sessions AS session
        SET issued_api_key_plaintext = NULL,
            status = CASE WHEN session.claimed_at IS NULL THEN 'expired' ELSE session.status END
        FROM candidates
        WHERE session.id = candidates.id
        RETURNING session.id::bigint, session.expires_at < $1 - interval '1 day'
        ",
    )
    .bind(now.naive_utc())
    .bind(limit.clamp(1, 1_000))
    .fetch_all(&mut **transaction)
    .await?;
    let old_ids: Vec<i64> = retired
        .iter()
        .filter_map(|(id, old)| old.then_some(*id))
        .collect();
    sqlx::query("DELETE FROM cli_link_sessions WHERE id::bigint = ANY($1)")
        .bind(old_ids)
        .execute(&mut **transaction)
        .await?;
    Ok(retired.len() as u64)
}

async fn load_locked_session(
    transaction: &mut Transaction<'_, Postgres>,
    session_id: &str,
) -> Result<Option<CliLinkSessionRow>, sqlx::Error> {
    sqlx::query_as::<_, CliLinkSessionRow>(
        r"
        SELECT
            link.id::bigint AS id,
            link.session_id,
            link.approve_token_hash,
            link.poll_token_hash,
            link.status,
            link.issued_api_key_plaintext,
            timezone('UTC', link.expires_at) AS expires_at,
            api_key.key_prefix
        FROM cli_link_sessions AS link
        LEFT JOIN user_api_keys AS api_key ON api_key.id = link.user_api_key_id
        WHERE link.session_id = $1
        FOR UPDATE OF link
        ",
    )
    .bind(session_id)
    .fetch_optional(&mut **transaction)
    .await
}

fn verify_token(
    raw_token: &str,
    token_hash: &str,
    error: CliLinkRepositoryError,
) -> Result<(), CliLinkRepositoryError> {
    if verify_api_key_hash(raw_token, token_hash) {
        Ok(())
    } else {
        Err(error)
    }
}

#[derive(Debug, FromRow)]
struct StartedCliLinkRow {
    session_id: String,
    expires_at: DateTime<Utc>,
}

#[derive(Debug, FromRow)]
struct CliLinkSessionRow {
    id: i64,
    session_id: String,
    approve_token_hash: String,
    poll_token_hash: String,
    status: String,
    issued_api_key_plaintext: Option<String>,
    expires_at: DateTime<Utc>,
    key_prefix: Option<String>,
}

#[derive(Debug, Error)]
pub enum CliLinkRepositoryError {
    #[error("CLI link database operation failed")]
    Sqlx(#[from] sqlx::Error),
    #[error("CLI link API key operation failed")]
    ApiKey(#[from] crate::ApiKeyRepositoryError),
    #[error("CLI link session not found")]
    NotFound,
    #[error("CLI link session expired")]
    Expired,
    #[error("Invalid CLI link approval token")]
    InvalidApprovalToken,
    #[error("Invalid CLI link polling token")]
    InvalidPollingToken,
    #[error("CLI link session already claimed")]
    AlreadyClaimed,
    #[error("CLI link API key missing")]
    MissingApiKey,
}

#[cfg(test)]
mod tests {
    use chrono::Duration;
    use sqlx::PgPool;

    use super::*;

    async fn setup(pool: &PgPool, expires_at: DateTime<Utc>) -> i64 {
        crate::run_migrations(pool).await.unwrap();
        let user = sqlx::query_scalar(
            "INSERT INTO users(apple_id,email,is_admin,is_active)
             VALUES('cli-link-test','cli-link-test@example.com',false,true) RETURNING id::bigint",
        )
        .fetch_one(pool)
        .await
        .unwrap();
        let mut tx = pool.begin().await.unwrap();
        start_cli_link(
            &mut tx,
            "session",
            &crate::hash_api_key("approve-token"),
            &crate::hash_api_key("poll-token"),
            None,
            expires_at,
        )
        .await
        .unwrap();
        tx.commit().await.unwrap();
        user
    }

    async fn approve(pool: &PgPool, user: i64, now: DateTime<Utc>) {
        let mut tx = pool.begin().await.unwrap();
        approve_cli_link(&mut tx, "session", "approve-token", user, None, now)
            .await
            .unwrap();
        tx.commit().await.unwrap();
    }

    async fn key_revoked(pool: &PgPool) -> bool {
        sqlx::query_scalar("SELECT revoked_at IS NOT NULL FROM user_api_keys")
            .fetch_one(pool)
            .await
            .unwrap()
    }

    async fn assert_expired_without_secret(pool: &PgPool) {
        let state: (String, bool) = sqlx::query_as(
            "SELECT status, issued_api_key_plaintext IS NULL FROM cli_link_sessions",
        )
        .fetch_one(pool)
        .await
        .unwrap();
        assert_eq!(state, ("expired".to_owned(), true));
        assert!(key_revoked(pool).await);
    }

    #[sqlx::test]
    async fn approved_key_cannot_be_claimed_at_expiry(pool: PgPool) {
        let now = Utc::now();
        let expiry = now + Duration::minutes(5);
        let user = setup(&pool, expiry).await;
        approve(&pool, user, now).await;
        let mut tx = pool.begin().await.unwrap();
        let result = poll_cli_link(&mut tx, "session", "poll-token", expiry)
            .await
            .unwrap();
        assert_eq!(result.status, CliLinkPollStatus::Expired);
        assert!(result.api_key.is_none());
        tx.commit().await.unwrap();
        assert_expired_without_secret(&pool).await;
    }

    #[sqlx::test]
    async fn expired_approval_retires_unclaimed_key(pool: PgPool) {
        let now = Utc::now();
        let expiry = now + Duration::minutes(5);
        let user = setup(&pool, expiry).await;
        approve(&pool, user, now).await;
        let mut tx = pool.begin().await.unwrap();
        assert!(matches!(
            approve_cli_link(&mut tx, "session", "approve-token", user, None, expiry).await,
            Err(CliLinkRepositoryError::Expired)
        ));
        tx.commit().await.unwrap();
        assert_expired_without_secret(&pool).await;
    }

    #[sqlx::test]
    async fn invalid_tokens_do_not_mutate_expired_session(pool: PgPool) {
        let now = Utc::now();
        let user = setup(&pool, now).await;
        let mut tx = pool.begin().await.unwrap();
        assert!(matches!(
            approve_cli_link(&mut tx, "session", "wrong", user, None, now).await,
            Err(CliLinkRepositoryError::InvalidApprovalToken)
        ));
        assert!(matches!(
            poll_cli_link(&mut tx, "session", "wrong", now).await,
            Err(CliLinkRepositoryError::InvalidPollingToken)
        ));
        tx.commit().await.unwrap();
        let status: String = sqlx::query_scalar("SELECT status FROM cli_link_sessions")
            .fetch_one(&pool)
            .await
            .unwrap();
        assert_eq!(status, "pending");
    }

    #[sqlx::test]
    async fn claimed_key_survives_expiry_and_cannot_be_released_twice(pool: PgPool) {
        let now = Utc::now();
        let expiry = now + Duration::minutes(5);
        let user = setup(&pool, expiry).await;
        approve(&pool, user, now).await;
        let mut tx = pool.begin().await.unwrap();
        let result = poll_cli_link(&mut tx, "session", "poll-token", now)
            .await
            .unwrap();
        assert_eq!(result.status, CliLinkPollStatus::Approved);
        assert!(result.api_key.is_some());
        tx.commit().await.unwrap();
        let mut tx = pool.begin().await.unwrap();
        let result = poll_cli_link(&mut tx, "session", "poll-token", expiry)
            .await
            .unwrap();
        assert_eq!(result.status, CliLinkPollStatus::Claimed);
        assert!(result.api_key.is_none());
        assert!(matches!(
            approve_cli_link(&mut tx, "session", "approve-token", user, None, expiry).await,
            Err(CliLinkRepositoryError::AlreadyClaimed)
        ));
        assert_eq!(
            sweep_expired_cli_links(&mut tx, expiry, 128).await.unwrap(),
            0
        );
        tx.commit().await.unwrap();
        assert!(!key_revoked(&pool).await);
    }

    #[sqlx::test]
    async fn sweep_retires_abandoned_and_historical_expired_approvals(pool: PgPool) {
        let now = Utc::now();
        let expiry = now + Duration::minutes(5);
        let user = setup(&pool, expiry).await;
        approve(&pool, user, now).await;
        // Historical code could mark a session expired without clearing/revoking its key.
        sqlx::query("UPDATE cli_link_sessions SET status = 'expired'")
            .execute(&pool)
            .await
            .unwrap();
        let mut tx = pool.begin().await.unwrap();
        assert_eq!(
            sweep_expired_cli_links(&mut tx, expiry, 1).await.unwrap(),
            1
        );
        assert_eq!(
            sweep_expired_cli_links(&mut tx, expiry, 1).await.unwrap(),
            0
        );
        tx.commit().await.unwrap();
        assert_expired_without_secret(&pool).await;
    }

    #[sqlx::test]
    async fn sweep_skips_locked_claim_and_expires_abandoned_approval(pool: PgPool) {
        let now = Utc::now();
        let expiry = now + Duration::minutes(5);
        let user = setup(&pool, expiry).await;
        approve(&pool, user, now).await;
        let mut locked = pool.begin().await.unwrap();
        load_locked_session(&mut locked, "session").await.unwrap();
        let mut sweep = pool.begin().await.unwrap();
        assert_eq!(
            sweep_expired_cli_links(&mut sweep, expiry, 1)
                .await
                .unwrap(),
            0
        );
        sweep.commit().await.unwrap();
        locked.rollback().await.unwrap();
        let mut sweep = pool.begin().await.unwrap();
        assert_eq!(
            sweep_expired_cli_links(&mut sweep, expiry, 1)
                .await
                .unwrap(),
            1
        );
        sweep.commit().await.unwrap();
        assert_expired_without_secret(&pool).await;
    }

    #[sqlx::test]
    async fn historical_expired_claim_is_not_revoked_and_old_session_is_removed(pool: PgPool) {
        let now = Utc::now();
        let expiry = now + Duration::minutes(5);
        let user = setup(&pool, expiry).await;
        approve(&pool, user, now).await;
        let mut tx = pool.begin().await.unwrap();
        poll_cli_link(&mut tx, "session", "poll-token", now)
            .await
            .unwrap();
        tx.commit().await.unwrap();
        sqlx::query("UPDATE cli_link_sessions SET status = 'expired'")
            .execute(&pool)
            .await
            .unwrap();
        let mut tx = pool.begin().await.unwrap();
        assert_eq!(
            sweep_expired_cli_links(&mut tx, expiry, 1).await.unwrap(),
            0
        );
        tx.commit().await.unwrap();
        assert!(!key_revoked(&pool).await);
        let mut tx = pool.begin().await.unwrap();
        assert_eq!(
            sweep_expired_cli_links(&mut tx, expiry + Duration::days(2), 1)
                .await
                .unwrap(),
            1
        );
        tx.commit().await.unwrap();
        let remaining: i64 = sqlx::query_scalar("SELECT count(*) FROM cli_link_sessions")
            .fetch_one(&pool)
            .await
            .unwrap();
        assert_eq!(remaining, 0);
        assert!(!key_revoked(&pool).await);
    }
}
