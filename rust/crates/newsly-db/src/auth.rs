use sqlx::{FromRow, PgPool};
use thiserror::Error;

use crate::api_keys::{extract_api_key_prefix, verify_api_key_hash};

#[derive(Debug, Clone, PartialEq, Eq, FromRow)]
pub struct AuthenticatedUserRow {
    pub id: i64,
    pub apple_id: String,
    pub is_active: bool,
}

#[derive(Debug, FromRow)]
struct ApiKeyCandidateRow {
    api_key_id: i64,
    key_hash: String,
    user_id: i64,
    apple_id: String,
    is_active: bool,
}

/// Resolve an access-token subject to a current user row.
///
/// # Errors
///
/// Returns [`AuthenticationRepositoryError::Sqlx`] when PostgreSQL cannot
/// complete the lookup.
pub async fn find_user_by_id(
    pool: &PgPool,
    user_id: i64,
) -> Result<Option<AuthenticatedUserRow>, AuthenticationRepositoryError> {
    let row = sqlx::query_as::<_, AuthenticatedUserRow>(
        r#"
        SELECT id::bigint AS id, apple_id, is_active
        FROM users
        WHERE id = $1
        "#,
    )
    .bind(user_id)
    .fetch_optional(pool)
    .await?;
    Ok(row)
}

/// Resolve and verify a Newsly API-key bearer token.
///
/// The lookup prefix narrows candidates; the complete secret is compared
/// against the stored SHA-256 digest in constant time. A rate-limited
/// `last_used_at` update preserves the existing audit behavior.
///
/// # Errors
///
/// Returns [`AuthenticationRepositoryError::InvalidApiKeyFormat`] for a
/// malformed Newsly key, or [`AuthenticationRepositoryError::Sqlx`] when
/// PostgreSQL cannot complete the operation.
pub async fn find_user_by_api_key(
    pool: &PgPool,
    raw_key: &str,
) -> Result<Option<AuthenticatedUserRow>, AuthenticationRepositoryError> {
    let key_prefix = extract_api_key_prefix(raw_key)
        .map_err(|_| AuthenticationRepositoryError::InvalidApiKeyFormat)?;
    let candidates = sqlx::query_as::<_, ApiKeyCandidateRow>(
        r#"
        SELECT
            api_key.id::bigint AS api_key_id,
            api_key.key_hash,
            app_user.id::bigint AS user_id,
            app_user.apple_id,
            app_user.is_active
        FROM user_api_keys AS api_key
        JOIN users AS app_user ON app_user.id = api_key.user_id
        WHERE api_key.key_prefix = $1
          AND api_key.revoked_at IS NULL
        "#,
    )
    .bind(key_prefix)
    .fetch_all(pool)
    .await?;

    for candidate in candidates {
        if verify_api_key_hash(raw_key, &candidate.key_hash) {
            sqlx::query(
                r#"
                UPDATE user_api_keys
                SET last_used_at = timezone('UTC', now())
                WHERE id = $1
                  AND (
                    last_used_at IS NULL
                    OR last_used_at < timezone('UTC', now()) - interval '5 minutes'
                  )
                "#,
            )
            .bind(candidate.api_key_id)
            .execute(pool)
            .await?;
            return Ok(Some(AuthenticatedUserRow {
                id: candidate.user_id,
                apple_id: candidate.apple_id,
                is_active: candidate.is_active,
            }));
        }
    }

    Ok(None)
}

#[derive(Debug, Error)]
pub enum AuthenticationRepositoryError {
    #[error("invalid Newsly API key format")]
    InvalidApiKeyFormat,
    #[error("PostgreSQL authentication lookup failed")]
    Sqlx(#[from] sqlx::Error),
}
