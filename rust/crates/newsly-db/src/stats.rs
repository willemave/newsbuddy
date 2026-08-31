use std::time::Duration;

use sqlx::PgPool;
use thiserror::Error;

#[derive(Debug, Clone, PartialEq, Eq, sqlx::FromRow)]
pub struct UnreadCountsProjection {
    pub article: i64,
    pub podcast: i64,
    pub news: i64,
}

pub async fn get_unread_counts(
    pool: &PgPool,
    user_id: i64,
) -> Result<UnreadCountsProjection, StatsRepositoryError> {
    Ok(sqlx::query_as::<_, UnreadCountsProjection>(
        r#"
        WITH valid_aggregators AS (
            SELECT
                lower(btrim(config::jsonb ->> 'key')) AS source_key,
                CASE
                    WHEN jsonb_typeof(config::jsonb -> 'topics') = 'array'
                    THEN config::jsonb -> 'topics'
                    ELSE '[]'::jsonb
                END AS topics
            FROM user_scraper_configs
            WHERE user_id::bigint = $1::bigint
              AND scraper_type = 'aggregator'
              AND is_active IS TRUE
              AND lower(btrim(config::jsonb ->> 'key')) = ANY(ARRAY[
                  'brutalist', 'finurls', 'hackernews', 'mediagazer',
                  'memeorandum', 'sciurls', 'techmeme'
              ])
        ), content_counts AS (
            SELECT c.content_type, count(*)::bigint AS item_count
            FROM contents AS c
            WHERE c.status = 'completed'
              AND (c.classification IS NULL OR c.classification <> 'skip')
              AND c.content_type <> 'news'
              AND EXISTS (
                  SELECT 1 FROM content_status AS cs
                  WHERE cs.content_id = c.id
                    AND cs.user_id::bigint = $1::bigint
                    AND cs.status = 'inbox'
              )
              AND NOT EXISTS (
                  SELECT 1 FROM content_read_status AS crs
                  WHERE crs.content_id = c.id
                    AND crs.user_id::bigint = $1::bigint
              )
            GROUP BY c.content_type
        ), news_count AS (
            SELECT count(*)::bigint AS item_count
            FROM news_items AS n
            WHERE n.status = 'ready'
              AND n.representative_news_item_id IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM news_item_read_status AS nrs
                  WHERE nrs.news_item_id = n.id
                    AND nrs.user_id::bigint = $1::bigint
              )
              AND (
                  (n.visibility_scope = 'user' AND n.owner_user_id::bigint = $1::bigint)
                  OR (
                      n.visibility_scope = 'global'
                      AND EXISTS (
                          SELECT 1 FROM valid_aggregators AS va
                          WHERE lower(n.platform) = va.source_key
                            AND (
                                va.source_key <> 'brutalist'
                                OR jsonb_array_length(va.topics) = 0
                                OR EXISTS (
                                    SELECT 1 FROM jsonb_array_elements_text(va.topics) AS topic(value)
                                    WHERE lower(btrim(topic.value)) = lower(btrim(
                                        n.raw_metadata::jsonb #>> '{aggregator,topic}'
                                    ))
                                )
                            )
                      )
                  )
              )
        )
        SELECT
            coalesce((SELECT item_count FROM content_counts WHERE content_type = 'article'), 0)::bigint AS article,
            coalesce((SELECT item_count FROM content_counts WHERE content_type = 'podcast'), 0)::bigint AS podcast,
            coalesce((SELECT item_count FROM news_count), 0)::bigint AS news
        "#,
    )
    .bind(user_id)
    .fetch_one(pool)
    .await?)
}

#[derive(Debug, Clone, PartialEq, Eq, sqlx::FromRow)]
pub struct ProcessingCountsProjection {
    pub processing_count: i64,
    pub long_form_count: i64,
    pub news_count: i64,
    pub news_crawl_count: i64,
}

pub async fn get_processing_counts(
    pool: &PgPool,
    user_id: i64,
    checkout_timeout: Duration,
) -> Result<ProcessingCountsProjection, StatsRepositoryError> {
    let timeout_seconds = i64::try_from(checkout_timeout.as_secs()).unwrap_or(i64::MAX);
    Ok(sqlx::query_as::<_, ProcessingCountsProjection>(
        r#"
        WITH valid_aggregators AS (
            SELECT
                lower(btrim(config::jsonb ->> 'key')) AS source_key,
                CASE
                    WHEN jsonb_typeof(config::jsonb -> 'topics') = 'array'
                    THEN config::jsonb -> 'topics'
                    ELSE '[]'::jsonb
                END AS topics
            FROM user_scraper_configs
            WHERE user_id::bigint = $1::bigint
              AND scraper_type = 'aggregator'
              AND is_active IS TRUE
              AND lower(btrim(config::jsonb ->> 'key')) = ANY(ARRAY[
                  'brutalist', 'finurls', 'hackernews', 'mediagazer',
                  'memeorandum', 'sciurls', 'techmeme'
              ])
        ), long_form AS (
            SELECT count(*)::bigint AS item_count
            FROM contents AS c
            WHERE EXISTS (
                SELECT 1 FROM content_status AS cs
                WHERE cs.content_id = c.id
                  AND cs.user_id::bigint = $1::bigint
                  AND cs.status = 'inbox'
            )
              AND (
                  c.content_type IN ('article', 'podcast')
                  OR (c.platform = 'youtube' AND c.content_type <> 'news')
              )
              AND (
                  (
                      c.status IN ('new', 'pending', 'processing')
                      AND (
                          EXISTS (
                              SELECT 1 FROM processing_tasks AS pt
                              WHERE pt.content_id = c.id
                                AND pt.status IN ('pending', 'processing')
                          )
                          OR (
                              c.checked_out_by IS NOT NULL
                              AND c.checked_out_at IS NOT NULL
                              AND c.checked_out_at >= timezone('UTC', now())
                                  - make_interval(secs => $2::bigint::integer)
                          )
                      )
                  )
                  OR (
                      c.status = 'awaiting_image'
                      AND EXISTS (
                          SELECT 1 FROM processing_tasks AS image_task
                          WHERE image_task.content_id = c.id
                            AND image_task.task_type = 'generate_image'
                            AND image_task.status IN ('pending', 'processing')
                      )
                  )
              )
        ), news AS (
            SELECT count(*)::bigint AS item_count
            FROM news_items AS n
            WHERE n.status IN ('new', 'processing')
              AND (
                  (n.visibility_scope = 'user' AND n.owner_user_id::bigint = $1::bigint)
                  OR (
                      n.visibility_scope = 'global'
                      AND EXISTS (
                          SELECT 1 FROM valid_aggregators AS va
                          WHERE lower(n.platform) = va.source_key
                            AND (
                                va.source_key <> 'brutalist'
                                OR jsonb_array_length(va.topics) = 0
                                OR EXISTS (
                                    SELECT 1 FROM jsonb_array_elements_text(va.topics) AS topic(value)
                                    WHERE lower(btrim(topic.value)) = lower(btrim(
                                        n.raw_metadata::jsonb #>> '{aggregator,topic}'
                                    ))
                                )
                            )
                      )
                  )
              )
        ), active_sources AS (
            SELECT DISTINCT source_key
            FROM (
                SELECT 'reddit'::text AS source_key
                FROM user_scraper_configs
                WHERE user_id::bigint = $1::bigint
                  AND scraper_type = 'reddit'
                  AND is_active IS TRUE
                UNION ALL
                SELECT lower(regexp_replace(config::jsonb ->> 'key', '[^a-z0-9]+', '', 'g'))
                FROM user_scraper_configs
                WHERE user_id::bigint = $1::bigint
                  AND scraper_type = 'aggregator'
                  AND is_active IS TRUE
                  AND lower(regexp_replace(config::jsonb ->> 'key', '[^a-z0-9]+', '', 'g')) = ANY(ARRAY[
                      'brutalist', 'finurls', 'hackernews', 'mediagazer',
                      'memeorandum', 'sciurls', 'techmeme'
                  ])
            ) AS configured
            WHERE source_key <> ''
        ), requested_sources AS (
            SELECT DISTINCT lower(regexp_replace(source.value, '[^a-z0-9]+', '', 'g')) AS source_key
            FROM processing_tasks AS pt
            CROSS JOIN LATERAL jsonb_array_elements_text(
                CASE
                    WHEN jsonb_typeof(pt.payload::jsonb -> 'sources') = 'array'
                    THEN pt.payload::jsonb -> 'sources'
                    ELSE '["all"]'::jsonb
                END
            ) AS source(value)
            WHERE pt.task_type = 'scrape'
              AND pt.status IN ('pending', 'processing')
        ), crawls AS (
            SELECT count(*)::bigint AS item_count
            FROM active_sources AS active
            WHERE EXISTS (
                SELECT 1 FROM requested_sources AS requested
                WHERE requested.source_key IN ('all', active.source_key)
            )
        )
        SELECT
            (long_form.item_count + news.item_count)::bigint AS processing_count,
            long_form.item_count::bigint AS long_form_count,
            news.item_count::bigint AS news_count,
            crawls.item_count::bigint AS news_crawl_count
        FROM long_form, news, crawls
        "#,
    )
    .bind(user_id)
    .bind(timeout_seconds)
    .fetch_one(pool)
    .await?)
}

pub async fn get_long_form_unread_count(
    pool: &PgPool,
    user_id: i64,
) -> Result<i64, StatsRepositoryError> {
    Ok(sqlx::query_scalar::<_, i64>(
        r#"
        SELECT count(*)::bigint
        FROM contents AS c
        WHERE c.status = 'completed'
          AND (c.classification IS NULL OR c.classification <> 'skip')
          AND (
              c.content_type IN ('article', 'podcast')
              OR (c.platform = 'youtube' AND c.content_type <> 'news')
          )
          AND EXISTS (
              SELECT 1 FROM content_status AS cs
              WHERE cs.content_id = c.id
                AND cs.user_id::bigint = $1::bigint
                AND cs.status = 'inbox'
          )
          AND NOT EXISTS (
              SELECT 1 FROM content_read_status AS crs
              WHERE crs.content_id = c.id
                AND crs.user_id::bigint = $1::bigint
          )
        "#,
    )
    .bind(user_id)
    .fetch_one(pool)
    .await?)
}

#[derive(Debug, Error)]
pub enum StatsRepositoryError {
    #[error("content statistics database operation failed")]
    Sqlx(#[from] sqlx::Error),
}
