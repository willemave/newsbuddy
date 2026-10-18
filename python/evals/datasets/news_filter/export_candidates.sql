WITH eligible AS (
SELECT id,platform,article_domain,published_at,ingested_at,processed_at,created_at,
COALESCE(NULLIF(trim(raw_metadata::jsonb#>>'{summary,title}'),''),NULLIF(trim(raw_metadata::jsonb#>>'{article,title}'),''),left(trim(summary_text),120)) AS title,
trim(summary_text) AS summary,summary_key_points::jsonb AS key_points,
COALESCE(raw_metadata::jsonb#>>'{aggregator,topic}','unclassified') AS topic,
regexp_replace(COALESCE(NULLIF(trim(article_url),''),NULLIF(trim(canonical_story_url),''),NULLIF(trim(canonical_item_url),'')),'[?#].*$','') AS public_url,
md5(COALESCE(NULLIF(trim(canonical_story_url),''),NULLIF(trim(article_url),''),canonical_item_url)) AS canonical_url_hash,
md5(trim(summary_text)) AS exact_summary_hash,
row_number() OVER (PARTITION BY COALESCE(raw_metadata::jsonb#>>'{aggregator,topic}','unclassified'),article_domain ORDER BY md5(id::text||'__SEED__')) AS publisher_rank
FROM news_items
WHERE visibility_scope='global' AND owner_user_id IS NULL AND status='ready'
AND representative_news_item_id IS NULL AND NULLIF(trim(summary_text),'') IS NOT NULL
AND platform='__PLATFORM__' AND ingested_at >= timestamp '__AS_OF_UTC__'-interval '__WINDOW_DAYS__ days' AND ingested_at <= timestamp '__AS_OF_UTC__'
),topic_balanced AS (
SELECT *,row_number() OVER (PARTITION BY topic ORDER BY publisher_rank,md5(id::text||'__SEED__')) AS topic_rank FROM eligible
),selected AS (SELECT * FROM topic_balanced ORDER BY topic_rank,md5(id::text||'__SEED__') LIMIT 500)
SELECT selected.*,COALESCE((SELECT jsonb_agg(DISTINCT jsonb_build_object('aggregator',member.platform,'topic',member.raw_metadata::jsonb#>>'{aggregator,topic}','publisher_domain',member.article_domain)) FROM news_items member WHERE (member.id=selected.id OR member.representative_news_item_id=selected.id) AND member.visibility_scope='global' AND member.owner_user_id IS NULL),'[]'::jsonb) AS aggregator_provenance
FROM selected ORDER BY topic_rank,md5(id::text||'__SEED__')
