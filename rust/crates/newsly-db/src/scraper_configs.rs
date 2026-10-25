use chrono::{DateTime, Utc};
use newsly_domain::AggregatorKey;
use serde_json::Value;
use sqlx::{FromRow, PgPool, Postgres, Transaction};
use thiserror::Error;

/// Private attribution history; never used to decide subscription identity.
pub const FEED_URL_ALIASES_KEY: &str = "feed_url_aliases";

pub fn feed_url_aliases(config: &Value) -> impl Iterator<Item = &str> {
    config
        .get(FEED_URL_ALIASES_KEY)
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
}

fn carry_feed_url_aliases(
    mut config: Value,
    previous: &Value,
    old_url: Option<&str>,
    new_url: Option<&str>,
) -> Value {
    let mut aliases = Vec::new();
    for alias in feed_url_aliases(previous) {
        if !aliases.iter().any(|stored| stored == alias) {
            aliases.push(alias.to_owned());
        }
    }
    if let (Some(old), Some(new)) = (old_url, new_url)
        && canonical_direct_feed_url(old) != canonical_direct_feed_url(new)
        && url::Url::parse(old).is_ok_and(|url| matches!(url.scheme(), "http" | "https"))
        && !aliases.iter().any(|alias| alias == old)
    {
        aliases.push(old.to_owned());
    }
    if let Some(object) = config.as_object_mut() {
        object.remove(FEED_URL_ALIASES_KEY);
        if !aliases.is_empty() {
            object.insert(FEED_URL_ALIASES_KEY.to_owned(), Value::from(aliases));
        }
    }
    config
}

pub const DIRECT_FEED_SCRAPER_TYPES: [&str; 3] = ["substack", "atom", "podcast_rss"];

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
    TopicsUpdated,
}

impl FeedSubscriptionMutation {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Created => "created",
            Self::Reactivated => "reactivated",
            Self::AlreadySubscribed => "already_exists",
            Self::TopicsUpdated => "topics_updated",
        }
    }

    pub const fn needs_backfill(self) -> bool {
        matches!(self, Self::Created | Self::Reactivated)
    }
}

/// An owned subscription selected before external feed validation.
#[derive(Debug, Clone)]
pub struct FeedSubscriptionReference {
    pub id: i64,
    pub feed_url: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct AppliedFeedSubscription {
    pub config: ScraperConfigProjection,
    pub mutation: FeedSubscriptionMutation,
}

#[path = "scraper_configs/direct_feeds.rs"]
mod direct_feeds;
pub use direct_feeds::{
    apply_validated_feed_subscription, canonical_direct_feed_url, matching_direct_subscription,
};
use direct_feeds::{lock_direct_feed_identity, subscription_feed_url};

/// Follows a catalog aggregator, or reactivates the user's existing subscription to it.
///
/// `topics` must already be catalog-spelled. When given it replaces the stored topic filter, and
/// an empty slice means every topic. `None` keeps an existing filter. Aggregators read shared
/// global items, so there is no per-user backfill to enqueue.
pub async fn apply_aggregator_subscription(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    key: AggregatorKey,
    topics: Option<&[&str]>,
) -> Result<AppliedFeedSubscription, ScraperConfigRepositoryError> {
    lock_active_user(transaction, user_id).await?;
    let feed_url = format!("aggregator://{}", key.as_str());
    let existing = subscription_candidates(transaction, user_id, &["aggregator"])
        .await?
        .into_iter()
        .find(|candidate| candidate.feed_url.as_deref() == Some(feed_url.as_str()));
    let config = aggregator_subscription_config(
        existing.as_ref().map(|candidate| &candidate.config),
        key,
        topics,
    );
    if let Some(existing) = existing {
        let mutation =
            if existing.is_active && existing.config.get("topics") != config.get("topics") {
                FeedSubscriptionMutation::TopicsUpdated
            } else if existing.is_active {
                FeedSubscriptionMutation::AlreadySubscribed
            } else {
                FeedSubscriptionMutation::Reactivated
            };
        let config = sqlx::query_as::<_, ScraperConfigProjection>(
            r"
            UPDATE user_scraper_configs
            SET is_active = TRUE, config = $2, updated_at = timezone('UTC', now())
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
        .bind(existing.id)
        .bind(config)
        .fetch_one(&mut **transaction)
        .await?;
        return Ok(AppliedFeedSubscription { config, mutation });
    }
    let config = sqlx::query_as::<_, ScraperConfigProjection>(
        r"
        INSERT INTO user_scraper_configs (
            user_id, scraper_type, display_name, feed_url, config, is_active,
            created_at, updated_at
        )
        VALUES (
            $1::bigint::integer, 'aggregator', $2, $3, $4, TRUE,
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
    .bind(key.display_name())
    .bind(&feed_url)
    .bind(config)
    .fetch_optional(&mut **transaction)
    .await?
    .ok_or(ScraperConfigRepositoryError::LostSubscriptionRace)?;
    Ok(AppliedFeedSubscription {
        config,
        mutation: FeedSubscriptionMutation::Created,
    })
}

/// Same stored shape as onboarding: `{key, limit, topics?}` plus any unrelated existing fields.
fn aggregator_subscription_config(
    existing: Option<&Value>,
    key: AggregatorKey,
    topics: Option<&[&str]>,
) -> Value {
    let mut config = existing
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    config.insert("key".to_owned(), Value::from(key.as_str()));
    config.entry("limit").or_insert_with(|| Value::from(1));
    match topics {
        Some([]) => {
            config.remove("topics");
        }
        Some(topics) => {
            config.insert("topics".to_owned(), Value::from(topics.to_vec()));
        }
        None => {}
    }
    Value::Object(config)
}

async fn lock_active_user(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
) -> Result<(), ScraperConfigRepositoryError> {
    sqlx::query_scalar::<_, i64>(
        "SELECT id::bigint FROM users WHERE id::bigint = $1 AND is_active IS TRUE FOR SHARE",
    )
    .bind(user_id)
    .fetch_optional(&mut **transaction)
    .await?
    .map(|_| ())
    .ok_or(ScraperConfigRepositoryError::UserMissingOrInactive)
}

async fn subscription_candidates(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    scraper_types: &[&str],
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
        WHERE user_id::bigint = $1 AND scraper_type = ANY($2::text[])
        ORDER BY id
        FOR UPDATE
        ",
    )
    .bind(user_id)
    .bind(scraper_types)
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
    let direct = DIRECT_FEED_SCRAPER_TYPES.contains(&scraper_type);
    let types = if direct {
        DIRECT_FEED_SCRAPER_TYPES.to_vec()
    } else {
        vec![scraper_type]
    };
    let canonical_feed_url = if direct {
        canonical_direct_feed_url(canonical_feed_url)
    } else {
        canonicalize_feed_url(canonical_feed_url)
    };
    let candidates = sqlx::query_as::<_, IdentityCandidate>(
        r"
        SELECT id::bigint AS id, feed_url, config
        FROM user_scraper_configs
        WHERE user_id::bigint = $1 AND scraper_type = ANY($2::text[])
        ",
    )
    .bind(user_id)
    .bind(types)
    .fetch_all(pool)
    .await?;
    Ok(candidates.iter().any(|candidate| {
        let stored = candidate
            .feed_url
            .as_deref()
            .or_else(|| candidate.config.get("feed_url").and_then(Value::as_str));
        stored.is_some_and(|value| {
            (if direct {
                canonical_direct_feed_url(value)
            } else {
                canonicalize_feed_url(value)
            }) == canonical_feed_url
        })
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
    let direct = DIRECT_FEED_SCRAPER_TYPES.contains(&config.scraper_type);
    let types = if direct {
        DIRECT_FEED_SCRAPER_TYPES.to_vec()
    } else {
        vec![config.scraper_type]
    };
    if direct {
        lock_active_user(transaction, config.user_id).await?;
        lock_direct_feed_identity(
            transaction,
            config.user_id,
            &canonical_direct_feed_url(config.feed_url),
        )
        .await?;
    }
    let candidates = sqlx::query_as::<_, IdentityCandidate>(
        r"
        SELECT id::bigint AS id, feed_url, config
        FROM user_scraper_configs
        WHERE user_id::bigint = $1 AND scraper_type = ANY($2::text[])
        ORDER BY id
        FOR UPDATE
        ",
    )
    .bind(config.user_id)
    .bind(types)
    .fetch_all(&mut **transaction)
    .await?;
    if candidates.iter().any(|candidate| {
        let stored = candidate
            .feed_url
            .as_deref()
            .or_else(|| candidate.config.get("feed_url").and_then(Value::as_str));
        stored.is_some_and(|value| {
            if direct {
                canonical_direct_feed_url(value) == canonical_direct_feed_url(config.feed_url)
            } else {
                canonicalize_feed_url(value) == config.feed_url
            }
        })
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
    .bind(carry_feed_url_aliases(
        config.config.clone(),
        &Value::Null,
        None,
        None,
    ))
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
    if patch.display_name.is_none()
        && patch.feed_url.is_none()
        && patch.config.is_none()
        && patch.is_active.is_none()
    {
        return Ok(locked);
    }

    let old_url = subscription_feed_url(&locked);
    let feed_url = patch.feed_url.map(|next| {
        old_url
            .filter(|old| canonical_direct_feed_url(old) == canonical_direct_feed_url(next))
            .unwrap_or(next)
    });
    let config = (patch.config.is_some() || feed_url.is_some()).then(|| {
        let mut config = patch.config.unwrap_or(&locked.config).clone();
        if let (Some(object), Some(url)) = (config.as_object_mut(), feed_url) {
            object.insert("feed_url".into(), Value::from(url));
        }
        carry_feed_url_aliases(config, &locked.config, old_url, feed_url)
    });
    sqlx::query_as::<_, ScraperConfigProjection>(
        r"
        UPDATE user_scraper_configs
        SET
            display_name = COALESCE($3, display_name),
            feed_url = COALESCE($4, feed_url),
            config = COALESCE($5::json, config),
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
    .bind(feed_url)
    .bind(config)
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
    use serde_json::json;
    use sqlx::PgPool;

    use super::{ScraperConfigPatch, canonicalize_feed_url, update_scraper_config};

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
        assert_eq!(
            canonicalize_feed_url("https://Example.com:443/Feed/?Token=AbC#fragment"),
            "https://example.com:443/Feed?Token=AbC"
        );
        assert_ne!(
            canonicalize_feed_url("https://example.com/Feed?Token=AbC"),
            canonicalize_feed_url("https://example.com/feed?token=abc")
        );
    }

    #[sqlx::test]
    async fn config_update_accepts_json_patch_and_preserves_it_when_absent(pool: PgPool) {
        let user_id: i64 = sqlx::query_scalar(
            "INSERT INTO users (apple_id, email, is_admin, is_active) VALUES ('scraper-json', 'scraper-json@example.test', FALSE, TRUE) RETURNING id::bigint",
        )
        .fetch_one(&pool)
        .await
        .unwrap();
        let config_id: i64 = sqlx::query_scalar(
            "INSERT INTO user_scraper_configs (user_id, scraper_type, display_name, config) VALUES ($1::integer, 'atom', 'Original', '{}'::json) RETURNING id::bigint",
        )
        .bind(user_id)
        .fetch_one(&pool)
        .await
        .unwrap();

        let new_config = json!({"feed_url": "https://example.test/feed", "feed_format": "atom"});
        let mut transaction = pool.begin().await.unwrap();
        let updated = update_scraper_config(
            &mut transaction,
            user_id,
            config_id,
            "atom",
            &ScraperConfigPatch {
                display_name: Some("Renamed"),
                config: Some(&new_config),
                ..ScraperConfigPatch::default()
            },
        )
        .await
        .unwrap();
        assert_eq!(updated.config, new_config);
        transaction.commit().await.unwrap();

        let mut transaction = pool.begin().await.unwrap();
        let updated = update_scraper_config(
            &mut transaction,
            user_id,
            config_id,
            "atom",
            &ScraperConfigPatch {
                display_name: Some("Renamed again"),
                ..ScraperConfigPatch::default()
            },
        )
        .await
        .unwrap();
        assert_eq!(updated.config, new_config);
        transaction.commit().await.unwrap();
    }
}

#[cfg(test)]
#[path = "scraper_configs/direct_subscription_tests.rs"]
mod direct_subscription_tests;
