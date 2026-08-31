use chrono::{DateTime, Utc};
use secrecy::ExposeSecret;
use sqlx::{FromRow, Postgres, Transaction};
use thiserror::Error;

use crate::{create_api_key, verify_api_key_hash};

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
    let session = load_locked_session(transaction, session_id)
        .await?
        .ok_or(CliLinkRepositoryError::NotFound)?;
    if session.expires_at <= now {
        sqlx::query("UPDATE cli_link_sessions SET status = 'expired' WHERE id = $1")
            .bind(session.id)
            .execute(&mut **transaction)
            .await?;
        return Err(CliLinkRepositoryError::Expired);
    }
    if session.status == "expired" {
        return Err(CliLinkRepositoryError::Expired);
    }
    verify_token(
        approve_token,
        &session.approve_token_hash,
        CliLinkRepositoryError::InvalidApprovalToken,
    )?;
    if session.status == "approved" {
        return Ok(ApprovedCliLink {
            session_id: session.session_id,
            key_prefix: session
                .key_prefix
                .ok_or(CliLinkRepositoryError::MissingApiKey)?,
            expires_at: session.expires_at,
        });
    }
    if session.status == "claimed" {
        return Err(CliLinkRepositoryError::AlreadyClaimed);
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
    if session.expires_at <= now && session.status == "pending" {
        sqlx::query("UPDATE cli_link_sessions SET status = 'expired' WHERE id = $1")
            .bind(session.id)
            .execute(&mut **transaction)
            .await?;
        "expired".clone_into(&mut session.status);
    }
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
    let status =
        if session.expires_at <= now && matches!(session.status.as_str(), "pending" | "approved") {
            CliLinkPollStatus::Expired
        } else {
            match session.status.as_str() {
                "approved" => CliLinkPollStatus::Approved,
                "claimed" => CliLinkPollStatus::Claimed,
                "expired" => CliLinkPollStatus::Expired,
                _ => CliLinkPollStatus::Pending,
            }
        };
    Ok(PolledCliLink {
        session_id: session.session_id,
        status,
        expires_at: session.expires_at,
        api_key: None,
        key_prefix: session.key_prefix,
    })
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
