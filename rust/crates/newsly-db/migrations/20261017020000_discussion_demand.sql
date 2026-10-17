-- Shared item-level demand for scheduled discussion refreshes and paid model calls.
-- Initial summaries remain available after the merge window, including explicit opens.
CREATE OR REPLACE FUNCTION news_item_discussion_is_demanded(item_id bigint, initial_summary boolean)
RETURNS boolean
LANGUAGE sql
STABLE
PARALLEL SAFE
AS $$
SELECT EXISTS (
    SELECT 1
    FROM news_items AS item
    JOIN users AS demand_user ON demand_user.is_active IS TRUE
      AND (
          initial_summary IS TRUE
          OR COALESCE(item.published_at, item.processed_at, item.ingested_at, item.created_at)
              + interval '24 hours' > timezone('UTC', now())
      )
      AND (
          (item.visibility_scope = 'user'
              AND item.owner_user_id = demand_user.id)
          OR
          (item.visibility_scope = 'global' AND EXISTS (
              SELECT 1
              FROM user_scraper_configs AS config
              WHERE config.user_id = demand_user.id
                AND config.is_active IS TRUE
                AND config.scraper_type = 'aggregator'
                AND aggregator_config_admits(
                    config.config::jsonb, item.platform, item.raw_metadata
                )
          ))
      )
    WHERE item.id::bigint = item_id
      AND (
          (
              EXISTS (
                  SELECT 1 FROM briefing_pending_sources AS pending
                  WHERE pending.user_id = demand_user.id
                    AND pending.source_kind = 'news'
                    AND pending.source_id = item.id
              )
              OR EXISTS (
                  SELECT 1 FROM briefing_segments AS segment
                  WHERE segment.user_id = demand_user.id
                    AND segment.status IN ('active', 'degraded')
                    AND segment.source_keys::jsonb @>
                        jsonb_build_array('news:' || item.id::text)
              )
          )
          AND (
              initial_summary IS TRUE
              OR NOT EXISTS (
                  SELECT 1
                  FROM news_item_read_status AS read_status
                  JOIN news_items AS member ON member.id = read_status.news_item_id
                  WHERE read_status.user_id = demand_user.id
                    AND COALESCE(member.representative_news_item_id, member.id) = item.id
              )
          )
          OR (
              initial_summary IS TRUE
              AND EXISTS (
                  SELECT 1
                  FROM news_item_read_status AS read_status
                  JOIN news_items AS member ON member.id = read_status.news_item_id
                  WHERE read_status.user_id = demand_user.id
                    AND COALESCE(member.representative_news_item_id, member.id) = item.id
              )
          )
      )
);
$$;
