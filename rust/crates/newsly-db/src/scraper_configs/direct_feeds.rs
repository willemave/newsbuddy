use super::{
    AppliedFeedSubscription, DIRECT_FEED_SCRAPER_TYPES, FeedSubscriptionMutation,
    FeedSubscriptionReference, Postgres, ScraperConfigProjection, ScraperConfigRepositoryError,
    Transaction, Value, canonicalize_feed_url, carry_feed_url_aliases, lock_active_user,
    normalize_subscription_config, subscription_candidates,
};

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
    reference: Option<&FeedSubscriptionReference>,
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
    let feed_url = canonical_direct_feed_url(feed_url);
    if feed_url.is_empty() {
        return Err(ScraperConfigRepositoryError::InvalidFeedUrl);
    }
    lock_active_user(transaction, user_id).await?;

    // URL identity spans all direct scraper types. Serialize the read/insert decision without
    // upgrading the user lock, which may already be held by the caller's message fence.
    let mut identities = vec![feed_url.clone()];
    if let Some(reference) = reference {
        identities.push(canonical_direct_feed_url(&reference.feed_url));
    }
    identities.sort();
    identities.dedup();
    for identity in identities {
        lock_direct_feed_identity(transaction, user_id, &identity).await?;
    }
    let candidates =
        subscription_candidates(transaction, user_id, &DIRECT_FEED_SCRAPER_TYPES).await?;
    let existing = if let Some(reference) = reference {
        let mut candidates = candidates;
        let index = candidates
            .iter()
            .position(|candidate| candidate.id == reference.id)
            .ok_or(ScraperConfigRepositoryError::ChangedDuringPrepare)?;
        let candidate = candidates.remove(index);
        if subscription_feed_url(&candidate) != Some(reference.feed_url.as_str()) {
            return Err(ScraperConfigRepositoryError::ChangedDuringPrepare);
        }
        if canonical_direct_feed_url(&reference.feed_url) == feed_url {
            Some(candidate)
        } else {
            // A verified redirect may land on another owned subscription. Reuse that row
            // rather than rewriting the source into a duplicate of the canonical target.
            matching_direct_subscription(candidates, scraper_type, &feed_url).or(Some(candidate))
        }
    } else {
        matching_direct_subscription(candidates, scraper_type, &feed_url)
    };
    if let Some(existing) = existing {
        return activate_existing_subscription(
            transaction,
            user_id,
            existing,
            display_name,
            &feed_url,
            feed_format,
        )
        .await;
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

    let candidates =
        subscription_candidates(transaction, user_id, &DIRECT_FEED_SCRAPER_TYPES).await?;
    let existing = matching_direct_subscription(candidates, scraper_type, &feed_url)
        .ok_or(ScraperConfigRepositoryError::LostSubscriptionRace)?;
    activate_existing_subscription(
        transaction,
        user_id,
        existing,
        display_name,
        &feed_url,
        feed_format,
    )
    .await
}

pub(super) async fn lock_direct_feed_identity(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    identity: &str,
) -> Result<(), sqlx::Error> {
    sqlx::query("SELECT pg_advisory_xact_lock(hashtextextended($1::text, $2::bigint))")
        .bind(format!("direct-feed-subscription:{identity}"))
        .bind(user_id)
        .execute(&mut **transaction)
        .await?;
    Ok(())
}

pub fn canonical_direct_feed_url(value: &str) -> String {
    match url::Url::parse(value.trim()) {
        Ok(url) => canonicalize_feed_url(url.as_str()),
        Err(_) => canonicalize_feed_url(value),
    }
}

pub(super) fn subscription_feed_url(config: &ScraperConfigProjection) -> Option<&str> {
    config
        .feed_url
        .as_deref()
        .or_else(|| config.config.get("feed_url").and_then(Value::as_str))
}

/// Chooses the canonical existing direct subscription, preferring an active row.
pub fn matching_direct_subscription(
    candidates: Vec<ScraperConfigProjection>,
    requested_type: &str,
    feed_url: &str,
) -> Option<ScraperConfigProjection> {
    let feed_url = canonical_direct_feed_url(feed_url);
    candidates
        .into_iter()
        .filter(|candidate| {
            subscription_feed_url(candidate)
                .is_some_and(|url| canonical_direct_feed_url(url) == feed_url)
        })
        .min_by_key(|candidate| {
            (
                !candidate.is_active,
                candidate.scraper_type != requested_type,
                candidate.id,
            )
        })
}

async fn activate_existing_subscription(
    transaction: &mut Transaction<'_, Postgres>,
    user_id: i64,
    existing: ScraperConfigProjection,
    display_name: Option<&str>,
    feed_url: &str,
    feed_format: &str,
) -> Result<AppliedFeedSubscription, ScraperConfigRepositoryError> {
    // Parser normalization must not rewrite an equivalent stored URL: content metadata uses
    // that exact spelling to identify its source.
    let feed_url = subscription_feed_url(&existing)
        .filter(|stored| canonical_direct_feed_url(stored) == canonical_direct_feed_url(feed_url))
        .unwrap_or(feed_url);
    let mutation = if existing.is_active {
        FeedSubscriptionMutation::AlreadySubscribed
    } else {
        FeedSubscriptionMutation::Reactivated
    };
    let config = carry_feed_url_aliases(
        normalize_subscription_config(&existing.config, feed_url, feed_format),
        &existing.config,
        subscription_feed_url(&existing),
        Some(feed_url),
    );
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
    .bind(feed_url)
    .bind(config)
    .fetch_one(&mut **transaction)
    .await?;
    Ok(AppliedFeedSubscription {
        config: updated,
        mutation,
    })
}
