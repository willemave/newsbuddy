use std::fmt::{self, Debug, Formatter};

use base64::Engine as _;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use chrono::{DateTime, Utc};
use secrecy::{ExposeSecret, SecretString};
use sha2::{Digest, Sha256};
use sqlx::{FromRow, PgPool, Postgres, Transaction};
use subtle::ConstantTimeEq;
use thiserror::Error;

const API_KEY_TOKEN_PREFIX: &str = "newsly_ak_";
const API_KEY_PUBLIC_ID_BYTES: usize = 4;
const API_KEY_SECRET_BYTES: usize = 24;
const SYSTEM_ADMIN_APPLE_ID: &str = "system-admin";
const SYSTEM_ADMIN_EMAIL: &str = "admin@system.local";

/// A freshly generated API key and its non-secret lookup prefix.
///
/// The plaintext value must only cross the create response boundary once. Its
/// custom debug representation deliberately never exposes the bearer secret.
#[derive(Clone)]
pub struct GeneratedApiKey {
    pub raw_key: SecretString,
    pub key_prefix: String,
}

impl Debug for GeneratedApiKey {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("GeneratedApiKey")
            .field("raw_key", &"[REDACTED]")
            .field("key_prefix", &self.key_prefix)
            .finish()
    }
}

#[derive(Debug, Clone, PartialEq, Eq, FromRow)]
pub struct ApiKeySummaryProjection {
    pub id: i64,
    pub user_id: i64,
    pub key_prefix: String,
    pub created_at: DateTime<Utc>,
    pub revoked_at: Option<DateTime<Utc>>,
    pub last_used_at: Option<DateTime<Utc>>,
    pub created_by_admin_user_id: Option<i64>,
}

/// Result of inserting an API key. `raw_key` is intentionally unavailable on
/// every later read/list path.
#[derive(Clone)]
pub struct CreatedApiKey {
    pub raw_key: SecretString,
    pub record: ApiKeySummaryProjection,
}

impl Debug for CreatedApiKey {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("CreatedApiKey")
            .field("raw_key", &"[REDACTED]")
            .field("record", &self.record)
            .finish()
    }
}

#[derive(Debug, Clone, PartialEq, Eq, FromRow)]
pub struct ApiKeyTargetUser {
    pub id: i64,
    pub email: String,
}

/// Generate a Newsly API key in the persisted token format using operating-system entropy.
///
/// # Errors
///
/// Returns [`ApiKeyRepositoryError::Entropy`] when secure operating-system
/// randomness is unavailable.
pub fn generate_api_key() -> Result<GeneratedApiKey, ApiKeyRepositoryError> {
    let mut public_id = [0_u8; API_KEY_PUBLIC_ID_BYTES];
    let mut secret = [0_u8; API_KEY_SECRET_BYTES];
    getrandom::fill(&mut public_id)?;
    getrandom::fill(&mut secret)?;

    let public_id = hex_encode(&public_id);
    let key_prefix = format!("{API_KEY_TOKEN_PREFIX}{public_id}");
    let secret = URL_SAFE_NO_PAD.encode(secret);
    Ok(GeneratedApiKey {
        raw_key: SecretString::from(format!("{key_prefix}_{secret}")),
        key_prefix,
    })
}

/// Return the stable SHA-256 digest stored instead of an API-key secret.
#[must_use]
pub fn hash_api_key(raw_key: &str) -> String {
    hex_encode(&Sha256::digest(raw_key.as_bytes()))
}

/// Compare a presented API key with a stored digest without secret-dependent
/// early exits.
#[must_use]
pub fn verify_api_key_hash(raw_key: &str, stored_hash: &str) -> bool {
    let presented_hash = hash_api_key(raw_key);
    presented_hash.len() == stored_hash.len()
        && bool::from(presented_hash.as_bytes().ct_eq(stored_hash.as_bytes()))
}

/// Extract the non-secret prefix used to narrow authentication candidates.
///
/// # Errors
///
/// Returns [`ApiKeyRepositoryError::InvalidFormat`] for a non-Newsly or
/// incomplete token.
pub fn extract_api_key_prefix(raw_key: &str) -> Result<&str, ApiKeyRepositoryError> {
    let suffix = raw_key
        .strip_prefix(API_KEY_TOKEN_PREFIX)
        .ok_or(ApiKeyRepositoryError::InvalidFormat)?;
    let (public_id, secret) = suffix
        .split_once('_')
        .ok_or(ApiKeyRepositoryError::InvalidFormat)?;
    if public_id.is_empty() || secret.is_empty() {
        return Err(ApiKeyRepositoryError::InvalidFormat);
    }
    let prefix_length = API_KEY_TOKEN_PREFIX.len() + public_id.len();
    Ok(&raw_key[..prefix_length])
}

/// Generate and insert a key in the caller-owned transaction. Only its digest
/// is persisted; the plaintext is returned once to the caller.
///
/// # Errors
///
/// Returns [`ApiKeyRepositoryError`] when secure generation or `PostgreSQL`
/// persistence fails.
pub async fn create_api_key(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    created_by_admin_user_id: Option<i64>,
) -> Result<CreatedApiKey, ApiKeyRepositoryError> {
    let generated = generate_api_key()?;
    let key_hash = hash_api_key(generated.raw_key.expose_secret());
    let record = sqlx::query_as::<_, ApiKeySummaryProjection>(
        r"
        INSERT INTO user_api_keys (
            user_id,
            key_prefix,
            key_hash,
            created_by_admin_user_id,
            created_at
        )
        VALUES ($1, $2, $3, $4, timezone('UTC', now()))
        RETURNING
            id::bigint AS id,
            user_id::bigint AS user_id,
            key_prefix,
            timezone('UTC', created_at) AS created_at,
            timezone('UTC', revoked_at) AS revoked_at,
            timezone('UTC', last_used_at) AS last_used_at,
            created_by_admin_user_id::bigint AS created_by_admin_user_id
        ",
    )
    .bind(user_id)
    .bind(&generated.key_prefix)
    .bind(key_hash)
    .bind(created_by_admin_user_id)
    .fetch_one(&mut **transaction)
    .await?;
    Ok(CreatedApiKey {
        raw_key: generated.raw_key,
        record,
    })
}

/// List API-key summaries, newest first, without ever loading key hashes.
///
/// # Errors
///
/// Returns [`ApiKeyRepositoryError::Sqlx`] when `PostgreSQL` cannot complete the
/// query.
pub async fn list_api_keys(
    pool: &PgPool,
    user_id: Option<i64>,
) -> Result<Vec<ApiKeySummaryProjection>, ApiKeyRepositoryError> {
    let rows = sqlx::query_as::<_, ApiKeySummaryProjection>(
        r"
        SELECT
            id::bigint AS id,
            user_id::bigint AS user_id,
            key_prefix,
            timezone('UTC', created_at) AS created_at,
            timezone('UTC', revoked_at) AS revoked_at,
            timezone('UTC', last_used_at) AS last_used_at,
            created_by_admin_user_id::bigint AS created_by_admin_user_id
        FROM user_api_keys
        WHERE $1::bigint IS NULL OR user_id::bigint = $1
        ORDER BY created_at DESC, id DESC
        ",
    )
    .bind(user_id)
    .fetch_all(pool)
    .await?;
    Ok(rows)
}

/// Revoke an API key idempotently and return its updated public projection.
///
/// # Errors
///
/// Returns [`ApiKeyRepositoryError::Sqlx`] when `PostgreSQL` cannot complete the
/// update.
pub async fn revoke_api_key(
    transaction: &mut Transaction<'_, Postgres>,
    api_key_id: i64,
) -> Result<Option<ApiKeySummaryProjection>, ApiKeyRepositoryError> {
    let record = sqlx::query_as::<_, ApiKeySummaryProjection>(
        r"
        UPDATE user_api_keys
        SET revoked_at = COALESCE(revoked_at, timezone('UTC', now()))
        WHERE id::bigint = $1
        RETURNING
            id::bigint AS id,
            user_id::bigint AS user_id,
            key_prefix,
            timezone('UTC', created_at) AS created_at,
            timezone('UTC', revoked_at) AS revoked_at,
            timezone('UTC', last_used_at) AS last_used_at,
            created_by_admin_user_id::bigint AS created_by_admin_user_id
        ",
    )
    .bind(api_key_id)
    .fetch_optional(&mut **transaction)
    .await?;
    Ok(record)
}

/// Return the users available in the admin API-key target selector.
///
/// # Errors
///
/// Returns [`ApiKeyRepositoryError::Sqlx`] when `PostgreSQL` cannot complete the
/// query.
pub async fn list_api_key_target_users(
    pool: &PgPool,
) -> Result<Vec<ApiKeyTargetUser>, ApiKeyRepositoryError> {
    let users = sqlx::query_as::<_, ApiKeyTargetUser>(
        "SELECT id::bigint AS id, email FROM users ORDER BY email ASC",
    )
    .fetch_all(pool)
    .await?;
    Ok(users)
}

/// Load or create the system admin identity used to attribute admin-created
/// keys, preserving the existing admin-session behavior.
///
/// # Errors
///
/// Returns [`ApiKeyRepositoryError::Sqlx`] when `PostgreSQL` cannot load or
/// create the identity.
pub async fn ensure_system_admin_user(
    transaction: &mut Transaction<'_, Postgres>,
) -> Result<i64, ApiKeyRepositoryError> {
    if let Some(admin_id) =
        sqlx::query_scalar::<_, i64>("SELECT id::bigint FROM users WHERE email = $1")
            .bind(SYSTEM_ADMIN_EMAIL)
            .fetch_optional(&mut **transaction)
            .await?
    {
        return Ok(admin_id);
    }

    let inserted_id = sqlx::query_scalar::<_, i64>(
        r"
        INSERT INTO users (
            apple_id,
            email,
            full_name,
            is_admin,
            is_active,
            has_completed_new_user_tutorial,
            has_completed_onboarding,
            reading_experience,
            created_at,
            updated_at
        )
        VALUES (
            $1,
            $2,
            'System Admin',
            TRUE,
            TRUE,
            FALSE,
            FALSE,
            'briefing',
            now(),
            now()
        )
        ON CONFLICT (email) DO NOTHING
        RETURNING id::bigint
        ",
    )
    .bind(SYSTEM_ADMIN_APPLE_ID)
    .bind(SYSTEM_ADMIN_EMAIL)
    .fetch_optional(&mut **transaction)
    .await?;
    match inserted_id {
        Some(admin_id) => Ok(admin_id),
        None => sqlx::query_scalar::<_, i64>("SELECT id::bigint FROM users WHERE email = $1")
            .bind(SYSTEM_ADMIN_EMAIL)
            .fetch_one(&mut **transaction)
            .await
            .map_err(ApiKeyRepositoryError::from),
    }
}

fn hex_encode(value: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut encoded = String::with_capacity(value.len() * 2);
    for byte in value {
        encoded.push(char::from(HEX[usize::from(*byte >> 4)]));
        encoded.push(char::from(HEX[usize::from(*byte & 0x0f)]));
    }
    encoded
}

#[derive(Debug, Error)]
pub enum ApiKeyRepositoryError {
    #[error("secure operating-system entropy is unavailable ({0})")]
    Entropy(getrandom::Error),
    #[error("invalid Newsly API key format")]
    InvalidFormat,
    #[error("PostgreSQL API-key operation failed")]
    Sqlx(#[from] sqlx::Error),
}

impl From<getrandom::Error> for ApiKeyRepositoryError {
    fn from(error: getrandom::Error) -> Self {
        Self::Entropy(error)
    }
}

#[cfg(test)]
mod tests {
    use secrecy::ExposeSecret as _;

    use super::{extract_api_key_prefix, generate_api_key, hash_api_key, verify_api_key_hash};

    #[test]
    fn generated_key_matches_the_persisted_wire_format() {
        let generated = generate_api_key().expect("operating-system entropy");
        let raw = generated.raw_key.expose_secret();
        assert!(raw.starts_with("newsly_ak_"));
        assert_eq!(generated.key_prefix.len(), "newsly_ak_".len() + 8);
        assert_eq!(raw.len(), generated.key_prefix.len() + 1 + 32);
        assert_eq!(
            extract_api_key_prefix(raw).expect("generated format"),
            generated.key_prefix
        );
    }

    #[test]
    fn hash_verification_accepts_only_the_original_secret() {
        let hash = hash_api_key("newsly_ak_a1b2c3d4_secret");
        assert!(verify_api_key_hash("newsly_ak_a1b2c3d4_secret", &hash));
        assert!(!verify_api_key_hash("newsly_ak_a1b2c3d4_other", &hash));
    }
}
