SELECT pg_catalog.jsonb_build_object(
    'alembic_heads', COALESCE((
        SELECT pg_catalog.jsonb_agg(version_num ORDER BY version_num)
        FROM public.alembic_version
    ), '[]'::jsonb),
    'retired_cerebras_chat_sessions', (
        SELECT count(*)
        FROM public.chat_sessions
        WHERE llm_provider = 'cerebras'
           OR llm_model LIKE 'cerebras:%'
    ),
    'retired_reddit_aggregator_configs', (
        SELECT count(*)
        FROM public.user_scraper_configs
        WHERE scraper_type = 'aggregator'
          AND (
              lower(feed_url) = 'aggregator://reddit'
              OR lower(config ->> 'key') = 'reddit'
          )
    )
)
