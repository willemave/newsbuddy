ALTER TABLE news_item_discussions
    ADD COLUMN refresh_claim_token uuid;

ALTER TABLE news_item_discussions
    ALTER COLUMN summary_incremental_update_count SET DEFAULT 0;
