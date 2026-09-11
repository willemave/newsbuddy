-- no-transaction
CREATE INDEX CONCURRENTLY ix_news_lens_tasks_item ON processing_tasks ((payload->>'news_item_id'), status, created_at)
    WHERE task_type='prepare_news_lens';
