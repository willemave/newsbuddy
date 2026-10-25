//! Fixed SQL fragments for News reads; user values are always bound by the caller.
use crate::news_visibility::visible_news_predicate;

pub(super) const NEWS_COLUMNS: &str = r#"
            item.id::bigint AS news_item_id,
            COALESCE(
                NULLIF(BTRIM(item.raw_metadata->'summary'->>'title'), ''),
                NULLIF(BTRIM(item.raw_metadata->'article'->>'title'), ''),
                NULLIF(BTRIM(item.summary_text), ''),
                'Untitled'
            ) AS title,
            NULLIF(BTRIM(item.source_label), '') AS source,
            item.article_url,
            item.canonical_story_url AS story_url,
            item.discussion_url,
            NULLIF(BTRIM(item.summary_text), '') AS summary,
            item.summary_key_points::jsonb AS key_points,
            COALESCE(item.published_at, item.processed_at, item.ingested_at, item.created_at) AS published_at,
            read.id IS NOT NULL AS is_read
"#;

pub(super) const NEWS_FROM: &str = r#"
        FROM news_items AS item
        JOIN users AS account ON account.id::bigint = $1::bigint AND account.is_active = TRUE
        LEFT JOIN news_item_read_status AS read
          ON read.news_item_id = item.id AND read.user_id::bigint = $1::bigint
"#;

pub(super) fn news_visibility_filter() -> String {
    format!(
        "WHERE item.status = 'ready' AND item.representative_news_item_id IS NULL AND {}",
        visible_news_predicate("item")
    )
}

pub(super) const NEWS_SEARCH_FILTER: &str = r#"
          AND (
              BTRIM($2::text) = ''
              OR (
                  setweight(to_tsvector('english', COALESCE(item.raw_metadata->'summary'->>'title', '')), 'A')
                  || setweight(to_tsvector('english', COALESCE(item.raw_metadata->'article'->>'title', '')), 'B')
                  || setweight(to_tsvector('english', COALESCE(item.summary_text, '')), 'C')
                  || setweight(to_tsvector('english', COALESCE(item.source_label, '') || ' ' || COALESCE(item.article_domain, '') || ' ' || COALESCE(item.raw_metadata->'cluster'->>'related_titles', '')), 'D')
              ) @@ websearch_to_tsquery('english', $2::text)
              OR COALESCE(item.raw_metadata->'summary'->>'title', '') OPERATOR(public.%) $2::text
              OR COALESCE(item.raw_metadata->'article'->>'title', '') OPERATOR(public.%) $2::text
              OR GREATEST(
                  public.word_similarity($2::text, COALESCE(item.raw_metadata->'summary'->>'title', '')),
                  public.word_similarity($2::text, COALESCE(item.raw_metadata->'article'->>'title', '')),
                  public.word_similarity($2::text, COALESCE(item.source_label, '')),
                  public.word_similarity($2::text, COALESCE(item.article_domain, '')),
                  public.word_similarity($2::text, COALESCE(item.raw_metadata->'cluster'->>'related_titles', ''))
              ) >= 0.5
          )
"#;

pub(super) const NEWS_SEARCH_RANK: &str = r#"
CASE WHEN BTRIM($2::text) = '' THEN 0 ELSE GREATEST(
              ts_rank_cd(
                  setweight(to_tsvector('english', COALESCE(item.raw_metadata->'summary'->>'title', '')), 'A')
                  || setweight(to_tsvector('english', COALESCE(item.raw_metadata->'article'->>'title', '')), 'B')
                  || setweight(to_tsvector('english', COALESCE(item.summary_text, '')), 'C')
                  || setweight(to_tsvector('english', COALESCE(item.source_label, '') || ' ' || COALESCE(item.article_domain, '') || ' ' || COALESCE(item.raw_metadata->'cluster'->>'related_titles', '')), 'D'),
                  websearch_to_tsquery('english', $2::text)
              ),
              GREATEST(
                  public.word_similarity($2::text, COALESCE(item.raw_metadata->'summary'->>'title', '')),
                  public.word_similarity($2::text, COALESCE(item.raw_metadata->'article'->>'title', '')),
                  public.word_similarity($2::text, COALESCE(item.source_label, '')),
                  public.word_similarity($2::text, COALESCE(item.article_domain, '')),
                  public.word_similarity($2::text, COALESCE(item.raw_metadata->'cluster'->>'related_titles', ''))
              ) * 0.25
          ) END
"#;
