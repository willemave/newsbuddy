use super::{NewsRow, Postgres, SUPPORTED_AGGREGATORS, Transaction};

pub(super) async fn firecrawl_fallback_allowed(
    transaction: &mut Transaction<'_, Postgres>,
    row: &NewsRow,
) -> Result<bool, sqlx::Error> {
    let platform = row
        .platform
        .as_deref()
        .unwrap_or_default()
        .trim()
        .to_lowercase();
    if row.visibility_scope != "global" || !SUPPORTED_AGGREGATORS.contains(&platform.as_str()) {
        return Ok(true);
    }
    sqlx::query_scalar(
        r#"
        SELECT EXISTS (
            SELECT 1
            FROM user_scraper_configs AS config
            JOIN users AS subscriber
              ON subscriber.id = config.user_id AND subscriber.is_active IS TRUE
            WHERE config.scraper_type = 'aggregator'
              AND config.is_active IS TRUE
              AND lower(btrim(COALESCE(config.config::jsonb ->> 'key', ''))) = $1
              AND (
                  $1 <> 'brutalist'
                  OR newsly_brutalist_visible(config.config::jsonb -> 'topics', $2::jsonb)
              )
        )
        "#,
    )
    .bind(platform)
    .bind(&row.raw_metadata)
    .fetch_one(&mut **transaction)
    .await
}
