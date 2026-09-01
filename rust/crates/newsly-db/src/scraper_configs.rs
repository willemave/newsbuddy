use chrono::{DateTime, Utc};
use serde_json::Value;
use sqlx::{FromRow, PgPool, Postgres, Transaction};
use thiserror::Error;

const DIRECT_FEED_SCRAPER_TYPES: [&str; 3] = ["substack", "atom", "podcast_rss"];

#[derive(Debug, Clone, PartialEq, FromRow)]
pub struct ScraperConfigProjection {
    pub id: i64,
    pub user_id: i64,
    pub scraper_type: String,
    pub display_name: Option<String>,
    pub feed_url: Option<String>,
    pub config: Value,
    pub is_active: bool,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Clone)]
pub struct NewScraperConfig<'a> {
    pub user_id: i64,
    pub scraper_type: &'a str,
    pub display_name: Option<&'a str>,
    pub feed_url: &'a str,
    pub config: &'a Value,
    pub is_active: bool,
}

#[derive(Debug, Clone, Default)]
pub struct ScraperConfigPatch<'a> {
    pub display_name: Option<&'a str>,
    pub feed_url: Option<&'a str>,
    pub config: Option<&'a Value>,
    pub is_active: Option<bool>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FeedSubscriptionMutation {
    Created,
    Reactivated,
    AlreadySubscribed,
}

impl FeedSubscriptionMutation {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Created => "created",
            Self::Reactivated => "reactivated",
            Self::AlreadySubscribed => "already_exists",
        }
    }

    pub const fn needs_backfill(self) -> bool {
        matches!(self, Self::Created | Self::Reactivated)
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct AppliedFeedSubscription {
    pub config: ScraperConfigProjection,
    pub mutation: FeedSubscriptionMutation,
}

/// Activates an already host-validated RSS, Atom, or podcast feed.
///
/// Network validation belongs in the caller's external-work phase. This operation only locks the
/// durable user/config rows and returns the exact mutation so the caller can enqueue an initial
/// backfill in the same transaction.
pub async fn apply_validated_feed_subscription(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    scraper_type: &str,
    feed_format: &str,
    display_name: Option<&str>,
    feed_url: &str,
) -> Result<AppliedFeedSubscription, ScraperConfigRepositoryError> {
    let scraper_type = scraper_type.trim();
    if !DIRECT_FEED_SCRAPER_TYPES.contains(&scraper_type) {
        return Err(ScraperConfigRepositoryError::UnsupportedFeedType(
            scraper_type.to_owned(),
        ));
    }
    let feed_format = feed_format.trim();
    if !matches!(feed_format, "rss" | "atom") {
        return Err(ScraperConfigRepositoryError::UnsupportedFeedFormat(
            feed_format.to_owned(),
        ));
    }
    let feed_url = canonicalize_feed_url(feed_url);
    if feed_url.is_empty() {
        return Err(ScraperConfigRepositoryError::InvalidFeedUrl);
    }
    let active_user = sqlx::query_scalar::<_, i64>(
        "SELECT id::bigint FROM users WHERE id::bigint = $1 AND is_active IS TRUE FOR SHARE",
    )
    .bind(user_id)
    .fetch_optional(&mut **transaction)
    .await?;
    if active_user.is_none() {
        return Err(ScraperConfigRepositoryError::UserMissingOrInactive);
    }

    let candidates = subscription_candidates(transaction, user_id, scraper_type).await?;
    if let Some(existing) = candidates.into_iter().find(|candidate| {
        candidate
            .feed_url
            .as_deref()
            .or_else(|| candidate.config.get("feed_url").and_then(Value::as_str))
            .is_some_and(|value| canonicalize_feed_url(value) == feed_url)
    }) {
        let mutation = if existing.is_active {
            FeedSubscriptionMutation::AlreadySubscribed
        } else {
            FeedSubscriptionMutation::Reactivated
        };
        let config = normalize_subscription_config(&existing.config, &feed_url, feed_format);
        let updated = sqlx::query_as::<_, ScraperConfigProjection>(
            r"
            UPDATE user_scraper_configs
            SET
                display_name = CASE
                    WHEN NULLIF(display_name, '') IS NULL THEN NULLIF(left($3, 255), '')
                    ELSE display_name
                END,
                feed_url = $4,
                config = $5,
                is_active = TRUE,
                updated_at = timezone('UTC', now())
            WHERE id::bigint = $1 AND user_id::bigint = $2
            RETURNING
                id::bigint AS id,
                user_id::bigint AS user_id,
                scraper_type,
                display_name,
                feed_url,
                config,
                is_active,
                timezone('UTC', created_at) AS created_at
            ",
        )
        .bind(existing.id)
        .bind(user_id)
        .bind(display_name)
        .bind(&feed_url)
        .bind(config)
        .fetch_one(&mut **transaction)
        .await?;
        return Ok(AppliedFeedSubscription {
            config: updated,
            mutation,
        });
    }

    let config = normalize_subscription_config(&Value::Null, &feed_url, feed_format);
    let inserted = sqlx::query_as::<_, ScraperConfigProjection>(
        r"
        INSERT INTO user_scraper_configs (
            user_id, scraper_type, display_name, feed_url, config, is_active,
            created_at, updated_at
        )
        VALUES (
            $1::bigint::integer, $2, NULLIF(left($3, 255), ''), $4, $5, TRUE,
            timezone('UTC', now()), timezone('UTC', now())
        )
        ON CONFLICT (user_id, scraper_type, feed_url) DO NOTHING
        RETURNING
            id::bigint AS id,
            user_id::bigint AS user_id,
            scraper_type,
            display_name,
            feed_url,
            config,
            is_active,
            timezone('UTC', created_at) AS created_at
        ",
    )
    .bind(user_id)
    .bind(scraper_type)
    .bind(display_name)
    .bind(&feed_url)
    .bind(config)
    .fetch_optional(&mut **transaction)
    .await?;
    if let Some(config) = inserted {
        return Ok(AppliedFeedSubscription {
            config,
            mutation: FeedSubscriptionMutation::Created,
        });
    }

    let config = subscription_candidates(transaction, user_id, scraper_type)
        .await?
        .into_iter()
        .find(|candidate| candidate.feed_url.as_deref() == Some(feed_url.as_str()))
        .ok_or(ScraperConfigRepositoryError::LostSubscriptionRace)?;
    if !config.is_active {
        let normalized = normalize_subscription_config(&config.config, &feed_url, feed_format);
        let config = sqlx::query_as::<_, ScraperConfigProjection>(
            r"
            UPDATE user_scraper_configs
            SET is_active = TRUE, feed_url = $2, config = $3,
                updated_at = timezone('UTC', now())
            WHERE id::bigint = $1
            RETURNING
                id::bigint AS id,
                user_id::bigint AS user_id,
                scraper_type,
                display_name,
                feed_url,
                config,
                is_active,
                timezone('UTC', created_at) AS created_at
            ",
        )
        .bind(config.id)
        .bind(&feed_url)
        .bind(normalized)
        .fetch_one(&mut **transaction)
        .await?;
        return Ok(AppliedFeedSubscription {
            config,
            mutation: FeedSubscriptionMutation::Reactivated,
        });
    }
    Ok(AppliedFeedSubscription {
        mutation: FeedSubscriptionMutation::AlreadySubscribed,
        config,
    })
}

async fn subscription_candidates(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    scraper_type: &str,
) -> Result<Vec<ScraperConfigProjection>, sqlx::Error> {
    sqlx::query_as::<_, ScraperConfigProjection>(
        r"
        SELECT
            id::bigint AS id,
            user_id::bigint AS user_id,
            scraper_type,
            display_name,
            feed_url,
            config,
            is_active,
            timezone('UTC', created_at) AS created_at
        FROM user_scraper_configs
        WHERE user_id::bigint = $1 AND scraper_type = $2
        ORDER BY id
        FOR UPDATE
        ",
    )
    .bind(user_id)
    .bind(scraper_type)
    .fetch_all(&mut **transaction)
    .await
}

fn normalize_subscription_config(config: &Value, feed_url: &str, feed_format: &str) -> Value {
    let mut config = config.as_object().cloned().unwrap_or_default();
    config.insert("feed_url".to_owned(), Value::String(feed_url.to_owned()));
    config.insert(
        "feed_format".to_owned(),
        Value::String(feed_format.to_owned()),
    );
    config
        .entry("limit".to_owned())
        .or_insert_with(|| Value::from(1));
    Value::Object(config)
}

/// List a user's scraper configurations in the stable newest-first order.
///
/// # Errors
///
/// Returns [`ScraperConfigRepositoryError`] when the database query fails.
pub async fn list_scraper_configs(
    pool: &PgPool,
    user_id: i64,
    allowed_types: Option<&[String]>,
) -> Result<Vec<ScraperConfigProjection>, ScraperConfigRepositoryError> {
    let rows = match allowed_types {
        Some(allowed_types) => {
            sqlx::query_as::<_, ScraperConfigProjection>(
                r"
                SELECT
                    id::bigint AS id,
                    user_id::bigint AS user_id,
                    scraper_type,
                    display_name,
                    feed_url,
                    config,
                    is_active,
                    timezone('UTC', created_at) AS created_at
                FROM user_scraper_configs
                WHERE user_id::bigint = $1
                  AND scraper_type = ANY($2::text[])
                ORDER BY created_at DESC
                ",
            )
            .bind(user_id)
            .bind(allowed_types)
            .fetch_all(pool)
            .await?
        }
        None => {
            sqlx::query_as::<_, ScraperConfigProjection>(
                r"
                SELECT
                    id::bigint AS id,
                    user_id::bigint AS user_id,
                    scraper_type,
                    display_name,
                    feed_url,
                    config,
                    is_active,
                    timezone('UTC', created_at) AS created_at
                FROM user_scraper_configs
                WHERE user_id::bigint = $1
                ORDER BY created_at DESC
                ",
            )
            .bind(user_id)
            .fetch_all(pool)
            .await?
        }
    };
    Ok(rows)
}

/// Load immutable update-planning data without retaining a transaction across external work.
///
/// # Errors
///
/// Returns [`ScraperConfigRepositoryError`] when the database query fails.
pub async fn find_scraper_config(
    pool: &PgPool,
    user_id: i64,
    config_id: i64,
) -> Result<Option<ScraperConfigProjection>, ScraperConfigRepositoryError> {
    Ok(sqlx::query_as::<_, ScraperConfigProjection>(
        r"
        SELECT
            id::bigint AS id,
            user_id::bigint AS user_id,
            scraper_type,
            display_name,
            feed_url,
            config,
            is_active,
            timezone('UTC', created_at) AS created_at
        FROM user_scraper_configs
        WHERE id::bigint = $1 AND user_id::bigint = $2
        ",
    )
    .bind(config_id)
    .bind(user_id)
    .fetch_optional(pool)
    .await?)
}

/// Checks the persisted subscription identity before performing an expensive external feed probe.
/// The fenced create transaction repeats this check so a concurrent writer cannot win unnoticed.
///
/// # Errors
///
/// Returns [`ScraperConfigRepositoryError`] when the database query fails.
pub async fn scraper_config_identity_exists(
    pool: &PgPool,
    user_id: i64,
    scraper_type: &str,
    canonical_feed_url: &str,
) -> Result<bool, ScraperConfigRepositoryError> {
    let canonical_feed_url = canonicalize_feed_url(canonical_feed_url);
    let candidates = sqlx::query_as::<_, IdentityCandidate>(
        r"
        SELECT id::bigint AS id, feed_url, config
        FROM user_scraper_configs
        WHERE user_id::bigint = $1 AND scraper_type = $2
        ",
    )
    .bind(user_id)
    .bind(scraper_type)
    .fetch_all(pool)
    .await?;
    Ok(candidates.iter().any(|candidate| {
        let stored = candidate
            .feed_url
            .as_deref()
            .or_else(|| candidate.config.get("feed_url").and_then(Value::as_str));
        stored.is_some_and(|value| canonicalize_feed_url(value) == canonical_feed_url)
    }))
}

/// Insert a normalized config inside the caller's fenced transaction.
///
/// # Errors
///
/// Returns [`ScraperConfigRepositoryError`] for duplicate identities or database failures.
pub async fn create_scraper_config(
    transaction: &mut Transaction<'_, Postgres>,
    config: &NewScraperConfig<'_>,
) -> Result<ScraperConfigProjection, ScraperConfigRepositoryError> {
    let candidates = sqlx::query_as::<_, IdentityCandidate>(
        r"
        SELECT id::bigint AS id, feed_url, config
        FROM user_scraper_configs
        WHERE user_id::bigint = $1 AND scraper_type = $2
        FOR UPDATE
        ",
    )
    .bind(config.user_id)
    .bind(config.scraper_type)
    .fetch_all(&mut **transaction)
    .await?;
    if candidates.iter().any(|candidate| {
        let stored = candidate
            .feed_url
            .as_deref()
            .or_else(|| candidate.config.get("feed_url").and_then(Value::as_str));
        stored.is_some_and(|value| canonicalize_feed_url(value) == config.feed_url)
    }) {
        return Err(ScraperConfigRepositoryError::AlreadyExists);
    }

    let inserted = sqlx::query_as::<_, ScraperConfigProjection>(
        r"
        INSERT INTO user_scraper_configs (
            user_id,
            scraper_type,
            display_name,
            feed_url,
            config,
            is_active,
            created_at,
            updated_at
        )
        VALUES (
            $1::bigint::integer,
            $2,
            $3,
            $4,
            $5,
            $6,
            timezone('UTC', now()),
            timezone('UTC', now())
        )
        RETURNING
            id::bigint AS id,
            user_id::bigint AS user_id,
            scraper_type,
            display_name,
            feed_url,
            config,
            is_active,
            timezone('UTC', created_at) AS created_at
        ",
    )
    .bind(config.user_id)
    .bind(config.scraper_type)
    .bind(config.display_name)
    .bind(config.feed_url)
    .bind(config.config)
    .bind(config.is_active)
    .fetch_one(&mut **transaction)
    .await;
    inserted.map_err(map_write_error)
}

/// Lock and update a user-owned config in the caller's fenced finalize transaction.
///
/// # Errors
///
/// Returns [`ScraperConfigRepositoryError`] when the row is missing, changed, duplicated, or the
/// database operation fails.
pub async fn update_scraper_config(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    config_id: i64,
    expected_scraper_type: &str,
    patch: &ScraperConfigPatch<'_>,
) -> Result<ScraperConfigProjection, ScraperConfigRepositoryError> {
    let locked = sqlx::query_as::<_, ScraperConfigProjection>(
        r"
        SELECT
            id::bigint AS id,
            user_id::bigint AS user_id,
            scraper_type,
            display_name,
            feed_url,
            config,
            is_active,
            timezone('UTC', created_at) AS created_at
        FROM user_scraper_configs
        WHERE id::bigint = $1 AND user_id::bigint = $2
        FOR UPDATE
        ",
    )
    .bind(config_id)
    .bind(user_id)
    .fetch_optional(&mut **transaction)
    .await?
    .ok_or(ScraperConfigRepositoryError::NotFound)?;
    if locked.scraper_type != expected_scraper_type {
        return Err(ScraperConfigRepositoryError::ChangedDuringPrepare);
    }
    if patch.display_name.is_none() && patch.config.is_none() && patch.is_active.is_none() {
        return Ok(locked);
    }

    sqlx::query_as::<_, ScraperConfigProjection>(
        r"
        UPDATE user_scraper_configs
        SET
            display_name = COALESCE($3, display_name),
            feed_url = COALESCE($4, feed_url),
            config = COALESCE($5, config),
            is_active = COALESCE($6, is_active),
            updated_at = timezone('UTC', now())
        WHERE id::bigint = $1 AND user_id::bigint = $2
        RETURNING
            id::bigint AS id,
            user_id::bigint AS user_id,
            scraper_type,
            display_name,
            feed_url,
            config,
            is_active,
            timezone('UTC', created_at) AS created_at
        ",
    )
    .bind(config_id)
    .bind(user_id)
    .bind(patch.display_name)
    .bind(patch.feed_url)
    .bind(patch.config)
    .bind(patch.is_active)
    .fetch_one(&mut **transaction)
    .await
    .map_err(map_write_error)
}

/// Delete only a config owned by the authenticated user.
///
/// # Errors
///
/// Returns [`ScraperConfigRepositoryError`] when the row is missing or the database delete fails.
pub async fn delete_scraper_config(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    config_id: i64,
) -> Result<(), ScraperConfigRepositoryError> {
    let deleted = sqlx::query_scalar::<_, i64>(
        r"
        DELETE FROM user_scraper_configs
        WHERE id::bigint = $1 AND user_id::bigint = $2
        RETURNING id::bigint
        ",
    )
    .bind(config_id)
    .bind(user_id)
    .fetch_optional(&mut **transaction)
    .await?;
    deleted
        .map(|_| ())
        .ok_or(ScraperConfigRepositoryError::NotFound)
}

#[derive(Debug, FromRow)]
struct IdentityCandidate {
    #[allow(dead_code)]
    id: i64,
    feed_url: Option<String>,
    config: Value,
}

fn map_write_error(error: sqlx::Error) -> ScraperConfigRepositoryError {
    if error
        .as_database_error()
        .is_some_and(|database| database.code().as_deref() == Some("23505"))
    {
        ScraperConfigRepositoryError::AlreadyExists
    } else {
        ScraperConfigRepositoryError::Sqlx(error)
    }
}

/// Stable URL identity retained for existing subscriptions and deduplication keys.
pub fn canonicalize_feed_url(value: &str) -> String {
    let trimmed = value.trim();
    let without_fragment = trimmed
        .split_once('#')
        .map_or(trimmed, |(prefix, _)| prefix);
    let Some((scheme, remainder)) = without_fragment.split_once("://") else {
        return trimmed.trim_end_matches('/').to_owned();
    };
    if scheme.is_empty()
        || !scheme.bytes().enumerate().all(|(index, byte)| match index {
            0 => byte.is_ascii_alphabetic(),
            _ => byte.is_ascii_alphanumeric() || matches!(byte, b'+' | b'-' | b'.'),
        })
    {
        return trimmed.trim_end_matches('/').to_owned();
    }
    let authority_end = remainder.find(['/', '?']).unwrap_or(remainder.len());
    let authority = &remainder[..authority_end];
    if authority.is_empty() {
        return trimmed.trim_end_matches('/').to_owned();
    }
    let suffix = &remainder[authority_end..];
    let (path, query) = suffix
        .split_once('?')
        .map_or((suffix, None), |(path, query)| (path, Some(query)));
    let mut canonical = format!(
        "{}://{}{}",
        scheme.to_ascii_lowercase(),
        authority.to_ascii_lowercase(),
        path.trim_end_matches('/')
    );
    if let Some(query) = query {
        canonical.push('?');
        canonical.push_str(query);
    }
    canonical
}

#[derive(Debug, Error)]
pub enum ScraperConfigRepositoryError {
    #[error("scraper config not found")]
    NotFound,
    #[error("scraper config already exists for this feed")]
    AlreadyExists,
    #[error("scraper config changed during external validation")]
    ChangedDuringPrepare,
    #[error("unsupported direct feed type: {0}")]
    UnsupportedFeedType(String),
    #[error("unsupported direct feed format: {0}")]
    UnsupportedFeedFormat(String),
    #[error("validated feed URL is empty")]
    InvalidFeedUrl,
    #[error("subscription user is missing or inactive")]
    UserMissingOrInactive,
    #[error("concurrent feed subscription was not visible after conflict")]
    LostSubscriptionRace,
    #[error("scraper config database operation failed")]
    Sqlx(#[from] sqlx::Error),
}

#[cfg(test)]
mod tests {
    use super::canonicalize_feed_url;

    #[test]
    fn canonical_url_identity_preserves_existing_rules() {
        assert_eq!(
            canonicalize_feed_url("  HTTPS://Example.COM/feed/?a=1#fragment  "),
            "https://example.com/feed?a=1"
        );
        assert_eq!(
            canonicalize_feed_url("aggregator://HackerNews/"),
            "aggregator://hackernews"
        );
        assert_eq!(canonicalize_feed_url("relative/feed/"), "relative/feed");
    }
}
