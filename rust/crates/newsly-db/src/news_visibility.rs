//! Shared ownership and subscription visibility; callers retain their status filters.

pub(crate) const SUBSCRIBED_AGGREGATORS_CTE: &str = r#"WITH subscribed_aggregators AS MATERIALIZED (
    SELECT config::jsonb AS config
    FROM user_scraper_configs
    WHERE user_id = $1::bigint AND scraper_type = 'aggregator' AND is_active IS TRUE
)"#;

pub(crate) fn visible_news_predicate(alias: &str) -> String {
    format!(
        r#"(
        ({alias}.visibility_scope = 'user' AND {alias}.owner_user_id::bigint = $1::bigint)
        OR ({alias}.visibility_scope = 'global' AND EXISTS (
            SELECT 1 FROM subscribed_aggregators AS subscription
            WHERE aggregator_config_admits(subscription.config, {alias}.platform, {alias}.raw_metadata)
        ))
    )"#
    )
}
