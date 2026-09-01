use sha2::{Digest, Sha256};
use sqlx::{FromRow, PgPool};
use subtle::ConstantTimeEq;
use thiserror::Error;

const API_KEY_PREFIX: &str = "newsly_ak_";
const HEX: &[u8; 16] = b"0123456789abcdef";

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
    let key_prefix = extract_key_prefix(raw_key)?;
    let presented_hash = sha256_hex(raw_key.as_bytes());
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
        if hashes_match(&presented_hash, &candidate.key_hash) {
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

pub fn is_api_key_token(token: &str) -> bool {
    token.starts_with(API_KEY_PREFIX)
}

fn extract_key_prefix(raw_key: &str) -> Result<&str, AuthenticationRepositoryError> {
    let suffix = raw_key
        .strip_prefix(API_KEY_PREFIX)
        .ok_or(AuthenticationRepositoryError::InvalidApiKeyFormat)?;
    let (public_id, secret) = suffix
        .split_once('_')
        .ok_or(AuthenticationRepositoryError::InvalidApiKeyFormat)?;
    if public_id.is_empty() || secret.is_empty() {
        return Err(AuthenticationRepositoryError::InvalidApiKeyFormat);
    }
    let prefix_length = API_KEY_PREFIX.len() + public_id.len();
    Ok(&raw_key[..prefix_length])
}

fn sha256_hex(value: &[u8]) -> String {
    let digest = Sha256::digest(value);
    let mut encoded = String::with_capacity(digest.len() * 2);
    for byte in digest {
        encoded.push(char::from(HEX[usize::from(byte >> 4)]));
        encoded.push(char::from(HEX[usize::from(byte & 0x0f)]));
    }
    encoded
}

fn hashes_match(presented: &str, stored: &str) -> bool {
    presented.len() == stored.len() && bool::from(presented.as_bytes().ct_eq(stored.as_bytes()))
}

#[derive(Debug, Error)]
pub enum AuthenticationRepositoryError {
    #[error("invalid Newsly API key format")]
    InvalidApiKeyFormat,
    #[error("PostgreSQL authentication lookup failed")]
    Sqlx(#[from] sqlx::Error),
}

#[cfg(test)]
mod tests {
    use super::{extract_key_prefix, hashes_match, is_api_key_token, sha256_hex};

    #[test]
    fn api_key_helpers_preserve_the_wire_contract() {
        let raw = "newsly_ak_a1b2c3d4_secret-value";
        assert!(is_api_key_token(raw));
        assert_eq!(
            extract_key_prefix(raw).expect("valid API key"),
            "newsly_ak_a1b2c3d4"
        );
        let digest = sha256_hex(raw.as_bytes());
        assert_eq!(digest.len(), 64);
        assert!(hashes_match(&digest, &digest));
        assert!(!hashes_match(&digest, &"0".repeat(64)));
    }
}
