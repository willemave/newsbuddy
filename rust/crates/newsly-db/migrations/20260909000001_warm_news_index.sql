-- no-transaction
CREATE INDEX CONCURRENTLY ix_news_items_onboarding_check ON news_items(lower(platform), ingested_at)
    WHERE visibility_scope='global';
