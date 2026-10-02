-- One owner for "a subscriber's aggregator config admits this global news item".
--
-- Roll-forward only. The data normalization below is one-way. After rolling binaries back, the
-- function may be dropped, but normalized configs stay as written (older binaries read them with
-- case-insensitive matching, so they remain valid).
--
-- The catalog lists are frozen as of this migration. The function itself encodes no aggregator
-- keys, so adding an aggregator later needs no migration.

-- Canonical key spelling.
UPDATE user_scraper_configs
SET config = jsonb_set(config::jsonb, '{key}', to_jsonb(lower(btrim(config::jsonb ->> 'key'))))::json
WHERE scraper_type = 'aggregator'
  AND config::jsonb ->> 'key' IS DISTINCT FROM lower(btrim(config::jsonb ->> 'key'));

-- Keys outside the catalog (legacy aliases such as 'hn') can no longer admit anything; deactivate
-- them so they cannot expose unrelated global items.
UPDATE user_scraper_configs
SET is_active = FALSE, updated_at = timezone('UTC', now())
WHERE scraper_type = 'aggregator'
  AND is_active IS TRUE
  AND NOT COALESCE(config::jsonb ->> 'key', '') = ANY (ARRAY[
      'hackernews', 'techmeme', 'mediagazer', 'memeorandum', 'sciurls', 'finurls',
      'brutalist', 'arxiv', 'hfpapers'
  ]);

-- Topics only exist on topic aggregators, use the catalog spelling, and are absent when empty.
WITH offered(key, topic) AS (
    VALUES
        ('brutalist', 'science'), ('brutalist', 'business'),
        ('brutalist', 'politics'), ('brutalist', 'sports'),
        ('arxiv', 'cs.AI'), ('arxiv', 'cs.LG'), ('arxiv', 'cs.CL'),
        ('arxiv', 'cs.CV'), ('arxiv', 'stat.ML')
), normalized AS (
    SELECT config.id,
           (
               SELECT jsonb_agg(DISTINCT offered.topic ORDER BY offered.topic)
               FROM jsonb_array_elements_text(
                   CASE WHEN jsonb_typeof(config.config::jsonb -> 'topics') = 'array'
                        THEN config.config::jsonb -> 'topics' ELSE '[]'::jsonb END
               ) AS selected(topic)
               JOIN offered
                 ON offered.key = config.config::jsonb ->> 'key'
                AND lower(offered.topic) = lower(btrim(selected.topic))
           ) AS topics
    FROM user_scraper_configs AS config
    WHERE config.scraper_type = 'aggregator'
      AND config.config::jsonb ? 'topics'
)
UPDATE user_scraper_configs AS config
SET config = CASE
        WHEN normalized.topics IS NULL THEN config.config::jsonb - 'topics'
        ELSE jsonb_set(config.config::jsonb, '{topics}', normalized.topics)
    END::json,
    updated_at = timezone('UTC', now())
FROM normalized
WHERE config.id = normalized.id
  AND config.config::jsonb -> 'topics' IS DISTINCT FROM normalized.topics;

-- Exact matching relies on the normalization above and on providers writing `platform` and
-- `aggregator.topic` in catalog spelling. Items without a topic, and configs without a non-empty
-- topics array, are unfiltered. Keep this a single inlinable expression: no sub-select, STRICT,
-- SECURITY DEFINER, or SET clause, or every caller falls back to a per-row function call.
CREATE OR REPLACE FUNCTION aggregator_config_admits(config jsonb, platform text, metadata json)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT config ->> 'key' = platform
       AND CASE
               WHEN metadata #>> '{aggregator,topic}' IS NULL
                 OR jsonb_typeof(config -> 'topics') IS DISTINCT FROM 'array'
                 OR config -> 'topics' = '[]'::jsonb
               THEN TRUE
               ELSE config -> 'topics' ? (metadata #>> '{aggregator,topic}')
           END
$$;
